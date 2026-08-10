#!/usr/bin/env python3
"""note.com/19770104 への記事投稿スクリプト。

note には公開された投稿APIが存在しないため、Playwright で実ブラウザを操作する。
ログイン状態は storage_state(ログイン済みcookie)としてファイルに保存する。

このリポジトリは 19770104 専用。設定はアカウントキーで引く作りになっているが、
公開前チェック(note_lint.py)は医療・リハビリのテーマ前提であり、
他ジャンルのアカウントにそのまま使うと誤検出で止まる。
運用方針は docs/note-strategy.md、使い方は docs/note-auto-post.md を参照。

  # 1. 一度だけログインして storage_state を作る
  python scripts/note_post.py login --manual

  # 2. Markdown を下書きとして保存する
  python scripts/note_post.py post --file posts/example.md

  # 3. 既存記事を上書きする
  python scripts/note_post.py update --file posts/example.md
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import note_lint  # noqa: E402  同じ scripts/ に置いた公開前チェッカー

# ---------------------------------------------------------------------------
# パス定義
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "note_accounts.json"
EXAMPLE_CONFIG_PATH = REPO_ROOT / "config" / "note_accounts.example.json"

NOTE_BASE = "https://note.com"
# 記事の編集画面は editor.note.com 側にある(末尾スラッシュまで含めて必要)
EDITOR_BASE = "https://editor.note.com"
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
# 先頭の候補は、実際の編集画面で確認が取れているもの
SEL_TITLE = [
    "textarea[placeholder='記事タイトル']",
    "textarea[placeholder*='記事タイトル']",
    "textarea[placeholder*='タイトル']",
    "[data-testid='post-title'] textarea",
    "h1[contenteditable='true']",
]
SEL_BODY = [
    "div[contenteditable='true'][role='textbox']",
    "div.ProseMirror[contenteditable='true']",
    "[data-testid='post-body'] [contenteditable='true']",
    "div[contenteditable='true']",
]
# 記事によって「一時保存」と「下書き保存」の両方がありうる。
# ボタンとして role が取れない場合があるため、テキスト一致も候補に入れる。
SEL_SAVE_DRAFT = [
    "button:has-text('下書き保存')",
    "button:has-text('一時保存')",
    "text='下書き保存'",
    "text='一時保存'",
    "button:has-text('保存')",
]
# 保存できたことを示す表示
SEL_SAVED_NOTICE = [
    "text=下書きを保存しました",
    "text=保存済み",
    "text=保存しました",
]
# 「複数画面で編集されています。どちらを保存しますか?」の競合ダイアログ。
# 初期選択は「別の画面」= 古い原稿。放置すると今回の変更が捨てられる。
SEL_CONFLICT_DIALOG = [
    "text=複数画面で編集",
    "[role='dialog']:has-text('どちらを保存')",
]
SEL_CONFLICT_CURRENT = [
    "[role='checkbox']:has-text('現在の画面')",
    "label:has-text('現在の画面')",
    "text=現在の画面",
]
SEL_CONFLICT_SAVE = [
    "button:has-text('保存する')",
]
# ログイン中アカウントをDOMから拾うときに、urlname ではないパスを除外するための一覧
NON_URLNAME_PATHS = {
    "notes", "settings", "search", "hashtag", "membership", "magazines",
    "login", "signup", "logout", "help", "about", "sitemap", "contest",
    "info", "premium", "store", "mypage", "following", "followers", "n",
}
SEL_UPDATE_PUBLISHED = [
    "button:has-text('更新する')",
    "button:has-text('公開設定を変更')",
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

# doctor コマンドが「どの候補が実際に当たったか」を報告するための対応表
SELECTOR_GROUPS = {
    "SEL_TITLE": SEL_TITLE,
    "SEL_BODY": SEL_BODY,
    "SEL_SAVE_DRAFT": SEL_SAVE_DRAFT,
    "SEL_GOTO_PUBLISH": SEL_GOTO_PUBLISH,
    "SEL_HASHTAG_INPUT": SEL_HASHTAG_INPUT,
    "SEL_PUBLISH_CONFIRM": SEL_PUBLISH_CONFIRM,
    "SEL_UPDATE_PUBLISHED": SEL_UPDATE_PUBLISHED,
}
# 保存後にしか出ないため、doctor では「なし」で正常
SELECTOR_GROUPS_TRANSIENT = {
    "SEL_SAVED_NOTICE": SEL_SAVED_NOTICE,
    "SEL_CONFLICT_DIALOG": SEL_CONFLICT_DIALOG,
}

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
    # 医療テーマのアカウントは事故防止のため既定で公開操作を禁止する
    allow_publish: bool = False

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
    # 既存記事を上書きする場合の note のURL(またはキー)
    note_url: str | None = None


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
            allow_publish=bool(value.get("allow_publish", False)),
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


def _parse_scalar(value: str, coerce_bool: bool = True):
    """front matter の値をゆるくパースする(YAMLの極小サブセット)。

    リストの要素(タグなど)は文字列のまま扱う。`公開` や `draft` といった語を
    タグに使ったときに真偽値へ化けるのを避けるため、coerce_bool=False で呼ぶ。
    """
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(item, coerce_bool=False) for item in inner.split(",")]
    if coerce_bool:
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
            meta[current_list_key].append(
                _parse_scalar(line.lstrip()[2:], coerce_bool=False)
            )
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
    # 原稿中のメモ(HTMLコメント)は note へ流し込まない
    body = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL)
    body = re.sub(r"\n{3,}", "\n\n", body).strip("\n")

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

    note_url = meta.get("note_url") or meta.get("note_key")

    return Article(
        title=str(title),
        body=body,
        tags=_normalize_tags(meta.get("tags") or meta.get("hashtags")),
        account=meta.get("account"),
        publish=publish if isinstance(publish, bool) else None,
        source=path,
        note_url=str(note_url) if note_url else None,
    )


# ---------------------------------------------------------------------------
# Markdown → note の本文HTML
#
# note のエディタ(ProseMirror)には HTML をクリップボード経由で貼り付ける。
# 1文字ずつ打ち込んで Markdown ショートカットの自動変換に期待する方式は、
# 変換されるとは限らず、遅い。HTML で渡せば見出し・リスト・リンクが確実に残る。
# ---------------------------------------------------------------------------


def _inline_md(text: str) -> str:
    """行内の記法を HTML にする。エスケープしてからリンクを組み立てる。"""
    escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(
        r"\[([^\]]+)\]\((https?://[^)]+)\)", r'<a href="\2">\1</a>', escaped
    )


def markdown_to_note_html(markdown: str) -> str:
    """本文の Markdown を、note へ貼り付けるための HTML に変換する。

    対応するのは見出し(## / ###)、引用、箇条書き、番号付きリスト、段落、リンク。
    画像・表・コードブロックには対応しない。

    **1行 = 1段落として扱う。** CommonMark は連続する行を1つの段落に結合するが、
    ここでは結合しない。note のエディタに段落内改行の概念が無く、Enter が常に
    新しいブロックを作るため、原稿の1行をそのまま1ブロックに対応させたほうが
    書き手の見たままになる。実際、
        制定：2020年11月14日
        最終改訂：2026年8月4日
    のような並びを結合すると1行に潰れてしまう。
    段落を分けたくない文は、原稿側でも1行にまとめること。
    """
    output: list[str] = []
    current_list: str | None = None

    def close_list() -> None:
        nonlocal current_list
        if current_list:
            output.append(f"</{current_list}>")
            current_list = None

    def open_list(kind: str) -> None:
        nonlocal current_list
        if current_list != kind:
            close_list()
            current_list = kind
            output.append(f"<{kind}>")

    for line in markdown.split("\n"):
        if not line.strip():
            close_list()
            continue

        heading = re.match(r"^(#{2,3})\s+(.+)", line)
        if heading:
            close_list()
            level = len(heading.group(1))
            output.append(f"<h{level}>{_inline_md(heading[2])}</h{level}>")
            continue

        quote = re.match(r"^>\s?(.*)", line)
        if quote:
            close_list()
            output.append(f"<blockquote><p>{_inline_md(quote[1])}</p></blockquote>")
            continue

        unordered = re.match(r"^\s*[-*+]\s+(.+)", line)
        if unordered:
            open_list("ul")
            output.append(f"<li><p>{_inline_md(unordered[1])}</p></li>")
            continue

        ordered = re.match(r"^\s*\d+\.\s+(.+)", line)
        if ordered:
            open_list("ol")
            output.append(f"<li><p>{_inline_md(ordered[1])}</p></li>")
            continue

        close_list()
        output.append(f"<p>{_inline_md(line)}</p>")

    close_list()
    return "".join(output)


def note_edit_url(url_or_key: str) -> tuple[str, str | None]:
    """note の記事URLまたはキーから、(編集画面のURL, アカウント名) を返す。

    アカウント名は公開URL形式のときだけ取れる。取れない形式では None を返し、
    アカウントの照合はログイン中アカウントの検証(_verify_account)に委ねる。

    受け付ける形:
      https://note.com/19770104/n/nedb0aaf045b1        (公開URL → urlname あり)
      https://editor.note.com/notes/nedb0aaf045b1/edit/ (編集URL → urlname なし)
      nedb0aaf045b1                                    (記事キー → urlname なし)
    """
    value = url_or_key.strip().split("?")[0].rstrip("/")

    public = re.search(r"note\.com/([0-9A-Za-z_-]+)/n/(n[0-9a-z]+)", value)
    if public and public.group(1) != "notes":
        return f"{EDITOR_BASE}/notes/{public.group(2)}/edit/", public.group(1)

    match = re.search(r"/n(?:otes)?/(n[0-9a-z]+)", value)
    if match:
        key = match.group(1)
    elif re.fullmatch(r"n[0-9a-z]+", value):
        key = value
    else:
        raise NotePostError(
            f"note の記事URL/キーとして解釈できません: {url_or_key}\n"
            "  例: https://note.com/19770104/n/nedb0aaf045b1 または nedb0aaf045b1"
        )
    return f"{EDITOR_BASE}/notes/{key}/edit/", None


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
    """セレクタ候補を順に試し、最初に表示されたものを返す。

    候補が複数の要素に当たった場合は警告する。黙って先頭を掴むと、
    意図しない要素へ書き込む事故につながるため。
    """
    deadline = time.monotonic() + timeout_ms / 1000
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        for selector in selectors:
            try:
                count = page.locator(selector).count()
                if count == 0:
                    continue
                locator = page.locator(selector).first
                if not locator.is_visible():
                    continue
                if count > 1:
                    print(
                        f"  [warn] {selector} が {count} 件に一致しました。"
                        "先頭を使いますが、対象が正しいか確認してください。"
                    )
                return locator
            except Exception as exc:  # ページ遷移中の一時的な失敗は握りつぶす
                last_error = exc
        page.wait_for_timeout(300)
    raise NotePostError(
        "要素が見つかりませんでした。noteのUIが変わった可能性があります。\n"
        f"  試したセレクタ: {selectors}\n"
        + (f"  最後のエラー: {last_error}\n" if last_error else "")
        + "\n  いま画面にあるもの:\n"
        + _format_elements(_collect_elements(page), limit=8)
        + "\n\n  scripts/note_post.py 冒頭のセレクタ定義を、上の内容に合わせて更新してください。\n"
        "  詳しい状況は次で採取できます: python scripts/note_post.py doctor"
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
# 画面の実物を調べる(セレクタがズレたときの復旧用)
# ---------------------------------------------------------------------------

# 画面上の操作可能な要素を集める。data-testid と class は、セレクタを直すときの手がかり。
_COLLECT_ELEMENTS_JS = """
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const describe = (el) => ({
    tag: el.tagName.toLowerCase(),
    text: (el.innerText || el.value || '').trim().slice(0, 40),
    testid: el.getAttribute('data-testid') || '',
    placeholder: el.getAttribute('placeholder') || '',
    cls: (el.getAttribute('class') || '').slice(0, 70),
  });
  const pick = (selector, limit) =>
    [...document.querySelectorAll(selector)].filter(visible).slice(0, limit).map(describe);
  return {
    buttons: pick('button, [role="button"]', 40),
    inputs: pick('input, textarea', 20),
    editables: pick('[contenteditable="true"]', 10),
  };
}
"""


def _collect_elements(page) -> dict:
    """画面上の操作可能な要素を集める。失敗しても空で返す。"""
    try:
        return page.evaluate(_COLLECT_ELEMENTS_JS) or {}
    except Exception:
        return {}


def _format_elements(elements: dict, limit: int = 12) -> str:
    """集めた要素を、そのまま貼れる形に整える。"""
    if not elements:
        return "  (画面の要素を取得できませんでした)"

    lines: list[str] = []
    labels = {"buttons": "ボタン", "inputs": "入力欄", "editables": "編集領域"}
    for kind, label in labels.items():
        items = elements.get(kind) or []
        if not items:
            continue
        lines.append(f"  [{label}] {len(items)}件")
        for item in items[:limit]:
            parts = [f"<{item['tag']}>"]
            if item.get("text"):
                parts.append(f"「{item['text']}」")
            if item.get("placeholder"):
                parts.append(f"placeholder={item['placeholder']}")
            if item.get("testid"):
                parts.append(f"data-testid={item['testid']}")
            if item.get("cls"):
                parts.append(f"class={item['cls']}")
            lines.append("    - " + " ".join(parts))
        if len(items) > limit:
            lines.append(f"    - ...他 {len(items) - limit} 件")
    return "\n".join(lines) if lines else "  (操作できる要素が見つかりませんでした)"


def _match_selector_groups(page, groups: dict | None = None) -> list[tuple[str, str | None]]:
    """定義済みセレクタ候補のうち、実際に当たったものを調べる。"""
    results: list[tuple[str, str | None]] = []
    for name, selectors in (groups or SELECTOR_GROUPS).items():
        hit = None
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                if locator.count() > 0 and locator.is_visible():
                    hit = selector
                    break
            except Exception:
                continue
        results.append((name, hit))
    return results


# ---------------------------------------------------------------------------
# アカウント同一性の検証
#
# 設定ファイルに書いたアカウントと、note 側で実際にログインしているアカウントは
# 別物になりうる。取り違えたまま書き込むと他人の記事を壊すため、必ず照合する。
# ---------------------------------------------------------------------------


def _dig_urlname(payload):
    """current_user のレスポンスから urlname を探す(形が変わっても拾えるように)。"""
    if isinstance(payload, dict):
        value = payload.get("urlname")
        if isinstance(value, str) and value:
            return value
        for key in ("data", "user", "current_user"):
            if key in payload:
                found = _dig_urlname(payload[key])
                if found:
                    return found
    return None


def _current_urlname_from_api(context) -> str | None:
    """note の current_user から、ログイン中アカウントの urlname を取得する。

    ブラウザコンテキストの cookie を共有する APIRequestContext を使うため、
    ページ遷移なしに読める。読み取り専用の照合にしか使わない。
    """
    try:
        response = context.request.get(f"{NOTE_BASE}/api/v2/current_user", timeout=15000)
        if not response.ok:
            return None
        return _dig_urlname(response.json())
    except Exception:
        return None


def _current_urlname_from_dom(page) -> str | None:
    """DOM からログイン中アカウントの urlname を拾う(APIが使えないときの代替)。"""
    try:
        page.goto(NOTE_BASE, wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        hrefs = page.eval_on_selector_all(
            "header a[href], nav a[href]", "els => els.map(e => e.getAttribute('href'))"
        )
    except Exception:
        return None

    for href in hrefs or []:
        match = re.fullmatch(r"/([0-9A-Za-z_-]+)", (href or "").split("?")[0].rstrip("/"))
        if match and match.group(1) not in NON_URLNAME_PATHS:
            return match.group(1)
    return None


def _verify_account(context, page, account: Account, allow_unverified: bool) -> str | None:
    """ログイン中アカウントが設定と一致するかを確かめる。

    一致すれば urlname を返す。不一致なら中断する。
    判定できなかった場合も既定では中断する(警告だけでは取り違えを防げないため)。
    """
    actual = _current_urlname_from_api(context) or _current_urlname_from_dom(page)

    if actual is None:
        message = (
            "ログイン中のアカウントを確認できませんでした。\n"
            "  セッション切れか、note 側の仕様変更の可能性があります。\n"
            f"  再ログイン: python scripts/note_post.py login --account {account.key} --manual\n"
            "  確認済みのうえで進める場合は --allow-unverified を付けます。"
        )
        if not allow_unverified:
            raise NotePostError(message)
        print(f"\n[warn] {message}")
        return None

    if actual != account.urlname:
        raise NotePostError(
            "アカウントが一致しません。書き込みを中止しました。\n"
            f"  設定 ({account.key}) : {account.urlname}\n"
            f"  実際のログイン       : {actual}\n"
            f"  {_rel(account.storage_state)} に別アカウントのセッションが入っています。\n"
            f"  正しいアカウントでログインし直してください: "
            f"python scripts/note_post.py login --account {account.key} --manual"
        )

    print(f"  ログイン中のアカウント: {actual} (設定と一致)")
    return actual


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

        # 別アカウントのセッションを保存してしまわないよう、保存前に照合する
        print("ログインしたアカウントを確認しています...")
        try:
            _verify_account(context, page, account, args.allow_unverified)
        except NotePostError:
            _shot(page, args.screenshot_dir, f"{account.key}-account-mismatch")
            browser.close()
            raise

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


# クリップボードに HTML とプレーンテキストの両方を載せる
_CLIPBOARD_WRITE_JS = """
async ([html, plain]) => {
  await navigator.clipboard.write([
    new ClipboardItem({
      'text/html': new Blob([html], { type: 'text/html' }),
      'text/plain': new Blob([plain], { type: 'text/plain' }),
    }),
  ]);
  return true;
}
"""

# クリップボードが使えない環境向け。paste イベントを直接起こす。
# ProseMirror は paste を拾うため、innerHTML の直接代入より安全。
_DISPATCH_PASTE_JS = """
([selector, html, plain]) => {
  const target = document.querySelector(selector);
  if (!target) return false;
  const data = new DataTransfer();
  data.setData('text/html', html);
  data.setData('text/plain', plain);
  target.focus();
  const event = new ClipboardEvent('paste', {
    clipboardData: data, bubbles: true, cancelable: true,
  });
  return target.dispatchEvent(event);
}
"""


def _paste_body(page, body_locator, html: str, plain: str, mode: str) -> str:
    """本文欄の中身を、HTML を貼り付けて置き換える。

    1文字ずつ打ち込む方式はやめた。note のエディタが Markdown 記法を
    自動変換するとは限らず、見出しやリストが崩れるため。
    戻り値は実際に使った方式。
    """
    body_locator.click()
    page.wait_for_timeout(300)

    if mode in ("auto", "clipboard"):
        try:
            page.evaluate(_CLIPBOARD_WRITE_JS, [html, plain])
            body_locator.press("ControlOrMeta+A")
            body_locator.press("ControlOrMeta+V")
            page.wait_for_timeout(1500)
            return "clipboard"
        except Exception as exc:
            if mode == "clipboard":
                raise NotePostError(f"クリップボードへの書き込みに失敗しました: {exc}") from exc
            print(f"  [warn] クリップボードが使えないため paste イベントに切り替えます: {exc}")

    # 全選択してから paste イベントを起こし、選択範囲を置き換える
    body_locator.press("ControlOrMeta+A")
    selector = SEL_BODY[0]
    for candidate in SEL_BODY:
        if page.locator(candidate).count() > 0:
            selector = candidate
            break
    if not page.evaluate(_DISPATCH_PASTE_JS, [selector, html, plain]):
        raise NotePostError(
            "本文の貼り付けに失敗しました。\n"
            "  --paste-mode clipboard を試すか、--headed で画面を確認してください。"
        )
    page.wait_for_timeout(1500)
    return "paste-event"


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


def _run_lint(article: Article, args) -> list:
    """公開前チェックを実行し、必要なら確認票を書き出す。

    重大な指摘があれば例外で止める(--force で続行可能)。
    """
    if getattr(args, "skip_check", False):
        print("\n[warn] --skip-check のため公開前チェックを飛ばしました。")
        return []

    findings = note_lint.lint(article.title, article.body)
    print("\n--- 公開前チェック ---")
    print(note_lint.format_findings(findings))

    review_dir = getattr(args, "review_sheet_dir", None)
    if review_dir:
        review_dir = Path(review_dir)
        review_dir.mkdir(parents=True, exist_ok=True)
        stem = article.source.stem if article.source else "article"
        sheet = review_dir / f"{stem}-review.md"
        sheet.write_text(
            note_lint.render_review_sheet(
                article.title, findings, str(article.source or "")
            ),
            encoding="utf-8",
        )
        print(f"\n公開前確認票: {_rel(sheet)}")

    worst = note_lint.worst_severity(findings)
    if worst == "high" and not getattr(args, "force", False):
        raise NotePostError(
            "重大な指摘があるため中断しました。原稿を直してから再実行してください。\n"
            "  内容を確認したうえで進める場合は --force を付けます。"
        )
    if worst == "medium" and getattr(args, "strict", False):
        raise NotePostError("--strict のため、要確認レベルの指摘で中断しました。")
    return findings


def _resolve_publish(args, article: Article, account: Account) -> bool:
    """公開するかどうかを決める。既定は下書き。

    優先順は --draft > --publish > front matter > 既定(下書き)。
    アカウント設定で allow_publish が false なら公開操作自体を拒否する。
    """
    if args.draft:
        return False
    publish = bool(args.publish or article.publish)
    if not publish:
        return False
    if not account.allow_publish:
        raise NotePostError(
            f"アカウント {account.key} は公開操作が無効です(allow_publish: false)。\n"
            "  医療テーマのため、既定では下書き保存までしか行いません。\n"
            "  公開まで自動化する場合は config/note_accounts.json で "
            '"allow_publish": true にしてください。'
        )
    if not args.yes:
        # 対話できない環境(cron、CI、リダイレクト)では確認を取れない。
        # 黙って公開してしまわないよう、--yes の明示を必須にする。
        if not sys.stdin.isatty():
            raise NotePostError(
                "対話できない環境では、確認なしに公開できません。\n"
                f"  対象: 「{article.title}」\n"
                "  意図した公開であれば --yes を付けてください。"
            )
        answer = input(f"\n本当に公開しますか? 「{article.title}」 [y/N] → ")
        if answer.strip().lower() not in {"y", "yes"}:
            raise NotePostError("公開を中止しました。")
    return True


def _open_editor(page, account: Account, target_url: str, shot_dir: Path | None):
    """エディタを開き、ログイン切れを検出する。"""
    page.goto(target_url, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    if _is_logged_out(page):
        _shot(page, shot_dir, f"{account.key}-session-expired")
        raise NotePostError(
            f"[{account.key}] ログインセッションが切れています。\n"
            f"  再ログイン: python scripts/note_post.py login "
            f"--account {account.key} --manual"
        )


def _write_article(page, article: Article, args) -> None:
    """タイトルと本文をエディタに流し込む。既存の内容は置き換わる。"""
    print("タイトルを入力しています...")
    _find(page, SEL_TITLE).fill(article.title)
    page.wait_for_timeout(500)

    print("本文を貼り付けています...")
    body_locator = _find(page, SEL_BODY)
    html = markdown_to_note_html(article.body)
    method = _paste_body(page, body_locator, html, article.body, args.paste_mode)
    print(f"  貼り付け方式: {method}")


# 書き込み結果を読み戻す。目視の前に、崩れていないかを機械的に確かめる。
_INSPECT_JS = """
([titleSelector, bodySelector]) => {
  const title = document.querySelector(titleSelector);
  const body = document.querySelector(bodySelector);
  if (!body) return null;
  const text = body.innerText || '';
  return {
    title: title ? (title.value ?? title.innerText) : null,
    chars: text.length,
    h2: body.querySelectorAll('h2').length,
    h3: body.querySelectorAll('h3').length,
    li: body.querySelectorAll('li').length,
    links: body.querySelectorAll('a').length,
    watashi: (text.match(/私は/g) || []).length,
    head: text.slice(0, 80),
    tail: text.slice(-80),
  };
}
"""


def _inspect_written(page, article: Article) -> dict | None:
    """貼り付け後の内容を読み戻して要約を返す。"""
    title_selector = next(
        (s for s in SEL_TITLE if page.locator(s).count() > 0), SEL_TITLE[0]
    )
    body_selector = next(
        (s for s in SEL_BODY if page.locator(s).count() > 0), SEL_BODY[0]
    )
    try:
        return page.evaluate(_INSPECT_JS, [title_selector, body_selector])
    except Exception as exc:
        print(f"  [warn] 書き込み結果を読み戻せませんでした: {exc}")
        return None


def _report_written(page, article: Article) -> None:
    """読み戻した内容を表示し、明らかにおかしい場合は警告する。"""
    result = _inspect_written(page, article)
    if not result:
        return

    expected_html = markdown_to_note_html(article.body)
    print("\n--- 書き込み結果 ---")
    print(f"  タイトル : {result.get('title')}")
    print(
        f"  本文     : {result.get('chars')}文字 / "
        f"h2 {result.get('h2')} / h3 {result.get('h3')} / "
        f"li {result.get('li')} / リンク {result.get('links')}"
    )
    print(f"  「私は」 : {result.get('watashi')}箇所")
    print(f"  冒頭     : {result.get('head')}")
    print(f"  末尾     : {result.get('tail')}")

    if result.get("title") != article.title:
        print("  [warn] タイトルが原稿と一致しません。")
    if not result.get("chars"):
        print("  [warn] 本文が空です。貼り付けに失敗しています。")
    for tag in ("h2", "h3", "li", "a"):
        key = "links" if tag == "a" else tag
        expected = expected_html.count(f"<{tag}>" if tag != "a" else "<a ")
        actual = result.get(key) or 0
        if expected and actual < expected:
            print(f"  [warn] {tag} が {expected} 個のはずが {actual} 個です。")


def _handle_save_conflict(page, shot_dir: Path | None) -> bool:
    """「複数画面で編集されています」の競合ダイアログを処理する。

    初期選択は「別の画面」= 保存前の古い原稿。放置すると今回の変更が捨てられるため、
    「現在の画面」を選び直してから保存する。処理したら True を返す。
    """
    if not _has_any(page, SEL_CONFLICT_DIALOG):
        return False

    print("  競合ダイアログが出ました。「現在の画面」を選び直します...")
    _shot(page, shot_dir, "save-conflict")

    # ラベルに日時と文字数が入るため、完全一致では拾えない
    current = None
    for selector in SEL_CONFLICT_CURRENT:
        locator = page.locator(selector).first
        try:
            if locator.count() > 0 and locator.is_visible():
                current = locator
                break
        except Exception:
            continue
    if current is None:
        raise NotePostError(
            "競合ダイアログの「現在の画面」を選べませんでした。中止します。\n"
            "  このまま保存すると、今回の変更ではなく古い原稿が残ります。\n"
            "  --headed で画面を開き、手動で「現在の画面」を選んで保存してください。\n\n"
            "  いま画面にあるもの:\n" + _format_elements(_collect_elements(page), limit=10)
        )

    try:
        current.check()
    except Exception:
        current.click()
    page.wait_for_timeout(500)

    _find(page, SEL_CONFLICT_SAVE, timeout_ms=8000).click()
    page.wait_for_timeout(3000)

    if _has_any(page, SEL_CONFLICT_DIALOG):
        raise NotePostError("競合ダイアログが閉じませんでした。手動で確認してください。")
    return True


def _save_draft(
    page, account: Account, shot_dir: Path | None, allow_unverified: bool = False
) -> None:
    """下書きとして保存する。保存されたことを確認できなければ中断する。

    保存できていないのに成功と報告すると、呼び出し側がブラウザを閉じて
    書いた内容が失われる。確認が取れない場合は黙って通さない。
    """
    print("下書きとして保存しています...")
    clicked = True
    try:
        _find(page, SEL_SAVE_DRAFT, timeout_ms=10000).click()
    except NotePostError as exc:
        # note は自動保存も走るため、ボタンが無くても保存されている可能性はある。
        # ただし「保存された」表示を確認できるまでは成功とみなさない。
        clicked = False
        print(f"  [warn] 保存ボタンが見つかりません。自動保存を確認します。\n{exc}")

    page.wait_for_timeout(2000)
    if clicked:
        _handle_save_conflict(page, shot_dir)

    saved = _has_any(page, SEL_SAVED_NOTICE)
    if not saved:
        page.wait_for_timeout(3000)
        saved = _has_any(page, SEL_SAVED_NOTICE)
    _shot(page, shot_dir, f"{account.key}-draft-saved")

    if saved:
        print("  保存を確認しました。")
        return

    message = (
        "保存されたことを確認できませんでした。\n"
        + ("  保存ボタンも見つかりませんでした。\n" if not clicked else "")
        + "  この状態でブラウザを閉じると、書いた内容が失われる可能性があります。\n"
        "  --headed で画面を開き、手動で保存してください。\n"
        "  確認したうえで続ける場合は --allow-unverified を付けます。\n\n"
        "  いま画面にあるもの:\n" + _format_elements(_collect_elements(page), limit=8)
    )
    if not allow_unverified:
        raise NotePostError(message)
    print(f"  [warn] {message}")





def _publish(page, account: Account, article: Article, shot_dir: Path | None) -> None:
    """公開設定画面へ進み、タグを入れて公開する。"""
    print("公開設定画面に進みます...")
    _find(page, SEL_GOTO_PUBLISH).click()
    page.wait_for_timeout(3000)
    _shot(page, shot_dir, f"{account.key}-publish-settings")
    _add_tags(page, article.tags, shot_dir)
    print("公開しています...")
    _find(page, SEL_PUBLISH_CONFIRM).click()
    page.wait_for_timeout(6000)
    _shot(page, shot_dir, f"{account.key}-published")


def _print_summary(article: Article, account: Account, publish: bool, mode: str) -> None:
    """実行前に何をするのかを表示する。"""
    print(f"操作       : {mode}")
    print(f"アカウント : {account.key} ({account.profile_url})")
    print(f"記事ファイル: {article.source}")
    print(f"タイトル   : {article.title}")
    print(f"タグ       : {', '.join(article.tags) or '(なし)'}")
    print(f"本文       : {len(article.body)}文字 / {len(article.body.splitlines())}行")
    print(f"保存方法   : {'公開' if publish else '下書き保存'}")


def _resolve_for_article(
    args, article: Article, accounts: dict[str, Account], default_key: str | None
) -> Account:
    """原稿と CLI の両方でアカウント指定があるとき、どちらを採ったかを明示する。"""
    if args.account and article.account and args.account != article.account:
        print(
            f"[注意] アカウント指定が食い違っています。"
            f"--account={args.account} を採用します"
            f"(原稿の front matter は {article.account})。"
        )
    return resolve_account(accounts, default_key, args.account or article.account)


def _require_session(account: Account) -> None:
    if not account.storage_state.exists():
        raise NotePostError(
            f"ログイン状態のファイルがありません: {account.storage_state}\n"
            f"  先に実行してください: "
            f"python scripts/note_post.py login --account {account.key} --manual"
        )


def _run_browser(account: Account, args, work) -> int:
    """ブラウザを起動して work(page) を実行する共通処理。"""
    sync_playwright = _import_playwright()
    shot_dir = Path(args.screenshot_dir) if args.screenshot_dir else None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed, slow_mo=args.slow_mo)
        context = browser.new_context(storage_state=str(account.storage_state))
        # 本文は HTML をクリップボード経由で貼り付けるため権限が要る
        try:
            context.grant_permissions(
                ["clipboard-read", "clipboard-write"], origin=EDITOR_BASE
            )
        except Exception as exc:
            print(f"[warn] クリップボード権限を付与できませんでした: {exc}")
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            print("\nログイン中のアカウントを確認しています...")
            _verify_account(context, page, account, args.allow_unverified)
            work(page, shot_dir)
            if args.keep_open:
                input("ブラウザを開いたままにしています。Enter で閉じます → ")
        except Exception:
            _shot(page, shot_dir, f"{account.key}-error")
            raise
        finally:
            browser.close()
    return 0


def cmd_post(args, accounts: dict[str, Account], default_key: str | None) -> int:
    """新規記事を作成する。"""
    article = load_article(Path(args.file))
    account = _resolve_for_article(args, article, accounts, default_key)

    if article.note_url and not args.allow_duplicate:
        raise NotePostError(
            f"この原稿には note_url が設定されています: {article.note_url}\n"
            "  既存記事の上書きは update を使ってください:\n"
            f"    python scripts/note_post.py update --file {args.file}\n"
            "  意図して新規記事を作る場合は --allow-duplicate を付けます。"
        )

    publish = _resolve_publish(args, article, account)
    _print_summary(article, account, publish, "新規作成")
    findings = _run_lint(article, args)

    if args.dry_run:
        print("\n--dry-run のため、ブラウザは起動しません。")
        print("--- 本文プレビュー(先頭20行) ---")
        for line in article.body.splitlines()[:20]:
            print(f"| {line}")
        return 0

    _require_session(account)

    def work(page, shot_dir):
        print("\nエディタを開いています...")
        _open_editor(page, account, NEW_NOTE_URL, shot_dir)
        _write_article(page, article, args)
        _report_written(page, article)
        _shot(page, shot_dir, f"{account.key}-editor")
        editor_url = page.url
        if publish:
            _publish(page, account, article, shot_dir)
            print(f"\n公開しました: {page.url}")
        else:
            _save_draft(page, account, shot_dir, args.allow_unverified)
            print(f"\n下書きを保存しました: {editor_url}")
            print(f"  note_url: {editor_url}  ← 原稿の front matter に控えておくと上書きできます")
        _print_followup(findings)

    return _run_browser(account, args, work)


def cmd_update(args, accounts: dict[str, Account], default_key: str | None) -> int:
    """既存記事を開いて本文を差し替える(下書き・公開済みの両方)。"""
    article = load_article(Path(args.file))
    account = _resolve_for_article(args, article, accounts, default_key)

    target = args.url or article.note_url
    if not target:
        raise NotePostError(
            "上書き先の記事が分かりません。\n"
            "  --url でURLを渡すか、原稿の front matter に note_url を書いてください。\n"
            "  例: note_url: https://note.com/19770104/n/nedb0aaf045b1"
        )
    edit_url, target_urlname = note_edit_url(target)
    if target_urlname and target_urlname != account.urlname:
        raise NotePostError(
            "上書き先の記事が、別アカウントのものです。中止しました。\n"
            f"  URL のアカウント : {target_urlname}\n"
            f"  操作するアカウント: {account.urlname} ({account.key})\n"
            f"  URL: {target}"
        )

    publish = _resolve_publish(args, article, account)
    _print_summary(article, account, publish, "既存記事の上書き")
    print(f"上書き先   : {edit_url}")
    findings = _run_lint(article, args)

    if args.dry_run:
        print("\n--dry-run のため、ブラウザは起動しません。")
        return 0

    _require_session(account)

    def work(page, shot_dir):
        print("\n既存記事のエディタを開いています...")
        _open_editor(page, account, edit_url, shot_dir)
        _shot(page, shot_dir, f"{account.key}-before-update")

        # 公開済み記事は「下書き保存」ボタンが無く「更新する」になる
        already_public = False
        try:
            _find(page, SEL_SAVE_DRAFT, timeout_ms=4000)
        except NotePostError:
            already_public = _has_any(page, SEL_UPDATE_PUBLISHED)

        if already_public and not publish:
            raise NotePostError(
                "この記事は公開済みのため、下書きとしては保存できません。\n"
                "  note 側で下書きに戻してから実行するか、\n"
                "  内容を確認のうえ --publish を付けて更新してください。"
            )

        _write_article(page, article, args)
        _report_written(page, article)
        _shot(page, shot_dir, f"{account.key}-after-update")

        if already_public:
            print("公開済み記事を更新しています...")
            _find(page, SEL_UPDATE_PUBLISHED).click()
            page.wait_for_timeout(5000)
            _shot(page, shot_dir, f"{account.key}-updated")
            print(f"\n更新しました: {edit_url}")
        elif publish:
            _publish(page, account, article, shot_dir)
            print(f"\n公開しました: {page.url}")
        else:
            _save_draft(page, account, shot_dir, args.allow_unverified)
            print(f"\n下書きを上書き保存しました: {edit_url}")
        _print_followup(findings)

    return _run_browser(account, args, work)


def _print_followup(findings: list) -> None:
    """公開前に人間がやることを最後に念押しする。"""
    print("\n次にやること:")
    print("  1. note の編集画面を目視で確認する(改行・見出し・リンク)")
    if findings:
        print(f"  2. 公開前チェックの指摘 {len(findings)} 件を確認する")
        print("  3. 見出し画像を設定する")
        print("  4. 問題なければ note 上で公開する")
    else:
        print("  2. 見出し画像を設定する")
        print("  3. 問題なければ note 上で公開する")


def _has_any(page, selectors: list[str]) -> bool:
    """セレクタ候補のいずれかが表示されているかを返す(待たない)。"""
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.count() > 0 and locator.is_visible():
                return True
        except Exception:
            continue
    return False


# ---------------------------------------------------------------------------
# doctor コマンド
# ---------------------------------------------------------------------------


def _doctor_report(page, account: Account, target_url: str, verified: str | None) -> str:
    """画面の実物とセレクタの当たり外れを Markdown にまとめる。"""
    matches = _match_selector_groups(page)
    missing = [name for name, hit in matches if hit is None]
    elements = _collect_elements(page)

    lines = [
        "# note 画面診断",
        "",
        f"- 採取日時: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- アカウント: {account.key} ({account.urlname})",
        f"- 開いたURL: {target_url}",
        f"- 実際のURL: {page.url}",
        f"- ログイン照合: {verified or '判定不能'}",
        "",
        "## セレクタの当たり外れ",
        "",
        "| 定義 | 当たった候補 |",
        "| --- | --- |",
    ]
    for name, hit in matches:
        lines.append(f"| `{name}` | {'`' + hit + '`' if hit else '**なし**'} |")

    lines += ["", "## 画面にある要素", ""]
    labels = {"buttons": "ボタン", "inputs": "入力欄", "editables": "編集領域"}
    for kind, label in labels.items():
        items = elements.get(kind) or []
        lines += [f"### {label}({len(items)}件)", ""]
        if not items:
            lines += ["(なし)", ""]
            continue
        lines += ["| tag | text | placeholder | data-testid | class |", "| --- | --- | --- | --- | --- |"]
        for item in items:
            cells = [
                item.get("tag", ""),
                item.get("text", "").replace("|", "/").replace("\n", " "),
                item.get("placeholder", "").replace("|", "/"),
                item.get("testid", ""),
                item.get("cls", "").replace("|", "/"),
            ]
            lines.append("| " + " | ".join(cells) + " |")
        lines.append("")

    lines += ["## 次にやること", ""]
    if missing:
        lines += [
            f"見つからなかった定義が {len(missing)} 件ある: "
            + ", ".join(f"`{name}`" for name in missing),
            "",
            "上の表から該当しそうな要素を探し、`scripts/note_post.py` 冒頭の "
            "セレクタ定義に候補を追加する。",
        ]
    else:
        lines.append("すべての定義が当たっている。セレクタの修正は不要。")
    lines.append("")
    return "\n".join(lines)


def cmd_doctor(args, accounts: dict[str, Account], default_key: str | None) -> int:
    """note の画面を開いて、セレクタが当たるかを調べて報告する。

    初回セットアップやUI変更のあと、実際に投稿する前に状況を採取するためのコマンド。
    書き込みは一切しない。
    """
    account = resolve_account(accounts, default_key, args.account)
    _require_session(account)

    target_url = NEW_NOTE_URL
    if args.url:
        target_url, target_urlname = note_edit_url(args.url)
        if target_urlname and target_urlname != account.urlname:
            raise NotePostError(
                "指定された記事が、別アカウントのものです。\n"
                f"  URL のアカウント : {target_urlname}\n"
                f"  操作するアカウント: {account.urlname} ({account.key})"
            )

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")

    sync_playwright = _import_playwright()
    print(f"アカウント: {account.key}")
    print(f"開くURL  : {target_url}")
    print("書き込みは行いません。\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed, slow_mo=args.slow_mo)
        try:
            context = browser.new_context(storage_state=str(account.storage_state))
            page = context.new_page()
            page.set_default_timeout(30000)

            print("ログイン中のアカウントを確認しています...")
            # 診断なので、照合できなくても止めずに結果だけ記録する
            verified = _verify_account(context, page, account, allow_unverified=True)

            print("画面を開いています...")
            page.goto(target_url, wait_until="domcontentloaded")
            page.wait_for_timeout(5000)

            if _is_logged_out(page):
                raise NotePostError(
                    "ログイン画面に飛ばされました。セッションが切れています。\n"
                    f"  再ログイン: python scripts/note_post.py login "
                    f"--account {account.key} --manual"
                )

            report = _doctor_report(page, account, target_url, verified)
            shot_path = out_dir / f"doctor-{stamp}.png"
            try:
                page.screenshot(path=str(shot_path), full_page=True)
            except Exception as exc:
                print(f"[warn] スクリーンショットに失敗: {exc}", file=sys.stderr)
                shot_path = None

            if args.keep_open:
                input("ブラウザを開いたままにしています。Enter で閉じます → ")
        finally:
            browser.close()

    report_path = out_dir / f"doctor-{stamp}.md"
    report_path.write_text(report, encoding="utf-8")

    matches = [line for line in report.splitlines() if line.startswith("| `SEL_")]
    print("\n--- セレクタの当たり外れ ---")
    for line in matches:
        print("  " + line.strip("| ").replace(" | ", " → "))

    print(f"\n診断結果: {_rel(report_path)}")
    if shot_path:
        print(f"画面の写し: {_rel(shot_path)}")
    print("\nセレクタに「なし」があれば、この2つをそのまま渡してもらえば修正できます。")
    return 0


# ---------------------------------------------------------------------------
# accounts コマンド
# ---------------------------------------------------------------------------


def _verify_saved_session(account: Account) -> str:
    """保存済みセッションが設定どおりのアカウントかを調べ、結果の文字列を返す。"""
    sync_playwright = _import_playwright()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(storage_state=str(account.storage_state))
            page = context.new_page()
            page.set_default_timeout(30000)
            actual = _current_urlname_from_api(context) or _current_urlname_from_dom(page)
        finally:
            browser.close()

    if actual is None:
        return "判定不能(セッション切れ、または note 側の仕様変更の可能性)"
    if actual != account.urlname:
        return f"不一致(note 側は {actual})。ログインし直してください"
    return f"OK (note 側のログインも {actual})"


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
        if not args.verify:
            continue
        if not account.storage_state.exists():
            print("    検証        : 未ログインのため確認できません")
            continue
        try:
            print(f"    検証        : {_verify_saved_session(account)}")
        except NotePostError as exc:
            print(f"    検証        : 確認できませんでした ({exc})")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="note_post.py",
        description="note.com/19770104 に Markdown 記事を下書き保存する",
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
    p_login.add_argument(
        "--allow-unverified",
        action="store_true",
        help="ログインしたアカウントを確認できなくてもセッションを保存する",
    )
    p_login.set_defaults(func=cmd_login)

    def add_editor_args(sub_parser) -> None:
        """post / update で共通のオプション。"""
        sub_parser.add_argument(
            "--account", help="アカウントキー(未指定なら front matter / 既定値)"
        )
        sub_parser.add_argument("--file", required=True, help="対象の Markdown ファイル")
        sub_parser.add_argument("--publish", action="store_true", help="下書きではなく公開する")
        sub_parser.add_argument(
            "--draft", action="store_true", help="front matter を無視して下書きにする"
        )
        sub_parser.add_argument("--yes", action="store_true", help="公開時の確認を省略する")
        sub_parser.add_argument(
            "--dry-run", action="store_true", help="ブラウザを起動せず内容とチェック結果だけ表示"
        )
        sub_parser.add_argument(
            "--skip-check", action="store_true", help="公開前チェックを行わない"
        )
        sub_parser.add_argument(
            "--strict", action="store_true", help="要確認レベルの指摘でも中断する"
        )
        sub_parser.add_argument(
            "--force", action="store_true", help="重大な指摘があっても続行する"
        )
        sub_parser.add_argument(
            "--allow-unverified",
            action="store_true",
            help="ログイン中アカウントを確認できなくても続行する",
        )
        sub_parser.add_argument(
            "--review-sheet-dir", type=Path, help="公開前確認票の出力先ディレクトリ"
        )
        sub_parser.add_argument("--headed", action="store_true", help="ブラウザを表示して実行する")
        sub_parser.add_argument(
            "--slow-mo", type=int, default=0, help="操作間の待ち(ms)。デバッグ用"
        )
        sub_parser.add_argument(
            "--paste-mode",
            choices=["auto", "clipboard", "event"],
            default="auto",
            help="本文の貼り付け方式。auto はクリップボードを試し、"
            "だめなら paste イベントに切り替える",
        )
        sub_parser.add_argument(
            "--screenshot-dir", type=Path, help="各段階のスクリーンショット出力先"
        )
        sub_parser.add_argument(
            "--keep-open", action="store_true", help="完了後もブラウザを閉じない"
        )

    p_post = sub.add_parser("post", help="新規記事として note に投稿する")
    add_editor_args(p_post)
    p_post.add_argument(
        "--allow-duplicate",
        action="store_true",
        help="note_url がある原稿でも新規記事として作る",
    )
    p_post.set_defaults(func=cmd_post)

    p_update = sub.add_parser("update", help="既存の記事を開いて本文を差し替える")
    add_editor_args(p_update)
    p_update.add_argument(
        "--url", help="上書き先の記事URLまたはキー(未指定なら front matter の note_url)"
    )
    p_update.set_defaults(func=cmd_update)

    p_doctor = sub.add_parser(
        "doctor", help="note の画面を開いてセレクタが当たるか調べる(書き込みなし)"
    )
    p_doctor.add_argument("--account", help="アカウントキー")
    p_doctor.add_argument("--url", help="既存記事の編集画面を調べる場合のURL")
    p_doctor.add_argument(
        "--out", type=Path, default=REPO_ROOT / "diagnostics", help="診断結果の出力先"
    )
    p_doctor.add_argument("--headed", action="store_true", help="ブラウザを表示して実行する")
    p_doctor.add_argument("--slow-mo", type=int, default=0, help="操作間の待ち(ms)")
    p_doctor.add_argument("--keep-open", action="store_true", help="完了後もブラウザを閉じない")
    p_doctor.set_defaults(func=cmd_doctor)

    p_accounts = sub.add_parser("accounts", help="設定済みアカウントを一覧表示する")
    p_accounts.add_argument(
        "--verify",
        action="store_true",
        help="保存済みセッションが設定どおりのアカウントかを実際に確認する",
    )
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
