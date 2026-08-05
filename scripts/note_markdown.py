"""Markdown を note のエディタが受け付ける HTML に変換する。

note のエディタは限られたタグしか解釈せず、さらに**ブロック要素ごとに
`name` / `id` 属性(UUID)が必要**という独特な仕様を持つ。このモジュールは
その形式を組み立てるだけで、ネットワークには一切触れない(テストしやすさの
ためにあえて分離している)。

note が解釈するブロック要素:
    h2 / h3 / p / blockquote / hr / ul / ol / li / pre>code /
    figure.note-image / table-of-contents

note には h1 も h4 以降も無いため、見出しレベルは h2 と h3 に丸める。
"""

from __future__ import annotations

import html
import os
import re
import uuid
from dataclasses import dataclass, field
from typing import Callable, Iterable

# 画像アップロードを担う関数の型。ローカルパスを受け取り (公開URL, 画像キー) を返す。
ImageResolver = Callable[[str], tuple[str, str]]

_IMAGE_RE = re.compile(r"^!\[(?P<alt>.*?)\]\((?P<src>.+?)\)$")
_LIST_RE = re.compile(r"^(?P<indent>[ \t]*)(?P<marker>[-*+]|\d+[.)])[ \t]+(?P<text>.*)$")
_HR_RE = re.compile(r"^(?:-{3,}|\*{3,}|_{3,})$")
_HEADING_RE = re.compile(r"^(?P<level>#{1,6})[ \t]+(?P<text>.*)$")
_FENCE_RE = re.compile(r"^```(?P<lang>[^`]*)$")
_TOC_RE = re.compile(r"^<(?:toc|table[-_ ]of[-_ ]contents?)>$", re.IGNORECASE)


def _uid() -> str:
    """note のブロック要素に付ける UUID を1つ発行する。"""
    return str(uuid.uuid4())


def _block_attrs() -> str:
    """`name` と `id` に同じ UUID を入れた属性文字列を返す。"""
    u = _uid()
    return f' name="{u}" id="{u}"'


@dataclass
class ConversionResult:
    """変換結果一式。"""

    body_html: str
    title: str | None = None
    tags: list[str] = field(default_factory=list)
    image_keys: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def text_length(self) -> int:
        """タグを除いた本文の文字数(note の `body_length` に渡す値)。"""
        return len(re.sub(r"<[^>]+>", "", self.body_html))


@dataclass
class _ListItem:
    """箇条書きの1行分。"""

    indent: int
    ordered: bool
    text: str


# --------------------------------------------------------------------------
# インライン記法
# --------------------------------------------------------------------------

def _inline(text: str) -> str:
    """インライン記法を HTML に変換する。

    先に HTML エスケープしてから記法を適用するため、本文中の `<` や `&` が
    そのまま出力されて note の本文を壊すことがない。
    コードスパンは他の記法に食われないよう、いったん退避してから戻す。
    """
    stash: list[str] = []

    def _stash_code(m: re.Match[str]) -> str:
        stash.append(html.escape(m.group(1), quote=False))
        return f"\x00{len(stash) - 1}\x00"

    # コードスパンはエスケープ前に抜き出す(中身に記法があっても無視させるため)
    text = re.sub(r"`([^`]+)`", _stash_code, text)
    text = html.escape(text, quote=False)

    # リンク: この時点で & と < は既にエスケープ済みなので、
    # 属性値を壊す引用符だけを追加で潰す(html.escape をもう一度かけると
    # &amp; が &amp;amp; になってしまう)
    def _link(m: re.Match[str]) -> str:
        label, url = m.group(1), m.group(2)
        return f'<a href="{url.replace(chr(34), "&quot;")}">{label}</a>'

    text = re.sub(r"\[(.+?)\]\((.+?)\)", _link, text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"<em>\1</em>", text)
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)

    for i, code in enumerate(stash):
        text = text.replace(f"\x00{i}\x00", f"<code>{code}</code>")
    return text


# --------------------------------------------------------------------------
# 箇条書き
# --------------------------------------------------------------------------

