"""note の内部 API クライアントと、アカウント設定の解決。

note には公開 API が無いため、note のエディタ自身が叩いている内部 API を
そのまま利用する。**非公式なので note 側の仕様変更で壊れうる**。壊れたときに
原因が分かるよう、失敗時は必ずステータスコードとレスポンス本文を添えて
`NoteError` を送出する。

認証は Cookie `_note_session_v5` のみ。CSRF 用の `XSRF-TOKEN` は最初の
GET でサーバから配られるものを拾うので、利用者が用意する必要はない。
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import urllib.parse
import uuid
from dataclasses import dataclass
from typing import Any

import requests

API_BASE = "https://note.com"
EDITOR_BASE = "https://editor.note.com"
SESSION_COOKIE = "_note_session_v5"
XSRF_COOKIE = "XSRF-TOKEN"

# ブラウザからの通信に見せるための共通ヘッダ。note の内部 API は
# X-Requested-With と Origin を見ているため省略できない。
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Origin": EDITOR_BASE,
    "Referer": f"{EDITOR_BASE}/",
    "X-Requested-With": "XMLHttpRequest",
}

LOCAL_ACCOUNTS_FILE = ".note-accounts.json"

COOKIE_HELP = """\
note の Cookie を取得して環境変数に設定してください:

  1. ブラウザで note に対象アカウントでログイン
  2. DevTools を開く → Application → Cookies → https://note.com
  3. `_note_session_v5` の値をコピー
  4. 環境設定の環境変数に登録:

       NOTE_<名前>_URLNAME=<note のユーザー名(URL の @ の後ろ)>
       NOTE_<名前>_SESSION=<コピーした値>
       NOTE_DEFAULT_ACCOUNT=<既定にする名前>

