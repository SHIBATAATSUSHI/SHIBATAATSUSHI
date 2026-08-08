"""端末表示。ANSIエスケープでの色付けと、ストリーミング出力の整形。"""

from __future__ import annotations

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


class UI:
    """端末への出力をまとめる。色を無効化した場合も同じ呼び出しで動く。"""

    def __init__(self, *, color: bool = True, stream: TextIO | None = None) -> None:
        self.stream = stream or sys.stdout
        # パイプ越しの実行では色を落とす。
        self.color = color and self.stream.isatty()
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
        self.line()
        self.line(self.paint(f"  {title}", "bold", "magenta"))
        self.line(self.paint(f"  {subtitle}", "grey"))
        self.line()

    def info(self, text: str) -> None:
        self.line(self.paint(text, "grey"))

    def notice(self, text: str) -> None:
        self.line(self.paint(text, "cyan"))

    def warn(self, text: str) -> None:
        self.line(self.paint(f"! {text}", "yellow"))

    def error(self, text: str) -> None:
        self.line(self.paint(f"✗ {text}", "red"))

    def assistant_chunk(self, text: str) -> None:
        """アシスタントの本文をストリーミング表示する。"""
        self.write(text)

    def thinking_chunk(self, text: str) -> None:
        """思考の要約を薄い色で表示する。"""
        self.write(self.paint(text, "grey", "italic"))

    def thinking_header(self) -> None:
        self.ensure_newline()
        self.line(self.paint("· 思考", "grey", "italic"))

    def tool_call(self, name: str, summary: str) -> None:
        """ツール呼び出しの1行サマリ。"""
        self.ensure_newline()
        label = self.paint(f"⚒ {name}", "blue", "bold")
        self.line(f"{label} {self.paint(summary, 'grey')}")

    def tool_result(self, preview: str, *, is_error: bool = False) -> None:
        """ツール結果のプレビューをインデントして表示する。"""
        style = ("red",) if is_error else ("grey",)
        for raw in preview.splitlines()[:12]:
            self.line("   " + self.paint(raw[:200], *style))

    def usage(self, text: str) -> None:
        self.line(self.paint(text, "grey"))

    def prompt(self) -> str:
        """ユーザー入力を1行読む。EOF は空文字ではなく EOFError を投げる。"""
        self.ensure_newline()
        marker = self.paint("› ", "magenta", "bold")
        return input(marker)

    def confirm(self, question: str, detail: str = "") -> bool:
        """実行してよいか確認する。y/yes 以外はすべて拒否とみなす。"""
        self.ensure_newline()
        if detail:
            for raw in detail.splitlines()[:10]:
                self.line("   " + self.paint(raw[:200], "grey"))
        marker = self.paint(f"? {question} [y/N] ", "yellow", "bold")
        try:
            answer = input(marker)
        except EOFError:
            return False
        return answer.strip().lower() in {"y", "yes"}
