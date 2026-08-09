"""履歴の圧縮(コンパクション)の検証。

一番の関心事は「畳んだ後の履歴が API に受け入れられる形かどうか」。
ツール呼び出しとその結果を分断すると、次のリクエストごと拒否される。
"""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shibata_code.agent import Agent  # noqa: E402
from shibata_code.backends.base import STOP_END_TURN, STOP_TOOL_USE, Backend, TurnResult  # noqa: E402
from shibata_code.compaction import (  # noqa: E402
    Compactor,
    find_split_point,
    prompt_tokens,
    render_transcript,
)
from shibata_code.config import build_config  # noqa: E402
from shibata_code.messages import ROLE_TOOL, ROLE_USER, Message, ToolCall, ToolResult  # noqa: E402
from shibata_code.session import Session  # noqa: E402
from shibata_code.tools import Toolbox  # noqa: E402
from shibata_code.ui import UI  # noqa: E402
from shibata_code.workspace import Workspace  # noqa: E402


def conversation(rounds: int) -> list[Message]:
    """ユーザー発言 → ツール呼び出し → 結果 → 返答、を繰り返した履歴。"""
    messages: list[Message] = []
    for index in range(rounds):
        messages.append(Message.user(f"依頼{index}"))
        messages.append(
            Message.assistant(
                text=f"やります{index}",
                tool_calls=[ToolCall(id=f"t{index}", name="read", arguments={"path": f"f{index}.py"})],
                provider="anthropic",
                model="claude-opus-5",
            )
        )
        messages.append(
            Message.tool([ToolResult(tool_call_id=f"t{index}", content=f"中身{index}")])
        )
        messages.append(
            Message.assistant(text=f"できました{index}", provider="anthropic", model="claude-opus-5")
        )
    return messages


class ScriptedBackend(Backend):
    """通常の応答と、要約の応答を出し分けるフェイク。"""

    name = "scripted"

    def __init__(self, results: list[TurnResult], summary: str = "これまでの要約") -> None:
        self.results = list(results)
        self.summary = summary
        self.calls: list[dict] = []
        self.summary_calls = 0

    def stream_turn(self, *, system, history, tools, sink) -> TurnResult:
        self.calls.append({"system": system, "history": list(history), "tools": list(tools)})
        if not tools:
            # ツールを渡さない呼び出し = 要約の依頼
            self.summary_calls += 1
            return TurnResult(text=self.summary, stop_reason=STOP_END_TURN, usage={})
        if not self.results:
            raise AssertionError("想定より多く呼ばれた")
        return self.results.pop(0)

    def check_credentials(self) -> str | None:
        return None


class FailingSummaryBackend(ScriptedBackend):
    """要約だけ失敗するフェイク。"""

    def stream_turn(self, *, system, history, tools, sink) -> TurnResult:
        if not tools:
            raise RuntimeError("要約に失敗")
        return super().stream_turn(system=system, history=history, tools=tools, sink=sink)


class PromptTokensTest(unittest.TestCase):
    def test_キャッシュ分も足して数える(self) -> None:
        # input_tokens はキャッシュに載らなかった分しか含まない
        usage = {
            "input_tokens": 1_000,
            "cache_read_tokens": 50_000,
            "cache_creation_tokens": 2_000,
            "output_tokens": 900,
        }
        self.assertEqual(prompt_tokens(usage), 53_000)

    def test_空なら0(self) -> None:
        self.assertEqual(prompt_tokens(None), 0)
        self.assertEqual(prompt_tokens({}), 0)


class SplitPointTest(unittest.TestCase):
    def test_ユーザー発言で切る(self) -> None:
        messages = conversation(4)
        split = find_split_point(messages, keep_recent=6)
        self.assertGreater(split, 0)
        self.assertEqual(messages[split].role, ROLE_USER)

    def test_ツール結果の直前では切らない(self) -> None:
        # どんな keep_recent でも、切った先頭がツール結果になってはいけない
        messages = conversation(5)
        for keep in range(1, len(messages) + 2):
            with self.subTest(keep=keep):
                split = find_split_point(messages, keep_recent=keep)
                if split > 0:
                    self.assertNotEqual(messages[split].role, ROLE_TOOL)

    def test_切った後の履歴にツール結果の孤児が無い(self) -> None:
        messages = conversation(5)
        for keep in range(1, len(messages) + 2):
            with self.subTest(keep=keep):
                split = find_split_point(messages, keep_recent=keep)
                if split <= 0:
                    continue
                tail = messages[split:]
                # 残る側にあるツール結果は、必ず残る側の tool_call に対応している
                available = {
                    call.id for m in tail for call in m.tool_calls
                }
                for message in tail:
                    for result in message.tool_results:
                        self.assertIn(result.tool_call_id, available)

    def test_先頭しか候補が無ければ畳まない(self) -> None:
        messages = [Message.user("最初の依頼")]
        self.assertEqual(find_split_point(messages, keep_recent=6), -1)

    def test_ユーザー発言が無ければ畳まない(self) -> None:
        messages = [Message.assistant(text="ひとりごと")]
        self.assertEqual(find_split_point(messages, keep_recent=1), -1)

    def test_直近に区切りが無ければ遡って探す(self) -> None:
        messages = [
            Message.user("依頼"),
            Message.assistant(text="1"),
            Message.assistant(text="2"),
            Message.assistant(text="3"),
        ]
        # keep_recent=1 だと末尾は assistant なので、遡って index 0 を見つける
        # (index 0 は畳む意味が無いので -1 になる)
        self.assertEqual(find_split_point(messages, keep_recent=1), -1)


