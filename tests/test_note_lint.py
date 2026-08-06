"""note_lint のテスト。ネットワーク非依存。

一人称の数え方が正しいこと(= 自己申告を置き換えられること)を中心に確かめる。
"""

from __future__ import annotations

import pytest

import note_lint


def codes(report: note_lint.LintReport) -> list[str]:
    """検出された指摘のコード一覧。"""
    return [f.code for f in report.findings]


def lint(text: str, **kwargs) -> note_lint.LintReport:
    """テスト用の呼び出し。

    テスト本文は短いので、文字数チェックは既定で切っておく(長さを検査したい
    テストだけ `min_chars` / `max_chars` を明示する)。
    """
    kwargs.setdefault("min_chars", None)
    kwargs.setdefault("max_chars", None)
    return note_lint.lint_text(text, **kwargs)


# --------------------------------------------------------------------------
# 一人称の実測 — このスクリプトの主目的
# --------------------------------------------------------------------------

def test_本文の私はを数えて行番号を出す():
    r = lint("# 題\n\n私は迷った。\n\nそして私は決めた。")
    assert r.watashiwa_count == 2
    lines = [f.line for f in r.findings if f.code == "first-person"]
    assert lines == [3, 5]


def test_タイトルの私はは数えない():
    # GPT 側の「本文中0回、タイトルのみ1回」と同じ扱いにする
    r = lint("# なぜ私は本が読めなかったのか\n\n本文には出てこない。")
    assert r.watashiwa_count == 0
    assert r.first_person_count == 0


def test_見出しの一人称は数えない():
    r = lint("# 題\n\n## 私はこう考えた\n\n本文。")
    assert r.first_person_count == 0


def test_コードブロックの中の一人称は数えない():
    r = lint('# 題\n\n```python\nx = "私は"\n```\n\n本文。')
    assert r.first_person_count == 0


def test_フロントマターのタイトルにある一人称は数えない():
    r = lint("---\ntitle: 私はこう考えた\n---\n\n本文。")
    assert r.first_person_count == 0


def test_引用内の一人称は引用と分かるように出す():
    r = lint("# 題\n\n> 私はそう思わない。")
    assert r.watashiwa_count == 1
    assert any("引用内" in f.message for f in r.findings)


def test_私たちは一人称として数えない():
    # 読者を含む総称で使うため
    r = lint("# 題\n\n私たちはそう考えがちだ。")
    assert r.first_person_count == 0


def test_自分は一人称として数えない():
    # 「当時の自分」は STYLE.md が「私は」の置換先として推奨する表現
    r = lint("# 題\n\n当時の自分には見えていなかった。自分の力を過信していた。")
    assert r.first_person_count == 0


def test_私は以外の一人称も拾う():
    r = lint("# 題\n\n僕の話をする。わたしの考えでは違う。")
    assert r.first_person_count == 2
    assert r.watashiwa_count == 0
    assert all(c == "first-person-other" for c in codes(r))


def test_置換後の表現は指摘されない():
    # GPT がたどり着いた置換のしかたが、そのまま lint を通ること
    r = lint(
        "# 題\n\n大学院時代には気づかなかった。臨床や教育では話が別だ。"
        "当時の自分は焦っていた。この本を読みながら考えた。"
    )
    assert r.first_person_count == 0
    assert [c for c in codes(r) if c.startswith("first-person")] == []


# --------------------------------------------------------------------------
# 意図して残した分の申告
# --------------------------------------------------------------------------

def test_申告した数に収まっていれば指摘しない():
    r = lint("---\ntitle: 題\nallow_first_person: 2\n---\n\n私はこう考える。\n\n私は後輩に話した。")
    assert r.watashiwa_count == 2
    assert [c for c in codes(r) if c.startswith("first-person")] == []


def test_申告した数を超えたら指摘する():
    r = lint(
        "---\ntitle: 題\nallow_first_person: 1\n---\n\n私はこう考える。\n\n私は後輩に話した。"
    )
    assert r.first_person_count == 2
    assert "first-person-over" in codes(r)


def test_申告が壊れていても落ちない():
    r = lint("---\ntitle: 題\nallow_first_person: たくさん\n---\n\n私はこう考える。")
    assert r.watashiwa_count == 1


# --------------------------------------------------------------------------
# タイトル
# --------------------------------------------------------------------------

def test_タイトルが無ければエラー():
    r = lint("本文だけがある。")
    assert "no-title" in codes(r)
    assert r.errors


