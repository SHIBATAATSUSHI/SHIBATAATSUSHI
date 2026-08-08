"""バックエンドの変換とストリーミング集約の検証。

外部通信はせず、SDK のオブジェクトを模したフェイクを流し込む。
"""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
import openai  # noqa: E402

from shibata_code.backends.anthropic_backend import AnthropicBackend  # noqa: E402
from shibata_code.backends.base import (  # noqa: E402
    STOP_END_TURN,
    STOP_MAX_TOKENS,
    STOP_TOOL_USE,
    BackendError,
)
from shibata_code.backends.openai_backend import OpenAICompatBackend, _repair_name  # noqa: E402
from shibata_code.config import build_config  # noqa: E402
from shibata_code.messages import Message, ToolCall, ToolResult  # noqa: E402
from shibata_code.tools import Toolbox  # noqa: E402
from shibata_code.ui import UI  # noqa: E402
from shibata_code.workspace import Workspace  # noqa: E402


# ----------------------------------------------------------------------
# OpenAI SDK のストリーミングを模したフェイク
# ----------------------------------------------------------------------
def delta_chunk(*, content=None, reasoning=None, tool_calls=None, finish=None, usage=None):
    """SDK が model_dump() で吐くのと同じ形(辞書)のチャンク。"""
    delta: dict = {}
    if content is not None:
        delta["content"] = content
    if reasoning is not None:
        delta["reasoning_content"] = reasoning
    if tool_calls is not None:
        delta["tool_calls"] = tool_calls
    chunk: dict = {"choices": [{"delta": delta, "finish_reason": finish}]}
    if usage is not None:
        chunk["usage"] = usage
    return chunk


def tool_fragment(index: int, *, call_id=None, name=None, arguments=None) -> dict:
    function: dict = {}
    if name is not None:
        function["name"] = name
    if arguments is not None:
        function["arguments"] = arguments
    fragment: dict = {"index": index, "function": function}
    if call_id is not None:
        fragment["id"] = call_id
    return fragment


class FakeCompletions:
    def __init__(self, chunks: list, error: Exception | None = None) -> None:
        self.chunks = chunks
        self.error = error
        self.calls: list[dict] = []

    def create(self, **params):
        self.calls.append(params)
        # 1回目だけ失敗させたい場合に使う
        if self.error is not None and len(self.calls) == 1:
            raise self.error
        return iter(self.chunks)


class FakeOpenAIClient:
    def __init__(self, chunks: list, error: Exception | None = None) -> None:
        self.completions = FakeCompletions(chunks, error)
        self.chat = SimpleNamespace(completions=self.completions)


def bad_request(message: str = "unsupported parameter") -> openai.BadRequestError:
    request = httpx.Request("POST", "http://example.invalid/v1/chat/completions")
    response = httpx.Response(400, request=request)
    return openai.BadRequestError(message, response=response, body=None)


class BackendTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.ui = UI(color=False, stream=io.StringIO())
        self.tools = Toolbox(Workspace(self.root)).specs

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def config(self, model: str = "opus", **kwargs):
        return build_config(model=model, workspace=self.root, **kwargs)


# ----------------------------------------------------------------------
# 共通のツール定義変換
# ----------------------------------------------------------------------
class ToolSchemaTest(BackendTestBase):
    def test_anthropic形式(self) -> None:
        schema = self.tools[0].to_anthropic()
        self.assertIn("input_schema", schema)
        self.assertEqual(schema["name"], "read")

    def test_openai形式(self) -> None:
        schema = self.tools[0].to_openai()
        self.assertEqual(schema["type"], "function")
        self.assertEqual(schema["function"]["name"], "read")
        self.assertIn("parameters", schema["function"])


