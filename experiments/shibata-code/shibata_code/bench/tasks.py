"""ベンチマークのタスク定義と、その合否判定。

モデルごとの「速さ・安さ・出来」を、印象ではなく数字で比べるための土台。
各タスクは次の3つを持つ。

1. `files`     … 作業ディレクトリに置く初期ファイル
2. `prompt`    … エージェントに渡す指示
3. `checks`    … 終わったあとに成果物を検証する条件

検証は成果物(ファイルの中身、コマンドの成否)を見るものを基本にする。
最終発言の文面だけで判定すると、言い方の違いで結果がぶれるため。
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# 検証コマンドの1件あたりの上限(秒)。無限ループしたテストで止まらないように。
CHECK_TIMEOUT = 60


@dataclass(frozen=True)
class CheckResult:
    """検証1件の結果。"""

    label: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True)
class RunContext:
    """検証に渡す、実行が終わったあとの状態。"""

    workspace: Path
    answer: str


class Check:
    """検証条件の基底。`verify` が合否を返す。"""

    @property
    def label(self) -> str:
        return type(self).__name__

    def verify(self, ctx: RunContext) -> CheckResult:  # pragma: no cover - 抽象
        raise NotImplementedError


@dataclass(frozen=True)
class FileExists(Check):
    """ファイルが存在すること。"""

    path: str

    @property
    def label(self) -> str:
        return f"{self.path} がある"

    def verify(self, ctx: RunContext) -> CheckResult:
        target = ctx.workspace / self.path
        return CheckResult(self.label, target.is_file(), "" if target.is_file() else "無い")


@dataclass(frozen=True)
class FileContains(Check):
    """ファイルに指定の文字列が含まれること。"""

    path: str
    needle: str

    @property
    def label(self) -> str:
        return f"{self.path} に「{self.needle}」がある"

    def verify(self, ctx: RunContext) -> CheckResult:
        target = ctx.workspace / self.path
        if not target.is_file():
            return CheckResult(self.label, False, f"{self.path} が無い")
        text = target.read_text(encoding="utf-8", errors="replace")
        ok = self.needle in text
        return CheckResult(self.label, ok, "" if ok else "見つからない")


@dataclass(frozen=True)
class FileLacks(Check):
    """ファイルに指定の文字列が残っていないこと(改名の確認などに使う)。"""

    path: str
    needle: str

    @property
    def label(self) -> str:
        return f"{self.path} に「{self.needle}」が残っていない"

    def verify(self, ctx: RunContext) -> CheckResult:
        target = ctx.workspace / self.path
        if not target.is_file():
            return CheckResult(self.label, False, f"{self.path} が無い")
        text = target.read_text(encoding="utf-8", errors="replace")
        ok = self.needle not in text
        return CheckResult(self.label, ok, "" if ok else "まだ残っている")


@dataclass(frozen=True)
class FileEquals(Check):
    """ファイルの中身が一字一句そのとおりであること。"""

    path: str
    expected: str

    @property
    def label(self) -> str:
        return f"{self.path} が期待どおり"

    def verify(self, ctx: RunContext) -> CheckResult:
        target = ctx.workspace / self.path
        if not target.is_file():
            return CheckResult(self.label, False, f"{self.path} が無い")
        actual = target.read_text(encoding="utf-8", errors="replace")
        if actual == self.expected:
            return CheckResult(self.label, True)
        return CheckResult(self.label, False, "余計な差分がある")


@dataclass(frozen=True)
class AnswerContains(Check):
    """最終発言に指定の文字列が含まれること。"""

    needle: str

    @property
    def label(self) -> str:
        return f"回答に「{self.needle}」がある"

    def verify(self, ctx: RunContext) -> CheckResult:
        ok = self.needle in ctx.answer
        return CheckResult(self.label, ok, "" if ok else "回答に無い")


@dataclass(frozen=True)
class CommandSucceeds(Check):
    """コマンドが終了コード0で終わること。

    `{python}` は実行中のインタプリタに置き換える。ベンチを動かした
    Python と、タスクの中で走る Python を揃えるため。
    """

    command: str
    timeout: int = CHECK_TIMEOUT

    @property
    def label(self) -> str:
        return f"`{self.command}` が通る"

    def verify(self, ctx: RunContext) -> CheckResult:
        command = self.command.replace("{python}", sys.executable)
        try:
            completed = subprocess.run(
                command,
                shell=True,
                cwd=str(ctx.workspace),
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            return CheckResult(self.label, False, f"{self.timeout} 秒で打ち切り")
        except OSError as exc:
            return CheckResult(self.label, False, f"起動できない: {exc}")

        if completed.returncode == 0:
            return CheckResult(self.label, True)
        tail = (completed.stderr or completed.stdout or "").strip().splitlines()
        detail = tail[-1] if tail else f"終了コード {completed.returncode}"
        return CheckResult(self.label, False, detail)


@dataclass(frozen=True)
class TaskSpec:
    """ベンチのタスク1件。"""

    key: str
    title: str
    prompt: str
    files: dict[str, str] = field(default_factory=dict)
    checks: tuple[Check, ...] = ()
    # 使う道具の目安(表示用)。判定には使わない。
    skills: tuple[str, ...] = ()
    # 自己検査用の模範解答。これを当てれば全チェックが通ること。
    solution: dict[str, str] = field(default_factory=dict)
    solution_answer: str = ""

    def materialize(self, root: Path) -> None:
        """初期ファイルを作業ディレクトリへ展開する。"""
        _write_all(root, self.files)

    def apply_solution(self, root: Path) -> None:
        """模範解答を当てる(自己検査でのみ使う)。"""
        _write_all(root, self.solution)

    def verify(self, ctx: RunContext) -> list[CheckResult]:
        return [check.verify(ctx) for check in self.checks]


def _write_all(root: Path, files: dict[str, str]) -> None:
    for relative, content in files.items():
        target = Path(root) / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


# ----------------------------------------------------------------------
# タスク一覧
# ----------------------------------------------------------------------

_NOTES = "見出し\n\n1行目\n2行目\n3行目\n\n終わり\n"

_CALC_BROKEN = '''"""合計を出す小さなモジュール。"""


def total(values):
    """数値の合計を返す。"""
    result = 0
    for value in values[:-1]:
        result += value
    return result
'''

_CALC_FIXED = '''"""合計を出す小さなモジュール。"""


def total(values):
    """数値の合計を返す。"""
    result = 0
    for value in values:
        result += value
    return result
'''

_CALC_TEST = '''"""total の振る舞い。"""

import unittest

from calc import total


class TotalTest(unittest.TestCase):
    def test_合計を返す(self):
        self.assertEqual(total([1, 2, 3]), 6)

    def test_空なら0(self):
        self.assertEqual(total([]), 0)

    def test_負の数も足す(self):
        self.assertEqual(total([5, -2]), 3)


if __name__ == "__main__":
    unittest.main()
'''

_SLUG_TEST = '''"""slugify の仕様。"""

import unittest

from slug import slugify


class SlugifyTest(unittest.TestCase):
    def test_小文字にする(self):
        self.assertEqual(slugify("Hello World"), "hello-world")

    def test_連続する空白は1つの区切りにする(self):
        self.assertEqual(slugify("a   b"), "a-b")

    def test_前後の空白は落とす(self):
        self.assertEqual(slugify("  hi  "), "hi")

    def test_記号は落とす(self):
        self.assertEqual(slugify("Go! Now?"), "go-now")

    def test_空文字は空文字(self):
        self.assertEqual(slugify(""), "")


if __name__ == "__main__":
    unittest.main()
'''

_SLUG_SOLUTION = '''"""見出しからURL用の文字列を作る。"""

import re


def slugify(text):
    """英数字だけを残し、空白をハイフンにした小文字の文字列を返す。"""
    cleaned = re.sub(r"[^a-zA-Z0-9\\s-]", "", text)
    words = cleaned.lower().split()
    return "-".join(words)
'''

_APP_BEFORE = '''"""入り口。"""

from helpers import fetch_data


def summarize():
    """件数を文字列で返す。"""
    return f"{len(fetch_data())} 件"
'''

_APP_AFTER = '''"""入り口。"""

from helpers import load_records


def summarize():
    """件数を文字列で返す。"""
    return f"{len(load_records())} 件"
'''

_HELPERS_BEFORE = '''"""データ取得。"""


def fetch_data():
    """固定のレコードを返す。"""
    return [{"id": 1}, {"id": 2}]
'''

_HELPERS_AFTER = '''"""データ取得。"""


def load_records():
    """固定のレコードを返す。"""
    return [{"id": 1}, {"id": 2}]
'''

_APP_TEST_BEFORE = '''"""summarize の振る舞い。"""

import unittest

from app import summarize
from helpers import fetch_data


class SummarizeTest(unittest.TestCase):
    def test_件数を返す(self):
        self.assertEqual(summarize(), "2 件")

    def test_取得できる(self):
        self.assertEqual(len(fetch_data()), 2)


if __name__ == "__main__":
    unittest.main()
'''

_APP_TEST_AFTER = '''"""summarize の振る舞い。"""

import unittest

from app import summarize
from helpers import load_records


class SummarizeTest(unittest.TestCase):
    def test_件数を返す(self):
        self.assertEqual(summarize(), "2 件")

    def test_取得できる(self):
        self.assertEqual(len(load_records()), 2)


if __name__ == "__main__":
    unittest.main()
'''

_CONFIG_BEFORE = """[server]
host = 127.0.0.1
port = 8080
timeout = 30
retries = 3

