"""端末表示の検証。特に細い画面(携帯からのSSH)での整形。"""

from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shibata_code.ui import MIN_WIDTH, UI  # noqa: E402


class WidthTest(unittest.TestCase):
    def make(self, width: int, compact: bool = False) -> tuple[UI, io.StringIO]:
        buffer = io.StringIO()
        return UI(color=False, stream=buffer, width=width, compact=compact), buffer

    def test_幅を超える行は切り詰める(self) -> None:
        ui, buffer = self.make(40)
        ui.info("あ" * 100)
        for line in buffer.getvalue().splitlines():
            self.assertLessEqual(len(line), 40)

    def test_切り詰めたら省略記号を付ける(self) -> None:
        ui, buffer = self.make(30)
        ui.info("x" * 100)
        self.assertIn("…", buffer.getvalue())

    def test_収まる行はそのまま(self) -> None:
        ui, buffer = self.make(40)
        ui.info("短い行")
        self.assertIn("短い行", buffer.getvalue())
        self.assertNotIn("…", buffer.getvalue())

    def test_ツール結果はインデント分も考慮して切る(self) -> None:
        ui, buffer = self.make(40)
        ui.tool_result("y" * 100)
        for line in buffer.getvalue().splitlines():
            self.assertLessEqual(len(line), 40)

    def test_ツール呼び出しのサマリも幅に収める(self) -> None:
        ui, buffer = self.make(40)
        ui.tool_call("bash", "z" * 200)
        for line in buffer.getvalue().splitlines():
            self.assertLessEqual(len(line), 40)

    def test_極端に狭くても下限を保つ(self) -> None:
        ui, _ = self.make(3)
        self.assertEqual(ui.width, MIN_WIDTH)

    def test_本文のストリーミングは切らない(self) -> None:
        # 本文は端末側に折り返させる。途中で切ると読めなくなるため。
        ui, buffer = self.make(30)
        ui.assistant_chunk("あ" * 100)
        self.assertEqual(buffer.getvalue(), "あ" * 100)


class CompactTest(unittest.TestCase):
    def make(self, compact: bool) -> tuple[UI, io.StringIO]:
        buffer = io.StringIO()
        return UI(color=False, stream=buffer, width=60, compact=compact), buffer

    def test_compactでは思考を出さない(self) -> None:
        ui, buffer = self.make(True)
        ui.thinking_header()
        ui.thinking_chunk("考え中")
        self.assertEqual(buffer.getvalue(), "")

    def test_通常は思考を出す(self) -> None:
        ui, buffer = self.make(False)
        ui.thinking_chunk("考え中")
        self.assertIn("考え中", buffer.getvalue())

    def test_compactではプレビューを短くする(self) -> None:
        ui, buffer = self.make(True)
        ui.tool_result("\n".join(f"行{i}" for i in range(20)))
        body = buffer.getvalue()
        self.assertIn("行0", body)
        self.assertNotIn("行9", body)
        self.assertIn("他 17 行", body)

    def test_通常はもっと多く出す(self) -> None:
        ui, buffer = self.make(False)
        ui.tool_result("\n".join(f"行{i}" for i in range(20)))
        body = buffer.getvalue()
        self.assertIn("行11", body)
        self.assertIn("他 8 行", body)

    def test_残りが無ければ省略行を出さない(self) -> None:
        ui, buffer = self.make(True)
        ui.tool_result("1行だけ")
        self.assertNotIn("他", buffer.getvalue())

    def test_compactのバナーは短い(self) -> None:
        ui, buffer = self.make(True)
        ui.banner("Shibata Code", "opus / high")
        # 空行で囲まない
        self.assertEqual(len(buffer.getvalue().splitlines()), 2)


class ConfirmTest(unittest.TestCase):
    def test_yes以外は拒否とみなす(self) -> None:
        buffer = io.StringIO()
        ui = UI(color=False, stream=buffer, width=60)
        for answer, expected in [("y", True), ("yes", True), ("Y", True), ("n", False), ("", False), ("たぶん", False)]:
            with self.subTest(answer=answer):
                ui_input = ui.confirm
                import builtins

                original = builtins.input
                builtins.input = lambda _prompt="": answer
                try:
                    self.assertEqual(ui_input("実行する"), expected)
                finally:
                    builtins.input = original

    def test_EOFは拒否とみなす(self) -> None:
        buffer = io.StringIO()
        ui = UI(color=False, stream=buffer, width=60)
        import builtins

        original = builtins.input

        def raise_eof(_prompt=""):
            raise EOFError

        builtins.input = raise_eof
        try:
            self.assertFalse(ui.confirm("実行する"))
        finally:
            builtins.input = original

    def test_詳細も幅に収める(self) -> None:
        buffer = io.StringIO()
        ui = UI(color=False, stream=buffer, width=40)
        import builtins

        original = builtins.input
        builtins.input = lambda _prompt="": "n"
        try:
            ui.confirm("実行する", "w" * 200)
        finally:
            builtins.input = original
        for line in buffer.getvalue().splitlines():
            self.assertLessEqual(len(line), 40)


if __name__ == "__main__":
    unittest.main()
