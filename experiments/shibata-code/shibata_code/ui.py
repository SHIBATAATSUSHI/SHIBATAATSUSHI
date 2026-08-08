"""端末表示。ANSIエスケープでの色付けと、ストリーミング出力の整形。

携帯からSSHで使う場合、端末幅は40桁程度しかないことがある。
固定幅で切ると折り返しが酷くなるので、実際の幅を見て整形する。
"""

from __future__ import annotations

import shutil
import sys
from typing import TextIO

_CODES = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "italic": "\033[3m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "magenta": "\033[35m",
    "cyan": "\033[36m",
    "grey": "\033[90m",
}

# 端末幅が取れないときの既定値。
FALLBACK_WIDTH = 80
# 幅が狭すぎる環境でも最低限これだけは出す。
MIN_WIDTH = 20

# ツール結果として出すプレビューの行数。
PREVIEW_LINES = 12
PREVIEW_LINES_COMPACT = 3
# 確認プロンプトに添える行数。
DETAIL_LINES = 10
DETAIL_LINES_COMPACT = 4


def detect_width(stream: TextIO, fallback: int = FALLBACK_WIDTH) -> int:
    """端末の幅を調べる。取れなければ既定値。"""
    try:
        if hasattr(stream, "fileno"):
            size = shutil.get_terminal_size(fallback=(fallback, 24))
            return max(MIN_WIDTH, size.columns)
    except (OSError, ValueError):
        pass
    return fallback


class UI:
    """端末への出力をまとめる。色を無効化した場合も同じ呼び出しで動く。"""

    def __init__(
        self,
        *,
        color: bool = True,
        stream: TextIO | None = None,
        width: int | None = None,
        compact: bool = False,
    ) -> None:
        self.stream = stream or sys.stdout
        # パイプ越しの実行では色を落とす。
        self.color = color and self.stream.isatty()
        # 携帯など細い画面向けに、出力量を絞るモード。
        self.compact = compact
        self.width = max(MIN_WIDTH, width) if width else detect_width(self.stream)
        # 直前の出力が改行で終わっているか(区切り線の重複を避けるため)
        self._at_line_start = True

    # ------------------------------------------------------------------
    # 低レベル
    # ------------------------------------------------------------------
    def paint(self, text: str, *styles: str) -> str:
        """スタイルを適用した文字列を返す。色無効時はそのまま返す。"""
        if not self.color or not styles:
            return text
        prefix = "".join(_CODES.get(s, "") for s in styles)
        return f"{prefix}{text}{_CODES['reset']}"

    def fit(self, text: str, *, indent: int = 0) -> str:
        """端末幅に収まるよう1行を切り詰める。"""
        limit = max(8, self.width - indent)
        if len(text) <= limit:
            return text
        return text[: limit - 1] + "…"

    def write(self, text: str) -> None:
        """整形せずそのまま書き出す(ストリーミング用)。"""
        if not text:
            return
        self.stream.write(text)
        self.stream.flush()
        self._at_line_start = text.endswith("\n")

    def line(self, text: str = "") -> None:
        """1行書き出す。"""
        self.ensure_newline()
        self.stream.write(text + "\n")
        self.stream.flush()
        self._at_line_start = True

    def ensure_newline(self) -> None:
        """行頭でなければ改行して行頭に戻す。"""
        if not self._at_line_start:
            self.stream.write("\n")
            self.stream.flush()
            self._at_line_start = True

    # ------------------------------------------------------------------
    # 用途別
    # ------------------------------------------------------------------
    def banner(self, title: str, subtitle: str) -> None:
        """起動時のヘッダ。"""
        if self.compact:
            self.line(self.paint(title, "bold", "magenta"))
            self.line(self.paint(self.fit(subtitle), "grey"))
            return
        self.line()
        self.line(self.paint(f"  {title}", "bold", "magenta"))
        self.line(self.paint(f"  {self.fit(subtitle, indent=2)}", "grey"))
        self.line()

    def info(self, text: str) -> None:
        self.line(self.paint(self.fit(text), "grey"))

    def notice(self, text: str) -> None:
        self.line(self.paint(self.fit(text), "cyan"))

    def warn(self, text: str) -> None:
        self.line(self.paint(self.fit(f"! {text}"), "yellow"))

    def error(self, text: str) -> None:
        self.line(self.paint(self.fit(f"✗ {text}"), "red"))

    def assistant_chunk(self, text: str) -> None:
        """アシスタントの本文をストリーミング表示する。"""
        self.write(text)

    def thinking_chunk(self, text: str) -> None:
        """思考の要約を薄い色で表示する。compact では出さない。"""
        if self.compact:
            return
        self.write(self.paint(text, "grey", "italic"))

    def thinking_header(self) -> None:
        if self.compact:
            return
        self.ensure_newline()
        self.line(self.paint("· 思考", "grey", "italic"))

    def tool_call(self, name: str, summary: str) -> None:
        """ツール呼び出しの1行サマリ。"""
        self.ensure_newline()
        label = self.paint(f"⚒ {name}", "blue", "bold")
        # ラベル分を引いた残り幅にサマリを収める
        room = max(8, self.width - len(name) - 3)
        trimmed = summary if len(summary) <= room else summary[: room - 1] + "…"
        self.line(f"{label} {self.paint(trimmed, 'grey')}")

    def tool_result(self, preview: str, *, is_error: bool = False) -> None:
        """ツール結果のプレビューをインデントして表示する。"""
        style = ("red",) if is_error else ("grey",)
        limit = PREVIEW_LINES_COMPACT if self.compact else PREVIEW_LINES
        lines = preview.splitlines()
        for raw in lines[:limit]:
            self.line("   " + self.paint(self.fit(raw, indent=3), *style))
        remaining = len(lines) - limit
        if remaining > 0:
            self.line("   " + self.paint(f"… 他 {remaining} 行", *style))

    def usage(self, text: str) -> None:
        self.line(self.paint(self.fit(text), "grey"))

    def prompt(self) -> str:
        """ユーザー入力を1行読む。EOF は EOFError を投げる。"""
        self.ensure_newline()
        marker = self.paint("› ", "magenta", "bold")
        return input(marker)

    def confirm(self, question: str, detail: str = "") -> bool:
        """実行してよいか確認する。y/yes 以外はすべて拒否とみなす。"""
        self.ensure_newline()
        if detail:
            limit = DETAIL_LINES_COMPACT if self.compact else DETAIL_LINES
            lines = detail.splitlines()
            for raw in lines[:limit]:
                self.line("   " + self.paint(self.fit(raw, indent=3), "grey"))
            remaining = len(lines) - limit
            if remaining > 0:
                self.line("   " + self.paint(f"… 他 {remaining} 行", "grey"))
        marker = self.paint(f"? {self.fit(question, indent=8)} [y/N] ", "yellow", "bold")
        try:
            answer = input(marker)
        except EOFError:
            return False
        return answer.strip().lower() in {"y", "yes"}
