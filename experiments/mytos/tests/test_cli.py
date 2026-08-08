"""CLI の引数解釈とスラッシュコマンドの検証。"""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mytos.agent import Agent  # noqa: E402
from mytos.cli import Repl, build_parser, compose_prompt, main  # noqa: E402
from mytos.config import build_config  # noqa: E402
from mytos.session import Session  # noqa: E402
from mytos.tools import Toolbox  # noqa: E402
from mytos.ui import UI  # noqa: E402
from mytos.workspace import Workspace  # noqa: E402


class ParserTest(unittest.TestCase):
    def test_既定値(self) -> None:
        args = build_parser().parse_args([])
        self.assertEqual(args.effort, "high")
        self.assertEqual(args.mode, "ask")
        self.assertEqual(args.prompt, [])
        self.assertFalse(args.print_mode)

    def test_指示は連結される(self) -> None:
        args = build_parser().parse_args(["テストを", "直して"])
        self.assertEqual(args.prompt, ["テストを", "直して"])

    def test_不正なeffortは弾かれる(self) -> None:
        # argparse は使い方を stderr に吐くので、テスト出力から隔離する。
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                build_parser().parse_args(["-e", "ultra"])

    def test_不正なモードは弾かれる(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                build_parser().parse_args(["--mode", "whatever"])

    def test_短縮オプションが効く(self) -> None:
        args = build_parser().parse_args(["-m", "sonnet", "-e", "low", "-p"])
        self.assertEqual(args.model, "sonnet")
        self.assertEqual(args.effort, "low")
        self.assertTrue(args.print_mode)


class ComposePromptTest(unittest.TestCase):
    def test_引数だけなら連結して返す(self) -> None:
        self.assertEqual(compose_prompt(["テストを", "直して"], ""), "テストを 直して")

    def test_標準入力だけならそれを返す(self) -> None:
        self.assertEqual(compose_prompt([], "  差分の中身  "), "差分の中身")

    def test_両方あれば指示のあとに本文を続ける(self) -> None:
        result = compose_prompt(["レビューして"], "diff --git a/x b/x\n")
        self.assertEqual(result, "レビューして\n\ndiff --git a/x b/x")

    def test_どちらも無ければ空(self) -> None:
        self.assertEqual(compose_prompt([], "   \n"), "")


class ReplCommandTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.buffer = io.StringIO()
        self.ui = UI(color=False, stream=self.buffer)
        config = build_config(workspace=self.root)
        # スラッシュコマンドしか叩かないので、クライアントはダミーでよい。
        self.agent = Agent(
            client=SimpleNamespace(messages=None, beta=None),
            config=config,
            toolbox=Toolbox(Workspace(self.root)),
            session=Session(workspace=self.root),
            ui=self.ui,
        )
        self.repl = Repl(self.agent, self.ui)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    @property
    def output(self) -> str:
        return self.buffer.getvalue()

    def test_exitは終了を要求する(self) -> None:
        self.assertTrue(self.repl.handle_command("/exit"))

    def test_helpは一覧を出す(self) -> None:
        self.assertFalse(self.repl.handle_command("/help"))
        self.assertIn("/model", self.output)

    def test_modelを変更できる(self) -> None:
        self.repl.handle_command("/model sonnet")
        self.assertEqual(self.agent.config.model.id, "claude-sonnet-5")

    def test_model単体なら現在値を表示する(self) -> None:
        self.repl.handle_command("/model")
        self.assertIn("claude-opus-5", self.output)

    def test_effortを変更できる(self) -> None:
        self.repl.handle_command("/effort low")
        self.assertEqual(self.agent.config.effort, "low")

    def test_不正なeffortは変更しない(self) -> None:
        self.repl.handle_command("/effort ultra")
        self.assertEqual(self.agent.config.effort, "high")
        self.assertIn("✗", self.output)

    def test_modeを変更するとゲートにも反映される(self) -> None:
        self.repl.handle_command("/mode yolo")
        self.assertEqual(self.agent.config.permission_mode, "yolo")
        self.assertEqual(self.agent.gate.mode, "yolo")

    def test_max_tokensはモデル上限で頭打ち(self) -> None:
        self.repl.handle_command("/max-tokens 999999")
        self.assertEqual(self.agent.config.max_tokens, self.agent.config.model.max_output)

    def test_max_tokensに整数以外を渡すとエラー(self) -> None:
        before = self.agent.config.max_tokens
        self.repl.handle_command("/max-tokens たくさん")
        self.assertEqual(self.agent.config.max_tokens, before)

    def test_toolsは一覧を出す(self) -> None:
        self.repl.handle_command("/tools")
        for name in ("read", "write", "edit", "glob", "grep", "bash"):
            self.assertIn(name, self.output)

    def test_clearは履歴を消す(self) -> None:
        self.agent.session.append_user_text("何か")
        self.repl.handle_command("/clear")
        self.assertEqual(self.agent.session.messages, [])

    def test_saveはファイルを作る(self) -> None:
        self.repl.handle_command("/save")
        self.assertTrue(self.agent.session.path().exists())

    def test_costは利用量を出す(self) -> None:
        self.repl.handle_command("/cost")
        self.assertIn("リクエスト", self.output)

    def test_未知のコマンドはエラーを出すが終了しない(self) -> None:
        self.assertFalse(self.repl.handle_command("/なにこれ"))
        self.assertIn("未知のコマンド", self.output)


class MainTest(unittest.TestCase):
    def test_存在しない作業ディレクトリは終了コード2(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()) as captured:
            code = main(["-C", "/存在しないはずのディレクトリ/xyz", "--no-color", "やあ"])
        self.assertEqual(code, 2)
        self.assertIn("作業ディレクトリが存在しない", captured.getvalue())


if __name__ == "__main__":
    unittest.main()
