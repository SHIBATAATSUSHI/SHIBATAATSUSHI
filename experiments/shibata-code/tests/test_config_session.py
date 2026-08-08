"""設定・プロバイダ・権限判定・セッション保存の検証。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shibata_code.backends.base import TurnResult  # noqa: E402
from shibata_code.config import MODEL_PRESETS, build_config, resolve_model  # noqa: E402
from shibata_code.messages import Message, ToolCall, ToolResult, parse_arguments  # noqa: E402
from shibata_code.permissions import PermissionGate, classify_command  # noqa: E402
from shibata_code.prompts import build_system_prompt, load_project_context  # noqa: E402
from shibata_code.providers import PROVIDERS, resolve_provider  # noqa: E402
from shibata_code.session import Session, Usage  # noqa: E402


class ModelResolutionTest(unittest.TestCase):
    def test_短縮名からモデルを解決する(self) -> None:
        self.assertEqual(resolve_model("opus").id, "claude-opus-5")
        self.assertEqual(resolve_model("fable").id, "claude-fable-5")
        self.assertEqual(resolve_model("mythos").id, "claude-mythos-5")

    def test_他社モデルの短縮名も解決する(self) -> None:
        cases = {
            "gpt": "openai",
            "gemini": "google",
            "deepseek": "deepseek",
            "kimi": "moonshot",
            "qwen": "qwen",
        }
        for name, provider in cases.items():
            with self.subTest(model=name):
                self.assertEqual(resolve_model(name).provider, provider)

    def test_未指定なら既定モデル(self) -> None:
        self.assertEqual(resolve_model(None).id, MODEL_PRESETS["opus"].id)

    def test_プロバイダ指定の書き方(self) -> None:
        spec = resolve_model("openrouter:z-ai/glm-5.2")
        self.assertEqual(spec.provider, "openrouter")
        self.assertEqual(spec.id, "z-ai/glm-5.2")

    def test_プロバイダ指定はコロンを含むIDも扱える(self) -> None:
        spec = resolve_model("openrouter:meta-llama/llama-4:free")
        self.assertEqual(spec.id, "meta-llama/llama-4:free")

    def test_未知のプロバイダは弾く(self) -> None:
        with self.assertRaises(ValueError):
            resolve_model("そんなの:model")

    def test_モデルIDが空なら弾く(self) -> None:
        with self.assertRaises(ValueError):
            resolve_model("deepseek:")

    def test_素のIDはAnthropic扱い(self) -> None:
        spec = resolve_model("claude-未来-9")
        self.assertEqual(spec.provider, "anthropic")
        self.assertTrue(spec.supports_adaptive_thinking)

    def test_fableはフォールバックに対応する(self) -> None:
        self.assertTrue(resolve_model("fable").supports_fallbacks)
        self.assertFalse(resolve_model("opus").supports_fallbacks)

    def test_haikuはadaptive_thinking非対応(self) -> None:
        spec = resolve_model("haiku")
        self.assertFalse(spec.supports_adaptive_thinking)
        self.assertFalse(spec.supports_effort)

    def test_openai系はmax_completion_tokensを使う(self) -> None:
        self.assertEqual(resolve_model("gpt").max_tokens_param, "max_completion_tokens")
        self.assertEqual(resolve_model("deepseek").max_tokens_param, "max_tokens")

    def test_価格未設定のモデルはhas_pricingが偽(self) -> None:
        self.assertTrue(resolve_model("opus").has_pricing)
        self.assertFalse(resolve_model("kimi").has_pricing)


class ProviderTest(unittest.TestCase):
    def test_全プロバイダに接続情報がある(self) -> None:
        for spec in PROVIDERS.values():
            with self.subTest(provider=spec.key):
                self.assertTrue(spec.api_key_envs)
                if spec.key != "anthropic":
                    self.assertTrue(spec.base_url)

    def test_未知のプロバイダ名は弾く(self) -> None:
        with self.assertRaises(ValueError):
            resolve_provider("nowhere")

    def test_環境変数からキーを探す(self) -> None:
        import os

        spec = PROVIDERS["deepseek"]
        os.environ["DEEPSEEK_API_KEY"] = "test-key"
        try:
            self.assertEqual(spec.find_api_key(), "test-key")
        finally:
            del os.environ["DEEPSEEK_API_KEY"]

    def test_キーが無ければNone(self) -> None:
        self.assertIsNone(PROVIDERS["moonshot"].find_api_key())


class ConfigTest(unittest.TestCase):
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

    def test_base_urlはプロバイダ既定を使う(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = build_config(model="deepseek", workspace=tmp)
            self.assertEqual(cfg.resolved_base_url(), "https://api.deepseek.com/v1")

    def test_base_urlは明示指定で上書きできる(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = build_config(
                model="local:llama-4", workspace=tmp, base_url="http://127.0.0.1:1234/v1"
            )
            self.assertEqual(cfg.resolved_base_url(), "http://127.0.0.1:1234/v1")


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


class MessageTest(unittest.TestCase):
    def test_往復して同じ内容になる(self) -> None:
        original = Message.assistant(
            text="やあ",
            tool_calls=[ToolCall(id="t1", name="read", arguments={"path": "a.txt"})],
            provider="anthropic",
            model="claude-opus-5",
            native=[{"type": "text", "text": "やあ"}],
        )
        restored = Message.from_dict(original.to_dict())
        self.assertEqual(restored.text, "やあ")
        self.assertEqual(restored.tool_calls[0].name, "read")
        self.assertEqual(restored.model, "claude-opus-5")

    def test_同一モデルなら生データを再利用してよい(self) -> None:
        message = Message.assistant(
            text="x", provider="anthropic", model="claude-opus-5", native=["x"]
        )
        self.assertTrue(message.native_matches("anthropic", "claude-opus-5"))
        self.assertFalse(message.native_matches("anthropic", "claude-sonnet-5"))
        self.assertFalse(message.native_matches("openai", "claude-opus-5"))

    def test_生データが無ければ再利用しない(self) -> None:
        message = Message.assistant(text="x", provider="anthropic", model="claude-opus-5")
        self.assertFalse(message.native_matches("anthropic", "claude-opus-5"))

    def test_中身が無いメッセージを判別する(self) -> None:
        self.assertTrue(Message.assistant(text="  ").is_empty())
        self.assertFalse(Message.user("やあ").is_empty())

    def test_壊れたJSON引数は空辞書になる(self) -> None:
        self.assertEqual(parse_arguments('{"a": 1}'), {"a": 1})
        self.assertEqual(parse_arguments("{壊れて"), {})
        self.assertEqual(parse_arguments(""), {})
        self.assertEqual(parse_arguments(None), {})
        self.assertEqual(parse_arguments("[1,2]"), {})


class SessionTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_保存して読み直せる(self) -> None:
        session = Session(workspace=self.root)
        session.append_user_text("こんにちは")
        session.append_turn(
            TurnResult(text="やあ"), provider="anthropic", model="claude-opus-5"
        )
        path = session.save()
        restored = Session.load(path)
        self.assertEqual(restored.session_id, session.session_id)
        self.assertEqual(len(restored.messages), 2)
        self.assertEqual(restored.messages[0].text, "こんにちは")
        self.assertEqual(restored.messages[1].model, "claude-opus-5")

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

    def test_空の応答は積まない(self) -> None:
        session = Session(workspace=self.root)
        session.append_turn(TurnResult(), provider="anthropic", model="claude-opus-5")
        self.assertEqual(session.messages, [])

    def test_ツール結果は1エントリにまとめる(self) -> None:
        session = Session(workspace=self.root)
        session.append_tool_results(
            [
                ToolResult(tool_call_id="a", content="1"),
                ToolResult(tool_call_id="b", content="2"),
            ]
        )
        self.assertEqual(len(session.messages), 1)
        self.assertEqual(len(session.messages[0].tool_results), 2)

    def test_clearは履歴だけ消す(self) -> None:
        session = Session(workspace=self.root)
        session.append_user_text("x")
        session.usage.input_tokens = 100
        session.clear()
        self.assertEqual(session.messages, [])
        self.assertEqual(session.usage.input_tokens, 100)

    def test_コストを概算できる(self) -> None:
        usage = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
        self.assertAlmostEqual(usage.cost(MODEL_PRESETS["opus"]), 30.0, places=4)

    def test_価格未設定ならサマリに価格未設定と出る(self) -> None:
        usage = Usage(input_tokens=100, output_tokens=50, requests=1)
        self.assertIn("価格未設定", usage.summary(MODEL_PRESETS["kimi"]))

    def test_利用量は辞書から加算する(self) -> None:
        usage = Usage()
        usage.add({"input_tokens": 10, "output_tokens": 3, "cache_read_tokens": 7})
        usage.add(None)
        self.assertEqual(usage.requests, 2)
        self.assertEqual(usage.input_tokens, 10)
        self.assertEqual(usage.cache_read_tokens, 7)


class PromptTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_システムプロンプトに実行環境が入る(self) -> None:
        prompt = build_system_prompt(self.root)
        self.assertIn(str(self.root), prompt)
        self.assertIn("Shibata Code", prompt)

    def test_プロジェクト指示を読み込む(self) -> None:
        (self.root / "CLAUDE.md").write_text("# 規約\n日本語で書く", encoding="utf-8")
        context = load_project_context(self.root)
        self.assertIn("日本語で書く", context)
        prompt = build_system_prompt(self.root, extra_context=context)
        self.assertIn("このプロジェクトの指示", prompt)

    def test_指示ファイルが無ければ空(self) -> None:
        self.assertEqual(load_project_context(self.root), "")

    def test_同じ入力なら同じプロンプト(self) -> None:
        import datetime

        today = datetime.date(2026, 1, 1)
        a = build_system_prompt(self.root, today=today)
        b = build_system_prompt(self.root, today=today)
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
