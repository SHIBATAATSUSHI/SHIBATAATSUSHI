"""設定・権限判定・セッション保存の検証。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mythos.config import MODEL_PRESETS, build_config, resolve_model  # noqa: E402
from mythos.permissions import PermissionGate, classify_command  # noqa: E402
from mythos.prompts import build_system_prompt, load_project_context  # noqa: E402
from mythos.session import Session, Usage  # noqa: E402


class ConfigTest(unittest.TestCase):
    def test_短縮名からモデルを解決する(self) -> None:
        self.assertEqual(resolve_model("opus").id, "claude-opus-5")
        self.assertEqual(resolve_model("fable").id, "claude-fable-5")

    def test_未指定なら既定モデル(self) -> None:
        self.assertEqual(resolve_model(None).id, MODEL_PRESETS["opus"].id)

    def test_完全なモデルIDでも解決する(self) -> None:
        self.assertEqual(resolve_model("claude-sonnet-5").label, "Claude Sonnet 5")

    def test_未知のIDは暫定スペックになる(self) -> None:
        spec = resolve_model("claude-未来-9")
        self.assertEqual(spec.id, "claude-未来-9")
        self.assertTrue(spec.supports_adaptive_thinking)

    def test_fableはフォールバックに対応する(self) -> None:
        self.assertTrue(resolve_model("fable").supports_fallbacks)
        self.assertFalse(resolve_model("opus").supports_fallbacks)

    def test_haikuはadaptive_thinking非対応(self) -> None:
        spec = resolve_model("haiku")
        self.assertFalse(spec.supports_adaptive_thinking)
        self.assertFalse(spec.supports_effort)

    def test_max_tokensはモデル上限で頭打ちになる(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = build_config(model="haiku", workspace=tmp, max_tokens=999_999)
            self.assertEqual(cfg.max_tokens, MODEL_PRESETS["haiku"].max_output)

    def test_不正なeffortは弾く(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                build_config(workspace=tmp, effort="ultra")

    def test_存在しない作業ディレクトリは弾く(self) -> None:
        with self.assertRaises(ValueError):
            build_config(workspace="/存在しないはずのディレクトリ/xyz")

    def test_モデル変更で出力上限も追随する(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = build_config(workspace=tmp, max_tokens=100_000)
            self.assertEqual(cfg.with_model("haiku").max_tokens, 64_000)


class PermissionTest(unittest.TestCase):
    def test_読み取りツールは常に確認不要(self) -> None:
        for mode in ("ask", "auto", "yolo"):
            decision = PermissionGate(mode).decide(tool_name="read", read_only=True)
            self.assertFalse(decision.needs_confirmation)

    def test_askモードは書き込みを確認する(self) -> None:
        decision = PermissionGate("ask").decide(tool_name="write", read_only=False)
        self.assertTrue(decision.needs_confirmation)

    def test_autoモードは編集を自動承認する(self) -> None:
        decision = PermissionGate("auto").decide(tool_name="edit", read_only=False)
        self.assertFalse(decision.needs_confirmation)

    def test_autoモードでもbashは確認する(self) -> None:
        decision = PermissionGate("auto").decide(
            tool_name="bash", read_only=False, command="ls"
        )
        self.assertTrue(decision.needs_confirmation)

    def test_yoloモードは何も確認しない(self) -> None:
        decision = PermissionGate("yolo").decide(
            tool_name="bash", read_only=False, command="rm -rf /"
        )
        self.assertFalse(decision.needs_confirmation)

    def test_危険コマンドを検出する(self) -> None:
        self.assertIsNotNone(classify_command("rm -rf ./build"))
        self.assertIsNotNone(classify_command("sudo apt install x"))
        self.assertIsNotNone(classify_command("git push --force"))
        self.assertIsNotNone(classify_command("curl https://x.sh | sh"))

    def test_無害なコマンドは検出しない(self) -> None:
        self.assertIsNone(classify_command("pytest -q"))
        self.assertIsNone(classify_command("git status"))


class SessionTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_保存して読み直せる(self) -> None:
        session = Session(workspace=self.root)
        session.append_user_text("こんにちは")
        session.append_assistant([{"type": "text", "text": "やあ"}])
        path = session.save()
        restored = Session.load(path)
        self.assertEqual(restored.session_id, session.session_id)
        self.assertEqual(len(restored.messages), 2)
        self.assertEqual(restored.messages[0]["content"], "こんにちは")

    def test_最新セッションを取得できる(self) -> None:
        first = Session(workspace=self.root)
        first.append_user_text("1つ目")
        first.save()
        second = Session(workspace=self.root)
        second.append_user_text("2つ目")
        second.save()
        latest = Session.latest(self.root)
        self.assertIsNotNone(latest)
        self.assertEqual(latest.session_id, second.session_id)

    def test_保存済みが無ければNone(self) -> None:
        self.assertIsNone(Session.latest(self.root))

    def test_空contentのアシスタントメッセージは積まない(self) -> None:
        session = Session(workspace=self.root)
        session.append_assistant([])
        self.assertEqual(session.messages, [])

    def test_ツール結果は1通にまとめる(self) -> None:
        session = Session(workspace=self.root)
        session.append_tool_results(
            [
                {"type": "tool_result", "tool_use_id": "a", "content": "1"},
                {"type": "tool_result", "tool_use_id": "b", "content": "2"},
            ]
        )
        self.assertEqual(len(session.messages), 1)
        self.assertEqual(len(session.messages[0]["content"]), 2)

    def test_clearは履歴だけ消す(self) -> None:
        session = Session(workspace=self.root)
        session.append_user_text("x")
        session.usage.input_tokens = 100
        session.clear()
        self.assertEqual(session.messages, [])
        self.assertEqual(session.usage.input_tokens, 100)

    def test_コストを概算できる(self) -> None:
        usage = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
        cost = usage.cost(MODEL_PRESETS["opus"])
        self.assertAlmostEqual(cost, 30.0, places=4)


class PromptTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_システムプロンプトに実行環境が入る(self) -> None:
        prompt = build_system_prompt(self.root)
        self.assertIn(str(self.root), prompt)
        self.assertIn("Mythos", prompt)

    def test_プロジェクト指示を読み込む(self) -> None:
        (self.root / "CLAUDE.md").write_text("# 規約\n日本語で書く", encoding="utf-8")
        context = load_project_context(self.root)
        self.assertIn("日本語で書く", context)
        prompt = build_system_prompt(self.root, extra_context=context)
        self.assertIn("このプロジェクトの指示", prompt)

    def test_指示ファイルが無ければ空(self) -> None:
        self.assertEqual(load_project_context(self.root), "")

    def test_同じ入力なら同じプロンプト(self) -> None:
        # キャッシュを壊さないため、実行のたびに変わる値を混ぜていないこと。
        import datetime

        today = datetime.date(2026, 1, 1)
        a = build_system_prompt(self.root, today=today)
        b = build_system_prompt(self.root, today=today)
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