def _render_list(items: list[_ListItem], pos: int) -> tuple[str, int]:
    """`items[pos]` から始まる同一階層のリストを HTML 化する。

    戻り値は (HTML, 次に処理すべき位置)。入れ子は再帰で直前の `<li>` の中に
    差し込む。
    """
    base_indent = items[pos].indent
    ordered = items[pos].ordered
    tag = "ol" if ordered else "ul"
    # 番号付きリストは note 側が開始番号を要求する
    start_attr = ' data-start="1"' if ordered else ""
    parts = [f"<{tag}{start_attr}{_block_attrs()}>"]

    i = pos
    while i < len(items):
        item = items[i]
        if item.indent < base_indent:
            break
        if item.indent == base_indent and item.ordered != ordered:
            # 同じ深さで種類が変わったら、いったん閉じて呼び出し元に戻す
            break
        if item.indent > base_indent:
            nested, i = _render_list(items, i)
            # 直前の <li> の内側に入れ子リストを押し込む
            parts[-1] = parts[-1].removesuffix("</li>") + nested + "</li>"
            continue
        parts.append(f"<li><p{_block_attrs()}>{_inline(item.text)}</p></li>")
        i += 1

    parts.append(f"</{tag}>")
    return "".join(parts), i


def _render_lists(items: list[_ListItem]) -> str:
    """溜まった箇条書きをまとめて HTML 化する。"""
    out: list[str] = []
    i = 0
    while i < len(items):
        chunk, i = _render_list(items, i)
        out.append(chunk)
    return "".join(out)


# --------------------------------------------------------------------------
# フロントマター
# --------------------------------------------------------------------------

