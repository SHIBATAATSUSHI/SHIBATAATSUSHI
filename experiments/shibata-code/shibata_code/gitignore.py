"""`.gitignore` を読んで、検索対象から除外する。

実際のリポジトリでは、固定の除外リストだけでは足りない。ビルド生成物や
キャッシュがプロジェクトごとに違う場所へ吐かれるので、検索結果が
それらで埋まってしまう。

git の仕様を完全に再現はしない。よく使う書き方(`*.log`、`build/`、
`/dist`、`**/tmp`、`!keep.txt`)を扱える範囲に絞っている。
`ripgrep` は元から `.gitignore` を見るので、こちらは主に glob と
純Python版の grep のためのもの。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Rule:
    """`.gitignore` の1行を表す。"""

    regex: re.Pattern[str]
    negated: bool
    dir_only: bool


def _translate(pattern: str) -> str:
    """gitignore の glob を正規表現の本体に変換する。"""
    out: list[str] = []
    index = 0
    length = len(pattern)

    while index < length:
        char = pattern[index]

        if char == "*":
            # `**` は階層をまたぐ、`*` はまたがない
            if index + 1 < length and pattern[index + 1] == "*":
                index += 2
                if index < length and pattern[index] == "/":
                    index += 1
                    out.append("(?:.*/)?")
                else:
                    out.append(".*")
                continue
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        elif char == "[":
            # 文字クラスはそのまま通す(閉じ括弧まで)
            end = pattern.find("]", index + 1)
            if end == -1:
                out.append(re.escape(char))
            else:
                body = pattern[index + 1 : end]
                if body.startswith("!"):
                    body = "^" + body[1:]
                out.append(f"[{body}]")
                index = end + 1
                continue
        else:
            out.append(re.escape(char))
        index += 1

    return "".join(out)


def compile_rule(line: str) -> Rule | None:
    """`.gitignore` の1行を Rule にする。無視してよい行なら None。"""
    stripped = line.rstrip("\n").rstrip()
    if not stripped or stripped.startswith("#"):
        return None

    negated = stripped.startswith("!")
    if negated:
        stripped = stripped[1:]

    # 行末が `\` で終わるエスケープは扱わない(稀なので)
    dir_only = stripped.endswith("/")
    if dir_only:
        stripped = stripped[:-1]
    if not stripped:
        return None

    anchored = stripped.startswith("/")
    if anchored:
        stripped = stripped[1:]
    # 途中に `/` を含むパターンはルートからの相対として扱う
    if "/" in stripped:
        anchored = True

    body = _translate(stripped)
    if anchored:
        regex = re.compile(f"^{body}$")
    else:
        # どの階層のどのファイル名にも当たる
        regex = re.compile(f"(?:^|.*/){body}$")

    return Rule(regex=regex, negated=negated, dir_only=dir_only)


class GitIgnore:
    """`.gitignore` の判定。"""

    def __init__(self, root: Path, rules: list[Rule] | None = None) -> None:
        self.root = Path(root).resolve()
        self.rules = rules or []

    @classmethod
    def load(cls, root: Path) -> "GitIgnore":
        """作業ディレクトリ直下の `.gitignore` を読む。

        入れ子の `.gitignore` は読まない(ルートのものだけ)。
        """
        root = Path(root).resolve()
        path = root / ".gitignore"
        rules: list[Rule] = []
        if path.is_file():
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            for line in text.splitlines():
                rule = compile_rule(line)
                if rule is not None:
                    rules.append(rule)
        return cls(root, rules)

    @property
    def active(self) -> bool:
        return bool(self.rules)

    def _match(self, relative: str, is_dir: bool) -> bool:
        """1つのパスに対する判定。後ろの行ほど強い(否定で打ち消せる)。"""
        ignored = False
        for rule in self.rules:
            if rule.dir_only and not is_dir:
                continue
            if rule.regex.match(relative):
                ignored = not rule.negated
        return ignored

    def is_ignored(self, path: Path) -> bool:
        """このパスを除外すべきか。

        途中のディレクトリが除外されていれば、その配下もすべて除外する。
        これは git 自身と同じ挙動で、除外されたディレクトリの中は
        `!` を書いても復活しない(git は除外したディレクトリを走査しないため)。
        """
        if not self.rules:
            return False
        try:
            relative = Path(path).resolve().relative_to(self.root)
        except ValueError:
            # 作業ディレクトリの外。ここでは判断しない。
            return False

        parts = relative.parts
        if not parts:
            return False

        for depth in range(1, len(parts) + 1):
            partial = "/".join(parts[:depth])
            # 末尾以外は必ずディレクトリ
            is_dir = depth < len(parts) or Path(path).is_dir()
            if self._match(partial, is_dir):
                return True
        return False
