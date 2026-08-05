"""note_lint のテスト。ネットワーク非依存。

一人称の数え方が正しいこと(= 自己申告を置き換えられること)を中心に確かめる。
"""

from __future__ import annotations

import note_lint


def codes(report: note_lint.LintReport) -> list[str]:
    """検出された指摘のコード一覧。"""
    return [f.code for f in report.findings]


def lint(text: str, **kwargs) -> note_lint.LintReport:
    """タイトル欠落の指摘が邪魔なときのために、既定でタイトルを補う。"""
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

def test_文字数の目安は指定しなければ検査しない():
    r = lint("# 題\n\n短い。")
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
# 文の検査
# --------------------------------------------------------------------------

def test_長すぎる一文は警告():
    r = lint("# 題\n\n" + "あ" * 150 + "。")
    assert "long-sentence" in codes(r)


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
