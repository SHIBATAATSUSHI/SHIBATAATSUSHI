"""エージェントループの検証。

API は叩かず、ストリーミングを模したフェイククライアントを差し込んで
停止理由の分岐・ツール往復・権限拒否の挙動を確かめる。
"""

from __future__ import annotations

import dataclasses
import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mytos.agent import Agent, AgentError  # noqa: E402
from mytos.config import build_config  # noqa: E402
from mytos.session import Session  # noqa: E402
from mytos.tools import Toolbox  # noqa: E402
from mytos.ui import UI  # noqa: E402
from mytos.workspace import Workspace  # noqa: E402


def text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def tool_block(tool_id: str, name: str, payload: dict) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=tool_id, name=name, input=payload)


def fake_message(content: list, stop_reason: str, **extra) -> SimpleNamespace:
    usage = SimpleNamespace(
        input_tokens=10,
        output_tokens=5,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
    )
    return SimpleNamespace(content=content, stop_reason=stop_reason, usage=usage, **extra)


class FakeStream:
    """`client.messages.stream(...)` が返すコンテキストマネージャの代役。"""

    def __init__(self, message: SimpleNamespace) -> None:
        self._message = message

    def __enter__(self) -> "FakeStream":
        return self

    def __exit__(self, *exc_info) -> bool:
        return False

    def __iter__(self):
        # 本文だけをテキストデルタとして流す。
        for block in self._message.content:
            if getattr(block, "type", "") == "text":
                yield SimpleNamespace(
                    type="content_block_start",
                    content_block=SimpleNamespace(type="text"),
                )
                yield SimpleNamespace(
                    type="content_block_delta",
                    delta=SimpleNamespace(type="text_delta", text=block.text),
                )

    def get_final_message(self) -> SimpleNamespace:
        return self._message