def split_front_matter(text: str) -> tuple[dict[str, str], str, int]:
    """先頭のフロントマターを切り出す。

    1行目がちょうど `---` のときだけフロントマターとみなす。本文中の `---`
    (水平線)を誤ってフロントマターと解釈しないための制約。

    戻り値は (メタ情報, 本文, 本文が始まる行の 0 始まり位置)。3つめは
    note_lint が元ファイルの行番号を復元するために使う。
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text, 0

    for i in range(1, len(lines)):
        if lines[i].strip() in ("---", "..."):
            meta: dict[str, str] = {}
            for line in lines[1:i]:
                if ":" not in line or line.lstrip().startswith("#"):
                    continue
                key, _, value = line.partition(":")
                meta[key.strip().lower()] = value.strip()
            return meta, "\n".join(lines[i + 1:]), i + 1
    # 閉じが無ければフロントマターではないと判断して素通しする
    return {}, text, 0


def _parse_tags(raw: str) -> list[str]:
    """`tags: [A, B]` / `tags: A, B` のどちらの書き方も配列にする。"""
    raw = raw.strip().strip("[]")
    return [t.strip().strip("\"'").lstrip("#") for t in raw.split(",") if t.strip()]


# --------------------------------------------------------------------------
# 本体
# --------------------------------------------------------------------------

def convert(
    markdown: str,
    *,
    image_resolver: ImageResolver | None = None,
    base_dir: str | None = None,
) -> ConversionResult:
    """Markdown 文字列を note 用 HTML に変換する。

    Args:
        markdown: 変換元の Markdown。
        image_resolver: 画像のローカルパスを受け取り (公開URL, 画像キー) を
            返す関数。`None` のときは画像をアップロードせず、警告に記録して
            その行を飛ばす(`--dry-run` 用)。
        base_dir: 画像の相対パスを解決する基準ディレクトリ。
    """
    meta, body, _ = split_front_matter(markdown)

    title: str | None = meta.get("title") or None
    tags = _parse_tags(meta["tags"]) if meta.get("tags") else []

    parts: list[str] = []
    image_keys: list[str] = []
    warnings: list[str] = []
    pending_list: list[_ListItem] = []

    def flush_list() -> None:
        """溜まっている箇条書きを吐き出す。"""
        if pending_list:
            parts.append(_render_lists(pending_list))
            pending_list.clear()

    lines = body.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()

        # コードブロック
        fence = _FENCE_RE.match(stripped)
        if fence:
            flush_list()
            lang = fence.group("lang").strip()
            code_lines: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            i += 1  # 閉じフェンスを読み飛ばす
            code = html.escape("\n".join(code_lines), quote=False)
            parts.append(
                f'<pre{_block_attrs()} data-lang="{html.escape(lang, quote=True)}">'
                f"<code>{code}</code></pre>"
            )
            continue

        if not stripped:
            flush_list()
            i += 1
            continue

        # 箇条書き(連続する限り溜めてからまとめて組み立てる)
        list_match = _LIST_RE.match(raw)
        if list_match:
            indent = len(list_match.group("indent").replace("\t", "    "))
            pending_list.append(
                _ListItem(
                    indent=indent,
                    ordered=bool(re.match(r"^\d", list_match.group("marker"))),
                    text=list_match.group("text").strip(),
                )
            )
            i += 1
            continue
        flush_list()

        # 目次
        if _TOC_RE.match(stripped):
            parts.append(f"<h2{_block_attrs()}>目次</h2>")
            parts.append(f"<table-of-contents{_block_attrs()}><br></table-of-contents>")
            i += 1
            continue

        # 画像
        image_match = _IMAGE_RE.match(stripped)
        if image_match:
            src = image_match.group("src").strip()
            alt = image_match.group("alt")
            if image_resolver is None:
                warnings.append(f"画像をアップロードせず飛ばしました: {src}")
            else:
                path = src
                if base_dir and not os.path.isabs(path) and not path.startswith(("http://", "https://")):
                    path = os.path.join(base_dir, path)
                url, key = image_resolver(path)
                image_keys.append(os.path.splitext(os.path.basename(key))[0])
                esc_url = html.escape(url, quote=True)
                parts.append(
                    f'<figure{_block_attrs()} class="note-image" '
                    f'data-image-key="{html.escape(key, quote=True)}">'
                    f'<a href="{esc_url}" rel="noopener noreferrer" target="_blank">'
                    f'<img src="{esc_url}" alt="{html.escape(alt, quote=True) or "画像"}" '
                    f'data-src="{esc_url}"></a>'
                    f"<figcaption>{_inline(alt)}</figcaption></figure>"
                )
            i += 1
            continue

        # 水平線
        if _HR_RE.match(stripped):
            parts.append(f"<hr{_block_attrs()}>")
            i += 1
            continue

        # 見出し
        heading = _HEADING_RE.match(stripped)
        if heading:
            text = heading.group("text").strip()
            # note には h1 も h4 以降も無い。# と ## は h2、### 以降は h3 に寄せる
            level = 2 if len(heading.group("level")) <= 2 else 3
            # 本文先頭の見出しはタイトル未確定ならタイトルとして吸い上げる
            if title is None and not parts and len(heading.group("level")) == 1:
                title = text
                i += 1
                continue
            parts.append(f"<h{level}{_block_attrs()}>{_inline(text)}</h{level}>")
            i += 1
            continue

        # 引用(連続行はまとめて1つの blockquote にする)
        if stripped.startswith(">"):
            quote_lines = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote_lines.append(lines[i].strip().lstrip(">").strip())
                i += 1
            joined = "<br>".join(_inline(line) for line in quote_lines)
            parts.append(f"<blockquote{_block_attrs()}>{joined}</blockquote>")
            continue

        # 通常の段落
        parts.append(f"<p{_block_attrs()}>{_inline(stripped)}</p>")
        i += 1

    flush_list()

    return ConversionResult(
        body_html="".join(parts),
        title=title,
        tags=tags,
        image_keys=image_keys,
        warnings=warnings,
    )


def convert_file(
    path: str,
    *,
    image_resolver: ImageResolver | None = None,
) -> ConversionResult:
    """Markdown ファイルを読み込んで変換する。画像の相対パスはファイル基準で解決する。"""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    return convert(text, image_resolver=image_resolver, base_dir=os.path.dirname(os.path.abspath(path)))


def iter_block_ids(html_text: str) -> Iterable[str]:
    """生成した HTML に含まれるブロックの `id` を列挙する(検証用)。"""
    return (m.group(1) for m in re.finditer(r'\bid="([^"]+)"', html_text))
