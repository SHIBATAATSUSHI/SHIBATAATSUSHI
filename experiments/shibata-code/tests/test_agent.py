"""エージェントループの検証。

API は叩かず、フェイクのバックエンドを差し込んで停止理由の分岐・
ツール往復・権限拒否・プロバイダ切り替えの挙動を確かめる。
"""

from __future__ import annotations

import dataclasses
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shibata_code.agent import Agent, AgentError  # noqa: E402
from shibata_code.backends.base import (  # noqa: E402
    STOP_END_TURN,
    STOP_MAX_TOKENS,
    STOP_PAUSE_TURN,
    STOP_REFUSAL,
    STOP_TOOL_USE,
    Backend,
    BackendError,
    TurnResult,
)
from shibata_code.config import build_config  # noqa: E402
from shibata_code.messages import ToolCall  # noqa: E402
from shibata_code.session import Session  # noqa: E402
from shibata_code.tools import Toolbox  # noqa: E402
from shibata_code.ui import UI  # noqa: E402
from shibata_code.workspace import Workspace  # noqa: E402


def text_turn(text: str, stop: str = STOP_END_TURN) -> TurnResult:
    return TurnResult(
        text=text,
        stop_reason=stop,
        usage={"input_tokens": 10, "output_tokens": 5},
    )


def tool_turn(call_id: str, name: str, arguments: dict) -> TurnResult:
    return TurnResult(
        tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)],
        stop_reason=STOP_TOOL_USE,
        usage={"input_tokens": 10, "output_tokens": 5},
    )


class FakeBackend(Backend):
    """用意した結果を順に返し、渡された引数を記録するバックエンド。"""

    name = "fake"

    def __init__(self, results: list[TurnResult]) -> None:
        self.results = list(results)
        self.calls: list[dict] = []

    def stream_turn(self, *, system, history, tools, sink) -> TurnResult:
        self.calls.append(
            {"system": system, "history": list(history), "tools": list(tools)}
        )
        if not self.results:
            raise AssertionError("想定より多く呼ばれた")
        result = self.results.pop(0)
        if result.text:
            sink.assistant_chunk(result.text)
        return result

    def check_credentials(self) -> str | None:
        return None


class ExplodingBackend(Backend):
    """必ず失敗するバックエンド。"""

    name = "exploding"

    def __init__(self, message: str = "接続できない") -> None:
        self.message = message

    def stream_turn(self, **kwargs) -> TurnResult:
        raise BackendError(self.message)

    def check_credentials(self) -> str | None:
        return self.message


class AgentTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.buffer = io.StringIO()
        self.ui = UI(color=False, stream=self.buffer)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def make_agent(
        self,
        results: list[TurnResult],
        *,
        model: str = "opus",
        mode: str = "yolo",
    ) -> Agent:
        config = build_config(model=model, workspace=self.root, permission_mode=mode)
        return Agent(
            config=config,
            toolbox=Toolbox(Workspace(self.root)),
            session=Session(workspace=self.root),
            ui=self.ui,
            backend=FakeBackend(results),
        )

    @property
    def output(self) -> str:
        return self.buffer.getvalue()


class TurnFlowTest(AgentTestBase):
    def test_end_turnで1往復して終わる(self) -> None:
        agent = self.make_agent([text_turn("完了した")])
        agent.run_turn("やあ")
        self.assertIn("完了した", self.output)
        self.assertEqual(len(agent.backend.calls), 1)
        self.assertEqual(len(agent.session.messages), 2)

    def test_tool_useならツールを実行して再度呼ぶ(self) -> None:
        (self.root / "target.txt").write_text("中身\n", encoding="utf-8")
        agent = self.make_agent(
            [tool_turn("t1", "read", {"path": "target.txt"}), text_turn("読んだ")]
        )
        agent.run_turn("target.txt を読んで")
        self.assertEqual(len(agent.backend.calls), 2)
        # user, assistant(tool_call), tool(result), assistant(text)
        self.assertEqual(len(agent.session.messages), 4)
        results = agent.session.messages[2].tool_results
        self.assertEqual(results[0].tool_call_id, "t1")
        self.assertIn("中身", results[0].content)

    def test_複数のツール結果は1エントリにまとまる(self) -> None:
        (self.root / "a.txt").write_text("A", encoding="utf-8")
        (self.root / "b.txt").write_text("B", encoding="utf-8")
        turn = TurnResult(
            tool_calls=[
                ToolCall(id="t1", name="read", arguments={"path": "a.txt"}),
                ToolCall(id="t2", name="read", arguments={"path": "b.txt"}),
            ],
            stop_reason=STOP_TOOL_USE,
        )
        agent = self.make_agent([turn, text_turn("両方読んだ")])
        agent.run_turn("2つ読んで")
        self.assertEqual(len(agent.session.messages[2].tool_results), 2)

    def test_ツール失敗はis_error付きで返して続行する(self) -> None:
        agent = self.make_agent(
            [tool_turn("t1", "read", {"path": "無い.txt"}), text_turn("見つからなかった")]
        )
        agent.run_turn("読んで")
        result = agent.session.messages[2].tool_results[0]
        self.assertTrue(result.is_error)
        self.assertEqual(len(agent.backend.calls), 2)

    def test_未知のツール名でも落ちない(self) -> None:
        agent = self.make_agent(
            [tool_turn("t1", "存在しない", {}), text_turn("すまない")]
        )
        agent.run_turn("何か")
        result = agent.session.messages[2].tool_results[0]
        self.assertTrue(result.is_error)

    def test_refusalは警告して止まる(self) -> None:
        agent = self.make_agent(
            [TurnResult(text="", stop_reason=STOP_REFUSAL, refusal_category="cyber")]
        )
        agent.run_turn("何か")
        self.assertIn("拒否", self.output)
        self.assertIn("cyber", self.output)

    def test_max_tokensは警告して止まる(self) -> None:
        agent = self.make_agent([text_turn("途中まで", STOP_MAX_TOKENS)])
        agent.run_turn("長い依頼")
        self.assertIn("出力上限", self.output)

    def test_pause_turnは同じターンで続きを要求する(self) -> None:
        agent = self.make_agent([text_turn("途中", STOP_PAUSE_TURN), text_turn("完了")])
        agent.run_turn("調べて")
        self.assertEqual(len(agent.backend.calls), 2)

    def test_往復上限で打ち切る(self) -> None:
        results = [tool_turn(f"t{i}", "glob", {"pattern": "*"}) for i in range(10)]
        agent = self.make_agent(results)
        agent.config = dataclasses.replace(agent.config, max_iterations=3)
        agent.set_backend(agent._backend)  # 設定変更に追随させる
        agent.run_turn("無限に探して")
        self.assertEqual(len(agent.backend.calls), 3)
        self.assertIn("打ち切った", self.output)

    def test_利用量が積み上がる(self) -> None:
        agent = self.make_agent(
            [tool_turn("t1", "glob", {"pattern": "*"}), text_turn("done")]
        )
        agent.run_turn("探して")
        self.assertEqual(agent.session.usage.requests, 2)
        self.assertEqual(agent.session.usage.input_tokens, 20)

    def test_空の応答は履歴に積まない(self) -> None:
        agent = self.make_agent([TurnResult(text="", stop_reason=STOP_END_TURN)])
        agent.run_turn("やあ")
        # user のみ
        self.assertEqual(len(agent.session.messages), 1)


