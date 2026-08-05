#!/usr/bin/env python3
"""note.com への記事自動投稿スクリプト(複数アカウント対応)。

note には公開された投稿APIが存在しないため、Playwright で実ブラウザを操作して投稿する。
アカウントごとに Playwright の storage_state(ログイン済みcookie)をファイルで持ち、
`--account` で切り替えることで複数アカウントを同じ仕組みで運用できる。

使い方の詳細は docs/note-auto-post.md を参照。

  # 1. アカウントごとに一度だけログインして storage_state を作る
  python scripts/note_post.py login --account 19770104 --manual

  # 2. Markdown を下書きとして投稿する
  python scripts/note_post.py post --account 19770104 --file posts/example.md

  # 3. そのまま公開する
  python scripts/note_post.py post --account 19770104 --file posts/example.md --publish
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# パス定義
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "note_accounts.json"
EXAMPLE_CONFIG_PATH = REPO_ROOT / "config" / "note_accounts.example.json"

NOTE_BASE = "https://note.com"
LOGIN_URL = f"{NOTE_BASE}/login"
NEW_NOTE_URL = f"{NOTE_BASE}/notes/new"

# ---------------------------------------------------------------------------
# セレクタ候補
#
# note のエディタはUI変更が入りやすい。壊れたときはこのブロックだけを直せば
# 復旧できるよう、DOMに依存する箇所は全てここに集約している。
# 上から順に試し、最初に見つかったものを使う。
# ---------------------------------------------------------------------------

SEL_LOGIN_EMAIL = [
    "#email",
    "input[name='email']",
    "input[type='email']",
]
SEL_LOGIN_PASSWORD = [
    "#password",
    "input[name='password']",
    "input[type='password']",
]
SEL_LOGIN_SUBMIT = [
    "button:has-text('ログイン')",
    "button[type='submit']",
]
SEL_TITLE = [
    "textarea[placeholder*='記事タイトル']",
    "textarea[placeholder*='タイトル']",
    "[data-testid='post-title'] textarea",
    "[data-testid='post-title']",
    "h1[contenteditable='true']",
]
SEL_BODY = [
    "div.ProseMirror[contenteditable='true']",
    "[data-testid='post-body'] [contenteditable='true']",
    "div[contenteditable='true']",
]
SEL_SAVE_DRAFT = [
    "button:has-text('下書き保存')",
    "button:has-text('保存')",
]
SEL_GOTO_PUBLISH = [
    "button:has-text('公開に進む')",
    "button:has-text('公開設定')",
    "a:has-text('公開に進む')",
]
SEL_HASHTAG_INPUT = [
    "input[placeholder*='ハッシュタグ']",
    "input[placeholder*='タグ']",
]
SEL_PUBLISH_CONFIRM = [
    "button:has-text('投稿する')",
    "button:has-text('公開する')",
    "button:has-text('有料エリア設定に進む') >> nth=-1",
]

# ---------------------------------------------------------------------------
# 設定・記事データ
# ---------------------------------------------------------------------------


@dataclass
class Account:
    """投稿先アカウント1件分の設定。"""

    key: str
    urlname: str
    storage_state: Path
    email_env: str | None = None
    password_env: str | None = None
    label: str = ""

    @property
    def profile_url(self) -> str:
        return f"{NOTE_BASE}/{self.urlname}"

    def email(self) -> str | None:
        return os.environ.get(self.email_env) if self.email_env else None

    def password(self) -> str | None:
        return os.environ.get(self.password_env) if self.password_env else None


@dataclass
class Article:
    """投稿する記事1件分のデータ。"""

    title: str
    body: str
    tags: list[str] = field(default_factory=list)
    account: str | None = None
    publish: bool | None = None
    source: Path | None = None


class NotePostError(RuntimeError):
    """このスクリプト由来のエラー。想定内の失敗はすべてこれで投げる。"""


def _rel(path: Path) -> str:
    """表示用にリポジトリルートからの相対パスにする(外部パスはそのまま)。"""
    try:
        return str(Path(path).resolve().relative_to(REPO_ROOT))
    except (ValueError, OSError):
        return str(path)


def load_accounts(config_path: Path) -> tuple[dict[str, Account], str | None]:
    """アカウント設定ファイルを読み込む。

    戻り値は (アカウント辞書, デフォルトアカウントのキー)。
    """
    if not config_path.exists():
        raise NotePostError(
            f"設定ファイルが見つかりません: {config_path}\n"
            f"  {_rel(EXAMPLE_CONFIG_PATH)} をコピーして作成してください。\n"
            f"  cp {_rel(EXAMPLE_CONFIG_PATH)} {_rel(config_path)}"
        )

    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise NotePostError(f"設定ファイルのJSONが壊れています: {config_path}\n  {exc}") from exc

    accounts: dict[str, Account] = {}
    for key, value in (raw.get("accounts") or {}).items():
        urlname = value.get("urlname", key)
        state = value.get("storage_state") or f"secrets/note_{key}_state.json"
        state_path = Path(state)
        if not state_path.is_absolute():
            state_path = REPO_ROOT / state_path
        accounts[key] = Account(
            key=key,
            urlname=urlname,
            storage_state=state_path,
            email_env=value.get("email_env"),
            password_env=value.get("password_env"),
            label=value.get("label", ""),
        )

    if not accounts:
        raise NotePostError(f"設定ファイルに accounts が1件もありません: {config_path}")

    return accounts, raw.get("default_account")


def resolve_account(
    accounts: dict[str, Account], default_key: str | None, requested: str | None
) -> Account:
    """--account / front matter / デフォルト の順でアカウントを決定する。"""
    key = requested or default_key
    if not key:
        raise NotePostError(
            "アカウントが指定されていません。--account を渡すか、"
            "設定ファイルに default_account を書いてください。\n"
            f"  利用可能: {', '.join(sorted(accounts))}"
        )
    if key not in accounts:
        raise NotePostError(
            f"未知のアカウントです: {key}\n  利用可能: {', '.join(sorted(accounts))}"
        )
    return accounts[key]


# ---------------------------------------------------------------------------
# Markdown / front matter
# ---------------------------------------------------------------------------

_FRONT_MATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
_TRUE_VALUES = {"true", "yes", "on", "1", "公開", "public"}
_FALSE_VALUES = {"false", "no", "off", "0", "下書き", "draft"}


def _parse_scalar(value: str):
    """front matter の値をゆるくパースする(YAMLの極小サブセット)。"""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(item) for item in inner.split(",")]
    lowered = value.lower()
    if lowered in _TRUE_VALUES:
        return True
    if lowered in _FALSE_VALUES:
        return False
    return value


def parse_front_matter(text: str) -> tuple[dict, str]:
    """`---` で囲まれた front matter を切り出し、(メタ情報, 本文) を返す。

    PyYAML には依存せず、`key: value` / `- item` 形式のみを扱う。
    """
    match = _FRONT_MATTER_RE.match(text)
    if not match:
        return {}, text

    meta: dict = {}
    current_list_key: str | None = None

    for line in match.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.lstrip().startswith("- ") and current_list_key:
            meta.setdefault(current_list_key, [])
            if not isinstance(meta[current_list_key], list):
                meta[current_list_key] = []
            meta[current_list_key].append(_parse_scalar(line.lstrip()[2:]))
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        if value.strip() == "":
            meta[key] = []
            current_list_key = key
        else:
            meta[key] = _parse_scalar(value)
            current_list_key = None

    return meta, text[match.end() :]


def _normalize_tags(value) -> list[str]:
    """tags を文字列リストに正規化する(先頭の # は落とす)。"""
    if value is None:
        return []
    if isinstance(value, str):
        items = re.split(r"[,\s]+", value)
    elif isinstance(value, list):
        items = [str(item) for item in value]
    else:
        items = [str(value)]
    return [item.strip().lstrip("#") for item in items if item and item.strip().lstrip("#")]


def load_article(path: Path) -> Article:
    """Markdown ファイルを読み込んで Article にする。

    タイトルは front matter の `title`、無ければ本文先頭の `# 見出し` を使う。
    """
    if not path.exists():
        raise NotePostError(f"記事ファイルが見つかりません: {path}")

    text = path.read_text(encoding="utf-8")
    meta, body = parse_front_matter(text)
    body = body.strip("\n")

    title = meta.get("title")
    if not title:
        heading = re.match(r"\A#\s+(.+?)\s*\n", body)
        if heading:
            title = heading.group(1)
            body = body[heading.end() :].lstrip("\n")
    if not title:
        raise NotePostError(
            f"タイトルが決まりません: {path}\n"
            "  front matter に `title: ...` を書くか、本文冒頭を `# タイトル` にしてください。"
        )

    publish = meta.get("publish")
    if publish is None and "status" in meta:
        status = meta["status"]
        # `public` / `公開` などは _parse_scalar の時点で True になっている
        publish = status if isinstance(status, bool) else str(status).lower() in {"publish"}

    return Article(
        title=str(title),
        body=body,
        tags=_normalize_tags(meta.get("tags") or meta.get("hashtags")),
        account=meta.get("account"),
        publish=publish if isinstance(publish, bool) else None,
        source=path,
    )


# ---------------------------------------------------------------------------
# Playwright ヘルパ
# ---------------------------------------------------------------------------


def _import_playwright():
    """Playwright を遅延importする(未インストール時に分かりやすく落とす)。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise NotePostError(
            "playwright がインストールされていません。\n"
            "  pip install playwright && playwright install chromium"
        ) from exc
    return sync_playwright


def _find(page, selectors: list[str], timeout_ms: int = 15000):
    """セレクタ候補を順に試し、最初に表示されたものを返す。"""
    deadline = time.monotonic() + timeout_ms / 1000
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                if locator.count() > 0 and locator.is_visible():
                    return locator
            except Exception as exc:  # ページ遷移中の一時的な失敗は握りつぶす
                last_error = exc
        page.wait_for_timeout(300)
    raise NotePostError(
        "要素が見つかりませんでした。noteのUIが変わった可能性があります。\n"
        f"  試したセレクタ: {selectors}\n"
        "  scripts/note_post.py 冒頭のセレクタ定義を更新してください。"
        + (f"\n  最後のエラー: {last_error}" if last_error else "")
    )


def _shot(page, shot_dir: Path | None, name: str) -> None:
    """デバッグ用スクリーンショット。--screenshot-dir 指定時のみ動く。"""
    if not shot_dir:
        return
    shot_dir.mkdir(parents=True, exist_ok=True)
    path = shot_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{name}.png"
    try:
        page.screenshot(path=str(path), full_page=True)
        print(f"  [shot] {path}")
    except Exception as exc:
        print(f"  [shot] 撮影に失敗: {exc}", file=sys.stderr)


def _is_logged_out(page) -> bool:
    """ログイン画面に飛ばされていないかを判定する。"""
    return "/login" in page.url or "/signup" in page.url


# ---------------------------------------------------------------------------
# login コマンド
# ---------------------------------------------------------------------------


def cmd_login(args, accounts: dict[str, Account], default_key: str | None) -> int:
    account = resolve_account(accounts, default_key, args.account)
    sync_playwright = _import_playwright()

    email = account.email()
    password = account.password()
    manual = args.manual or not (email and password)

    if manual:
        print(f"[{account.key}] 手動ログインモードでブラウザを開きます。")
        print("  表示されたブラウザで note にログインしてください(2段階認証もここで通します)。")
        print("  ログインが完了したら、この画面で Enter を押してください。")
    else:
        print(f"[{account.key}] {account.email_env} / {account.password_env} でログインします。")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not manual and args.headless)
        context = browser.new_context()
        page = context.new_page()
        page.goto(LOGIN_URL, wait_until="domcontentloaded")

        if manual:
            input("  ログイン完了後に Enter → ")
        else:
            _find(page, SEL_LOGIN_EMAIL).fill(email)
            _find(page, SEL_LOGIN_PASSWORD).fill(password)
            _find(page, SEL_LOGIN_SUBMIT).click()
            page.wait_for_timeout(5000)

        page.goto(f"{NOTE_BASE}/settings/account", wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        if _is_logged_out(page):
            _shot(page, args.screenshot_dir, f"{account.key}-login-failed")
            browser.close()
            raise NotePostError(
                "ログインが完了していません。--manual を付けて手動でログインしてください。"
            )

        account.storage_state.parent.mkdir(parents=True, exist_ok=True)
        context.storage_state(path=str(account.storage_state))
        browser.close()

    try:
        account.storage_state.chmod(0o600)
    except OSError:
        pass

    print(f"[{account.key}] ログイン状態を保存しました: {_rel(account.storage_state)}")
    print("  このファイルは実質パスワードです。git に含めないでください(.gitignore 済み)。")
    return 0


# ---------------------------------------------------------------------------
# post コマンド
# ---------------------------------------------------------------------------


def _type_body(page, body_locator, body: str, delay_ms: int, use_markdown: bool) -> None:
    """本文を1行ずつ入力する。

    note のエディタは `## ` や `- ` などの Markdown ショートカットに反応するため、
    素の Markdown をそのまま打ち込むと見出し・リストに変換される。
    変換させたくない場合は --no-markdown-shortcut を使う(記号を除去して入力)。
    """
    body_locator.click()
    lines = body.split("\n")
    for index, line in enumerate(lines):
        text = line
        if not use_markdown:
            text = re.sub(r"^\s{0,3}(#{1,6}\s+|[-*+]\s+|>\s+)", "", text)
        if index > 0:
            page.keyboard.press("Enter")
            # 直前がリスト項目で今回が空行なら、もう一度 Enter でリストを抜ける
            prev = lines[index - 1].lstrip()
            if not text.strip() and re.match(r"^([-*+]\s+|\d+\.\s+|>\s+)", prev):
                page.keyboard.press("Enter")
        if text:
            page.keyboard.type(text, delay=delay_ms)


def _add_tags(page, tags: list[str], shot_dir: Path | None) -> None:
    """公開設定画面でハッシュタグを入力する。失敗しても投稿自体は止めない。"""
    if not tags:
        return
    try:
        tag_input = _find(page, SEL_HASHTAG_INPUT, timeout_ms=8000)
    except NotePostError:
        print("  [warn] ハッシュタグ入力欄が見つかりませんでした。タグはスキップします。")
        _shot(page, shot_dir, "tag-input-not-found")
        return
    for tag in tags:
        tag_input.click()
        page.keyboard.type(tag, delay=30)
        page.keyboard.press("Enter")
        page.wait_for_timeout(400)
    print(f"  タグを追加: {', '.join(tags)}")


def cmd_post(args, accounts: dict[str, Account], default_key: str | None) -> int:
    article = load_article(Path(args.file))
    account = resolve_account(accounts, default_key, args.account or article.account)

    # 公開するかどうかは CLI > front matter > 既定(下書き) の優先順
    if args.publish:
        publish = True
    elif args.draft:
        publish = False
    else:
        publish = bool(article.publish)

    print(f"アカウント : {account.key} ({account.profile_url})")
    print(f"記事ファイル: {article.source}")
    print(f"タイトル   : {article.title}")
    print(f"タグ       : {', '.join(article.tags) or '(なし)'}")
    print(f"本文       : {len(article.body)}文字 / {len(article.body.splitlines())}行")
    print(f"モード     : {'公開' if publish else '下書き保存'}")

    if args.dry_run:
        print("\n--dry-run のため、ブラウザは起動しません。")
        print("--- 本文プレビュー(先頭20行) ---")
        for line in article.body.splitlines()[:20]:
            print(f"| {line}")
        return 0

    if not account.storage_state.exists():
        raise NotePostError(
            f"ログイン状態のファイルがありません: {account.storage_state}\n"
            f"  先に実行してください: "
            f"python scripts/note_post.py login --account {account.key} --manual"
        )

    sync_playwright = _import_playwright()
    shot_dir = Path(args.screenshot_dir) if args.screenshot_dir else None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed, slow_mo=args.slow_mo)
        context = browser.new_context(storage_state=str(account.storage_state))
        page = context.new_page()
        page.set_default_timeout(30000)

        try:
            print("\nエディタを開いています...")
            page.goto(NEW_NOTE_URL, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)

            if _is_logged_out(page):
                _shot(page, shot_dir, f"{account.key}-session-expired")
                raise NotePostError(
                    f"[{account.key}] ログインセッションが切れています。\n"
                    f"  再ログイン: python scripts/note_post.py login "
                    f"--account {account.key} --manual"
                )

            print("タイトルを入力しています...")
            title_locator = _find(page, SEL_TITLE)
            title_locator.click()
            page.keyboard.type(article.title, delay=args.typing_delay)
            page.wait_for_timeout(500)

            print("本文を入力しています...")
            body_locator = _find(page, SEL_BODY)
            _type_body(
                page,
                body_locator,
                article.body,
                args.typing_delay,
                not args.no_markdown_shortcut,
            )
            page.wait_for_timeout(2000)
            _shot(page, shot_dir, f"{account.key}-editor")

            editor_url = page.url

            if not publish:
                print("下書きとして保存しています...")
                try:
                    _find(page, SEL_SAVE_DRAFT, timeout_ms=8000).click()
                    page.wait_for_timeout(3000)
                except NotePostError:
                    # note は自動保存されるため、保存ボタンが無くても致命傷ではない
                    print("  [warn] 下書き保存ボタンが見つかりません。自動保存に任せます。")
                    page.wait_for_timeout(3000)
                _shot(page, shot_dir, f"{account.key}-draft-saved")
                print(f"\n下書きを保存しました: {editor_url}")
            else:
                print("公開設定画面に進みます...")
                _find(page, SEL_GOTO_PUBLISH).click()
                page.wait_for_timeout(3000)
                _shot(page, shot_dir, f"{account.key}-publish-settings")

                _add_tags(page, article.tags, shot_dir)

                print("公開しています...")
                _find(page, SEL_PUBLISH_CONFIRM).click()
                page.wait_for_timeout(6000)
                _shot(page, shot_dir, f"{account.key}-published")
                print(f"\n公開しました: {page.url}")

            if args.keep_open:
                input("ブラウザを開いたままにしています。Enter で閉じます → ")

        except Exception:
            _shot(page, shot_dir, f"{account.key}-error")
            raise
        finally:
            browser.close()

    return 0


# ---------------------------------------------------------------------------
# accounts コマンド
# ---------------------------------------------------------------------------


def cmd_accounts(args, accounts: dict[str, Account], default_key: str | None) -> int:
    print(f"設定ファイル: {args.config}")
    for key in sorted(accounts):
        account = accounts[key]
        mark = " (default)" if key == default_key else ""
        state = "ログイン済み" if account.storage_state.exists() else "未ログイン"
        label = f" — {account.label}" if account.label else ""
        print(f"\n- {key}{mark}{label}")
        print(f"    プロフィール: {account.profile_url}")
        print(f"    セッション  : {account.storage_state} [{state}]")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="note_post.py",
        description="note.com に Markdown 記事を自動投稿する(複数アカウント対応)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"アカウント設定ファイル (既定: {DEFAULT_CONFIG_PATH})",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_login = sub.add_parser("login", help="ログインして storage_state を保存する")
    p_login.add_argument("--account", help="アカウントキー")
    p_login.add_argument(
        "--manual",
        action="store_true",
        help="ブラウザを開いて手動でログインする(2段階認証はこちら)",
    )
    p_login.add_argument("--headless", action="store_true", help="自動ログイン時にヘッドレス実行")
    p_login.add_argument("--screenshot-dir", type=Path, help="失敗時のスクリーンショット出力先")
    p_login.set_defaults(func=cmd_login)

    p_post = sub.add_parser("post", help="Markdown を note に投稿する")
    p_post.add_argument("--account", help="アカウントキー(未指定なら front matter / 既定値)")
    p_post.add_argument("--file", required=True, help="投稿する Markdown ファイル")
    p_post.add_argument("--publish", action="store_true", help="下書きではなく公開する")
    p_post.add_argument("--draft", action="store_true", help="front matter を無視して下書きにする")
    p_post.add_argument("--dry-run", action="store_true", help="ブラウザを起動せず内容だけ表示")
    p_post.add_argument("--headed", action="store_true", help="ブラウザを表示して実行する")
    p_post.add_argument("--slow-mo", type=int, default=0, help="操作間の待ち(ms)。デバッグ用")
    p_post.add_argument("--typing-delay", type=int, default=8, help="1文字あたりの入力遅延(ms)")
    p_post.add_argument(
        "--no-markdown-shortcut",
        action="store_true",
        help="`#` や `-` を除去して素のテキストとして入力する",
    )
    p_post.add_argument("--screenshot-dir", type=Path, help="各段階のスクリーンショット出力先")
    p_post.add_argument("--keep-open", action="store_true", help="完了後もブラウザを閉じない")
    p_post.set_defaults(func=cmd_post)

    p_accounts = sub.add_parser("accounts", help="設定済みアカウントを一覧表示する")
    p_accounts.set_defaults(func=cmd_accounts)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        accounts, default_key = load_accounts(args.config)
        return args.func(args, accounts, default_key)
    except NotePostError as exc:
        print(f"\nエラー: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n中断しました。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