# ----------------------------------------------------------------------
# Anthropic
# ----------------------------------------------------------------------
class AnthropicRenderTest(BackendTestBase):
    def backend(self, model: str = "opus") -> AnthropicBackend:
        return AnthropicBackend(self.config(model), client=object())

    def test_ユーザー発言を変換する(self) -> None:
        rendered = self.backend().render_history([Message.user("やあ")])
        self.assertEqual(rendered, [{"role": "user", "content": "やあ"}])

    def test_同一モデルなら生データをそのまま使う(self) -> None:
        native = [{"type": "thinking", "thinking": "考え", "signature": "sig"}]
        history = [
            Message.assistant(
                text="答え",
                provider="anthropic",
                model="claude-opus-5",
                native=native,
            )
        ]
        rendered = self.backend().render_history(history)
        self.assertEqual(rendered[0]["content"], native)

    def test_別モデルなら中立表現から組み直す(self) -> None:
        native = [{"type": "thinking", "thinking": "考え", "signature": "sig"}]
        history = [
            Message.assistant(
                text="答え",
                tool_calls=[ToolCall(id="t1", name="read", arguments={"path": "a"})],
                provider="anthropic",
                model="claude-sonnet-5",
                native=native,
            )
        ]
        rendered = self.backend("opus").render_history(history)
        blocks = rendered[0]["content"]
        self.assertEqual(blocks[0], {"type": "text", "text": "答え"})
        self.assertEqual(blocks[1]["type"], "tool_use")
        self.assertEqual(blocks[1]["name"], "read")

    def test_他プロバイダの履歴も引き継げる(self) -> None:
        history = [
            Message.user("やあ"),
            Message.assistant(text="はい", provider="deepseek", model="deepseek-chat"),
        ]
        rendered = self.backend().render_history(history)
        self.assertEqual(rendered[1]["content"], [{"type": "text", "text": "はい"}])

    def test_ツール結果は1通のuserメッセージになる(self) -> None:
        history = [
            Message.tool(
                [
                    ToolResult(tool_call_id="t1", content="OK"),
                    ToolResult(tool_call_id="t2", content="NG", is_error=True),
                ]
            )
        ]
        rendered = self.backend().render_history(history)
        self.assertEqual(len(rendered), 1)
        self.assertEqual(rendered[0]["role"], "user")
        self.assertEqual(len(rendered[0]["content"]), 2)
        self.assertNotIn("is_error", rendered[0]["content"][0])
        self.assertTrue(rendered[0]["content"][1]["is_error"])

    def test_空メッセージは飛ばす(self) -> None:
        rendered = self.backend().render_history([Message.assistant(text="   ")])
        self.assertEqual(rendered, [])


class AnthropicParamsTest(BackendTestBase):
    def test_opusではthinkingとeffortを送る(self) -> None:
        backend = AnthropicBackend(self.config("opus"), client=object())
        params = backend.build_params(system="sys", history=[], tools=self.tools)
        self.assertEqual(params["thinking"]["type"], "adaptive")
        self.assertEqual(params["output_config"]["effort"], "high")
        self.assertEqual(params["system"][0]["cache_control"], {"type": "ephemeral"})
        self.assertNotIn("fallbacks", params)

    def test_haikuではthinkingとeffortを送らない(self) -> None:
        backend = AnthropicBackend(self.config("haiku"), client=object())
        params = backend.build_params(system="sys", history=[], tools=self.tools)
        self.assertNotIn("thinking", params)
        self.assertNotIn("output_config", params)

    def test_fableではフォールバックを有効にする(self) -> None:
        backend = AnthropicBackend(self.config("fable"), client=object())
        params = backend.build_params(system="sys", history=[], tools=self.tools)
        self.assertEqual(params["fallbacks"], "default")
        self.assertIn("server-side-fallback-2026-07-01", params["betas"])