def test_長すぎるタイトルは警告():
    r = lint("# " + "あ" * 50 + "\n\n本文。")
    assert "long-title" in codes(r)
    # 警告なのでエラーにはしない
    assert not r.errors


# --------------------------------------------------------------------------
# 文字数
# --------------------------------------------------------------------------

def test_既定の文字数レンジは既存記事の実測から決めている():
    # 記事01=4,595字 / 記事02=5,095字 / 記事03=3,614字 が全部収まる幅
    assert note_lint.TARGET_MIN_CHARS == 3500
    assert note_lint.TARGET_MAX_CHARS == 5500


def test_Noneを渡せば文字数を検査しない():
    r = lint("# 題\n\n短い。", min_chars=None)
    assert "too-short" not in codes(r)


def test_下限を下回れば警告():
    r = lint("# 題\n\n短い。", min_chars=1000)
    assert "too-short" in codes(r)


def test_上限を超えれば警告():
    r = lint("# 題\n\n" + "あ" * 200, max_chars=100)
    assert "too-long" in codes(r)


def test_文字数の数え方は投稿時と一致する():
    import note_markdown

    text = "# 題\n\n本文はここにある。"
    assert lint(text).text_length == note_markdown.convert(text).text_length


# --------------------------------------------------------------------------
# note の制約・未対応記法
# --------------------------------------------------------------------------

def test_h4以降は丸められると警告():
    r = lint("# 題\n\n#### 深い見出し\n\n本文。")
    assert "deep-heading" in codes(r)


def test_表は崩れると警告():
    r = lint("# 題\n\n| A | B |\n|---|---|\n| 1 | 2 |")
    assert "unsupported" in codes(r)


def test_脚注は解釈されないと警告():
    r = lint("# 題\n\n本文に脚注[^1]を置く。")
    assert "unsupported" in codes(r)


def test_コードブロック内の表は警告しない():
    r = lint("# 題\n\n```\n| A | B |\n```\n\n本文。")
    assert "unsupported" not in codes(r)


def test_toc記法は未対応扱いしない():
    r = lint("# 題\n\n<toc>\n\n本文。")
    assert "unsupported" not in codes(r)


# --------------------------------------------------------------------------
# 投稿しないマーカー(公開事故の防止)
# --------------------------------------------------------------------------

MEMO = "## note投稿設定\n\n- 見出し画像案: 夜の机に本を置く\n- 推奨設定: 無料"


def test_マーカーが無いメモ見出しは指摘する():
    r = lint(f"# 題\n\n本文。\n\n{MEMO}")
    assert "unpublished-section" in codes(r)


def test_マーカーがあれば指摘しない():
    r = lint(f"# 題\n\n本文。\n\n<!-- 投稿しない -->\n\n{MEMO}")
    assert "unpublished-section" not in codes(r)


def test_マーカー以降は文字数に数えない():
    with_memo = lint(f"# 題\n\n本文。\n\n<!-- 投稿しない -->\n\n{MEMO}")
    without = lint("# 題\n\n本文。")
    assert with_memo.text_length == without.text_length


def test_マーカー以降の一人称は数えない():
    r = lint("# 題\n\n本文。\n\n<!-- 投稿しない -->\n\nメモ: 私はこう書くつもりだった。")
    assert r.first_person_count == 0


def test_マーカー以降の記法は指摘しない():
    # メモに表を書いても、公開されないので警告する意味がない
    r = lint("# 題\n\n本文。\n\n<!-- 投稿しない -->\n\n| A | B |\n|---|---|")
    assert "unsupported" not in codes(r)


# --------------------------------------------------------------------------
# Amazon アソシエイト
# --------------------------------------------------------------------------

AMAZON_PLAIN = "[Amazonで見る](https://www.amazon.co.jp/dp/4087213129)"
AMAZON_AFFILIATE = "[Amazonで見る](https://www.amazon.co.jp/dp/4087213129?tag=akutagawatora-22)"
DISCLOSURE = "Amazonのアソシエイトとして、芥川寅之介は適格販売により収入を得ています。"


def test_通常リンクだけなら指摘しない():
    # アソシエイト参加前の、いまの正しい状態
    r = lint(f"# 題\n\n本文。\n\n{AMAZON_PLAIN}")
    assert "no-disclosure" not in codes(r)
    assert "false-disclosure" not in codes(r)


