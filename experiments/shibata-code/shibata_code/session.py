"""会話履歴と利用量の管理、およびセッションの保存・再開。

履歴はプロバイダ非依存の中立表現(`messages.Message`)で持つ。
送信直前に各バックエンドがそれぞれの形へ変換するので、会話の途中で
モデルやプロバイダを切り替えても続きから話せる。
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .messages import Message, ToolResult

SESSION_DIR_NAME = ".shibata-code"


@dataclass
class Usage:
    """セッション全体のトークン利用量と概算コスト。"""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    requests: int = 0

    def add(self, usage: dict[str, int] | None) -> None:
        """1レスポンス分の利用量を加算する。"""
        self.requests += 1
        if not usage:
            return
        self.input_tokens += usage.get("input_tokens", 0) or 0
        self.output_tokens += usage.get("output_tokens", 0) or 0
        self.cache_creation_tokens += usage.get("cache_creation_tokens", 0) or 0
        self.cache_read_tokens += usage.get("cache_read_tokens", 0) or 0

    def cost_breakdown(self, spec: Any) -> dict[str, float]:
        """費用の内訳(USD)。合計だけ見ると理由が分からないので分けて持つ。

        短い作業ではキャッシュ書き込みが費用の大半を占めることがある。
        入力・出力のトークン数だけ眺めていると勘定が合わなくなるため、
        表示側がここを参照できるようにしてある。
        """
        return {
            "input": self.input_tokens * spec.input_price / 1_000_000,
            "output": self.output_tokens * spec.output_price / 1_000_000,
            "cache_write": self.cache_creation_tokens * spec.cache_write_price / 1_000_000,
            "cache_read": self.cache_read_tokens * spec.cache_read_price / 1_000_000,
        }

    def cost(self, spec: Any) -> float:
        """モデル価格から概算コスト(USD)を出す。価格未設定なら 0。"""
        return sum(self.cost_breakdown(spec).values())

    def summary(self, spec: Any) -> str:
        """端末表示用の1行サマリ。"""
        cached = ""
        if self.cache_read_tokens:
            cached = f" / キャッシュ読み {self.cache_read_tokens:,}"
        if spec.has_pricing:
            tail = f" / 約 ${self.cost(spec):.4f}"
        else:
            tail = " / 価格未設定"
        return (
            f"[{self.requests} リクエスト / 入力 {self.input_tokens:,}"
            f" / 出力 {self.output_tokens:,}{cached}{tail}]"
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
    messages: list[Message] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    created_at: float = field(default_factory=time.time)

    # ------------------------------------------------------------------
    # 履歴の操作
    # ------------------------------------------------------------------
    def append_user_text(self, text: str) -> None:
        self.messages.append(Message.user(text))

    def append_turn(self, result: Any, *, provider: str, model: str) -> None:
        """バックエンドが返した `TurnResult` を履歴に積む。

        中身が空のときは積まない(空メッセージはAPIに送れない)。
        """
        message = Message.assistant(
            text=result.text,
            tool_calls=result.tool_calls,
            provider=provider,
            model=model,
            native=result.native,
        )
        if message.is_empty():
            return
        self.messages.append(message)

    def append_tool_results(self, results: list[ToolResult]) -> None:
        """複数のツール結果を1エントリにまとめて積む。"""
        if not results:
            return
        self.messages.append(Message.tool(results))

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
            "messages": [m.to_dict() for m in self.messages],
        }
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return target

    @classmethod
    def load(cls, path: Path) -> "Session":
        """保存済みセッションを読み込む。"""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            workspace=Path(data.get("workspace", ".")),
            session_id=data.get("session_id", uuid.uuid4().hex[:12]),
            messages=[Message.from_dict(m) for m in data.get("messages", [])],
            usage=Usage(**(data.get("usage") or {})),
            created_at=data.get("created_at", time.time()),
        )

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