[log]
level = info
timeout = 5
"""

_CONFIG_AFTER = """[server]
host = 127.0.0.1
port = 8080
timeout = 90
retries = 3

[log]
level = info
timeout = 5
"""


def _haystack() -> dict[str, str]:
    """目的の定数を1つだけ含む、それらしいファイル群を作る。"""
    files = {
        "pkg/__init__.py": '"""パッケージ。"""\n',
        "pkg/deep/__init__.py": '"""下位パッケージ。"""\n',
        "pkg/deep/settings.py": (
            '"""設定値。"""\n\nDEBUG = False\nTARGET_CONSTANT = 42\nRETRIES = 3\n'
        ),
    }
    for index in range(1, 9):
        files[f"pkg/module_{index}.py"] = (
            f'"""ダミー {index}。"""\n\n\ndef helper_{index}(value):\n'
            f'    """値をそのまま返す。"""\n    return value\n'
        )
    return files


TASKS: dict[str, TaskSpec] = {
    "count-lines": TaskSpec(
        key="count-lines",
        title="ファイルの行数を数える",
        prompt="notes.md の行数を数えて、最後に数字だけで答えて",
        files={"notes.md": _NOTES},
        checks=(AnswerContains("7"),),
        skills=("read",),
        solution_answer="7",
    ),
    "find-symbol": TaskSpec(
        key="find-symbol",
        title="定義箇所を探す",
        prompt="TARGET_CONSTANT を定義しているファイルのパスを答えて",
        files=_haystack(),
        checks=(AnswerContains("pkg/deep/settings.py"),),
        skills=("glob", "grep"),
        solution_answer="pkg/deep/settings.py にある",
    ),
    "fix-test": TaskSpec(
        key="fix-test",
        title="失敗するテストを直す",
        prompt="テストが通るように calc.py を直して。テストファイルは変えないこと",
        files={"calc.py": _CALC_BROKEN, "test_calc.py": _CALC_TEST},
        checks=(
            CommandSucceeds("{python} -m unittest test_calc"),
            FileContains("test_calc.py", "def test_負の数も足す"),
        ),
        skills=("read", "edit", "bash"),
        solution={"calc.py": _CALC_FIXED},
    ),
    "implement": TaskSpec(
        key="implement",
        title="テストから実装を起こす",
        prompt="test_slug.py が通るように slug.py を新しく作って",
        files={"test_slug.py": _SLUG_TEST},
        checks=(
            FileExists("slug.py"),
            CommandSucceeds("{python} -m unittest test_slug"),
        ),
        skills=("read", "write", "bash"),
        solution={"slug.py": _SLUG_SOLUTION},
    ),
    "rename": TaskSpec(
        key="rename",
        title="複数ファイルにまたがる改名",
        prompt=(
            "関数 fetch_data を load_records に改名して。"
            "呼び出し側もテストも全部そろえて、テストが通る状態にすること"
        ),
        files={
            "app.py": _APP_BEFORE,
            "helpers.py": _HELPERS_BEFORE,
            "test_app.py": _APP_TEST_BEFORE,
        },
        checks=(
            FileLacks("app.py", "fetch_data"),
            FileLacks("helpers.py", "fetch_data"),
            FileLacks("test_app.py", "fetch_data"),
            FileContains("helpers.py", "def load_records"),
            CommandSucceeds("{python} -m unittest test_app"),
        ),
        skills=("grep", "edit", "bash"),
        solution={
            "app.py": _APP_AFTER,
            "helpers.py": _HELPERS_AFTER,
            "test_app.py": _APP_TEST_AFTER,
        },
    ),
    "careful-edit": TaskSpec(
        key="careful-edit",
        title="1か所だけ正確に書き換える",
        prompt="config.ini の [server] の timeout を 90 にして。他の行は一切触らないこと",
        files={"config.ini": _CONFIG_BEFORE},
        checks=(FileEquals("config.ini", _CONFIG_AFTER),),
        skills=("read", "edit"),
        solution={"config.ini": _CONFIG_AFTER},
    ),
}

#: 既定で走らせる順番。軽いものから並べてある。
DEFAULT_ORDER = (
    "count-lines",
    "find-symbol",
    "careful-edit",
    "fix-test",
    "implement",
    "rename",
)


def resolve_tasks(names: str | None) -> list[TaskSpec]:
    """カンマ区切りの指定からタスクを選ぶ。未指定なら全部。"""
    if not names or names.strip().lower() == "all":
        return [TASKS[key] for key in DEFAULT_ORDER]

    selected: list[TaskSpec] = []
    for raw in names.split(","):
        key = raw.strip()
        if not key:
            continue
        if key not in TASKS:
            known = ", ".join(DEFAULT_ORDER)
            raise ValueError(f"未知のタスク: {key}(使えるのは {known})")
        selected.append(TASKS[key])
    if not selected:
        raise ValueError("タスクが1つも選ばれていない")
    return selected