詳しい手順は docs/note-posting.md を参照。"""


class NoteError(RuntimeError):
    """note API の呼び出しに失敗したときに送出する。"""


class AuthError(NoteError):
    """Cookie が無効・期限切れのときに送出する。"""


# --------------------------------------------------------------------------
# アカウント設定
# --------------------------------------------------------------------------

@dataclass
class Account:
    """投稿先アカウント1つ分の設定。"""

    name: str
    urlname: str
    session: str
    source: str  # "環境変数" または設定ファイルのパス

    @property
    def masked_session(self) -> str:
        """ログ表示用に Cookie を伏せた文字列を返す。"""
        if len(self.session) <= 6:
            return "*" * len(self.session)
        return f"{self.session[:6]}…({len(self.session)}文字)"


def _accounts_from_env(env: dict[str, str]) -> dict[str, Account]:
    """`NOTE_<名前>_SESSION` 形式の環境変数からアカウントを組み立てる。"""
    found: dict[str, Account] = {}
    for key, value in env.items():
        m = re.fullmatch(r"NOTE_([A-Z0-9_]+)_SESSION", key)
        if not m or not value.strip():
            continue
        raw_name = m.group(1)
        # NOTE_DEFAULT_ACCOUNT のような制御用の変数と衝突させない
        if raw_name == "DEFAULT":
            continue
        urlname = env.get(f"NOTE_{raw_name}_URLNAME", "").strip()
        if not urlname:
            continue
        name = raw_name.lower()
        found[name] = Account(name=name, urlname=urlname, session=value.strip(), source="環境変数")
    return found


def _accounts_from_file(path: str) -> dict[str, Account]:
    """ローカルの JSON 設定ファイルからアカウントを読む(存在しなければ空)。"""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise NoteError(f"{path} を読めませんでした: {e}") from e

    found: dict[str, Account] = {}
    for name, conf in (data or {}).items():
        if not isinstance(conf, dict):
            continue
        urlname = str(conf.get("urlname", "")).strip()
        session = str(conf.get("session", "")).strip()
        if urlname and session:
            found[name.lower()] = Account(
                name=name.lower(), urlname=urlname, session=session, source=path
            )
    return found


def load_accounts(
    *, env: dict[str, str] | None = None, config_path: str = LOCAL_ACCOUNTS_FILE
) -> dict[str, Account]:
    """環境変数とローカル設定ファイルを合わせてアカウント一覧を返す。

    同じ名前があれば環境変数を優先する(手元の使い捨て設定より、環境に
    正式登録されたもののほうが確かなため)。
    """
    env = os.environ if env is None else env
    accounts = _accounts_from_file(config_path)
    accounts.update(_accounts_from_env(dict(env)))
    return accounts


def resolve_account(
    name: str | None,
    *,
    env: dict[str, str] | None = None,
    config_path: str = LOCAL_ACCOUNTS_FILE,
) -> Account:
    """使用するアカウントを1つに決める。

    優先順位: 明示指定 → NOTE_DEFAULT_ACCOUNT → 設定が1つだけならそれ。
    """
    env = os.environ if env is None else env
    accounts = load_accounts(env=env, config_path=config_path)

    if not accounts:
        raise AuthError("note のアカウント設定が見つかりません。\n\n" + COOKIE_HELP)

    if name:
        key = name.lower()
        if key not in accounts:
            available = "、".join(sorted(accounts)) or "(なし)"
            raise AuthError(f"アカウント '{name}' の設定がありません。利用できるのは: {available}")
        return accounts[key]

    default = env.get("NOTE_DEFAULT_ACCOUNT", "").strip().lower()
    if default:
        if default not in accounts:
            available = "、".join(sorted(accounts)) or "(なし)"
            raise AuthError(
                f"NOTE_DEFAULT_ACCOUNT='{default}' に対応する設定がありません。"
                f"利用できるのは: {available}"
            )
        return accounts[default]

    if len(accounts) == 1:
        return next(iter(accounts.values()))

    available = "、".join(sorted(accounts))
    raise AuthError(
        f"アカウントが複数あります。--account で指定するか NOTE_DEFAULT_ACCOUNT を設定してください。"
        f"利用できるのは: {available}"
    )


# --------------------------------------------------------------------------
# API クライアント
# --------------------------------------------------------------------------

@dataclass
class PostResult:
    """投稿の結果。"""

    note_id: int
    note_key: str
    published: bool
    edit_url: str
    public_url: str | None = None


class NoteClient:
    """note の内部 API を叩く最小限のクライアント。"""

    def __init__(self, account: Account, *, timeout: int = 30):
        self.account = account
        self.timeout = timeout
        self.http = requests.Session()
        self.http.headers.update(DEFAULT_HEADERS)
        self.http.cookies.set(SESSION_COOKIE, account.session, domain=".note.com")
        self._authenticated = False

    # -- 低レベル ---------------------------------------------------------

    @property
    def _xsrf(self) -> str:
        """Cookie の XSRF-TOKEN を URL デコードして返す。"""
        token = self.http.cookies.get(XSRF_COOKIE, domain=".note.com") or self.http.cookies.get(
            XSRF_COOKIE
        )
        return urllib.parse.unquote(token) if token else ""

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        """API を1回叩き、JSON の `data` 部分を返す。失敗時は NoteError。"""
        url = path if path.startswith("http") else f"{API_BASE}{path}"
        headers = dict(kwargs.pop("headers", {}))
        if self._xsrf:
            headers["X-XSRF-TOKEN"] = self._xsrf

        try:
            resp = self.http.request(method, url, headers=headers, timeout=self.timeout, **kwargs)
        except requests.exceptions.ProxyError as e:
            # Claude Code の実行環境ではネットワーク許可リストに note.com が
            # 無いとここで落ちる。原因が分かりにくいので個別に案内する
            raise NoteError(
                f"note.com に接続できませんでした(プロキシに拒否されました)。\n"
                f"実行環境のネットワーク許可リストに note.com / editor.note.com / "
                f"assets.st-note.com を追加してください。\n"
                f"参照: https://code.claude.com/docs/en/claude-code-on-the-web\n\n"
                f"詳細: {e}"
            ) from e
        except requests.RequestException as e:
            raise NoteError(f"{method} {url} に接続できませんでした: {e}") from e

        if resp.status_code in (401, 403):
            raise AuthError(
                f"note の認証に失敗しました (HTTP {resp.status_code})。"
                f"Cookie が期限切れの可能性があります。\n\n" + COOKIE_HELP
            )
        if not resp.ok:
            body = resp.text[:500]
            raise NoteError(f"{method} {url} が失敗しました (HTTP {resp.status_code}): {body}")

        if not resp.content:
            return None
        try:
            payload = resp.json()
        except ValueError:
            return resp.text
        return payload.get("data", payload) if isinstance(payload, dict) else payload

    # -- 認証 -------------------------------------------------------------

    def authenticate(self) -> dict[str, Any]:
        """Cookie の有効性を確認し、同時に XSRF-TOKEN を受け取る。

        note は GET のレスポンスで XSRF-TOKEN Cookie を配るため、この1回で
        以降の POST / PUT に必要なトークンが揃う。
        """
        data = self._request("GET", "/api/v3/users/user_features")
        if not self._xsrf:
            raise AuthError(
                "XSRF-TOKEN を取得できませんでした。Cookie が無効な可能性があります。\n\n"
                + COOKIE_HELP
            )
        self._authenticated = True
        return data if isinstance(data, dict) else {}

    def _ensure_auth(self) -> None:
        if not self._authenticated:
            self.authenticate()

    # -- 画像 -------------------------------------------------------------

    def upload_image(self, path: str) -> tuple[str, str]:
        """本文中の画像をアップロードし (公開URL, 画像キー) を返す。"""
        self._ensure_auth()
        if not os.path.exists(path):
            raise NoteError(f"画像が見つかりません: {path}")

        ext = os.path.splitext(path)[1] or ".png"
        upload_name = f"{uuid.uuid4()}{ext}"

        # 1) アップロード先の署名付き URL を発行してもらう
        presigned = self._request(
            "POST",
            "/api/v3/images/upload/presigned_post",
            files={"filename": (None, upload_name)},
        )
        if not isinstance(presigned, dict):
            raise NoteError(f"画像アップロード先の取得に失敗しました: {presigned!r}")

        post_url = presigned.get("url")
        fields = presigned.get("fields") or {}
        if not post_url:
            raise NoteError(f"画像アップロード先の応答に url がありません: {presigned!r}")

        # 2) 実体を送る(S3 互換の署名付き POST。note の Cookie は不要)
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as f:
            try:
                resp = requests.post(
                    post_url,
                    data=fields,
                    files={"file": (upload_name, f, mime)},
                    timeout=self.timeout,
                )
            except requests.RequestException as e:
                raise NoteError(f"画像のアップロードに失敗しました ({path}): {e}") from e
        if not resp.ok:
            raise NoteError(
                f"画像のアップロードに失敗しました ({path}, HTTP {resp.status_code}): "
                f"{resp.text[:300]}"
            )

        url = presigned.get("public_url") or presigned.get("image_url") or presigned.get("url")
        key = presigned.get("path") or presigned.get("key") or upload_name
        # presigned のキーは "${filename}" のようなプレースホルダを含むことがある
        key = key.replace("${filename}", upload_name)
        if url and "${filename}" in url:
            url = url.replace("${filename}", upload_name)
        return str(url), str(key)

    def upload_eyecatch(self, note_id: int, path: str) -> None:
        """アイキャッチ画像を設定する。"""
        self._ensure_auth()
        if not os.path.exists(path):
            raise NoteError(f"アイキャッチ画像が見つかりません: {path}")

        mime = mimetypes.guess_type(path)[0] or "image/png"
        with open(path, "rb") as f:
            self._request(
                "POST",
                "/api/v1/image_upload/note_eyecatch",
                data={"id": str(note_id)},
                files={"file": (os.path.basename(path), f, mime)},
            )

    # -- 記事 -------------------------------------------------------------

    def create_draft_note(self) -> tuple[int, str, dict[str, Any]]:
        """空の記事を作り (note_id, note_key, 記事データ) を返す。"""
        self._ensure_auth()
        data = self._request("POST", "/api/v1/text_notes", json={"template_key": None})
        if not isinstance(data, dict):
            raise NoteError(f"記事の新規作成に失敗しました: {data!r}")
        note_id, note_key = data.get("id"), data.get("key")
        if not note_id or not note_key:
            raise NoteError(f"記事の新規作成の応答に id / key がありません: {data!r}")
        return int(note_id), str(note_key), data

    def save_draft(
        self,
        note_id: int,
        *,
        title: str,
        body_html: str,
        body_length: int,
        image_keys: list[str] | None = None,
        index: bool = False,
    ) -> None:
        """記事の本文を下書きとして保存する。"""
        self._ensure_auth()
        self._request(
            "POST",
            f"/api/v1/text_notes/draft_save?id={note_id}&is_temp_saved=true",
            json={
                "body": body_html,
                "body_length": body_length,
                "name": title,
                "index": index,
                "is_lead_form": False,
                "image_keys": image_keys or [],
            },
        )

    def publish(
        self,
        note_id: int,
        note_data: dict[str, Any],
        *,
        title: str,
        body_html: str,
        body_length: int,
        image_keys: list[str] | None = None,
        hashtags: list[str] | None = None,
    ) -> None:
        """下書き保存済みの記事を公開する。"""
        self._ensure_auth()
        payload = dict(note_data)
        payload.update(
            {
                "name": title,
                "body": body_html,
                "free_body": body_html,
                "pay_body": "",
                "status": "published",
                "price": 0,
                "is_refund": False,
                "limited": False,
                "index": True,
                "image_keys": image_keys or [],
                "hashtags": [t if t.startswith("#") else f"#{t}" for t in (hashtags or [])],
                "magazine_ids": [],
                "magazine_keys": [],
                "body_length": body_length,
                "send_notifications_flag": True,
                "lead_form": {"is_active": False, "consent_url": ""},
                "line_add_friend": {"is_active": False, "keyword": "", "add_friend_url": ""},
            }
        )
        # None のキーを送ると note 側でバリデーションに落ちることがある
        payload = {k: v for k, v in payload.items() if v is not None}
        self._request("PUT", f"/api/v1/text_notes/{note_id}", json=payload)

    # -- まとめ -----------------------------------------------------------

    def post(
        self,
        *,
        title: str,
        body_html: str,
        body_length: int,
        image_keys: list[str] | None = None,
        hashtags: list[str] | None = None,
        eyecatch: str | None = None,
        publish: bool = False,
    ) -> PostResult:
        """記事を1本投稿する(既定は下書き保存まで)。"""
        note_id, note_key, note_data = self.create_draft_note()

        if eyecatch:
            self.upload_eyecatch(note_id, eyecatch)

        self.save_draft(
            note_id,
            title=title,
            body_html=body_html,
            body_length=body_length,
            image_keys=image_keys,
            index=publish,
        )

        result = PostResult(
            note_id=note_id,
            note_key=note_key,
            published=False,
            edit_url=f"{EDITOR_BASE}/notes/{note_key}/edit",
        )
        if not publish:
            return result

        self.publish(
            note_id,
            note_data,
            title=title,
            body_html=body_html,
            body_length=body_length,
            image_keys=image_keys,
            hashtags=hashtags,
        )
        result.published = True
        result.public_url = f"{API_BASE}/{self.account.urlname}/n/{note_key}"
        return result