class BackendSelectionTest(AgentTestBase):
    def test_モデルを変えるとバックエンドが作り直される(self) -> None:
        agent = self.make_agent([])
        first = agent.backend
        agent.config = agent.config.with_model("deepseek")
        second = agent.backend
        self.assertIsNot(first, second)
        self.assertEqual(second.name, "openai_compat")

    def test_anthropicモデルではanthropicバックエンドになる(self) -> None:
        agent = self.make_agent([])
        agent.set_backend(None)
        self.assertEqual(agent.backend.name, "anthropic")

    def test_openai互換モデルでは互換バックエンドになる(self) -> None:
        for name in ("gemini", "deepseek", "kimi", "qwen", "gpt"):
            with self.subTest(model=name):
                agent = self.make_agent([], model=name)
                agent.set_backend(None)
                self.assertEqual(agent.backend.name, "openai_compat")

    def test_差し込んだバックエンドは勝手に置き換わらない(self) -> None:
        agent = self.make_agent([text_turn("はい")])
        injected = agent.backend
        agent.run_turn("やあ")
        self.assertIs(agent.backend, injected)


class PermissionFlowTest(AgentTestBase):
    def test_拒否するとツールは実行されない(self) -> None:
        agent = self.make_agent(
            [
                tool_turn("t1", "write", {"path": "x.txt", "content": "内容"}),
                text_turn("分かった"),
            ],
            mode="ask",
        )
        agent.ui.confirm = lambda question, detail="": False  # type: ignore[method-assign]
        agent.run_turn("書いて")
        self.assertFalse((self.root / "x.txt").exists())
        result = agent.session.messages[2].tool_results[0]
        self.assertTrue(result.is_error)
        self.assertIn("拒否", result.content)

    def test_承認するとツールが実行される(self) -> None:
        agent = self.make_agent(
            [
                tool_turn("t1", "write", {"path": "x.txt", "content": "内容"}),
                text_turn("書いた"),
            ],
            mode="ask",
        )
        agent.ui.confirm = lambda question, detail="": True  # type: ignore[method-assign]
        agent.run_turn("書いて")
        self.assertEqual((self.root / "x.txt").read_text(encoding="utf-8"), "内容")

    def test_読み取りツールは確認されない(self) -> None:
        (self.root / "a.txt").write_text("A", encoding="utf-8")
        asked: list[str] = []
        agent = self.make_agent(
            [tool_turn("t1", "read", {"path": "a.txt"}), text_turn("読んだ")],
            mode="ask",
        )
        agent.ui.confirm = lambda question, detail="": asked.append(question) or True  # type: ignore[method-assign]
        agent.run_turn("読んで")
        self.assertEqual(asked, [])


class ErrorTest(AgentTestBase):
    def test_バックエンドの失敗はAgentErrorになる(self) -> None:
        agent = self.make_agent([])
        agent.set_backend(ExplodingBackend("API に接続できない"))
        with self.assertRaises(AgentError) as ctx:
            agent.run_turn("やあ")
        self.assertIn("接続できない", str(ctx.exception))

    def test_認証情報の案内を取り出せる(self) -> None:
        agent = self.make_agent([])
        agent.set_backend(ExplodingBackend("APIキーが無い"))
        self.assertIn("APIキー", agent.check_credentials())


if __name__ == "__main__":
    unittest.main()
