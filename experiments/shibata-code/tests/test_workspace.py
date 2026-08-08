"""作業ディレクトリ境界の検証。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shibata_code.workspace import Workspace, WorkspaceError  # noqa: E402


class WorkspaceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.ws = Workspace(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_相対パスは作業ディレクトリ基準で解決される(self) -> None:
        self.assertEqual(self.ws.resolve("a/b.txt"), self.root / "a" / "b.txt")

    def test_未作成のパスも解決できる(self) -> None:
        resolved = self.ws.resolve("まだ/無い/file.txt")
        self.assertEqual(resolved, self.root / "まだ" / "無い" / "file.txt")

    def test_親ディレクトリへの脱出は拒否する(self) -> None:
        with self.assertRaises(WorkspaceError):
            self.ws.resolve("../outside.txt")

    def test_絶対パスでの脱出は拒否する(self) -> None:
        with self.assertRaises(WorkspaceError):
            self.ws.resolve("/etc/passwd")

    def test_シンボリックリンク経由の脱出は拒否する(self) -> None:
        outside = Path(tempfile.mkdtemp()).resolve()
        try:
            (outside / "secret.txt").write_text("秘密", encoding="utf-8")
            link = self.root / "link"
            os.symlink(outside, link)
            with self.assertRaises(WorkspaceError):
                self.ws.resolve("link/secret.txt")
        finally:
            for path in sorted(outside.rglob("*"), reverse=True):
                path.unlink()
            outside.rmdir()

    def test_作業ディレクトリ自身は許可する(self) -> None:
        self.assertEqual(self.ws.resolve("."), self.root)

    def test_空パスは拒否する(self) -> None:
        with self.assertRaises(WorkspaceError):
            self.ws.resolve("   ")

    def test_display_は相対パスを返す(self) -> None:
        self.assertEqual(self.ws.display(self.root / "x" / "y.py"), os.path.join("x", "y.py"))


if __name__ == "__main__":
    unittest.main()
