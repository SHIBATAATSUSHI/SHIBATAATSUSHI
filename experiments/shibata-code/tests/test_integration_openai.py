"""実際の openai SDK を通した結合テスト。

ローカルに OpenAI 互換の `/v1/chat/completions` を模したサーバを立て、
そこへ本物の SDK を向ける。フェイクでは検証できない「SDK のストリーミング
解析を通ったあとに、こちらのループが正しく動くか」を確かめる。
外部ネットワークには一切出ない。
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import openai  # noqa: E402

from shibata_code.agent import Agent  # noqa: E402
from shibata_code.backends.openai_backend import OpenAICompatBackend  # noqa: E402
from shibata_code.config import build_config  # noqa: E402
from shibata_code.session import Session  # noqa: E402
from shibata_code.tools import Toolbox  # noqa: E402
from shibata_code.ui import UI  # noqa: E402
from shibata_code.workspace import Workspace  # noqa: E402


def chunk(delta: dict, *, finish: str | None = None, usage: dict | None = None) -> str:
    """OpenAI 互換のストリーミングチャンク1つ分。"""
    payload = {
        "id": "chatcmpl-test",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "test-model",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    if usage is not None:
        payload["usage"] = usage
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def text_response(text: str, *, finish: str = "stop") -> str:
    return (
        chunk({"role": "assistant", "content": ""})
        + chunk({"content": text})
        + chunk({}, finish=finish)
        + chunk({}, usage={"prompt_tokens": 42, "completion_tokens": 7})
        + "data: [DONE]\n\n"
    )


def tool_response(name: str, arguments: dict) -> str:
    raw = json.dumps(arguments, ensure_ascii=False)
    half = len(raw) // 2
    return (
        chunk({"role": "assistant", "content": ""})
        + chunk(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": name, "arguments": raw[:half]},
                    }
                ]
            }
        )
        # 引数は分割して届く。組み立てられることを確かめる。
        + chunk(
            {
                "tool_calls": [
                    {"index": 0, "function": {"arguments": raw[half:]}}
                ]
            }
        )
        + chunk({}, finish="tool_calls")
        + "data: [DONE]\n\n"
    )


class _Handler(BaseHTTPRequestHandler):
    responses: list[str] = []
    received: list[dict] = []

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        type(self).received.append(json.loads(body) if body else {})

        if not type(self).responses:
            self.send_error(500, "no canned response left")
            return
        payload = type(self).responses.pop(0).encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args) -> None:
        return


class OpenAIIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()

        _Handler.responses = []
        _Handler.received = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address[:2]
        self.base_url = f"http://{host}:{port}/v1"

        self.buffer = io.StringIO()
        self.ui = UI(color=False, stream=self.buffer)

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self._tmp.cleanup()

    def make_agent(self, model: str = "deepseek") -> Agent:
        client = openai.OpenAI(
            api_key="test-key",
            base_url=self.base_url,
            max_retries=0,
            # プロキシ設定を拾ってローカル接続を壊さないようにする
            http_client=openai.DefaultHttpxClient(trust_env=False),
        )
        config = build_config(
            model=model,
            workspace=self.root,
            permission_mode="yolo",
            base_url=self.base_url,
        )
        return Agent(
            config=config,
            toolbox=Toolbox(Workspace(self.root)),
            session=Session(workspace=self.root),
            ui=self.ui,
            backend=OpenAICompatBackend(config, client=client),
        )

    def test_テキスト応答がストリーミングで表示される(self) -> None:
        _Handler.responses = [text_response("こんにちは")]
        agent = self.make_agent()
        agent.run_turn("あいさつして")
        self.assertIn("こんにちは", self.buffer.getvalue())
        self.assertEqual(agent.session.usage.requests, 1)
        self.assertEqual(agent.session.usage.input_tokens, 42)

    def test_tool_useから実ファイル作成まで通る(self) -> None:
        _Handler.responses = [
            tool_response("write", {"path": "生成.txt", "content": "本文\n"}),
            text_response("書き終えた"),
        ]
        agent = self.make_agent()
        agent.run_turn("生成.txt を作って")

        self.assertEqual((self.root / "生成.txt").read_text(encoding="utf-8"), "本文\n")
        self.assertEqual(len(_Handler.received), 2)

        # 2回目のリクエストに tool ロールの結果が載っている
        second = _Handler.received[1]
        tool_message = second["messages"][-1]
        self.assertEqual(tool_message["role"], "tool")
        self.assertEqual(tool_message["tool_call_id"], "call_1")

    def test_送信リクエストの形が期待どおり(self) -> None:
        _Handler.responses = [text_response("はい")]
        agent = self.make_agent()
        agent.run_turn("やあ")

        sent = _Handler.received[0]
        self.assertEqual(sent["model"], "deepseek-chat")
        self.assertTrue(sent["stream"])
        self.assertEqual(sent["messages"][0]["role"], "system")
        self.assertEqual(len(sent["tools"]), 6)
        self.assertEqual(sent["tools"][0]["type"], "function")
        self.assertIn("max_tokens", sent)

    def test_ツール失敗も含めて往復が続く(self) -> None:
        _Handler.responses = [
            tool_response("read", {"path": "無いファイル.txt"}),
            text_response("見つからなかった"),
        ]
        agent = self.make_agent()
        agent.run_turn("読んで")

        second = _Handler.received[1]
        tool_message = second["messages"][-1]
        self.assertEqual(tool_message["role"], "tool")
        self.assertIn("[エラー]", tool_message["content"])

    def test_Anthropicの履歴を引き継いで送れる(self) -> None:
        # 先に Anthropic 形式の発言を履歴へ入れておく
        from shibata_code.messages import Message

        _Handler.responses = [text_response("引き継いだ")]
        agent = self.make_agent()
        agent.session.messages.append(
            Message.assistant(
                text="前のモデルの発言",
                provider="anthropic",
                model="claude-opus-5",
                native=[{"type": "thinking", "thinking": "内緒", "signature": "sig"}],
            )
        )
        agent.run_turn("続けて")

        sent = _Handler.received[0]
        roles = [m["role"] for m in sent["messages"]]
        self.assertEqual(roles, ["system", "assistant", "user"])
        # Anthropic 固有の thinking は送られていない
        self.assertNotIn("thinking", json.dumps(sent, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
