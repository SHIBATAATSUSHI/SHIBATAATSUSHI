"""プロバイダ非依存の会話履歴表現。

Anthropic と OpenAI 互換ではメッセージの形が違うので、履歴は中立な形で
持っておき、送信直前に各バックエンドがそれぞれの形に変換する。
こうしておくと、会話の途中で `/model` を跨いでも履歴を引き継げる。

アシスタントの発言には、元のプロバイダが返した生データ(`native`)も
併せて持たせる。同じモデルで続ける場合は、そちらをそのまま送り直す
(Anthropic の thinking ブロックは改変せず返す必要があるため)。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
ROLE_TOOL = "tool"


@dataclass
class ToolCall:
    """モデルからのツール呼び出し1件。"""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "arguments": self.arguments}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolCall":
        return cls(
            id=str(data.get("id", "")),
            name=str(data.get("name", "")),
            arguments=data.get("arguments") or {},
        )


@dataclass
class ToolResult:
    """ツール実行の結果1件。"""

    tool_call_id: str
    content: str
    is_error: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_call_id": self.tool_call_id,
            "content": self.content,
            "is_error": self.is_error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolResult":
        return cls(
            tool_call_id=str(data.get("tool_call_id", "")),
            content=str(data.get("content", "")),
            is_error=bool(data.get("is_error", False)),
        )


@dataclass
class Message:
    """履歴の1エントリ。"""

    role: str
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    # このアシスタント発言を生成したプロバイダとモデル
    provider: str | None = None
    model: str | None = None
    # プロバイダ固有の生データ(同一モデルで続ける場合に再利用する)
    native: Any = None

    # ------------------------------------------------------------------
    # 構築
    # ------------------------------------------------------------------
    @classmethod
    def user(cls, text: str) -> "Message":
        return cls(role=ROLE_USER, text=text)

    @classmethod
    def assistant(
        cls,
        *,
        text: str = "",
        tool_calls: list[ToolCall] | None = None,
        provider: str | None = None,
        model: str | None = None,
        native: Any = None,
    ) -> "Message":
        return cls(
            role=ROLE_ASSISTANT,
            text=text,
            tool_calls=list(tool_calls or []),
            provider=provider,
            model=model,
            native=native,
        )

    @classmethod
    def tool(cls, results: list[ToolResult]) -> "Message":
        return cls(role=ROLE_TOOL, tool_results=list(results))

    # ------------------------------------------------------------------
    # 判定
    # ------------------------------------------------------------------
    def is_empty(self) -> bool:
        """送る中身が何も無いか。空メッセージはAPIに送れない。"""
        return not (self.text.strip() or self.tool_calls or self.tool_results)

    def native_matches(self, provider: str, model: str) -> bool:
        """生データをそのまま再利用してよいか。

        別のプロバイダやモデルに切り替えた後は、生データを使わず
        中立表現から組み直す。
        """
        return (
            self.native is not None
            and self.provider == provider
            and self.model == model
        )

    # ------------------------------------------------------------------
    # 保存
    # ------------------------------------------------------------------
    def to_dict(self, *, include_native: bool = True) -> dict[str, Any]:
        data: dict[str, Any] = {"role": self.role}
        if self.text:
            data["text"] = self.text
        if self.tool_calls:
            data["tool_calls"] = [c.to_dict() for c in self.tool_calls]
        if self.tool_results:
            data["tool_results"] = [r.to_dict() for r in self.tool_results]
        if self.provider:
            data["provider"] = self.provider
        if self.model:
            data["model"] = self.model
        if include_native and self.native is not None:
            data["native"] = _to_jsonable(self.native)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Message":
        return cls(
            role=str(data.get("role", ROLE_USER)),
            text=str(data.get("text", "")),
            tool_calls=[ToolCall.from_dict(c) for c in data.get("tool_calls", [])],
            tool_results=[ToolResult.from_dict(r) for r in data.get("tool_results", [])],
            provider=data.get("provider"),
            model=data.get("model"),
            native=data.get("native"),
        )


def _to_jsonable(value: Any) -> Any:
    """SDK のモデルオブジェクトを含む構造を JSON 化できる形にする。"""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return _to_jsonable(dump(mode="json", exclude_none=True))
    return str(value)


def parse_arguments(raw: str | dict[str, Any] | None) -> dict[str, Any]:
    """ツール引数を辞書にする。

    OpenAI 互換はJSON文字列で来るが、壊れたJSONを返すモデルもあるので、
    失敗しても例外にせず空の辞書にしてツール側でエラーにさせる。
    """
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    text = str(raw).strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
