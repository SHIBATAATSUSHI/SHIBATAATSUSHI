"""`.gitignore` の解釈と、検索からの除外の検証。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shibata_code.gitignore import GitIgnore, compile_rule  # noqa: E402
from shibata_code.tools import Toolbox  # noqa: E402
from shibata_code.workspace import Workspace  # noqa: E402


class CompileRuleTest(unittest.TestCase):
    def test_空行とコメントは無視する(self) -> None:
        self.assertIsNone(compile_rule(""))
        self.assertIsNone(compile_rule("   "))
        self.assertIsNone(compile_rule("# これはコメント"))

    def test_否定を認識する(self) -> None:
        rule = compile_rule("!keep.txt")
        self.assertIsNotNone(rule)
        self.assertTrue(rule.negated)

    def test_ディレクトリ限定を認識する(self) -> None:
        rule = compile_rule("build/")
        self.assertTrue(rule.dir_only)

    def test_スラッシュだけの行は無視する(self) -> None:
        self.assertIsNone(compile_rule("/"))


class MatchTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def build(self, *lines: str) -> GitIgnore:
        (self.root / ".gitignore").write_text("\n".join(lines), encoding="utf-8")
        return GitIgnore.load(self.root)

    def touch(self, relative: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
        return path

    def test_拡張子パターン(self) -> None:
        ignore = self.build("*.log")
        self.assertTrue(ignore.is_ignored(self.touch("app.log")))
        self.assertTrue(ignore.is_ignored(self.touch("deep/dir/app.log")))
        self.assertFalse(ignore.is_ignored(self.touch("app.py")))

    def test_ディレクトリ配下を丸ごと除外する(self) -> None:
        ignore = self.build("build/")
        self.assertTrue(ignore.is_ignored(self.touch("build/out.js")))
        self.assertTrue(ignore.is_ignored(self.touch("build/nested/deep.js")))
        self.assertFalse(ignore.is_ignored(self.touch("src/main.js")))

    def test_ルート限定パターン(self) -> None:
        ignore = self.build("/dist")
        self.assertTrue(ignore.is_ignored(self.touch("dist/a.js")))
        # 深い場所の同名は対象外
        self.assertFalse(ignore.is_ignored(self.touch("pkg/dist/a.js")))

    def test_パスを含むパターン(self) -> None:
        ignore = self.build("src/generated")
        self.assertTrue(ignore.is_ignored(self.touch("src/generated/x.py")))
        self.assertFalse(ignore.is_ignored(self.touch("other/generated/x.py")))

    def test_二重アスタリスク(self) -> None:
        ignore = self.build("**/tmp")
        self.assertTrue(ignore.is_ignored(self.touch("tmp/a")))
        self.assertTrue(ignore.is_ignored(self.touch("a/b/tmp/c")))

    def test_否定で除外を打ち消す(self) -> None:
        ignore = self.build("*.log", "!important.log")
        self.assertTrue(ignore.is_ignored(self.touch("debug.log")))
        self.assertFalse(ignore.is_ignored(self.touch("important.log")))

    def test_後の行が優先される(self) -> None:
        ignore = self.build("!keep.txt", "keep.txt")
        self.assertTrue(ignore.is_ignored(self.touch("keep.txt")))

    def test_疑問符は1文字に当たる(self) -> None:
        ignore = self.build("a?.txt")
        self.assertTrue(ignore.is_ignored(self.touch("ab.txt")))
        self.assertFalse(ignore.is_ignored(self.touch("abc.txt")))

    def test_アスタリスクは階層をまたがない(self) -> None:
        ignore = self.build("/src/*.py")
        self.assertTrue(ignore.is_ignored(self.touch("src/a.py")))
        self.assertFalse(ignore.is_ignored(self.touch("src/deep/a.py")))

    def test_gitignoreが無ければ何も除外しない(self) -> None:
        ignore = GitIgnore.load(self.root)
        self.assertFalse(ignore.active)
        self.assertFalse(ignore.is_ignored(self.touch("anything.log")))

    def test_作業ディレクトリ外は判断しない(self) -> None:
        ignore = self.build("*.log")
        self.assertFalse(ignore.is_ignored(Path("/tmp/other.log")))


class ToolboxIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        (self.root / ".gitignore").write_text(
            "\n".join(["*.log", "generated/", "!generated/keep.py"]), encoding="utf-8"
        )
        for relative in (
            "src/main.py",
            "src/debug.log",
            "generated/auto.py",
            "generated/keep.py",
        ):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("対象の文字列 target_symbol\n", encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def tools(self, respect: bool = True) -> Toolbox:
        return Toolbox(Workspace(self.root), respect_gitignore=respect)

    def test_globがgitignoreを尊重する(self) -> None:
        output = self.tools().run("glob", {"pattern": "**/*.py"})
        self.assertIn("main.py", output)
        self.assertNotIn("auto.py", output)

    def test_無効化すれば除外しない(self) -> None:
        output = self.tools(respect=False).run("glob", {"pattern": "**/*.py"})
        self.assertIn("auto.py", output)

    def test_純Python版grepがgitignoreを尊重する(self) -> None:
        tools = self.tools()
        output = tools._grep_with_python(
            "target_symbol", self.root, "files_with_matches", None, False
        )
        self.assertIn("main.py", output)
        self.assertNotIn("auto.py", output)
        self.assertNotIn("debug.log", output)

    def test_親ディレクトリが除外なら否定は効かない(self) -> None:
        # git 自身の挙動に合わせている。除外されたディレクトリの中は
        # `!` を書いても復活しない(git は除外したディレクトリを走査しない)。
        tools = self.tools()
        output = tools._grep_with_python(
            "target_symbol", self.root, "files_with_matches", None, False
        )
        self.assertNotIn("keep.py", output)

    def test_セッション保存先は常に除外する(self) -> None:
        session_dir = self.root / ".shibata-code" / "sessions"
        session_dir.mkdir(parents=True)
        (session_dir / "old.json").write_text("target_symbol", encoding="utf-8")
        output = self.tools().run("glob", {"pattern": "**/*.json"})
        self.assertNotIn("shibata-code", output)


if __name__ == "__main__":
    unittest.main()