# ----------------------------------------------------------------------
# OpenAI 互換
# ----------------------------------------------------------------------
class OpenAIRenderTest(BackendTestBase):
    def backend(self, model: str = "deepseek", client=None) -> OpenAICompatBackend:
        return OpenAICompatBackend(self.config(model), client=client or object())

    def test_systemが先頭に入る(self) -> None:
        rendered = self.backend().render_history("指示", [Message.user("やあ")])
        self.assertEqual(rendered[0], {"role": "system", "content": "指示"})
        self.assertEqual(rendered[1], {"role": "user", "content": "やあ"})

    def test_ツール呼び出しはtool_callsになる(self) -> None:
        history = [
            Message.assistant(
                text="読む",
                tool_calls=[ToolCall(id="t1", name="read", arguments={"path": "a.txt"})],
            )
        ]
        rendered = self.backend().render_history("s", history)
        entry = rendered[1]
        self.assertEqual(entry["tool_calls"][0]["id"], "t1")
        self.assertEqual(entry["tool_calls"][0]["function"]["name"], "read")
        self.assertIn("a.txt", entry["tool_calls"][0]["function"]["arguments"])

    def test_ツール結果は結果ごとに1メッセージ(self) -> None:
        history = [
            Message.tool(
                [
                    ToolResult(tool_call_id="t1", content="OK"),
                    ToolResult(tool_call_id="t2", content="失敗", is_error=True),
                ]
            )
        ]
        rendered = self.backend().render_history("s", history)
        self.assertEqual(len(rendered), 3)  # system + 2件
        self.assertEqual(rendered[1]["role"], "tool")
        self.assertEqual(rendered[1]["tool_call_id"], "t1")
        self.assertTrue(rendered[2]["content"].startswith("[エラー]"))

    def test_Anthropicの履歴も引き継げる(self) -> None:
        history = [
            Message.assistant(
                text="はい",
                provider="anthropic",
                model="claude-opus-5",
                native=[{"type": "thinking", "thinking": "内緒"}],
            )
        ]
        rendered = self.backend().render_history("s", history)
        # 生データは使わず、中立表現から組み直す
        self.assertEqual(rendered[1]["content"], "はい")
        self.assertNotIn("thinking", str(rendered[1]))

    def test_出力上限のパラメータ名がモデルで変わる(self) -> None:
        deepseek = self.backend("deepseek").build_params(
            system="s", history=[], tools=self.tools
        )
        self.assertIn("max_tokens", deepseek)
        gpt = self.backend("gpt").build_params(system="s", history=[], tools=self.tools)
        self.assertIn("max_completion_tokens", gpt)
        self.assertEqual(gpt["reasoning_effort"], "high")

    def test_reasoning_effortは既定では送らない(self) -> None:
        params = self.backend("deepseek").build_params(
            system="s", history=[], tools=self.tools
        )
        self.assertNotIn("reasoning_effort", params)

    def test_強制指定でreasoning_effortを送れる(self) -> None:
        config = self.config("deepseek", force_reasoning_effort=True, effort="max")
        backend = OpenAICompatBackend(config, client=object())
        params = backend.build_params(system="s", history=[], tools=self.tools)
        # OpenAI 系は high までしか無いので丸められる
        self.assertEqual(params["reasoning_effort"], "high")