class FakeMessages:
    """呼ばれたパラメータを記録し、用意した応答を順に返す。"""

    def __init__(self, responses: list[SimpleNamespace]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def stream(self, **params) -> FakeStream:
        self.calls.append(params)
        if not self.responses:
            raise AssertionError("想定より多く API が呼ばれた")
        return FakeStream(self.responses.pop(0))


class FakeClient:
    def __init__(self, responses: list[SimpleNamespace]) -> None:
        self.messages = FakeMessages(responses)
        self.beta = SimpleNamespace(messages=self.messages)


class AgentTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.buffer = io.StringIO()
        self.ui = UI(color=False, stream=self.buffer)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def make_agent(self, responses: list[SimpleNamespace], *, model: str = "opus", mode: str = "yolo") -> Agent:
        config = build_config(model=model, workspace=self.root, permission_mode=mode)
        return Agent(
            client=FakeClient(responses),
            config=config,
            toolbox=Toolbox(Workspace(self.root)),
            session=Session(workspace=self.root),
            ui=self.ui,
        )

    @property
    def output(self) -> str:
        return self.buffer.getvalue()


class RequestShapeTest(AgentTestBase):
    def test_システムプロンプトにキャッシュ指示が付く(self) -> None:
        agent = self.make_agent([])
        params = agent._build_params()
        self.assertEqual(params["system"][0]["cache_control"], {"type": "ephemeral"})

    def test_opusにはadaptive_thinkingとeffortを送る(self) -> None:
        agent = self.make_agent([])
        params = agent._build_params()
        self.assertEqual(params["thinking"]["type"], "adaptive")
        self.assertEqual(params["output_config"]["effort"], "high")

    def test_haikuにはthinkingとeffortを送らない(self) -> None:
        agent = self.make_agent([], model="haiku")
        params = agent._build_params()
        self.assertNotIn("thinking", params)
        self.assertNotIn("output_config", params)

    def test_ツール定義が全部載る(self) -> None:
        agent = self.make_agent([])
        names = {tool["name"] for tool in agent._build_params()["tools"]}
        self.assertEqual(names, {"read", "write", "edit", "glob", "grep", "bash"})

    def test_fableではフォールバックを有効にする(self) -> None:
        agent = self.make_agent([fake_message([text_block("はい")], "end_turn")], model="fable")
        agent.run_turn("やあ")
        call = agent.client.messages.calls[0]
        self.assertEqual(call["fallbacks"], "default")
        self.assertIn("server-side-fallback-2026-07-01", call["betas"])

    def test_opusではフォールバックを送らない(self) -> None:
        agent = self.make_agent([fake_message([text_block("はい")], "end_turn")])
        agent.run_turn("やあ")
        self.assertNotIn("fallbacks", agent.client.messages.calls[0])


class TurnFlowTest(AgentTestBase):
    def test_end_turnで1往復して終わる(self) -> None:
        agent = self.make_agent([fake_message([text_block("完了した")], "end_turn")])
        agent.run_turn("やあ")
        self.assertIn("完了した", self.output)
        self.assertEqual(len(agent.client.messages.calls), 1)
        # user / assistant の2メッセージが残る
        self.assertEqual(len(agent.session.messages), 2)

    def test_tool_useならツールを実行して再度呼ぶ(self) -> None:
        (self.root / "target.txt").write_text("中身\n", encoding="utf-8")
        agent = self.make_agent(
            [
                fake_message(
                    [tool_block("t1", "read", {"path": "target.txt"})], "tool_use"
                ),
                fake_message([text_block("読んだ")], "end_turn"),
            ]
        )
        agent.run_turn("target.txt を読んで")
        self.assertEqual(len(agent.client.messages.calls), 2)
        # user, assistant(tool_use), user(tool_result), assistant(text)
        self.assertEqual(len(agent.session.messages), 4)
        results = agent.session.messages[2]["content"]
        self.assertEqual(results[0]["tool_use_id"], "t1")
        self.assertIn("中身", results[0]["content"])

    def test_複数のツール結果は1通にまとまる(self) -> None:
        (self.root / "a.txt").write_text("A", encoding="utf-8")
        (self.root / "b.txt").write_text("B", encoding="utf-8")
        agent = self.make_agent(
            [
                fake_message(
                    [
                        tool_block("t1", "read", {"path": "a.txt"}),
                        tool_block("t2", "read", {"path": "b.txt"}),
                    ],
                    "tool_use",
                ),
                fake_message([text_block("両方読んだ")], "end_turn"),
            ]
        )
        agent.run_turn("2つ読んで")
        tool_message = agent.session.messages[2]
        self.assertEqual(len(tool_message["content"]), 2)

    def test_ツール失敗はis_error付きで返して続行する(self) -> None:
        agent = self.make_agent(
            [
                fake_message(
                    [tool_block("t1", "read", {"path": "無い.txt"})], "tool_use"
                ),
                fake_message([text_block("見つからなかった")], "end_turn"),
            ]
        )
        agent.run_turn("読んで")
        result = agent.session.messages[2]["content"][0]
        self.assertTrue(result["is_error"])
        self.assertEqual(len(agent.client.messages.calls), 2)

    def test_refusalは警告して止まる(self) -> None:
        agent = self.make_agent(
            [
                fake_message(
                    [], "refusal", stop_details=SimpleNamespace(category="cyber")
                )
            ]
        )
        agent.run_turn("何か")
        self.assertIn("拒否", self.output)
        self.assertIn("cyber", self.output)

    def test_max_tokensは警告して止まる(self) -> None:
        agent = self.make_agent([fake_message([text_block("途中まで")], "max_tokens")])
        agent.run_turn("長い依頼")
        self.assertIn("出力上限", self.output)

    def test_pause_turnは同じターンで続きを要求する(self) -> None:
        agent = self.make_agent(
            [
                fake_message([text_block("途中")], "pause_turn"),
                fake_message([text_block("完了")], "end_turn"),
            ]
        )
        agent.run_turn("調べて")
        self.assertEqual(len(agent.client.messages.calls), 2)

    def test_往復上限で打ち切る(self) -> None:
        # 常に tool_use を返し続けるモデルを模す
        responses = [
            fake_message([tool_block(f"t{i}", "glob", {"pattern": "*"})], "tool_use")
            for i in range(10)
        ]
        agent = self.make_agent(responses)
        agent.config = dataclasses.replace(agent.config, max_iterations=3)
        agent.run_turn("無限に探して")
        self.assertEqual(len(agent.client.messages.calls), 3)
        self.assertIn("打ち切った", self.output)

    def test_利用量が積み上がる(self) -> None:
        agent = self.make_agent(
            [
                fake_message([tool_block("t1", "glob", {"pattern": "*"})], "tool_use"),
                fake_message([text_block("done")], "end_turn"),
            ]
        )
        agent.run_turn("探して")
        self.assertEqual(agent.session.usage.requests, 2)
        self.assertEqual(agent.session.usage.input_tokens, 20)


class PermissionFlowTest(AgentTestBase):
    def test_拒否するとツールは実行されない(self) -> None:
        agent = self.make_agent(
            [
                fake_message(
                    [tool_block("t1", "write", {"path": "x.txt", "content": "内容"})],
                    "tool_use",
                ),
                fake_message([text_block("分かった")], "end_turn"),
            ],
            mode="ask",
        )
        # 確認プロンプトで必ず「いいえ」と答える
        agent.ui.confirm = lambda question, detail="": False  # type: ignore[method-assign]
        agent.run_turn("書いて")
        self.assertFalse((self.root / "x.txt").exists())
        result = agent.session.messages[2]["content"][0]
        self.assertTrue(result["is_error"])
        self.assertIn("拒否", result["content"])

    def test_承認するとツールが実行される(self) -> None:
        agent = self.make_agent(
            [
                fake_message(
                    [tool_block("t1", "write", {"path": "x.txt", "content": "内容"})],
                    "tool_use",
                ),
                fake_message([text_block("書いた")], "end_turn"),
            ],
            mode="ask",
        )
        agent.ui.confirm = lambda question, detail="": True  # type: ignore[method-assign]
        agent.run_turn("書いて")
        self.assertEqual((self.root / "x.txt").read_text(encoding="utf-8"), "内容")

    def test_読み取りツールは確認されない(self) -> None:
        (self.root / "a.txt").write_text("A", encoding="utf-8")
        asked = []
        agent = self.make_agent(
            [
                fake_message([tool_block("t1", "read", {"path": "a.txt"})], "tool_use"),
                fake_message([text_block("読んだ")], "end_turn"),
            ],
            mode="ask",
        )
        agent.ui.confirm = lambda question, detail="": asked.append(question) or True  # type: ignore[method-assign]
        agent.run_turn("読んで")
        self.assertEqual(asked, [])


class ErrorTest(AgentTestBase):
    def test_APIエラーはAgentErrorになる(self) -> None:
        import anthropic

        class ExplodingMessages(FakeMessages):
            def stream(self, **params):
                raise anthropic.APIConnectionError(request=None)  # type: ignore[arg-type]

        agent = self.make_agent([])
        agent.client.messages = ExplodingMessages([])
        with self.assertRaises(AgentError):
            agent.run_turn("やあ")

    def test_認証情報が無いときは分かる文言で落ちる(self) -> None:
        class UnauthenticatedMessages(FakeMessages):
            def stream(self, **params):
                raise TypeError("Could not resolve authentication method.")

        agent = self.make_agent([])
        agent.client.messages = UnauthenticatedMessages([])
        with self.assertRaises(AgentError) as ctx:
            agent.run_turn("やあ")
        self.assertIn("ANTHROPIC_API_KEY", str(ctx.exception))

    def test_無関係なTypeErrorは握りつぶさない(self) -> None:
        class BuggyMessages(FakeMessages):
            def stream(self, **params):
                raise TypeError("バグ由来の型エラー")

        agent = self.make_agent([])
        agent.client.messages = BuggyMessages([])
        with self.assertRaises(TypeError):
            agent.run_turn("やあ")


if __name__ == "__main__":
    unittest.main()