class TranscriptTest(unittest.TestCase):
    def test_役割ごとに整形する(self) -> None:
        text = render_transcript(conversation(1))
        self.assertIn("## ユーザー", text)
        self.assertIn("## アシスタント", text)
        self.assertIn("## ツール呼び出し: read", text)
        self.assertIn("## ツール結果", text)

    def test_長いツール出力は切り詰める(self) -> None:
        messages = [Message.tool([ToolResult(tool_call_id="t", content="x" * 5000)])]
        text = render_transcript(messages, tool_limit=100)
        self.assertLess(len(text), 400)
        self.assertIn("残り", text)

    def test_失敗した結果は失敗と分かる(self) -> None:
        messages = [
            Message.tool([ToolResult(tool_call_id="t", content="無い", is_error=True)])
        ]
        self.assertIn("## ツール失敗", render_transcript(messages))

    def test_空の発言は出さない(self) -> None:
        self.assertEqual(render_transcript([Message.user("   ")]), "")


class CompactorTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.ui = UI(color=False, stream=io.StringIO())

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def config(self, **kwargs):
        return build_config(workspace=self.root, **kwargs)

    def test_閾値未満なら畳まない(self) -> None:
        compactor = Compactor(self.config(context_window=100_000), self.ui)
        self.assertFalse(
            compactor.should_compact(
                last_usage={"input_tokens": 1_000}, messages=conversation(4)
            )
        )

    def test_閾値を超えたら畳む(self) -> None:
        compactor = Compactor(self.config(context_window=10_000), self.ui)
        self.assertTrue(
            compactor.should_compact(
                last_usage={"input_tokens": 9_000}, messages=conversation(4)
            )
        )

    def test_無効化していれば畳まない(self) -> None:
        compactor = Compactor(self.config(context_window=10_000, compaction=False), self.ui)
        self.assertFalse(
            compactor.should_compact(
                last_usage={"input_tokens": 9_000}, messages=conversation(4)
            )
        )

    def test_畳めない履歴なら発動しない(self) -> None:
        compactor = Compactor(self.config(context_window=10_000), self.ui)
        self.assertFalse(
            compactor.should_compact(
                last_usage={"input_tokens": 9_000}, messages=[Message.user("最初")]
            )
        )

    def test_畳むと要約1件と直近だけが残る(self) -> None:
        compactor = Compactor(self.config(), self.ui)
        messages = conversation(5)
        before = len(messages)
        outcome = compactor.compact(messages, ScriptedBackend([]))

        self.assertTrue(outcome.performed)
        self.assertEqual(outcome.before, before)
        self.assertLess(len(messages), before)
        self.assertEqual(messages[0].role, ROLE_USER)
        self.assertIn("これまでの要約", messages[0].text)
        self.assertIn("要約に置き換えられている", messages[0].text)

    def test_畳んだ後もツール結果の孤児が無い(self) -> None:
        compactor = Compactor(self.config(), self.ui)
        messages = conversation(6)
        compactor.compact(messages, ScriptedBackend([]))

        available = {call.id for m in messages for call in m.tool_calls}
        for message in messages:
            for result in message.tool_results:
                self.assertIn(result.tool_call_id, available)

    def test_要約にはツールを渡さない(self) -> None:
        backend = ScriptedBackend([])
        Compactor(self.config(), self.ui).compact(conversation(4), backend)
        self.assertEqual(backend.summary_calls, 1)
        self.assertEqual(backend.calls[0]["tools"], [])

    def test_要約が失敗しても履歴を壊さない(self) -> None:
        compactor = Compactor(self.config(), self.ui)
        messages = conversation(4)
        snapshot = list(messages)
        outcome = compactor.compact(messages, FailingSummaryBackend([]))

        self.assertFalse(outcome.performed)
        self.assertIn("要約に失敗", outcome.reason)
        self.assertEqual(messages, snapshot)

    def test_要約が空なら畳まない(self) -> None:
        compactor = Compactor(self.config(), self.ui)
        messages = conversation(4)
        outcome = compactor.compact(messages, ScriptedBackend([], summary="   "))
        self.assertFalse(outcome.performed)
        self.assertEqual(len(messages), 16)


class AgentCompactionTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.buffer = io.StringIO()
        self.ui = UI(color=False, stream=self.buffer)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def make_agent(self, backend, **kwargs) -> Agent:
        config = build_config(workspace=self.root, permission_mode="yolo", **kwargs)
        session = Session(workspace=self.root)
        return Agent(
            config=config,
            toolbox=Toolbox(Workspace(self.root)),
            session=session,
            ui=self.ui,
            backend=backend,
        )

    def test_大きな応答のあと次のターンで畳む(self) -> None:
        backend = ScriptedBackend(
            [
                TurnResult(text="1回目", stop_reason=STOP_END_TURN, usage={"input_tokens": 9_500}),
                TurnResult(text="2回目", stop_reason=STOP_END_TURN, usage={"input_tokens": 100}),
            ]
        )
        agent = self.make_agent(backend, context_window=10_000)
        agent.session.messages.extend(conversation(4))

        agent.run_turn("最初")
        self.assertEqual(backend.summary_calls, 0)

        agent.run_turn("次")
        self.assertEqual(backend.summary_calls, 1)
        self.assertIn("畳んだ", self.buffer.getvalue())

    def test_閾値未満なら畳まない(self) -> None:
        backend = ScriptedBackend(
            [
                TurnResult(text="1", stop_reason=STOP_END_TURN, usage={"input_tokens": 10}),
                TurnResult(text="2", stop_reason=STOP_END_TURN, usage={"input_tokens": 10}),
            ]
        )
        agent = self.make_agent(backend, context_window=1_000_000)
        agent.session.messages.extend(conversation(4))
        agent.run_turn("最初")
        agent.run_turn("次")
        self.assertEqual(backend.summary_calls, 0)

    def test_手動で畳める(self) -> None:
        backend = ScriptedBackend([])
        agent = self.make_agent(backend)
        agent.session.messages.extend(conversation(4))
        before = len(agent.session.messages)

        self.assertTrue(agent.maybe_compact(force=True))
        self.assertLess(len(agent.session.messages), before)

    def test_畳めないときは知らせて続行する(self) -> None:
        backend = ScriptedBackend([])
        agent = self.make_agent(backend)
        agent.session.messages.append(Message.user("最初だけ"))

        self.assertFalse(agent.maybe_compact(force=True))
        self.assertIn("畳めなかった", self.buffer.getvalue())


class InterruptedToolTest(unittest.TestCase):
    """中断してもツール呼び出しに結果が残ることの検証。

    結果の欠けた tool_call が履歴に残ると、次のリクエストが拒否される。
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.ui = UI(color=False, stream=io.StringIO())

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_中断しても全ての呼び出しに結果が付く(self) -> None:
        calls = [
            ToolCall(id="t1", name="read", arguments={"path": "a.txt"}),
            ToolCall(id="t2", name="read", arguments={"path": "b.txt"}),
            ToolCall(id="t3", name="read", arguments={"path": "c.txt"}),
        ]
        backend = ScriptedBackend([])
        config = build_config(workspace=self.root, permission_mode="yolo")
        agent = Agent(
            config=config,
            toolbox=Toolbox(Workspace(self.root)),
            session=Session(workspace=self.root),
            ui=self.ui,
            backend=backend,
        )

        # 2件目の実行中に中断されたことにする
        executed: list[str] = []
        original = agent._execute_tool

        def interrupting(call):
            if len(executed) >= 1:
                raise KeyboardInterrupt
            executed.append(call.id)
            return original(call)

        agent._execute_tool = interrupting  # type: ignore[method-assign]

        with self.assertRaises(KeyboardInterrupt):
            agent._run_tools(calls)

        results = agent.session.messages[-1].tool_results
        self.assertEqual({r.tool_call_id for r in results}, {"t1", "t2", "t3"})
        interrupted = [r for r in results if r.tool_call_id in {"t2", "t3"}]
        self.assertTrue(all(r.is_error for r in interrupted))
        self.assertTrue(all("中断" in r.content for r in interrupted))


if __name__ == "__main__":
    unittest.main()
