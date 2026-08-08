"""ツール実装の検証。"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mytos.tools import ToolError, Toolbox  # noqa: E402
from mytos.workspace import Workspace  # noqa: E402


class ToolboxTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.tools = Toolbox(Workspace(self.root), bash_timeout=10)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write_file(self, name: str, body: str) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        return path

    # -- スキーマ -------------------------------------------------------
    def test_全ツールがAPIスキーマを持つ(self) -> None:
        schemas = self.tools.api_schemas()
        names = {schema["name"] for schema in schemas}
        self.assertEqual(names, {"read", "write", "edit", "glob", "grep", "bash"})
        for schema in schemas:
            self.assertTrue(schema["description"].strip())
            self.assertEqual(schema["input_schema"]["type"], "object")

    def test_スキーマの順序は決定的(self) -> None:
        # プロンプトキャッシュを壊さないため、並びが毎回同じである必要がある。
        first = [s["name"] for s in self.tools.api_schemas()]
        second = [s["name"] for s in Toolbox(Workspace(self.root)).api_schemas()]
        self.assertEqual(first, second)

    # -- read ----------------------------------------------------------
    def test_readは行番号付きで返す(self) -> None:
        self.write_file("a.txt", "一行目\n二行目\n")
        output = self.tools.run("read", {"path": "a.txt"})
        self.assertIn("一行目", output)
        self.assertIn("1\t", output)

    def test_readはoffsetとlimitで範囲を絞れる(self) -> None:
        self.write_file("n.txt", "\n".join(str(i) for i in range(1, 51)))
        output = self.tools.run("read", {"path": "n.txt", "offset": 10, "limit": 3})
        self.assertIn("10\t10", output)
        self.assertNotIn("14\t14", output)

    def test_readはディレクトリの一覧を返す(self) -> None:
        self.write_file("dir/inner.txt", "x")
        output = self.tools.run("read", {"path": "dir"})
        self.assertIn("inner.txt", output)

    def test_存在しないファイルはToolError(self) -> None:
        with self.assertRaises(ToolError):
            self.tools.run("read", {"path": "missing.txt"})

    def test_バイナリは読まない(self) -> None:
        (self.root / "b.bin").write_bytes(b"\x00\x01\x02")
        with self.assertRaises(ToolError):
            self.tools.run("read", {"path": "b.bin"})

    # -- write ---------------------------------------------------------
    def test_writeは新規作成する(self) -> None:
        output = self.tools.run("write", {"path": "new/dir/x.py", "content": "print(1)\n"})
        self.assertIn("作成", output)
        self.assertEqual((self.root / "new/dir/x.py").read_text(encoding="utf-8"), "print(1)\n")

    def test_writeは上書きできる(self) -> None:
        self.write_file("x.txt", "古い")
        self.tools.run("write", {"path": "x.txt", "content": "新しい"})
        self.assertEqual((self.root / "x.txt").read_text(encoding="utf-8"), "新しい")

    # -- edit ----------------------------------------------------------
    def test_read前のeditは拒否する(self) -> None:
        self.write_file("e.txt", "abc")
        with self.assertRaises(ToolError) as ctx:
            self.tools.run("edit", {"path": "e.txt", "old_string": "abc", "new_string": "xyz"})
        self.assertIn("read", str(ctx.exception))

    def test_read後のeditは成功する(self) -> None:
        self.write_file("e.txt", "def greet():\n    return 1\n")
        self.tools.run("read", {"path": "e.txt"})
        output = self.tools.run(
            "edit", {"path": "e.txt", "old_string": "return 1", "new_string": "return 2"}
        )
        self.assertIn("編集", output)
        self.assertIn("return 2", (self.root / "e.txt").read_text(encoding="utf-8"))

    def test_read以降に変更されていたら拒否する(self) -> None:
        path = self.write_file("e.txt", "abc")
        self.tools.run("read", {"path": "e.txt"})
        time.sleep(0.01)
        path.write_text("外部から書き換えた", encoding="utf-8")
        # mtime を確実に進める
        future = path.stat().st_mtime + 5
        import os

        os.utime(path, (future, future))
        with self.assertRaises(ToolError) as ctx:
            self.tools.run(
                "edit", {"path": "e.txt", "old_string": "外部", "new_string": "内部"}
            )
        self.assertIn("変更されている", str(ctx.exception))

    def test_複数一致はreplace_allなしだと拒否する(self) -> None:
        self.write_file("e.txt", "x\nx\n")
        self.tools.run("read", {"path": "e.txt"})
        with self.assertRaises(ToolError):
            self.tools.run("edit", {"path": "e.txt", "old_string": "x", "new_string": "y"})

    def test_replace_allで全置換できる(self) -> None:
        self.write_file("e.txt", "x\nx\n")
        self.tools.run("read", {"path": "e.txt"})
        self.tools.run(
            "edit",
            {"path": "e.txt", "old_string": "x", "new_string": "y", "replace_all": True},
        )
        self.assertEqual((self.root / "e.txt").read_text(encoding="utf-8"), "y\ny\n")

    def test_一致しないold_stringは拒否する(self) -> None:
        self.write_file("e.txt", "abc")
        self.tools.run("read", {"path": "e.txt"})
        with self.assertRaises(ToolError):
            self.tools.run("edit", {"path": "e.txt", "old_string": "zzz", "new_string": "y"})

    # -- glob ----------------------------------------------------------
    def test_globはパターンに一致するファイルを返す(self) -> None:
        self.write_file("src/a.py", "")
        self.write_file("src/b.txt", "")
        output = self.tools.run("glob", {"pattern": "**/*.py"})
        self.assertIn("a.py", output)
        self.assertNotIn("b.txt", output)

    def test_globは無視ディレクトリを除外する(self) -> None:
        self.write_file("node_modules/pkg/index.py", "")
        self.write_file("app.py", "")
        output = self.tools.run("glob", {"pattern": "**/*.py"})
        self.assertIn("app.py", output)
        self.assertNotIn("node_modules", output)

    def test_一致無しでも例外にしない(self) -> None:
        output = self.tools.run("glob", {"pattern": "**/*.rs"})
        self.assertIn("なかった", output)

    # -- grep ----------------------------------------------------------
    def test_grepはファイル名を返す(self) -> None:
        self.write_file("m.py", "def 対象():\n    pass\n")
        output = self.tools.run("grep", {"pattern": "対象"})
        self.assertIn("m.py", output)

    def test_grepはcontentモードで一致行を返す(self) -> None:
        self.write_file("m.py", "alpha\nbeta\n")
        output = self.tools.run("grep", {"pattern": "beta", "output_mode": "content"})
        self.assertIn("beta", output)

    def test_grepはglobで対象を絞れる(self) -> None:
        self.write_file("a.py", "needle")
        self.write_file("b.md", "needle")
        output = self.tools.run("grep", {"pattern": "needle", "glob": "*.py"})
        self.assertIn("a.py", output)
        self.assertNotIn("b.md", output)

    def test_純Python実装でも同じ結果になる(self) -> None:
        self.write_file("p.py", "target_symbol = 1\n")
        output = self.tools._grep_with_python("target_symbol", self.root, "content", None, False)
        self.assertIn("target_symbol", output)

    # -- bash ----------------------------------------------------------
    def test_bashは標準出力を返す(self) -> None:
        output = self.tools.run("bash", {"command": "echo こんにちは"})
        self.assertIn("こんにちは", output)
        self.assertIn("終了コード 0", output)

    def test_bashは作業ディレクトリで動く(self) -> None:
        output = self.tools.run("bash", {"command": "pwd"})
        self.assertIn(str(self.root), output)

    def test_失敗したコマンドはToolError(self) -> None:
        with self.assertRaises(ToolError) as ctx:
            self.tools.run("bash", {"command": "exit 3"})
        self.assertIn("終了コード 3", str(ctx.exception))

    def test_タイムアウトはToolError(self) -> None:
        with self.assertRaises(ToolError) as ctx:
            self.tools.run("bash", {"command": "sleep 5", "timeout": 1})
        self.assertIn("タイムアウト", str(ctx.exception))

    # -- 共通 -----------------------------------------------------------
    def test_未知のツールはToolError(self) -> None:
        with self.assertRaises(ToolError):
            self.tools.run("存在しない", {})

    def test_作業ディレクトリ外の操作はToolError(self) -> None:
        with self.assertRaises(ToolError):
            self.tools.run("read", {"path": "../../etc/passwd"})


if __name__ == "__main__":
    unittest.main()
