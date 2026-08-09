"""会話履歴の圧縮(コンパクション)。

長い作業を続けると履歴がコンテキスト長を超えて止まる。そうなる前に、
古い部分をモデル自身に要約させて1件に畳む。

プロバイダ非依存にするため、サーバ側の圧縮機能ではなく自前でやっている
(Anthropic にはサーバ側の仕組みがあるが、OpenAI 互換では使えないため)。

畳むときに一番気をつけるのは、**ツール呼び出しとその結果を分断しないこと**。
assistant の tool_call だけ残して結果を捨てる(あるいはその逆)と、
API はリクエストごと拒否する。そのため分割位置は「ユーザーの発言」に限る。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .messages import ROLE_ASSISTANT, ROLE_TOOL, ROLE_USER, Message

# 要約を頼むときのシステムプロンプト。
SUMMARY_SYSTEM = """\
あなたは作業中のセッションを引き継ぐための記録係。
これまでのやり取りを、作業を再開できるだけの引き継ぎメモにまとめる。

含めること:
- 何を頼まれたか(元の依頼)
- 何をしたか(触ったファイルのパス、加えた変更)
- 何が分かったか(調査で判明した事実、再現手順、原因)
- 今どうなっているか(通っているもの、落ちているもの)
- 残っている作業

削ること:
- 探索の途中経過、読んだだけで終わったファイル
- ツール出力の全文(結論だけ残す)
- 言い回しや相槌

事実だけを書く。推測を混ぜない。ファイルパスや関数名は省略せず正確に書く。
"""

SUMMARY_REQUEST = """\
次はこれまでのやり取りの記録。作業を引き継ぐためのメモにまとめること。

<やり取り>
{transcript}
</やり取り>
"""

# 畳んだ履歴を置き換える1件の本文。
COMPACTED_PREFIX = """\
[これ以前のやり取りは、長くなったため要約に置き換えられている]

{summary}

[要約ここまで。以降は実際のやり取りが続く]"""

# ツール結果は全文だと長すぎるので、要約に渡す前にこの長さで切る。
TRANSCRIPT_TOOL_LIMIT = 400
# 要約そのものの出力上限。
SUMMARY_MAX_TOKENS = 4_000


class CompactionError(Exception):
    """畳めなかった。呼び出し側は続行してよい(致命的ではない)。"""


@dataclass
class CompactionResult:
    """畳んだ結果。"""

    performed: bool
    before: int = 0
    after: int = 0
    summary: str = ""
    reason: str = ""


def prompt_tokens(usage: dict[str, int] | None) -> int:
    """1レスポンスの usage から、送った入力の総量を出す。

    `input_tokens` はキャッシュに載らなかった分しか含まないので、
    キャッシュ読み書きの分を足さないと実際の履歴の大きさが分からない。
    """
    if not usage:
        return 0
    return (
        (usage.get("input_tokens", 0) or 0)
        + (usage.get("cache_read_tokens", 0) or 0)
        + (usage.get("cache_creation_tokens", 0) or 0)
    )


def find_split_point(messages: list[Message], keep_recent: int) -> int:
    """畳む範囲と残す範囲の境目を探す。

    返り値はインデックスで、`messages[:i]` を畳み `messages[i:]` を残す。
    畳めない場合は -1。

    境目は必ずユーザーの発言にする。assistant の tool_call と、それに対応する
    tool の結果の間で切ると、API がリクエストを拒否するため。
    """
    if keep_recent < 1:
        keep_recent = 1
    target = max(0, len(messages) - keep_recent)

    # target 以降で最初に現れるユーザー発言を境目にする
    for index in range(target, len(messages)):
        if messages[index].role == ROLE_USER:
            # 先頭を境目にしても何も畳めない
            return index if index > 0 else -1

    # 直近に区切りが無ければ、target より前へ遡って探す
    for index in range(target - 1, 0, -1):
        if messages[index].role == ROLE_USER:
            return index

    return -1


def render_transcript(messages: list[Message], *, tool_limit: int = TRANSCRIPT_TOOL_LIMIT) -> str:
    """要約に渡すため、履歴を読みやすい平文にする。"""
    lines: list[str] = []
    for message in messages:
        if message.role == ROLE_USER:
            if message.text.strip():
                lines.append(f"## ユーザー\n{message.text.strip()}")

        elif message.role == ROLE_ASSISTANT:
            if message.text.strip():
                lines.append(f"## アシスタント\n{message.text.strip()}")
            for call in message.tool_calls:
                argument = ", ".join(
                    f"{key}={_short(str(value), 120)}"
                    for key, value in (call.arguments or {}).items()
                )
                lines.append(f"## ツール呼び出し: {call.name}({argument})")

        elif message.role == ROLE_TOOL:
            for result in message.tool_results:
                label = "失敗" if result.is_error else "結果"
                lines.append(f"## ツール{label}\n{_short(result.content, tool_limit)}")

    return "\n\n".join(lines)


def _short(text: str, limit: int) -> str:
    """長い文字列を切り詰める。"""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + f" …(残り {len(text) - limit} 文字)"


class Compactor:
    """履歴を畳む役。"""

    def __init__(self, config: Any, ui: Any) -> None:
        self.config = config
        self.ui = ui

    # ------------------------------------------------------------------
    def should_compact(self, *, last_usage: dict[str, int] | None, messages: list[Message]) -> bool:
        """畳むべきか判断する。"""
        if not self.config.compaction:
            return False
        used = prompt_tokens(last_usage)
        if used < self.config.compact_threshold():
            return False
        # 畳める境目が無いなら意味が無い
        return find_split_point(messages, self.config.compact_keep_recent) > 0

    # ------------------------------------------------------------------
    def compact(self, messages: list[Message], backend: Any) -> CompactionResult:
        """履歴を畳んで、新しい履歴を返す(破壊的に置き換える)。

        要約に失敗した場合は履歴を変えずに返す。畳めないことは
        作業を止める理由にはならない。
        """
        split = find_split_point(messages, self.config.compact_keep_recent)
        if split <= 0:
            return CompactionResult(performed=False, reason="畳める境目が無い")

        head = messages[:split]
        tail = messages[split:]

        transcript = render_transcript(head)
        if not transcript.strip():
            return CompactionResult(performed=False, reason="要約する内容が無い")

        try:
            summary = self._summarize(transcript, backend)
        except Exception as exc:  # 要約の失敗で作業を止めない
            return CompactionResult(performed=False, reason=f"要約に失敗: {exc}")

        if not summary.strip():
            return CompactionResult(performed=False, reason="要約が空だった")

        compacted = Message.user(COMPACTED_PREFIX.format(summary=summary.strip()))
        before = len(messages)
        messages[:] = [compacted, *tail]

        return CompactionResult(
            performed=True,
            before=before,
            after=len(messages),
            summary=summary.strip(),
        )

    # ------------------------------------------------------------------
    def _summarize(self, transcript: str, backend: Any) -> str:
        """バックエンドに要約を頼む。ツールは渡さない。"""
        request = [Message.user(SUMMARY_REQUEST.format(transcript=transcript))]
        result = backend.stream_turn(
            system=SUMMARY_SYSTEM,
            history=request,
            tools=[],
            sink=_SilentSink(),
        )
        return result.text


class _SilentSink:
    """要約中の出力は見せない。端末に流すと本筋と混ざって読みにくい。"""

    def assistant_chunk(self, text: str) -> None:
        return

    def thinking_chunk(self, text: str) -> None:
        return

    def thinking_header(self) -> None:
        return

    def ensure_newline(self) -> None:
        return
