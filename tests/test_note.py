#!/usr/bin/env python3
"""note 自動投稿まわりのテスト。

ブラウザを使わない部分だけを対象にする。実際の note への書き込みは
この環境から実行できないため、Playwright の呼び出しはスタブで置き換える。

  python -m unittest discover tests
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import note_lint  # noqa: E402
import note_post  # noqa: E402


def write(path: Path, text: str) -> Path:
    """テスト用のファイルを書き出す。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class FrontMatterTest(unittest.TestCase):
    """原稿の読み込み。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_front_matter_keys(self):
        path = write(
            self.tmp / "a.md",
            "---\ntitle: テスト\naccount: 19770104\ntags: [AI, 臨床]\n"
            "publish: false\nnote_url: https://note.com/19770104/n/nabc123\n---\n\n本文\n",
        )
        article = note_post.load_article(path)
        self.assertEqual(article.title, "テスト")
        self.assertEqual(article.account, "19770104")
        self.assertEqual(article.tags, ["AI", "臨床"])
        self.assertFalse(article.publish)
        self.assertEqual(article.note_url, "https://note.com/19770104/n/nabc123")

    def test_tags_as_list_and_hash_stripped(self):
        path = write(self.tmp / "b.md", "---\ntitle: T\ntags:\n  - AI\n  - '#習慣'\n---\n本文\n")
        self.assertEqual(note_post.load_article(path).tags, ["AI", "習慣"])

    def test_tags_keep_reserved_words_as_text(self):
        """「公開」「draft」等をタグに使っても真偽値へ化けないこと。"""
        for line in ["tags: [公開, AI]", "tags:\n  - 公開\n  - AI"]:
            path = write(self.tmp / "g.md", f"---\ntitle: T\n{line}\n---\n本文\n")
            self.assertEqual(note_post.load_article(path).tags, ["公開", "AI"])

    def test_title_with_colon_needs_no_quoting(self):
        path = write(self.tmp / "h.md", "---\ntitle: 境界線: 入力してはいけない情報\n---\n本文\n")
        self.assertEqual(note_post.load_article(path).title, "境界線: 入力してはいけない情報")

    def test_title_from_heading(self):
        path = write(self.tmp / "c.md", "# 見出しタイトル\n\n本文だよ\n")
        article = note_post.load_article(path)
        self.assertEqual(article.title, "見出しタイトル")
        self.assertEqual(article.body, "本文だよ")

    def test_status_variants(self):
        for status, expected in [
            ("public", True), ("publish", True), ("公開", True),
            ("draft", False), ("下書き", False),
        ]:
            path = write(self.tmp / "d.md", f"---\ntitle: T\nstatus: {status}\n---\n本文\n")
            self.assertIs(note_post.load_article(path).publish, expected, status)

    def test_html_comment_is_stripped(self):
        """原稿中のメモが note へ流れ込まないこと。"""
        path = write(self.tmp / "e.md", "---\ntitle: T\n---\n<!-- 内部メモ -->\n本文\n")
        self.assertNotIn("内部メモ", note_post.load_article(path).body)

    def test_missing_title_is_error(self):
        path = write(self.tmp / "f.md", "本文だけ\n")
        with self.assertRaises(note_post.NotePostError):
            note_post.load_article(path)


class EditUrlTest(unittest.TestCase):
    """記事URLの解釈とアカウント名の抽出。"""

    def test_public_url_yields_urlname(self):
        """編集画面は editor.note.com 側で、末尾スラッシュまで必要。"""
        url, name = note_post.note_edit_url("https://note.com/19770104/n/n52aef685b03f")
        self.assertEqual(url, "https://editor.note.com/notes/n52aef685b03f/edit/")
        self.assertEqual(name, "19770104")

    def test_other_account_url_is_detected(self):
        _, name = note_post.note_edit_url(
            "https://note.com/akutagawa_tora/n/nedb0aaf045b1?app_launch=false"
        )
        self.assertEqual(name, "akutagawa_tora")

    def test_edit_url_and_bare_key_have_no_urlname(self):
        for value in [
            "https://editor.note.com/notes/nedb0aaf045b1/edit/",
            "https://note.com/notes/nedb0aaf045b1/edit",
            "nedb0aaf045b1",
        ]:
            url, name = note_post.note_edit_url(value)
            self.assertEqual(url, "https://editor.note.com/notes/nedb0aaf045b1/edit/")
            self.assertIsNone(name)

    def test_garbage_is_rejected(self):
        with self.assertRaises(note_post.NotePostError):
            note_post.note_edit_url("https://example.com/foo")


class MarkdownToHtmlTest(unittest.TestCase):
    """本文の HTML 変換。note へはこの HTML を貼り付ける。"""

    def convert(self, markdown):
        return note_post.markdown_to_note_html(markdown)

    def test_headings(self):
        self.assertEqual(self.convert("## 大見出し"), "<h2>大見出し</h2>")
        self.assertEqual(self.convert("### 小見出し"), "<h3>小見出し</h3>")

    def test_paragraph(self):
        self.assertEqual(self.convert("本文です。"), "<p>本文です。</p>")

    def test_unordered_list_is_wrapped_once(self):
        html = self.convert("- 一つ目\n- 二つ目")
        self.assertEqual(html, "<ul><li><p>一つ目</p></li><li><p>二つ目</p></li></ul>")

    def test_ordered_list(self):
        html = self.convert("1. 最初\n2. 次")
        self.assertEqual(html, "<ol><li><p>最初</p></li><li><p>次</p></li></ol>")

    def test_blank_line_closes_list(self):
        html = self.convert("- 項目\n\n段落")
        self.assertEqual(html, "<ul><li><p>項目</p></li></ul><p>段落</p>")

    def test_list_type_switch_closes_previous(self):
        html = self.convert("- 箇条\n1. 番号")
        self.assertEqual(
            html, "<ul><li><p>箇条</p></li></ul><ol><li><p>番号</p></li></ol>"
        )

    def test_blockquote(self):
        self.assertEqual(self.convert("> 引用"), "<blockquote><p>引用</p></blockquote>")

    def test_link(self):
        self.assertEqual(
            self.convert("参考 [出典](https://example.org/a)"),
            '<p>参考 <a href="https://example.org/a">出典</a></p>',
        )

    def test_bold(self):
        """`**強調**` を変換しないと、アスタリスクが文字として note に入る。"""
        self.assertEqual(
            self.convert("これは**重要な点**である。"),
            "<p>これは<strong>重要な点</strong>である。</p>",
        )

    def test_bold_inside_list_and_with_link(self):
        self.assertEqual(
            self.convert("- **見出し**: [出典](https://e.org)"),
            '<ul><li><p><strong>見出し</strong>: '
            '<a href="https://e.org">出典</a></p></li></ul>',
        )

    def test_lone_asterisks_are_left_alone(self):
        self.assertEqual(self.convert("2 * 3 = 6"), "<p>2 * 3 = 6</p>")

    def test_html_is_escaped(self):
        html = self.convert("<script>alert(1)</script> と A & B")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("&amp;", html)

    def test_unclosed_list_is_closed_at_end(self):
        self.assertTrue(self.convert("- 最後の項目").endswith("</ul>"))

    def test_each_line_is_its_own_paragraph(self):
        """1行 = 1段落。CommonMark と違い、連続行を結合しない。

        note のエディタは Enter が常に新しいブロックを作るため、原稿の1行を
        そのまま1ブロックに対応させる。結合すると
        「制定：2020年11月14日」「最終改訂：2026年8月4日」のような並びが潰れる。
        """
        self.assertEqual(
            self.convert("制定：2020年11月14日\n最終改訂：2026年8月4日"),
            "<p>制定：2020年11月14日</p><p>最終改訂：2026年8月4日</p>",
        )

    def test_structure_counts_match_source(self):
        """見出し・リスト・リンクの数が原稿と一致すること。"""
        markdown = "## A\n\n本文\n\n- x\n- y\n\n### B\n\n[l](https://e.org)"
        html = self.convert(markdown)
        self.assertEqual(html.count("<h2>"), 1)
        self.assertEqual(html.count("<h3>"), 1)
        self.assertEqual(html.count("<li>"), 2)
        self.assertEqual(html.count("<a "), 1)


# --- ブラウザのスタブ -------------------------------------------------------


class StubResponse:
    def __init__(self, payload, ok=True):
        self._payload, self.ok = payload, ok

    def json(self):
        return self._payload


class StubContext:
    """context.request.get だけを持つ最小のスタブ。"""

    def __init__(self, payload, ok=True):
        outer = self

        class _Request:
            def get(self, url, timeout=None):
                return StubResponse(outer._payload, outer._ok)

        self._payload, self._ok = payload, ok
        self.request = _Request()


class StubPage:
    """DOM フォールバックが働かない状況を作る。"""

    def goto(self, *args, **kwargs):
        raise RuntimeError("この環境では note に到達できない")


class VerifyAccountTest(unittest.TestCase):
    """ログイン中アカウントの照合。取り違え防止の要。"""

    def setUp(self):
        self.account = note_post.Account(
            key="19770104", urlname="19770104", storage_state=Path("/tmp/state.json")
        )

    def test_matching_account_passes(self):
        """レスポンスの形が変わっても urlname を拾えること。"""
        for payload in [
            {"urlname": "19770104"},
            {"data": {"urlname": "19770104"}},
            {"data": {"user": {"urlname": "19770104"}}},
        ]:
            result = note_post._verify_account(
                StubContext(payload), StubPage(), self.account, False
            )
            self.assertEqual(result, "19770104")

    def test_different_account_aborts(self):
        with self.assertRaises(note_post.NotePostError) as ctx:
            note_post._verify_account(
                StubContext({"data": {"urlname": "akutagawa_tora"}}),
                StubPage(), self.account, False,
            )
        self.assertIn("akutagawa_tora", str(ctx.exception))

    def test_undetermined_aborts_by_default(self):
        with self.assertRaises(note_post.NotePostError):
            note_post._verify_account(
                StubContext(None, ok=False), StubPage(), self.account, False
            )

    def test_undetermined_passes_with_allow_unverified(self):
        result = note_post._verify_account(
            StubContext(None, ok=False), StubPage(), self.account, True
        )
        self.assertIsNone(result)


class LintTest(unittest.TestCase):
    """公開前チェック。医療テーマ固有の禁止事項が止まること。"""

    def _rules(self, title, body):
        return {finding.rule for finding in note_lint.lint(title, body)}

    def test_clean_article_has_no_findings(self):
        body = "評価の手順を整理する。\n\n## 確認する項目\n\n- 覚醒\n- 注意\n"
        self.assertEqual(note_lint.lint("評価の手順", body), [])

    def test_medical_claims_are_high(self):
        for body in [
            "この方法で腰痛は必ず治ります。",
            "薬をやめて運動に切り替えるとよい。",
            "自己診断できます。",
        ]:
            self.assertEqual(note_lint.worst_severity(note_lint.lint("T", body)), "high", body)

    def test_patient_information_is_high(self):
        for body in ["68歳男性の症例では改善した。", "カルテに記載している。", "当院での運用を紹介する。"]:
            self.assertEqual(note_lint.worst_severity(note_lint.lint("T", body)), "high", body)

    def test_first_person_is_counted(self):
        body = "私は評価する。私は伝える。私の経験では有効だ。"
        rules = self._rules("T", body)
        self.assertIn("voice/first-person", rules)

    def test_repeated_opening_is_flagged(self):
        body = "これは必要です。これは重要です。これは有効です。これは基本です。"
        self.assertIn("slop/repeated-opening", self._rules("T", body))

    def test_number_without_source_is_medium(self):
        rules = self._rules("T", "改善率は87%だった。")
        self.assertIn("citation/number", rules)

    def test_number_with_source_is_not_flagged(self):
        body = "改善率は87%だった。 https://example.org/paper"
        self.assertNotIn("citation/number", self._rules("T", body))

    def test_title_first_person_is_flagged(self):
        self.assertIn("title/first-person", self._rules("私が考える評価の順番", "本文。"))

    def test_review_sheet_lists_manual_checks(self):
        findings = note_lint.lint("T", "必ず治ります。")
        sheet = note_lint.render_review_sheet("T", findings, "posts/x.md")
        self.assertIn("公開前確認票", sheet)
        for item in note_lint.MANUAL_CHECKS:
            self.assertIn(item, sheet)


class DoctorReportTest(unittest.TestCase):
    """画面診断の出力。セレクタがズレたときの復旧材料になること。"""

    def test_format_elements(self):
        elements = {
            "buttons": [
                {"tag": "button", "text": "下書き保存", "testid": "save", "placeholder": "", "cls": "btn"}
            ],
            "inputs": [],
            "editables": [],
        }
        output = note_post._format_elements(elements)
        self.assertIn("下書き保存", output)
        self.assertIn("data-testid=save", output)

    def test_format_elements_handles_empty(self):
        self.assertIn("取得できません", note_post._format_elements({}))

    def test_report_marks_missing_selectors(self):
        class Page:
            url = "https://note.com/notes/new"

            def locator(self, selector):
                raise RuntimeError("何も当たらない")

            def evaluate(self, script):
                return {"buttons": [], "inputs": [], "editables": []}

        account = note_post.Account(
            key="19770104", urlname="19770104", storage_state=Path("/tmp/s.json")
        )
        report = note_post._doctor_report(Page(), account, note_post.NEW_NOTE_URL, "19770104")
        self.assertIn("**なし**", report)
        self.assertIn("SEL_TITLE", report)
        self.assertIn("見つからなかった定義", report)


class CliGuardTest(unittest.TestCase):
    """CLI が事故を止めること。いずれも書き込みには進まない。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.config = self.tmp / "accounts.json"
        self.config.write_text(
            json.dumps(
                {
                    "default_account": "19770104",
                    "accounts": {
                        "19770104": {
                            "urlname": "19770104",
                            "storage_state": str(self.tmp / "state.json"),
                            "allow_publish": False,
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        self.article = write(self.tmp / "post.md", "---\ntitle: T\n---\n本文です。\n")

    def run_cli(self, *args):
        return note_post.main(["--config", str(self.config), *args])

    def test_publish_is_blocked_when_not_allowed(self):
        self.assertEqual(
            self.run_cli("post", "--file", str(self.article), "--publish", "--dry-run"), 1
        )

    def test_publish_requires_yes_when_not_interactive(self):
        """cron や CI から、確認なしに公開されないこと。"""
        config = json.loads(self.config.read_text(encoding="utf-8"))
        config["accounts"]["19770104"]["allow_publish"] = True
        self.config.write_text(json.dumps(config), encoding="utf-8")

        article = write(
            self.tmp / "pub.md", "---\ntitle: T\npublish: true\n---\n本文です。\n"
        )
        # テストは非対話(isatty が False)で走るため、--yes 無しは中断されるはず
        self.assertEqual(self.run_cli("post", "--file", str(article), "--dry-run"), 1)
        self.assertEqual(
            self.run_cli("post", "--file", str(article), "--dry-run", "--yes"), 0
        )

    def test_draft_dry_run_succeeds(self):
        self.assertEqual(self.run_cli("post", "--file", str(self.article), "--dry-run"), 0)

    def test_update_to_other_account_url_is_blocked(self):
        self.assertEqual(
            self.run_cli(
                "update", "--file", str(self.article),
                "--url", "https://note.com/akutagawa_tora/n/nedb0aaf045b1", "--dry-run",
            ),
            1,
        )

    def test_update_without_target_is_blocked(self):
        self.assertEqual(self.run_cli("update", "--file", str(self.article), "--dry-run"), 1)

    def test_post_with_note_url_is_blocked(self):
        article = write(
            self.tmp / "has_url.md",
            "---\ntitle: T\nnote_url: https://note.com/19770104/n/nabc123\n---\n本文。\n",
        )
        self.assertEqual(self.run_cli("post", "--file", str(article), "--dry-run"), 1)

    def test_high_severity_blocks_posting(self):
        article = write(self.tmp / "bad.md", "---\ntitle: T\n---\nこの方法で必ず治ります。\n")
        self.assertEqual(self.run_cli("post", "--file", str(article), "--dry-run"), 1)

    def test_force_overrides_high_severity(self):
        article = write(self.tmp / "bad2.md", "---\ntitle: T\n---\nこの方法で必ず治ります。\n")
        self.assertEqual(
            self.run_cli("post", "--file", str(article), "--dry-run", "--force"), 0
        )

    def test_unknown_account_is_rejected(self):
        self.assertEqual(
            self.run_cli("post", "--file", str(self.article), "--account", "nope", "--dry-run"), 1
        )


if __name__ == "__main__":
    unittest.main()
