"""エージェントループ本体。

モデルを呼び、返ってきたツール呼び出しを実行し、その結果を積んでまた呼ぶ。
これを停止理由が tool_use でなくなるまで繰り返す。

プロバイダごとの差分はバックエンドに閉じ込めてあるので、ここは
どのモデルを使っていても同じコードで動く。
"""

from __future__ import annotations

from typing import Any

from .backends import (
    STOP_MAX_TOKENS,
    STOP_PAUSE_TURN,
    STOP_REFUSAL,
    STOP_TOOL_USE,
    Backend,
    BackendError,
    build_backend,
)
from .compaction import Compactor
from .config import Config
from .messages import ToolCall, ToolResult
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
        config: Config,
        toolbox: Toolbox,
        session: Session,
        ui: UI,
        backend: Backend | None = None,
    ) -> None:
        self.config = config
        self.toolbox = toolbox
        self.session = session
        self.ui = ui
        self.gate = PermissionGate(config.permission_mode)
        self._backend = backend
        # どの設定で作ったバックエンドかを覚えておき、設定が変わったら作り直す
        self._backend_config = config if backend is not None else None
        self.compactor = Compactor(config, ui)
        # 直近のリクエストで送った入力量。履歴を畳む判断に使う。
        self._last_usage: dict[str, int] | None = None
        # システムプロンプトはセッション中固定にする(キャッシュを壊さないため)
        self.system_prompt = build_system_prompt(
            config.workspace,
            extra_context=load_project_context(config.workspace),
        )

    # ------------------------------------------------------------------
    # バックエンド
    # ------------------------------------------------------------------
    @property
    def backend(self) -> Backend:
        """現在の設定に合うバックエンド。設定が変わっていれば作り直す。"""
        if self._backend is None or self._backend_config is not self.config:
            self._backend = build_backend(self.config)
            self._backend_config = self.config
        return self._backend

    def set_backend(self, backend: Backend | None) -> None:
        """バックエンドを差し替える(テストやモデル変更時に使う)。"""
        self._backend = backend
        self._backend_config = self.config if backend is not None else None

    def check_credentials(self) -> str | None:
        """認証情報が見当たらない場合の案内文。"""
        try:
            return self.backend.check_credentials()
        except BackendError as exc:
            return str(exc)

    # ------------------------------------------------------------------
    # ターンの実行
    # ------------------------------------------------------------------
    def run_turn(self, user_input: str) -> None:
        """ユーザー入力を1件処理し、モデルがツールを呼ばなくなるまで回す。"""
        self.session.append_user_text(user_input)

        for _ in range(self.config.max_iterations):
            # 送る前に畳む。この時点の履歴は必ず整合している
            # (ユーザー発言かツール結果で終わっている)。
            self.maybe_compact()
            backend = self.backend
            try:
                result = backend.stream_turn(
                    system=self.system_prompt,
                    history=self.session.messages,
                    tools=self.toolbox.specs,
                    sink=self.ui,
                )
            except BackendError as exc:
                raise AgentError(str(exc)) from exc

            self.session.usage.add(result.usage)
            self._last_usage = result.usage
            self.session.append_turn(
                result, provider=self.config.model.provider, model=self.config.model.id
            )

            if result.stop_reason == STOP_REFUSAL:
                suffix = f"(分類: {result.refusal_category})" if result.refusal_category else ""
                self.ui.warn(f"モデルがこのリクエストを拒否した {suffix}".strip())
                return

            if result.stop_reason == STOP_MAX_TOKENS:
                self.ui.warn(
                    f"出力上限 {self.config.max_tokens:,} トークンに達して途中で切れた。"
                    "/max-tokens で上限を上げるか、依頼を分割すること"
                )
                return

            if result.stop_reason == STOP_PAUSE_TURN:
                # サーバ側ツールが区切りに達しただけ。そのまま続きを要求する。
                continue

            if result.stop_reason != STOP_TOOL_USE or not result.tool_calls:
                return

            self._run_tools(result.tool_calls)
        else:
            self.ui.warn(
                f"ツール呼び出しが {self.config.max_iterations} 回に達したので打ち切った。"
                "続けるなら「続けて」と伝えること"
            )

    # ------------------------------------------------------------------
    # 履歴の圧縮
    # ------------------------------------------------------------------
    def maybe_compact(self, *, force: bool = False) -> bool:
        """必要なら履歴を畳む。畳んだら True。"""
        if not force and not self.compactor.should_compact(
            last_usage=self._last_usage, messages=self.session.messages
        ):
            return False

        self.ui.info("履歴が長くなったので要約して畳む…")
        outcome = self.compactor.compact(self.session.messages, self.backend)
        if not outcome.performed:
            self.ui.warn(f"畳めなかった({outcome.reason})。そのまま続ける")
            return False

        self.ui.notice(
            f"履歴を {outcome.before} 件から {outcome.after} 件に畳んだ"
        )
        # 畳んだ直後は送信量が読めないので、次の応答まで判断を保留する
        self._last_usage = None
        return True

    # ------------------------------------------------------------------
    # ツール実行
    # ------------------------------------------------------------------
    def _run_tools(self, calls: list[ToolCall]) -> None:
        """ツールを順に実行し、結果をまとめて積む。

        途中で中断された場合も、呼び出された分すべてに結果を返す。
        tool_call に対応する結果が欠けた履歴は、次のリクエストで
        API に拒否されるため。
        """
        results: list[ToolResult] = []
        try:
            for call in calls:
                results.append(self._execute_tool(call))
        except KeyboardInterrupt:
            # 未実行の分を埋めてから中断を伝える
            done = {result.tool_call_id for result in results}
            for call in calls:
                if call.id not in done:
                    results.append(
                        ToolResult(
                            tool_call_id=call.id,
                            content="ユーザーが作業を中断した",
                            is_error=True,
                        )
                    )
            self.session.append_tool_results(results)
            raise
        self.session.append_tool_results(results)

    def _execute_tool(self, call: ToolCall) -> ToolResult:
        """ツール呼び出し1件を実行し、結果を返す。"""
        payload = dict(call.arguments or {})

        try:
            spec = self.toolbox.get(call.name)
        except ToolError as exc:
            return ToolResult(tool_call_id=call.id, content=str(exc), is_error=True)

        self.ui.tool_call(call.name, spec.summarize(payload))

        decision = self.gate.decide(
            tool_name=call.name,
            read_only=spec.read_only,
            command=payload.get("command") if call.name == "bash" else None,
        )
        if decision.needs_confirmation:
            detail = self._confirmation_detail(call.name, payload)
            question = f"{call.name} を実行する({decision.reason})"
            if not self.ui.confirm(question, detail):
                self.ui.info("   実行を拒否した")
                return ToolResult(
                    tool_call_id=call.id,
                    content=(
                        "ユーザーがこのツール実行を拒否した。別の方法を検討するか、"
                        "なぜこの操作が必要かを説明すること"
                    ),
                    is_error=True,
                )

        try:
            output = self.toolbox.run(call.name, payload)
        except ToolError as exc:
            self.ui.tool_result(str(exc), is_error=True)
            return ToolResult(tool_call_id=call.id, content=str(exc), is_error=True)
        except Exception as exc:  # 想定外のエラーもモデルに返して回復させる
            text = f"{type(exc).__name__}: {exc}"
            self.ui.tool_result(text, is_error=True)
            return ToolResult(tool_call_id=call.id, content=text, is_error=True)

        self.ui.tool_result(output)
        return ToolResult(tool_call_id=call.id, content=output)

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