def test_専用リンクがあるのに参加者表示が無ければ指摘():
    r = lint(f"# 題\n\n本文。\n\n{AMAZON_AFFILIATE}")
    assert "no-disclosure" in codes(r)


def test_専用リンクと参加者表示が揃っていれば指摘しない():
    r = lint(f"# 題\n\n本文。\n\n{AMAZON_AFFILIATE}\n\n{DISCLOSURE}")
    assert "no-disclosure" not in codes(r)
    assert "false-disclosure" not in codes(r)


def test_参加前に参加者表示だけ置くと指摘する():
    # 「収入を得ています」は、まだ参加していない段階では事実に反する
    r = lint(f"# 題\n\n本文。\n\n{AMAZON_PLAIN}\n\n{DISCLOSURE}")
    assert "false-disclosure" in codes(r)


def test_コードブロック内のリンクは対象外():
    r = lint(f"# 題\n\n```\n{AMAZON_AFFILIATE}\n```\n\n本文。")
    assert "no-disclosure" not in codes(r)


def test_amzn_toの短縮リンクも見る():
    r = lint("# 題\n\n本文。\n\n[本](https://amzn.to/abc123?tag=akutagawatora-22)")
    assert "no-disclosure" in codes(r)


def test_将来形の説明を参加者表示と誤検知しない():
    # 既存2本の冒頭にあるこの一文を false-disclosure にしていた(誤検知)
    notice = (
        "現在のAmazonリンクは通常の商品リンクであり、芥川寅之介への紹介料は発生しません。"
        "Amazonアソシエイトを有効にした場合は、記事冒頭にその旨を明記します。"
    )
    r = lint(f"# 題\n\n{notice}\n\n{AMAZON_PLAIN}")
    assert "false-disclosure" not in codes(r)


def test_収益の主張があれば参加者表示とみなす():
    for claim in [
        "Amazonのアソシエイトとして、適格販売により収入を得ています。",
        "リンクを経由して購入すると、筆者に紹介料が入る場合があります。",
    ]:
        r = lint(f"# 題\n\n本文。\n\n{AMAZON_PLAIN}\n\n{claim}")
        assert "false-disclosure" in codes(r), claim


# --------------------------------------------------------------------------
# 未読の本の扱い
# --------------------------------------------------------------------------

# books.yaml の実データに依存しないよう、テスト用の未読リストを渡す
UNREAD = {"4003420934": "プロテスタンティズムの倫理と資本主義の精神"}
LINK = "[Amazonで見る](https://www.amazon.co.jp/dp/4003420934)"


def unread_findings(text: str) -> list[str]:
    """未読本の断定検査だけを走らせる。"""
    return [f.code for f in note_lint._check_unread_assertions("<text>", text, UNREAD)]


def test_未読の本の内容を断定すると指摘する():
    # 実際に一度書いてしまった文言
    text = f"- **プロ倫** — 働くこと自体が倫理になっていく過程の由来をたどれる。{LINK}"
    assert "unread-assertion" in unread_findings(text)


def test_見込みとして書けば指摘しない():
    text = f"- **プロ倫** — どこから来たものなのかを遡るために読もうとしている。まだ手をつけていない。{LINK}"
    assert unread_findings(text) == []


@pytest.mark.parametrize(
    "phrase",
    ["と論じている", "が分かる", "が見えてくる", "をたどれる", "と書かれている", "を教えてくれる"],
)
def test_断定の言い方をいくつか拾う(phrase: str):
    assert "unread-assertion" in unread_findings(f"- 本 — 中身{phrase}。{LINK}")


def test_読了済みの本なら断定してよい():
    # 未読リストに無い ASIN は対象外
    text = "- **なぜ働いていると本が読めなくなるのか** — 労働と読書の歴史が描かれている。[見る](https://www.amazon.co.jp/dp/4087213129)"
    assert unread_findings(text) == []


def test_リンクが無い行は対象外():
    assert unread_findings("本文で何かが分かると書いても、本の紹介ではない。") == []


