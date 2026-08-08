"""OpenAI 互換バックエンド。

GPT / Gemini / DeepSeek / Kimi / Qwen / OpenRouter / ローカルモデルは、
どれも OpenAI 互換の `/v1/chat/completions` を出している。接続先と
モデルIDが違うだけなので、実装はこの1本で足りる。

互換とはいえ細部は揃っていない(`stream_options` を受けない、
`reasoning_effort` を知らない等)。落ちた場合はオプションを外して
1度だけやり直す。
"""

from __future__ import annotations

import json
from typing import Any

from ..config import OPENAI_EFFORTS
from ..messages import Message, ToolCall, parse_arguments
from .http_transport import HTTPTransportError, post_sse
from .base import (
    STOP_END_TURN,
    STOP_MAX_TOKENS,
    STOP_REFUSAL,
    STOP_TOOL_USE,
    Backend,
    BackendError,
    StreamSink,
    TurnResult,
)

_FINISH_MAP = {
    "stop": STOP_END_TURN,
    "tool_calls": STOP_TOOL_USE,
    "function_call": STOP_TOOL_USE,
    "length": STOP_MAX_TOKENS,
    "content_filter": STOP_REFUSAL,
}

# 互換性の問題で落ちたときに、順に外していくパラメータ。
_OPTIONAL_PARAMS = ("stream_options", "reasoning_effort", "parallel_tool_calls")


