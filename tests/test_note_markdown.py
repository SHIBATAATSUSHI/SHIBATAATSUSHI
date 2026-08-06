"""note_markdown の変換テスト。ネットワークに触れないので単体で回せる。"""

from __future__ import annotations

import re

import pytest

import note_markdown as nm


_INLINE_TAGS = {"br", "strong", "em", "s", "a", "code", "img", "figcaption"}


def blocks(html: str) -> list[str]:
    """生成 HTML に現れるブロック要素のタグ名を順番に返す(閉じタグ・インラインは除く)。"""
    found = re.findall(r"<([a-z][a-z0-9-]*)[ >]", html)
    return [t for t in found if t not in _INLINE_TAGS]


# --------------------------------------------------------------------------
# ブロック要素
# --------------------------------------------------------------------------

def test_見出しはh2とh3に丸められる():
    r = nm.convert("## 大見出し\n\n### 小見出し\n\n#### さらに小さい")
    assert "<h2" in r.body_html
    assert r.body_html.count("<h3") == 2  # #### も h3 に寄せる
    assert "<h4" not in r.body_html


def test_先頭のh1はタイトルとして吸い上げられ本文に残らない():
    r = nm.convert("# 記事タイトル\n\n本文")
    assert r.title == "記事タイトル"
    assert "記事タイトル" not in r.body_html
    assert "本文" in r.body_html


def test_途中のh1は本文の見出しとして残る():
    r = nm.convert("最初の段落\n\n# あとから来た見出し")
    assert r.title is None
    assert "<h2" in r.body_html
    assert "あとから来た見出し" in r.body_html


def test_段落と水平線と引用():
    r = nm.convert("段落です。\n\n---\n\n> 引用1\n> 引用2")
    assert blocks(r.body_html) == ["p", "hr", "blockquote"]
    # 連続する引用行は1つの blockquote にまとめる
    assert r.body_html.count("<blockquote") == 1
    assert "引用1<br>引用2" in r.body_html


def test_全ブロックにnameとidのUUIDが付く():
    r = nm.convert("# T\n\n本文\n\n## 見出し\n\n- 項目")
    ids = list(nm.iter_block_ids(r.body_html))
    assert ids, "id が1つも付いていない"
    uuid_re = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    assert all(uuid_re.match(i) for i in ids)
    # name と id は同じ値で対になっている
    names = re.findall(r'name="([^"]+)"', r.body_html)
    assert names == ids


# --------------------------------------------------------------------------
# 箇条書き
# --------------------------------------------------------------------------

def test_箇条書きの入れ子は親のliの中に入る():
    r = nm.convert("- 親1\n  - 子1\n  - 子2\n- 親2")
    html = r.body_html
    assert html.count("<ul") == 2
    assert html.count("<li>") == 4
    # 子リストは親 li の内側にある(li の直後に ul が来ない = 閉じる前に入る)
    assert "</p><ul" in html
    assert html.rstrip().endswith("</ul>")


def test_番号付きリストにはdata_startが付く():
    r = nm.convert("1. one\n2. two")
    assert '<ol data-start="1"' in r.body_html
    assert r.body_html.count("<li>") == 2


def test_同じ深さで種類が変わると別のリストになる():
    r = nm.convert("- a\n- b\n1. c")
    assert r.body_html.count("<ul") == 1
    assert r.body_html.count("<ol") == 1


def test_空行で箇条書きが区切られる():
    r = nm.convert("- a\n\n- b")
    assert r.body_html.count("<ul") == 2


# --------------------------------------------------------------------------
# エスケープ(参考にした OSS が抜けていた部分)
# --------------------------------------------------------------------------

def test_本文のHTML特殊文字はエスケープされる():
    r = nm.convert("a < b かつ c & d と <script>alert(1)</script>")
    assert "<script>" not in r.body_html
    assert "&lt;script&gt;" in r.body_html
    assert "&amp;" in r.body_html


def test_コードブロックの中身もエスケープされ改行が保たれる():
    r = nm.convert('```python\nif a < b:\n    print("x")\n```')
    assert '<pre' in r.body_html and 'data-lang="python"' in r.body_html
    assert "if a &lt; b:" in r.body_html
    assert '    print("x")' in r.body_html


def test_リンクのURLは二重エスケープされない():
    r = nm.convert("[リンク](https://example.com/?a=1&b=2)")
    assert 'href="https://example.com/?a=1&amp;b=2"' in r.body_html
    assert "&amp;amp;" not in r.body_html


def test_コードスパンの中の記法は展開されない():
    r = nm.convert("`**これは太字にしない**` けれど **これは太字**")
    assert "<code>**これは太字にしない**</code>" in r.body_html
    assert "<strong>これは太字</strong>" in r.body_html


