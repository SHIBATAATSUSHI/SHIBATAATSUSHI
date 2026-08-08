"""追加パッケージが1つも無い環境で動くことの検証。

iOS(a-Shell 等)では anthropic / openai SDK を入れられない。
そこで、この2つの import を遮断した状態でエージェントを1往復させ、
標準ライブラリだけで完結することを確かめる。

遮断中に SDK を import しようとすれば ImportError で落ちるので、
このテストが通ること自体が「SDK に触れていない」ことの証明になる。
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BLOCKED = ("anthropic", "openai")


class _BlockSDKFinder:
    """指定モジュールの import を失敗させる meta path finder。"""

    def find_module(self, fullname, path=None):  # 古い API 互換のため
        return None

    def find_spec(self, fullname, path=None, target=None):
        root = fullname.split(".")[0]
        if root in BLOCKED:
            raise ImportError(f"[テスト] {fullname} は遮断されている")
        return None


@contextlib.contextmanager
def sdk_blocked():
    """anthropic / openai を import できない状態にする。"""
    finder = _BlockSDKFinder()
    saved = {name: sys.modules.pop(name, None) for name in BLOCKED}
    # サブモジュールも退避する
    submodules = {
        name: sys.modules.pop(name)
        for name in list(sys.modules)
        if name.split(".")[0] in BLOCKED
    }
    sys.meta_path.insert(0, finder)
    try:
        yield
    finally:
        sys.meta_path.remove(finder)
        for name, module in {**saved, **submodules}.items():
            if module is not None:
                sys.modules[name] = module


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


def anthropic_sse(text: str) -> str:
    """Anthropic 形式の最小レスポンス。"""
    events = [
        {
            "type": "message_start",
            "message": {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-5",
                "content": [],
                "stop_reason": None,
                "usage": {"input_tokens": 5, "output_tokens": 1},
            },
        },
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": text},
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 3},
        },
        {"type": "message_stop"},
    ]
    return "".join(f"data: {json.dumps(e, ensure_ascii=False)}\n\n" for e in events)


def openai_sse(text: str) -> str:
    """OpenAI 互換形式の最小レスポンス。"""
    chunks = [
        {
            "id": "c1",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "m",
            "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
        },
        {
            "id": "c1",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "m",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        },
    ]
    body = "".join(f"data: {json.dumps(c, ensure_ascii=False)}\n\n" for c in chunks)
    return body + "data: [DONE]\n\n"


class NoSDKTest(unittest.TestCase):
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

        import os

        self._saved_env = {
            name: os.environ.get(name)
            for name in ("ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY")
        }
        os.environ["ANTHROPIC_API_KEY"] = "test-key"
        os.environ["DEEPSEEK_API_KEY"] = "test-key"

    def tearDown(self) -> None:
        import os

        for name, value in self._saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self._tmp.cleanup()

    # ------------------------------------------------------------------
    def test_遮断が効いていることを確かめる(self) -> None:
        # このテスト自体が正しく効いているかの確認
        with sdk_blocked():
            with self.assertRaises(ImportError):
                import anthropic  # noqa: F401
            with self.assertRaises(ImportError):
                import openai  # noqa: F401
        # 抜けたら元に戻る
        import anthropic  # noqa: F401
        import openai  # noqa: F401

    def run_turn_without_sdk(self, *, model: str, base_suffix: str = "") -> object:
        """SDK を遮断した状態で1往復させる。"""
        from shibata_code.agent import Agent
        from shibata_code.backends import build_backend
        from shibata_code.config import build_config
        from shibata_code.session import Session
        from shibata_code.tools import Toolbox
        from shibata_code.ui import UI
        from shibata_code.workspace import Workspace

        config = build_config(
            model=model,
            workspace=self.root,
            permission_mode="yolo",
            base_url=self.base_url + base_suffix,
            transport="auto",  # SDK が無いので自動で標準ライブラリ版になる
        )
        backend = build_backend(config)
        # プロキシ設定を拾わせない(ローカル接続のため)
        backend.trust_env = False

        agent = Agent(
            config=config,
            toolbox=Toolbox(Workspace(self.root)),
            session=Session(workspace=self.root),
            ui=UI(color=False, stream=self.buffer, width=44, compact=True),
            backend=backend,
        )
        agent.run_turn("やあ")
        return agent

    def test_SDK無しでAnthropicと1往復できる(self) -> None:
        _Handler.responses = [anthropic_sse("こんにちは")]
        with sdk_blocked():
            agent = self.run_turn_without_sdk(model="opus")
        self.assertIn("こんにちは", self.buffer.getvalue())
        self.assertEqual(agent.backend.name, "anthropic_http")
        self.assertEqual(len(_Handler.received), 1)

    def test_SDK無しでOpenAI互換と1往復できる(self) -> None:
        _Handler.responses = [openai_sse("你好")]
        with sdk_blocked():
            agent = self.run_turn_without_sdk(model="deepseek", base_suffix="/v1")
        self.assertIn("你好", self.buffer.getvalue())
        self.assertEqual(agent.backend.name, "openai_compat_http")

    def test_SDKがあればSDK版が選ばれる(self) -> None:
        from shibata_code.backends import build_backend
        from shibata_code.config import build_config

        config = build_config(workspace=self.root, transport="auto")
        self.assertEqual(build_backend(config).name, "anthropic")

    def test_httpを明示すればSDKがあっても標準ライブラリ版(self) -> None:
        from shibata_code.backends import build_backend
        from shibata_code.config import build_config

        config = build_config(workspace=self.root, transport="http")
        self.assertEqual(build_backend(config).name, "anthropic_http")

    def test_ツールもSDK無しで動く(self) -> None:
        # read/write/edit/glob/grep は標準ライブラリだけで実装してある
        with sdk_blocked():
            from shibata_code.tools import Toolbox
            from shibata_code.workspace import Workspace

            tools = Toolbox(Workspace(self.root))
            tools.run("write", {"path": "a.txt", "content": "本文\n"})
            body = tools.run("read", {"path": "a.txt"})
            found = tools.run("grep", {"pattern": "本文"})
        self.assertIn("本文", body)
        self.assertIn("a.txt", found)


if __name__ == "__main__":
    unittest.main()
