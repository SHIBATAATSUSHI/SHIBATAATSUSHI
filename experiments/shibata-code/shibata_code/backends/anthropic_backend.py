"""Anthropic 用バックエンド。

公式SDKをそのまま使い、adaptive thinking・effort・プロンプトキャッシュ・
サーバ側フォールバックといった Anthropic 固有の機能を活かす。
"""

from __future__ import annotations

from typing import Any

from ..messages import Message, ToolCall
from .base import (
    STOP_END_TURN,
    STOP_MAX_TOKENS,
    STOP_PAUSE_TURN,
    STOP_REFUSAL,
    STOP_TOOL_USE,
    Backend,
    BackendError,
    StreamSink,
    TurnResult,
)

# Anthropic の stop_reason を正規化した値へ対応づける。
_STOP_MAP = {
    "end_turn": STOP_END_TURN,
    "tool_use": STOP_TOOL_USE,
    "max_tokens": STOP_MAX_TOKENS,
    "refusal": STOP_REFUSAL,
    "pause_turn": STOP_PAUSE_TURN,
    "stop_sequence": STOP_END_TURN,
    "model_context_window_exceeded": STOP_MAX_TOKENS,
}


class AnthropicBackend(Backend):
    """Anthropic Messages API を喋るバックエンド。"""

    name = "anthropic"

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
            import anthropic
        except ImportError as exc:  # pragma: no cover
            raise BackendError(
                "anthropic SDK が入っていない。`pip install anthropic` を実行すること"
            ) from exc
        kwargs: dict[str, Any] = {}
        if self.config.base_url:
            kwargs["base_url"] = self.config.base_url
        try:
            return anthropic.Anthropic(**kwargs)
        except Exception as exc:
            raise BackendError(f"Anthropic クライアントを作れなかった: {exc}") from exc

    def check_credentials(self) -> str | None:
        provider = self.config.provider
        if provider.find_api_key():
            return None
        # `ant auth login` のプロファイル認証もあり得るので、断定はしない。
        return (
            f"{provider.label} のAPIキーが見つからない。"
            f"{provider.env_hint()} を設定するか、`ant auth login` で認証すること"
        )

    # ------------------------------------------------------------------
    # 履歴とツールの変換
    # ------------------------------------------------------------------
    def render_history(self, history: list[Message]) -> list[dict[str, Any]]:
        """中立表現を Anthropic のメッセージ列に変換する。"""
        rendered: list[dict[str, Any]] = []
        model_id = self.config.model.id

        for message in history:
            if message.is_empty():
                continue

            if message.role == "user":
                rendered.append({"role": "user", "content": message.text})

            elif message.role == "assistant":
                # 同じモデルで続けるなら、生データをそのまま返す。
                # thinking ブロックは改変せず送り返す必要があるため。
                if message.native_matches("anthropic", model_id):
                    rendered.append({"role": "assistant", "content": message.native})
                    continue
                blocks: list[dict[str, Any]] = []
                if message.text.strip():
                    blocks.append({"type": "text", "text": message.text})
                for call in message.tool_calls:
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": call.id,
                            "name": call.name,
                            "input": call.arguments,
                        }
                    )
                if blocks:
                    rendered.append({"role": "assistant", "content": blocks})

            elif message.role == "tool":
                # ツール結果は必ず1通にまとめる。分けるとモデルが
                # 並列ツール呼び出しをやめてしまう。
                blocks = [
                    {
                        "type": "tool_result",
                        "tool_use_id": result.tool_call_id,
                        "content": result.content,
                        **({"is_error": True} if result.is_error else {}),
                    }
                    for result in message.tool_results
                ]
                if blocks:
                    rendered.append({"role": "user", "content": blocks})

        return rendered

    @staticmethod
    def render_tools(tools: list[Any]) -> list[dict[str, Any]]:
        return [spec.to_anthropic() for spec in tools]

    # ------------------------------------------------------------------
    # 送信
    # ------------------------------------------------------------------
    def build_params(
        self, *, system: str, history: list[Message], tools: list[Any]
    ) -> dict[str, Any]:
        """Messages API に渡すパラメータを組み立てる。"""
        spec = self.config.model
        params: dict[str, Any] = {
            "model": spec.id,
            "max_tokens": self.config.max_tokens,
            "system": [
                {
                    "type": "text",
                    "text": system,
                    # tools + system をまとめてキャッシュする
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "tools": self.render_tools(tools),
            "messages": self.render_history(history),
        }
        if spec.supports_effort:
            params["output_config"] = {"effort": self.config.effort}
        if spec.supports_adaptive_thinking:
            params["thinking"] = {
                "type": "adaptive",
                "display": self.config.thinking_display,
            }
        if spec.supports_fallbacks:
            # 安全性分類器による refusal をフォールバックモデルで救済する。
            params["betas"] = ["server-side-fallback-2026-07-01"]
            params["fallbacks"] = "default"
        return params

    def stream_turn(
        self,
        *,
        system: str,
        history: list[Message],
        tools: list[Any],
        sink: StreamSink,
    ) -> TurnResult:
        import anthropic

        params = self.build_params(system=system, history=history, tools=tools)
        use_beta = "fallbacks" in params
        messages_api = self.client.beta.messages if use_beta else self.client.messages

        try:
            message = self._consume_stream(messages_api, params, sink)
        except anthropic.APIStatusError as exc:
            raise BackendError(_describe_api_error(exc)) from exc
        except anthropic.APIConnectionError as exc:
            raise BackendError(f"API に接続できない: {exc}") from exc
        except anthropic.AnthropicError as exc:
            raise BackendError(f"Anthropic SDK エラー: {exc}") from exc
        except TypeError as exc:
            # 認証情報が無い場合、SDK はリクエスト時に TypeError を投げる。
            if "authentication" in str(exc).lower():
                raise BackendError(
                    "認証情報が見つからない。ANTHROPIC_API_KEY を設定するか、"
                    "`ant auth login` で認証すること"
                ) from exc
            raise

        return self._to_result(message)

    def _consume_stream(self, messages_api: Any, params: dict[str, Any], sink: StreamSink) -> Any:
        """ストリーミングを消費して確定メッセージを返す。"""
        thinking_started = False
        with messages_api.stream(**params) as stream:
            for event in stream:
                if event.type == "content_block_start":
                    block_type = getattr(event.content_block, "type", "")
                    if block_type == "thinking" and not thinking_started:
                        thinking_started = True
                        sink.thinking_header()
                    elif block_type == "text":
                        sink.ensure_newline()
                elif event.type == "content_block_delta":
                    delta_type = getattr(event.delta, "type", "")
                    if delta_type == "thinking_delta":
                        sink.thinking_chunk(getattr(event.delta, "thinking", ""))
                    elif delta_type == "text_delta":
                        sink.assistant_chunk(getattr(event.delta, "text", ""))
            message = stream.get_final_message()
        sink.ensure_newline()
        return message

    @staticmethod
    def _to_result(message: Any) -> TurnResult:
        """SDK のメッセージを正規化する。"""
        content = list(getattr(message, "content", []) or [])
        texts: list[str] = []
        calls: list[ToolCall] = []
        for block in content:
            block_type = getattr(block, "type", "")
            if block_type == "text":
                texts.append(getattr(block, "text", ""))
            elif block_type == "tool_use":
                calls.append(
                    ToolCall(
                        id=getattr(block, "id", ""),
                        name=getattr(block, "name", ""),
                        arguments=dict(getattr(block, "input", {}) or {}),
                    )
                )

        raw_stop = getattr(message, "stop_reason", None)
        stop = _STOP_MAP.get(raw_stop or "", STOP_END_TURN)
        # tool_use ブロックがあるのに stop_reason がそれ以外でも、実行はする。
        if calls and stop == STOP_END_TURN:
            stop = STOP_TOOL_USE

        details = getattr(message, "stop_details", None)
        category = getattr(details, "category", None) if details else None

        usage = getattr(message, "usage", None)
        return TurnResult(
            text="".join(texts),
            tool_calls=calls,
            stop_reason=stop,
            native=content,
            usage={
                "input_tokens": getattr(usage, "input_tokens", 0) or 0,
                "output_tokens": getattr(usage, "output_tokens", 0) or 0,
                "cache_creation_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
                "cache_read_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
            },
            refusal_category=category,
        )


def _describe_api_error(exc: Any) -> str:
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
