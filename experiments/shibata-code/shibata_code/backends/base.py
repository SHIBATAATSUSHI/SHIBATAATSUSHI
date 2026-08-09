"""バックエンドの共通インターフェース。

エージェントループはここで定義した形しか知らない。プロバイダごとの
差分(メッセージ形式・ツール形式・ストリーミングイベント・停止理由)は
各バックエンドの中に閉じ込める。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..messages import Message, ToolCall

# 正規化した停止理由。
STOP_END_TURN = "end_turn"
STOP_TOOL_USE = "tool_use"
STOP_MAX_TOKENS = "max_tokens"
STOP_REFUSAL = "refusal"
STOP_PAUSE_TURN = "pause_turn"


class BackendError(Exception):
    """このターンを続行できない問題。利用者に見せる日本語を入れる。

    HTTP のステータスが分かる場合は `status` に入れる。呼び出し側が
    「待てば直る(429/5xx)」と「何度やっても同じ(401/403)」を
    区別できるようにするため。
    """

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status

    @property
    def is_auth_failure(self) -> bool:
        """認証・権限の問題か。再試行しても結果は変わらない。"""
        return self.status in (401, 403)


class StreamSink(Protocol):
    """ストリーミング中の表示先。UI を直接渡さず、必要な口だけ定義する。"""

    def assistant_chunk(self, text: str) -> None: ...

    def thinking_chunk(self, text: str) -> None: ...

    def thinking_header(self) -> None: ...

    def ensure_newline(self) -> None: ...


@dataclass
class TurnResult:
    """1リクエスト分の結果を正規化したもの。"""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = STOP_END_TURN
    # プロバイダ固有の生データ。同一モデルで続けるときに再送する。
    native: Any = None
    usage: dict[str, int] = field(default_factory=dict)
    refusal_category: str | None = None

    def is_empty(self) -> bool:
        return not (self.text.strip() or self.tool_calls)


class Backend(ABC):
    """1プロバイダ分の送受信を担う。"""

    #: 表示用の名前
    name: str = "backend"

    @abstractmethod
    def stream_turn(
        self,
        *,
        system: str,
        history: list[Message],
        tools: list[Any],
        sink: StreamSink,
    ) -> TurnResult:
        """1リクエストを送り、結果を正規化して返す。"""

    @abstractmethod
    def check_credentials(self) -> str | None:
        """認証情報が見当たらない場合に、案内文を返す。問題なければ None。"""