class OpenAICompatBackend(Backend):
    """OpenAI 互換エンドポイントを喋るバックエンド。"""

    name = "openai_compat"

    def __init__(self, config, client: Any = None) -> None:
        self.config = config
        self._client = client

    # ------------------------------------------------------------------
    # クライアント
    # ------------------------------------------------------------------
    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = self._make_client()
        return self._client

    def _make_client(self) -> Any:
        try:
            import openai
        except ImportError as exc:  # pragma: no cover
            raise BackendError(
                "openai SDK が入っていない。`pip install openai` を実行すること"
            ) from exc

        provider = self.config.provider
        api_key = provider.find_api_key()
        if not api_key:
            # ローカルモデルは鍵不要なことが多いので、ダミーを入れて通す。
            api_key = "not-needed" if provider.key == "local" else None
        if not api_key:
            raise BackendError(
                f"{provider.label} のAPIキーが無い。{provider.env_hint()} を設定すること"
            )
        try:
            return openai.OpenAI(
                api_key=api_key,
                base_url=self.config.resolved_base_url(),
                max_retries=2,
            )
        except Exception as exc:
            raise BackendError(f"{provider.label} のクライアントを作れなかった: {exc}") from exc

    def check_credentials(self) -> str | None:
        provider = self.config.provider
        if provider.key == "local" or provider.find_api_key():
            return None
        return (
            f"{provider.label} のAPIキーが見つからない。"
            f"{provider.env_hint()} を設定すること"
        )

    # ------------------------------------------------------------------
    # 履歴とツールの変換
    # ------------------------------------------------------------------
    @staticmethod
    def render_history(system: str, history: list[Message]) -> list[dict[str, Any]]:
        """中立表現を OpenAI 形式のメッセージ列に変換する。"""
        rendered: list[dict[str, Any]] = [{"role": "system", "content": system}]

        for message in history:
            if message.is_empty():
                continue

            if message.role == "user":
                rendered.append({"role": "user", "content": message.text})

            elif message.role == "assistant":
                entry: dict[str, Any] = {"role": "assistant"}
                entry["content"] = message.text or None
                if message.tool_calls:
                    entry["tool_calls"] = [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": json.dumps(call.arguments, ensure_ascii=False),
                            },
                        }
                        for call in message.tool_calls
                    ]
                rendered.append(entry)

            elif message.role == "tool":
                # OpenAI 形式では結果1件につき1メッセージ。
                # is_error に相当する欄が無いので、本文の先頭で伝える。
                for result in message.tool_results:
                    body = result.content
                    if result.is_error:
                        body = f"[エラー] {body}"
                    rendered.append(
                        {
                            "role": "tool",
                            "tool_call_id": result.tool_call_id,
                            "content": body,
                        }
                    )

        return rendered

    @staticmethod
    def render_tools(tools: list[Any]) -> list[dict[str, Any]]:
        return [spec.to_openai() for spec in tools]

    # ------------------------------------------------------------------
    # 送信
    # ------------------------------------------------------------------
    def build_params(
        self, *, system: str, history: list[Message], tools: list[Any]
    ) -> dict[str, Any]:
        spec = self.config.model
        params: dict[str, Any] = {
            "model": spec.id,
            "messages": self.render_history(system, history),
            "tools": self.render_tools(tools),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        # 出力上限のパラメータ名はモデルによって違う。
        params[spec.max_tokens_param] = self.config.max_tokens

        if spec.supports_reasoning_effort or self.config.force_reasoning_effort:
            params["reasoning_effort"] = OPENAI_EFFORTS.get(self.config.effort, "medium")
        return params

    def stream_turn(
        self,
        *,
        system: str,
        history: list[Message],
        tools: list[Any],
        sink: StreamSink,
    ) -> TurnResult:
        params = self.build_params(system=system, history=history, tools=tools)
        return self._stream_with_fallback(params, tools, sink)

    def _stream_with_fallback(
        self, params: dict[str, Any], tools: list[Any], sink: StreamSink
    ) -> TurnResult:
        """互換性の穴でリクエストが弾かれたら、任意パラメータを外して再試行する。"""
        import openai

        attempt = dict(params)
        droppable = [name for name in _OPTIONAL_PARAMS if name in attempt]

        while True:
            try:
                return self._consume_stream(attempt, tools, sink)
            except openai.BadRequestError as exc:
                if not droppable:
                    raise BackendError(self._describe_error(exc)) from exc
                dropped = droppable.pop(0)
                attempt.pop(dropped, None)
                sink.ensure_newline()
                continue
            except openai.APIStatusError as exc:
                raise BackendError(self._describe_error(exc)) from exc
            except openai.APIConnectionError as exc:
                raise BackendError(
                    f"{self.config.provider.label} に接続できない: {exc}"
                ) from exc
            except openai.OpenAIError as exc:
                raise BackendError(f"{self.config.provider.label} エラー: {exc}") from exc

    def _consume_stream(
        self, params: dict[str, Any], tools: list[Any], sink: StreamSink
    ) -> TurnResult:
        """SDK のストリーミングを消費して結果を組み立てる。

        チャンクを辞書へ均してから積み上げる。標準ライブラリ経由でも
        同じ組み立てを使い回すため。
        """
        stream = self.client.chat.completions.create(**params)
        return self.accumulate((_as_dict(chunk) for chunk in stream), tools, sink)

    def accumulate(
        self, chunks: Any, tools: list[Any], sink: StreamSink
    ) -> TurnResult:
        """チャンク(辞書)の並びから結果を組み立てる。"""
        texts: list[str] = []
        # index ごとにツール呼び出しの断片を溜める
        slots: dict[int, dict[str, str]] = {}
        finish_reason = ""
        usage: dict[str, int] = {}
        thinking_started = False
        text_started = False

        for chunk in chunks:
            chunk_usage = chunk.get("usage")
            if chunk_usage:
                usage = {
                    "input_tokens": chunk_usage.get("prompt_tokens", 0) or 0,
                    "output_tokens": chunk_usage.get("completion_tokens", 0) or 0,
                    "cache_creation_tokens": 0,
                    "cache_read_tokens": _cached_tokens(chunk_usage),
                }

            choices = chunk.get("choices") or []
            if not choices:
                continue
            choice = choices[0] or {}

            if choice.get("finish_reason"):
                finish_reason = choice["finish_reason"]

            delta = choice.get("delta")
            if not delta:
                continue

            # 推論内容を返すモデル(DeepSeek Reasoner 等)は別フィールドで来る。
            reasoning = delta.get("reasoning_content") or delta.get("reasoning")
            if reasoning:
                if not thinking_started:
                    thinking_started = True
                    sink.thinking_header()
                sink.thinking_chunk(str(reasoning))

            content = delta.get("content")
            if content:
                if not text_started:
                    text_started = True
                    sink.ensure_newline()
                texts.append(content)
                sink.assistant_chunk(content)

            for fragment in delta.get("tool_calls") or []:
                index = fragment.get("index", 0) or 0
                slot = slots.setdefault(index, {"id": "", "name": "", "arguments": ""})
                if fragment.get("id"):
                    slot["id"] = fragment["id"]
                function = fragment.get("function") or {}
                if function.get("name"):
                    slot["name"] += function["name"]
                if function.get("arguments"):
                    slot["arguments"] += function["arguments"]

        sink.ensure_newline()

        known_names = {spec.name for spec in tools}
        calls = [
            ToolCall(
                id=slot["id"] or f"call_{index}",
                name=_repair_name(slot["name"], known_names),
                arguments=parse_arguments(slot["arguments"]),
            )
            for index, slot in sorted(slots.items())
            if slot["name"] or slot["arguments"]
        ]

        stop = _FINISH_MAP.get(finish_reason, STOP_END_TURN)
        if calls and stop == STOP_END_TURN:
            # finish_reason を正しく返さない実装があるので、実態を優先する。
            stop = STOP_TOOL_USE

        return TurnResult(
            text="".join(texts),
            tool_calls=calls,
            stop_reason=stop,
            native=None,
            usage=usage,
        )

    def _describe_error(self, exc: Any) -> str:
        status = getattr(exc, "status_code", None)
        label = self.config.provider.label
        body = getattr(exc, "message", str(exc))
        hints = {
            400: "リクエストが不正。そのモデルが対応していないパラメータかもしれない",
            401: f"APIキーが無効。{self.config.provider.env_hint()} を確認すること",
            403: "このAPIキーではこのモデルを使えない",
            404: "モデルIDが見つからない。プロバイダのモデル一覧を確認すること",
            429: "レート制限に達した。少し待って再試行すること",
        }
        hint = hints.get(status, "")
        if status and status >= 500:
            hint = "サーバ側の問題。少し待って再試行すること"
        return f"{label} エラー {status}: {body}" + (f" — {hint}" if hint else "")


class OpenAICompatHTTPBackend(OpenAICompatBackend):
    """openai SDK を使わず、標準ライブラリだけで喋る版。

    iOS など SDK を入れられない環境向け。組み立てと解釈は SDK 版と
    共通で、通信部分だけ差し替えている。
    """

    name = "openai_compat_http"

    def __init__(self, config, *, trust_env: bool = True) -> None:
        super().__init__(config, client=None)
        self.trust_env = trust_env

    def _endpoint(self) -> str:
        base = (self.config.resolved_base_url() or "").rstrip("/")
        if not base:
            raise BackendError("接続先が分からない。--base-url を指定すること")
        return f"{base}/chat/completions"

    def _headers(self) -> dict[str, str]:
        provider = self.config.provider
        api_key = provider.find_api_key()
        if not api_key and provider.key == "local":
            api_key = "not-needed"
        if not api_key:
            raise BackendError(
                f"{provider.label} のAPIキーが無い。{provider.env_hint()} を設定すること"
            )
        return {"Authorization": f"Bearer {api_key}"}

    def stream_turn(
        self,
        *,
        system: str,
        history: list[Message],
        tools: list[Any],
        sink: StreamSink,
    ) -> TurnResult:
        params = self.build_params(system=system, history=history, tools=tools)
        url = self._endpoint()
        headers = self._headers()

        attempt = dict(params)
        droppable = [name for name in _OPTIONAL_PARAMS if name in attempt]

        while True:
            try:
                events = post_sse(
                    url, headers=headers, payload=attempt, trust_env=self.trust_env
                )
                return self.accumulate(events, tools, sink)
            except HTTPTransportError as exc:
                # 互換性の穴で弾かれた場合だけ、任意パラメータを外して再試行する
                if exc.status == 400 and droppable:
                    attempt.pop(droppable.pop(0), None)
                    sink.ensure_newline()
                    continue
                raise BackendError(self._describe_http_error(exc)) from exc

    def _describe_http_error(self, exc: HTTPTransportError) -> str:
        label = self.config.provider.label
        hints = {
            400: "リクエストが不正。そのモデルが対応していないパラメータかもしれない",
            401: f"APIキーが無効。{self.config.provider.env_hint()} を確認すること",
            403: "このAPIキーではこのモデルを使えない",
            404: "モデルIDが見つからない。プロバイダのモデル一覧を確認すること",
            429: "レート制限に達した。少し待って再試行すること",
        }
        hint = hints.get(exc.status or 0, "")
        if exc.status and exc.status >= 500:
            hint = "サーバ側の問題。少し待って再試行すること"
        head = f"{label} エラー {exc.status}: " if exc.status else f"{label}: "
        return head + str(exc) + (f" — {hint}" if hint else "")


def _as_dict(chunk: Any) -> dict[str, Any]:
    """SDK のチャンクを辞書に均す。すでに辞書ならそのまま。"""
    if isinstance(chunk, dict):
        return chunk
    dump = getattr(chunk, "model_dump", None)
    if callable(dump):
        return dump()
    return {}


def _cached_tokens(usage: dict[str, Any]) -> int:
    """プロンプトキャッシュのヒット数を拾う(取れないプロバイダは 0)。"""
    details = usage.get("prompt_tokens_details")
    if isinstance(details, dict):
        return details.get("cached_tokens", 0) or 0
    # DeepSeek は独自の欄で返す
    return usage.get("prompt_cache_hit_tokens", 0) or 0


def _repair_name(name: str, known: set[str]) -> str:
    """ツール名の断片を組み立て直す。

    チャンクごとに完全な名前を送り直す実装があり、素朴に連結すると
    "readread" のようになる。既知のツール名と突き合わせて直す。
    """
    if not name or name in known:
        return name
    for candidate in known:
        # 重複して連結された形("readread")を元に戻す
        if candidate and name == candidate * (len(name) // len(candidate)):
            return candidate
        if name.startswith(candidate):
            return candidate
    return name