# --------------------------------------------------------------------------
# インライン装飾
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("src", "expected"),
    [
        ("**太字**", "<strong>太字</strong>"),
        ("*斜体*", "<em>斜体</em>"),
        ("~~打消~~", "<s>打消</s>"),
        ("`コード`", "<code>コード</code>"),
    ],
)
def test_インライン装飾(src: str, expected: str):
    assert expected in nm.convert(src).body_html


def test_太字は斜体より先に処理される():
    r = nm.convert("**強い**")
    assert "<strong>強い</strong>" in r.body_html
    assert "<em>" not in r.body_html


# --------------------------------------------------------------------------
# フロントマター・タイトル・タグ
# --------------------------------------------------------------------------

def test_フロントマターからタイトルとタグを取る():
    r = nm.convert("---\ntitle: フロントマターの題\ntags: [AI, 日記]\n---\n\n本文")
    assert r.title == "フロントマターの題"
    assert r.tags == ["AI", "日記"]
    assert "本文" in r.body_html
    assert "title:" not in r.body_html


def test_タグは角括弧なしでも読める():
    r = nm.convert("---\ntags: AI, #日記\n---\n本文")
    assert r.tags == ["AI", "日記"]


def test_フロントマターは1行目のときだけ有効():
    # 本文中の --- は水平線であってフロントマターではない
    r = nm.convert("本文\n\n---\n\ntitle: これは見出しではない\n\n---")
    assert r.title is None
    assert r.body_html.count("<hr") == 2


def test_閉じのないフロントマターは本文として扱う():
    r = nm.convert("---\ntitle: 閉じ忘れ\n\n本文")
    assert r.title is None


def test_フロントマターのタイトルが先頭見出しより優先される():
    r = nm.convert("---\ntitle: 優先されるほう\n---\n# 見出しのほう\n\n本文")
    assert r.title == "優先されるほう"
    # タイトルに使われなかった見出しは本文に残る
    assert "見出しのほう" in r.body_html


# --------------------------------------------------------------------------
# 投稿しないマーカー
# --------------------------------------------------------------------------

def test_マーカー以降は変換されない():
    r = nm.convert("# 題\n\n本文。\n\n<!-- 投稿しない -->\n\n## note投稿設定\n\n- 見出し画像案: 机")
    assert "本文。" in r.body_html
    assert "note投稿設定" not in r.body_html
    assert "見出し画像案" not in r.body_html


def test_マーカーが無ければ全部変換される():
    r = nm.convert("# 題\n\n本文。\n\n## note投稿設定\n\n- 見出し画像案: 机")
    assert "note投稿設定" in r.body_html


@pytest.mark.parametrize(
    "marker",
    ["<!-- 投稿しない -->", "<!-- ここから下は投稿しない -->", "<!-- draft-only -->", "<!-- no-post -->"],
)
def test_マーカーの書き方をいくつか受け付ける(marker: str):
    r = nm.convert(f"# 題\n\n本文。\n\n{marker}\n\nメモ")
    assert "メモ" not in r.body_html


def test_split_publishableは本文とメモを分ける():
    body, memo = nm.split_publishable("本文\n\n<!-- 投稿しない -->\n\nメモ")
    assert body.strip() == "本文"
    assert memo.strip() == "メモ"


def test_マーカーが無ければメモは空():
    body, memo = nm.split_publishable("本文だけ")
    assert body == "本文だけ"
    assert memo == ""


def test_フロントマターとマーカーは併用できる():
    r = nm.convert("---\ntitle: 題\ntags: [A]\n---\n\n本文。\n\n<!-- 投稿しない -->\n\nメモ")
    assert r.title == "題"
    assert r.tags == ["A"]
    assert "本文。" in r.body_html
    assert "メモ" not in r.body_html


# --------------------------------------------------------------------------
# 目次・画像
# --------------------------------------------------------------------------

def test_toc記法が目次ブロックになる():
    r = nm.convert("<toc>")
    assert "<table-of-contents" in r.body_html
    assert "目次" in r.body_html


def test_画像はresolverが無いと飛ばして警告する():
    r = nm.convert("![説明](img/a.png)")
    assert "<figure" not in r.body_html
    assert r.warnings and "img/a.png" in r.warnings[0]


def test_画像はresolverがあるとfigureになる():
    r = nm.convert(
        "![説明](img/a.png)",
        image_resolver=lambda p: ("https://assets.st-note.com/x.png", "note/img/abc123.png"),
    )
    assert 'class="note-image"' in r.body_html
    assert 'data-image-key="note/img/abc123.png"' in r.body_html
    assert "<figcaption>説明</figcaption>" in r.body_html
    assert r.image_keys == ["abc123"]


# --------------------------------------------------------------------------
# 本文長
# --------------------------------------------------------------------------

def test_text_lengthはタグを除いた文字数():
    r = nm.convert("あいうえお")
    assert r.text_length == 5


def test_空の入力でも壊れない():
    r = nm.convert("")
    assert r.body_html == ""
    assert r.title is None
    assert r.text_length == 0
