"""実際の anthropic SDK を通した結合テスト。

ローカルに Messages API を模した SSE サーバを立て、そこへ本物の SDK を
向ける。フェイククライアントでは検証できない「SDK のストリーミング解析を
通ったあとに、こちらのループが正しく動くか」を確かめるためのもの。
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

import anthropic  # noqa: E402

from mytos.agent import Agent  # noqa: E402
from mytos.config import build_config  # noqa: E402
from mytos.session import Session  # noqa: E402
from mytos.tools import Toolbox  # noqa: E402
from mytos.ui import UI  # noqa: E402
from mytos.workspace import Workspace  # noqa: E402


def sse(event: str, payload: dict) -> str:
    """1つの Server-Sent Event を組み立てる。"""
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def text_response(text: str, *, stop_reason: str = "end_turn") -> str:
    """テキストだけを返すレスポンスの SSE 列。"""
    chunks = [
        sse(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg_text",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-opus-5",
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 12, "output_tokens": 1},
                },
            },
        ),
        sse(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        sse(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": text},
            },
        ),
        sse("content_block_stop", {"type": "content_block_stop", "index": 0}),
        sse(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                "usage": {"output_tokens": 7},
            },
        ),
        sse("message_stop", {"type": "message_stop"}),
    ]
    return "".join(chunks)


def tool_use_response(tool_name: str, payload: dict) -> str:
    """tool_use を1件返すレスポンスの SSE 列。"""
    chunks = [
        sse(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg_tool",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-opus-5",
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 20, "output_tokens": 1},
                },
            },
        ),
        sse(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": tool_name,
                    "input": {},
                },
            },
        ),
        sse(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {
                    "type": "input_json_delta",
                    "partial_json": json.dumps(payload, ensure_ascii=False),
                },
            },
        ),
        sse("content_block_stop", {"type": "content_block_stop", "index": 0}),
        sse(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                "usage": {"output_tokens": 15},
            },
        ),
        sse("message_stop", {"type": "message_stop"}),
    ]
    return "".join(chunks)


class _Handler(BaseHTTPRequestHandler):
    """用意した SSE を順に返すだけのハンドラ。"""

    # クラス変数でサーバ間の状態を共有する(テストごとに差し替える)
    responses: list[str] = []
    received: list[dict] = []

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler の規約
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

    def log_message(self, *args) -> None:  # テスト出力を汚さない
        return


class SSEIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()

        _Handler.responses = []
        _Handler.received = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address[:2]
        self.base_url = f"http://{host}:{port}"

        self.buffer = io.StringIO()
        self.ui = UI(color=False, stream=self.buffer)

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self._tmp.cleanup()

    def make_agent(self) -> Agent:
        client = anthropic.Anthropic(
            api_key="test-key",
            base_url=self.base_url,
            max_retries=0,
            # プロキシ設定を拾ってローカル接続を壊さないようにする
            http_client=anthropic.DefaultHttpxClient(trust_env=False),
        )
        config = build_config(workspace=self.root, permission_mode="yolo")
        return Agent(
            client=client,
            config=config,
            toolbox=Toolbox(Workspace(self.root)),
            session=Session(workspace=self.root),
            ui=self.ui,
        )

    def test_テキスト応答がストリーミングで表示される(self) -> None:
        _Handler.responses = [text_response("こんにちは")]
        agent = self.make_agent()
        agent.run_turn("あいさつして")
        self.assertIn("こんにちは", self.buffer.getvalue())
        self.assertEqual(agent.session.usage.requests, 1)

    def test_tool_useから実ファイル作成まで通る(self) -> None:
        _Handler.responses = [
            tool_use_response("write", {"path": "生成.txt", "content": "本文\n"}),
            text_response("書き終えた"),
        ]
        agent = self.make_agent()
        agent.run_turn("生成.txt を作って")

        # ツールが実際に走ってファイルができている
        self.assertEqual(
            (self.root / "生成.txt").read_text(encoding="utf-8"), "本文\n"
        )
        # 2往復している
        self.assertEqual(len(_Handler.received), 2)
        # 2回目のリクエストに tool_result が載っている
        second = _Handler.received[1]
        tool_result_msg = second["messages"][-1]
        self.assertEqual(tool_result_msg["role"], "user")
        self.assertEqual(tool_result_msg["content"][0]["type"], "tool_result")
        self.assertEqual(tool_result_msg["content"][0]["tool_use_id"], "toolu_1")

    def test_送信リクエストの形が期待どおり(self) -> None:
        _Handler.responses = [text_response("はい")]
        agent = self.make_agent()
        agent.run_turn("やあ")

        sent = _Handler.received[0]
        self.assertEqual(sent["model"], "claude-opus-5")
        self.assertTrue(sent["stream"])
        self.assertEqual(sent["thinking"], {"type": "adaptive", "display": "summarized"})
        self.assertEqual(sent["output_config"], {"effort": "high"})
        self.assertEqual(sent["system"][0]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(len(sent["tools"]), 6)

    def test_ツール失敗も含めて往復が続く(self) -> None:
        _Handler.responses = [
            tool_use_response("read", {"path": "無いファイル.txt"}),
            text_response("見つからなかった"),
        ]
        agent = self.make_agent()
        agent.run_turn("読んで")

        second = _Handler.received[1]
        result = second["messages"][-1]["content"][0]
        self.assertTrue(result["is_error"])
        self.assertIn("存在しない", result["content"])


if __name__ == "__main__":
    unittest.main()