class OpenAIStreamTest(BackendTestBase):
    def run_stream(self, chunks: list, *, error=None, model: str = "deepseek"):
        client = FakeOpenAIClient(chunks, error)
        backend = OpenAICompatBackend(self.config(model), client=client)
        result = backend.stream_turn(
            system="指示", history=[Message.user("やあ")], tools=self.tools, sink=self.ui
        )
        return result, client

    def test_テキストを組み立てる(self) -> None:
        result, _ = self.run_stream(
            [
                delta_chunk(content="こん"),
                delta_chunk(content="にちは"),
                delta_chunk(finish="stop"),
            ]
        )
        self.assertEqual(result.text, "こんにちは")
        self.assertEqual(result.stop_reason, STOP_END_TURN)

    def test_ツール呼び出しを組み立てる(self) -> None:
        result, _ = self.run_stream(
            [
                delta_chunk(tool_calls=[tool_fragment(0, call_id="c1", name="read")]),
                delta_chunk(tool_calls=[tool_fragment(0, arguments='{"path": ')]),
                delta_chunk(tool_calls=[tool_fragment(0, arguments='"a.txt"}')]),
                delta_chunk(finish="tool_calls"),
            ]
        )
        self.assertEqual(result.stop_reason, STOP_TOOL_USE)
        self.assertEqual(len(result.tool_calls), 1)
        call = result.tool_calls[0]
        self.assertEqual(call.id, "c1")
        self.assertEqual(call.name, "read")
        self.assertEqual(call.arguments, {"path": "a.txt"})

    def test_並列のツール呼び出しを分けて組み立てる(self) -> None:
        result, _ = self.run_stream(
            [
                delta_chunk(
                    tool_calls=[
                        tool_fragment(0, call_id="c1", name="read", arguments='{"path":"a"}'),
                        tool_fragment(1, call_id="c2", name="glob", arguments='{"pattern":"*"}'),
                    ]
                ),
                delta_chunk(finish="tool_calls"),
            ]
        )
        self.assertEqual([c.name for c in result.tool_calls], ["read", "glob"])

    def test_finish_reasonが無くてもツールがあればtool_use(self) -> None:
        result, _ = self.run_stream(
            [
                delta_chunk(
                    tool_calls=[tool_fragment(0, call_id="c1", name="read", arguments="{}")]
                )
            ]
        )
        self.assertEqual(result.stop_reason, STOP_TOOL_USE)

    def test_lengthはmax_tokensに対応づける(self) -> None:
        result, _ = self.run_stream([delta_chunk(content="途中", finish="length")])
        self.assertEqual(result.stop_reason, STOP_MAX_TOKENS)

    def test_推論内容は思考として表示する(self) -> None:
        buffer = io.StringIO()
        ui = UI(color=False, stream=buffer)
        client = FakeOpenAIClient(
            [delta_chunk(reasoning="考え中"), delta_chunk(content="答え", finish="stop")]
        )
        backend = OpenAICompatBackend(self.config("deepseek"), client=client)
        backend.stream_turn(system="s", history=[], tools=self.tools, sink=ui)
        self.assertIn("考え中", buffer.getvalue())

    def test_利用量を拾う(self) -> None:
        usage = {
            "prompt_tokens": 120,
            "completion_tokens": 30,
            "prompt_tokens_details": {"cached_tokens": 100},
        }
        result, _ = self.run_stream(
            [delta_chunk(content="x", finish="stop"), delta_chunk(usage=usage)]
        )
        self.assertEqual(result.usage["input_tokens"], 120)
        self.assertEqual(result.usage["cache_read_tokens"], 100)

    def test_壊れた引数でも落ちない(self) -> None:
        result, _ = self.run_stream(
            [
                delta_chunk(
                    tool_calls=[tool_fragment(0, call_id="c1", name="read", arguments="{壊れ")]
                ),
                delta_chunk(finish="tool_calls"),
            ]
        )
        self.assertEqual(result.tool_calls[0].arguments, {})

    def test_非対応パラメータで落ちたら外して再試行する(self) -> None:
        result, client = self.run_stream(
            [delta_chunk(content="はい", finish="stop")], error=bad_request()
        )
        self.assertEqual(result.text, "はい")
        self.assertEqual(len(client.completions.calls), 2)
        # 1回目にはあった stream_options が2回目には無い
        self.assertIn("stream_options", client.completions.calls[0])
        self.assertNotIn("stream_options", client.completions.calls[1])

    def test_外すものが無くなればエラーにする(self) -> None:
        client = FakeOpenAIClient([], error=None)

        class AlwaysBad(FakeCompletions):
            def create(self, **params):
                self.calls.append(params)
                raise bad_request("だめ")

        client.completions = AlwaysBad([])
        client.chat = SimpleNamespace(completions=client.completions)
        backend = OpenAICompatBackend(self.config("deepseek"), client=client)
        with self.assertRaises(BackendError):
            backend.stream_turn(system="s", history=[], tools=self.tools, sink=self.ui)


class CredentialTest(BackendTestBase):
    def test_キーが無いプロバイダは案内を返す(self) -> None:
        backend = OpenAICompatBackend(self.config("kimi"), client=object())
        message = backend.check_credentials()
        self.assertIn("MOONSHOT_API_KEY", message)

    def test_ローカルはキー不要(self) -> None:
        backend = OpenAICompatBackend(self.config("local:llama"), client=object())
        self.assertIsNone(backend.check_credentials())


class RepairNameTest(unittest.TestCase):
    def test_既知の名前はそのまま(self) -> None:
        self.assertEqual(_repair_name("read", {"read", "write"}), "read")

    def test_重複した名前を直す(self) -> None:
        self.assertEqual(_repair_name("readread", {"read", "write"}), "read")

    def test_接頭辞が一致すれば切り出す(self) -> None:
        self.assertEqual(_repair_name("readX", {"read"}), "read")

    def test_見当がつかなければそのまま返す(self) -> None:
        self.assertEqual(_repair_name("unknown", {"read"}), "unknown")


if __name__ == "__main__":
    unittest.main()
