"""エージェントループ本体。

Messages API を呼び、返ってきた tool_use を実行し、その結果を積んでまた呼ぶ。
これを stop_reason が tool_use でなくなるまで繰り返す。
tool_runner ではなく手書きのループにしているのは、1ツールごとに権限確認を挟み、
端末表示を自前で制御したいため。
"""

from __future__ import annotations

from typing import Any

import anthropic

from .config import Config
from .permissions import PermissionGate
from .prompts import build_system_prompt, load_project_context
from .session import Session
from .tools import ToolError, Toolbox
from .ui import UI


class AgentError(Exception):
    """ターンを続行できない致命的な問題。"""


class Agent:
    """1つの会話を進めるエージェント。"""

    def __init__(
        self,
        *,
        client: anthropic.Anthropic,
        config: Config,
        toolbox: Toolbox,
        session: Session,
        ui: UI,
    ) -> None:
        self.client = client
        self.config = config
        self.toolbox = toolbox
        self.session = session
        self.ui = ui
        self.gate = PermissionGate(config.permission_mode)
        # システムプロンプトはセッション中固定にする(キャッシュを壊さないため)
        self.system_prompt = build_system_prompt(
            config.workspace,
            extra_context=load_project_context(config.workspace),
        )

    # ------------------------------------------------------------------
    # リクエストの組み立て
    # ------------------------------------------------------------------
    def _build_params(self) -> dict[str, Any]:
        """Messages API に渡すパラメータを組み立てる。"""
        spec = self.config.model
        params: dict[str, Any] = {
            "model": spec.id,
            "max_tokens": self.config.max_tokens,
            "system": [
                {
                    "type": "text",
                    "text": self.system_prompt,
                    # tools + system をまとめてキャッシュする
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "tools": self.toolbox.api_schemas(),
            "messages": self.session.messages,
        }
        if spec.supports_effort:
            params["output_config"] = {"effort": self.config.effort}
        if spec.supports_adaptive_thinking:
            params["thinking"] = {
                "type": "adaptive",
                "display": self.config.thinking_display,
            }
        return params

    def _stream_once(self) -> Any:
        """1リクエスト分をストリーミングし、確定したメッセージを返す。"""
        params = self._build_params()
        spec = self.config.model

        if spec.supports_fallbacks:
            # 安全性分類器による refusal をフォールバックモデルで救済する。
            params["betas"] = ["server-side-fallback-2026-07-01"]
            params["fallbacks"] = "default"
            stream_ctx = self.client.beta.messages.stream(**params)
        else:
            stream_ctx = self.client.messages.stream(**params)

        thinking_started = False
        with stream_ctx as stream:
            for event in stream:
                if event.type == "content_block_start":
                    block_type = getattr(event.content_block, "type", "")
                    if block_type == "thinking" and not thinking_started:
                        thinking_started = True
                        self.ui.thinking_header()
                    elif block_type == "text":
                        self.ui.ensure_newline()
                elif event.type == "content_block_delta":
                    delta_type = getattr(event.delta, "type", "")
                    if delta_type == "thinking_delta":
                        self.ui.thinking_chunk(getattr(event.delta, "thinking", ""))
                    elif delta_type == "text_delta":
                        self.ui.assistant_chunk(getattr(event.delta, "text", ""))
            message = stream.get_final_message()

        self.ui.ensure_newline()
        self.session.usage.add(getattr(message, "usage", None))
        return message

    # ------------------------------------------------------------------
    # ターンの実行
    # ------------------------------------------------------------------
    def run_turn(self, user_input: str) -> None:
        """ユーザー入力を1件処理し、モデルがツールを呼ばなくなるまで回す。"""
        self.session.append_user_text(user_input)

        for iteration in range(self.config.max_iterations):
            try:
                message = self._stream_once()
            except anthropic.APIStatusError as exc:
                raise AgentError(self._describe_api_error(exc)) from exc
            except anthropic.APIConnectionError as exc:
                raise AgentError(f"API に接続できない: {exc}") from exc
            except anthropic.AnthropicError as exc:
                raise AgentError(f"Anthropic SDK エラー: {exc}") from exc
            except TypeError as exc:
                # 認証情報が無い場合、SDK はリクエスト時に TypeError を投げる。
                if "authentication" in str(exc).lower():
                    raise AgentError(
                        "認証情報が見つからない。ANTHROPIC_API_KEY を設定するか、"
                        "`ant auth login` で認証すること"
                    ) from exc
                raise

            content = list(getattr(message, "content", []) or [])
            stop_reason = getattr(message, "stop_reason", None)

            # 安全性分類器による拒否。content は空か途中までしかない。
            if stop_reason == "refusal":
                self.session.append_assistant(content)
                details = getattr(message, "stop_details", None)
                category = getattr(details, "category", None) if details else None
                suffix = f"(分類: {category})" if category else ""
                self.ui.warn(f"モデルがこのリクエストを拒否した {suffix}".strip())
                return

            self.session.append_assistant(content)

            if stop_reason == "max_tokens":
                self.ui.warn(
                    f"出力上限 {self.config.max_tokens:,} トークンに達して途中で切れた。"
                    "/max-tokens で上限を上げるか、依頼を分割すること"
                )
                return

            if stop_reason == "pause_turn":
                # サーバ側ツールが区切りに達しただけ。そのまま続きを要求する。
                continue

            tool_uses = [b for b in content if getattr(b, "type", "") == "tool_use"]
            if not tool_uses:
                return

            results = [self._execute_tool(block) for block in tool_uses]
            self.session.append_tool_results(results)
        else:
            self.ui.warn(
                f"ツール呼び出しが {self.config.max_iterations} 回に達したので打ち切った。"
                "続けるなら「続けて」と伝えること"
            )

    # ------------------------------------------------------------------
    # ツール実行
    # ------------------------------------------------------------------
    def _execute_tool(self, block: Any) -> dict[str, Any]:
        """tool_use ブロック1件を実行し、tool_result を返す。"""
        name = getattr(block, "name", "")
        payload = dict(getattr(block, "input", {}) or {})
        tool_use_id = getattr(block, "id", "")

        try:
            spec = self.toolbox.get(name)
        except ToolError as exc:
            return self._error_result(tool_use_id, str(exc))

        self.ui.tool_call(name, spec.summarize(payload))

        decision = self.gate.decide(
            tool_name=name,
            read_only=spec.read_only,
            command=payload.get("command") if name == "bash" else None,
        )
        if decision.needs_confirmation:
            detail = self._confirmation_detail(name, payload)
            question = f"{name} を実行する({decision.reason})"
            if not self.ui.confirm(question, detail):
                self.ui.info("   実行を拒否した")
                return self._error_result(
                    tool_use_id,
                    "ユーザーがこのツール実行を拒否した。別の方法を検討するか、"
                    "なぜこの操作が必要かを説明すること",
                )

        try:
            output = self.toolbox.run(name, payload)
        except ToolError as exc:
            self.ui.tool_result(str(exc), is_error=True)
            return self._error_result(tool_use_id, str(exc))
        except Exception as exc:  # ツールの想定外エラーもモデルに返して回復させる
            text = f"{type(exc).__name__}: {exc}"
            self.ui.tool_result(text, is_error=True)
            return self._error_result(tool_use_id, text)

        self.ui.tool_result(output)
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": output,
        }

    def _confirmation_detail(self, name: str, payload: dict[str, Any]) -> str:
        """確認プロンプトに添える、操作内容のプレビュー。"""
        if name == "bash":
            return str(payload.get("command", ""))
        if name == "write":
            content = str(payload.get("content", ""))
            head = "\n".join(content.splitlines()[:8])
            return f"{payload.get('path', '')} に {content.count(chr(10)) + 1} 行を書き込む\n{head}"
        if name == "edit":
            old = str(payload.get("old_string", "")).splitlines()[:4]
            new = str(payload.get("new_string", "")).splitlines()[:4]
            lines = [f"{payload.get('path', '')} を編集"]
            lines += [f"- {line}" for line in old]
            lines += [f"+ {line}" for line in new]
            return "\n".join(lines)
        return ""

    @staticmethod
    def _error_result(tool_use_id: str, text: str) -> dict[str, Any]:
        """失敗した tool_result を作る。落とさずモデルに返して回復させる。"""
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": text,
            "is_error": True,
        }

    @staticmethod
    def _describe_api_error(exc: anthropic.APIStatusError) -> str:
        """API エラーを、対処が分かる日本語にする。"""
        status = getattr(exc, "status_code", None)
        message = getattr(exc, "message", str(exc))
        hints = {
            400: "リクエストが不正。モデルが対応していないパラメータの可能性がある",
            401: "APIキーが無効。ANTHROPIC_API_KEY を確認すること",
            403: "このAPIキーではこのモデルを使えない",
            404: "モデルIDが見つからない。/model で確認すること",
            429: "レート制限に達した。少し待って再試行すること",
        }
        hint = hints.get(status, "")
        if status and status >= 500:
            hint = "サーバ側の問題。少し待って再試行すること"
        return f"API エラー {status}: {message}" + (f" — {hint}" if hint else "")
