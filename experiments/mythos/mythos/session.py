"""会話履歴と利用量の管理、およびセッションの保存・再開。

Messages API はステートレスなので、履歴はこちら側で全部持つ。
API から返ってきた content ブロック(thinking を含む)は改変せずそのまま
積み直す必要があるため、保存時だけ JSON に落とす。
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SESSION_DIR_NAME = ".mythos"


def _to_jsonable(value: Any) -> Any:
    """SDK のモデルオブジェクトを含む構造を JSON 化できる形にする。"""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    # pydantic のモデル(content ブロック等)
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return _to_jsonable(dump(mode="json", exclude_none=True))
    return str(value)


@dataclass
class Usage:
    """セッション全体のトークン利用量と概算コスト。"""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    requests: int = 0

    def add(self, usage: Any) -> None:
        """1レスポンス分の usage を加算する。"""
        if usage is None:
            return
        self.requests += 1
        self.input_tokens += getattr(usage, "input_tokens", 0) or 0
        self.output_tokens += getattr(usage, "output_tokens", 0) or 0
        self.cache_creation_tokens += getattr(usage, "cache_creation_input_tokens", 0) or 0
        self.cache_read_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0

    def cost(self, spec: Any) -> float:
        """モデル価格から概算コスト(USD)を出す。"""
        return (
            self.input_tokens * spec.input_price
            + self.output_tokens * spec.output_price
            + self.cache_creation_tokens * spec.cache_write_price
            + self.cache_read_tokens * spec.cache_read_price
        ) / 1_000_000

    def summary(self, spec: Any) -> str:
        """端末表示用の1行サマリ。"""
        cached = ""
        if self.cache_read_tokens:
            cached = f" / キャッシュ読み {self.cache_read_tokens:,}"
        cost = self.cost(spec)
        cost_text = f" / 約 ${cost:.4f}" if cost else ""
        return (
            f"[{self.requests} リクエスト / 入力 {self.input_tokens:,}"
            f" / 出力 {self.output_tokens:,}{cached}{cost_text}]"
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_creation_tokens": self.cache_creation_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "requests": self.requests,
        }


@dataclass
class Session:
    """1つの会話。メッセージ列と利用量を保持する。"""

    workspace: Path
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    messages: list[dict[str, Any]] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    created_at: float = field(default_factory=time.time)

    # ------------------------------------------------------------------
    # 履歴の操作
    # ------------------------------------------------------------------
    def append_user_text(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def append_assistant(self, content: Any) -> None:
        """アシスタントの応答をそのまま積む。

        thinking ブロックは同一モデルで会話を続ける限り改変せず返す必要があるため、
        SDK のオブジェクトのまま保持する。
        """
        if not content:
            # 空 content のメッセージは API に送れないので積まない。
            return
        self.messages.append({"role": "assistant", "content": content})

    def append_tool_results(self, results: list[dict[str, Any]]) -> None:
        """複数のツール結果を1つの user メッセージにまとめて積む。

        分割して送るとモデルが並列ツール呼び出しをやめてしまうため、必ず1通にする。
        """
        if not results:
            return
        self.messages.append({"role": "user", "content": results})

    def clear(self) -> None:
        """履歴だけ消す(利用量の累計は残す)。"""
        self.messages = []

    # ------------------------------------------------------------------
    # 保存・読み込み
    # ------------------------------------------------------------------
    def storage_dir(self) -> Path:
        return self.workspace / SESSION_DIR_NAME / "sessions"

    def path(self) -> Path:
        return self.storage_dir() / f"{self.session_id}.json"

    def save(self) -> Path:
        """セッションを JSON に保存し、そのパスを返す。"""
        target = self.path()
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "session_id": self.session_id,
            "workspace": str(self.workspace),
            "created_at": self.created_at,
            "saved_at": time.time(),
            "usage": self.usage.to_dict(),
            "messages": _to_jsonable(self.messages),
        }
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return target

    @classmethod
    def load(cls, path: Path) -> "Session":
        """保存済みセッションを読み込む。"""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        usage_data = data.get("usage") or {}
        session = cls(
            workspace=Path(data.get("workspace", ".")),
            session_id=data.get("session_id", uuid.uuid4().hex[:12]),
            messages=data.get("messages", []),
            usage=Usage(**usage_data),
            created_at=data.get("created_at", time.time()),
        )
        return session

    @classmethod
    def latest(cls, workspace: Path) -> "Session | None":
        """作業ディレクトリで最後に保存されたセッションを返す。"""
        directory = workspace / SESSION_DIR_NAME / "sessions"
        if not directory.is_dir():
            return None
        candidates = sorted(
            directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
        )
        if not candidates:
            return None
        return cls.load(candidates[0])
