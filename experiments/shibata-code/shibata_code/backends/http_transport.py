"""標準ライブラリだけで喋る HTTP + SSE の送受信。

iOS(a-Shell 等)では anthropic / openai SDK が入らない。これらは
pydantic のコンパイル済み拡張に依存していて、iOS 向けのホイールが
無いため。そこで、追加パッケージ無しで同じことをやる経路を用意する。

やることは「JSONをPOSTして、Server-Sent Events を1件ずつ返す」だけ。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Iterator

# ストリーミングは長時間になり得るので、既定のタイムアウトは長めに取る。
DEFAULT_TIMEOUT = 600


class HTTPTransportError(Exception):
    """HTTP 層での失敗。状態コードと本文を持つ。"""

    def __init__(self, message: str, *, status: int | None = None, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body


def _build_opener(trust_env: bool) -> urllib.request.OpenerDirector:
    """プロキシを使うかどうかを決めた opener を作る。"""
    if trust_env:
        # 環境変数のプロキシ設定に従う(既定)
        return urllib.request.build_opener()
    # ローカル接続などでプロキシを迂回したい場合
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def post_sse(
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: int = DEFAULT_TIMEOUT,
    trust_env: bool = True,
) -> Iterator[dict[str, Any]]:
    """JSON を POST し、返ってくる SSE を1件ずつ辞書で返す。

    `data:` 行だけを拾う。`event:` 行は無視してよい(本文の JSON に
    種別が入っているため)。`[DONE]` が来たら終わり。
    """
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            **headers,
        },
        method="POST",
    )

    opener = _build_opener(trust_env)
    try:
        response = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")
        except Exception:  # 本文が読めなくても状態コードは伝える
            pass
        raise HTTPTransportError(
            _summarize_error(detail) or str(exc), status=exc.code, body=detail
        ) from exc
    except urllib.error.URLError as exc:
        raise HTTPTransportError(f"接続できない: {exc.reason}") from exc
    except OSError as exc:
        raise HTTPTransportError(f"通信に失敗: {exc}") from exc

    with response:
        for raw in response:
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line or line.startswith(":"):
                # 空行はイベントの区切り、コロン始まりはコメント(心拍)
                continue
            if not line.startswith("data:"):
                continue
            chunk = line[len("data:") :].strip()
            if not chunk or chunk == "[DONE]":
                if chunk == "[DONE]":
                    return
                continue
            try:
                parsed = json.loads(chunk)
            except ValueError:
                # 壊れた行は捨てる。落とすほどのことではない。
                continue
            if isinstance(parsed, dict):
                yield parsed


def _summarize_error(body: str) -> str:
    """エラー本文から人が読める部分を取り出す。"""
    if not body:
        return ""
    try:
        parsed = json.loads(body)
    except ValueError:
        return body[:300]
    error = parsed.get("error") if isinstance(parsed, dict) else None
    if isinstance(error, dict):
        return str(error.get("message") or error.get("type") or body[:300])
    if isinstance(error, str):
        return error
    return body[:300]