def test_books_yamlから未読の本だけを読める():
    unread = note_lint.load_unread_asins()
    # read: done は入らない
    assert "4087213129" not in unread  # なぜ働いていると本が読めなくなるのか
    assert "4101035415" not in unread  # 暇と退屈の倫理学
    # read: reading も入らない。読んだ範囲について書くのは正当なため
    assert "4003420934" not in unread  # プロ倫
    assert "4101035423" not in unread  # 中動態の世界
    assert "4000282921" not in unread  # 荘子 鶏となって時を告げよ(中島隆博)
    # read: unread だけが対象
    assert "4000282964" in unread  # 老子 〈道〉への回帰(神塚淑子)
    assert "4910063056" in unread  # 世界は贈与でできている
    # 原典の訳注は入門書のあとに読むので、いずれも未読
    assert "4003320514" in unread  # 老子(岩波文庫)
    assert "4003320611" in unread  # 荘子 第一冊 内篇(岩波文庫)


def test_読み途中の本は断定検査の対象外(tmp_path):
    books = tmp_path / "books.yaml"
    books.write_text(
        'a:\n  read: reading\n  title: 読み途中の本\n  asin: "1111111111"\n'
        'b:\n  read: unread\n  title: 未読の本\n  asin: "2222222222"\n'
        'c:\n  read: done\n  title: 読了の本\n  asin: "3333333333"\n',
        encoding="utf-8",
    )
    unread = note_lint.load_unread_asins(str(books))
    assert list(unread) == ["2222222222"]


def test_books_yamlが無くても落ちない():
    assert note_lint.load_unread_asins("/nonexistent/books.yaml") == {}


# --------------------------------------------------------------------------
# 文の検査
# --------------------------------------------------------------------------

def test_長すぎる一文は警告():
    r = lint("# 題\n\n" + "あ" * 200 + "。")
    assert "long-sentence" in codes(r)


def test_120字程度の一文は許容する():
    # 既存記事では100字超が2〜3割あり、長い一文はこのアカウントの文体
    r = lint("# 題\n\n" + "あ" * 120 + "。")
    assert "long-sentence" not in codes(r)


def test_URLを含む行は長文として数えない():
    # 参考資料の URL 行を「一文が117字」と誤検知していた
    url = "https://shinsho-plus.shueisha.co.jp/interview/interview_category/miyake_mizuno_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    r = lint(f"# 題\n\n本文。\n\n- 参考: {url}")
    assert "long-sentence" not in codes(r)


def test_刻みすぎると警告する():
    # 上限だけ見ていると短いほうへ寄る事故に気づけない。実際に一度やった
    choppy = "# 題\n\n" + "短い文である。" * 25
    r = lint(choppy)
    assert "too-choppy" in codes(r)


def test_既存記事と同じ長さなら警告しない():
    natural = "# 題\n\n" + ("あ" * 74 + "。") * 25
    r = lint(natural)
    assert "too-choppy" not in codes(r)


def test_文が少なければ中央値を判定しない():
    # 短い記事や書誌だけのファイルで誤検知しないよう、20文未満は見ない
    r = lint("# 題\n\n短い。" * 3)
    assert "too-choppy" not in codes(r)


def test_同じ文末が3文続いたら警告():
    r = lint("# 題\n\nこれは本です。あれも本です。それも本です。")
    assert "monotonous" in codes(r)


def test_文末が変化していれば警告しない():
    r = lint("# 題\n\nこれは本だ。あれは資料になる。それは読み終えた。")
    assert "monotonous" not in codes(r)


def test_見出しは文末の検査に含めない():
    r = lint("# 題\n\n## 一つです。\n\n## 二つです。\n\n## 三つです。")
    assert "monotonous" not in codes(r)


# --------------------------------------------------------------------------
# 終了コード
# --------------------------------------------------------------------------

def test_指摘なしなら終了コード0(tmp_path):
    p = tmp_path / "a.md"
    p.write_text("# 題\n\n本文はここにある。", encoding="utf-8")
    assert note_lint.main([str(p)]) == 0


def test_エラーがあれば終了コード1(tmp_path):
    p = tmp_path / "a.md"
    p.write_text("タイトルが無い本文。", encoding="utf-8")
    assert note_lint.main([str(p)]) == 1


def test_警告だけなら既定では0だがstrictで1(tmp_path):
    p = tmp_path / "a.md"
    p.write_text("# 題\n\n私はこう考える。", encoding="utf-8")
    assert note_lint.main([str(p)]) == 0
    assert note_lint.main([str(p), "--strict"]) == 1


def test_複数ファイルをまとめて検査できる(tmp_path):
    a, b = tmp_path / "a.md", tmp_path / "b.md"
    a.write_text("# 題A\n\n本文A。", encoding="utf-8")
    b.write_text("タイトルの無い本文B。", encoding="utf-8")
    assert note_lint.main([str(a), str(b)]) == 1
