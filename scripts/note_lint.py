#!/usr/bin/env python3
"""note 記事の文体を機械的に検査する。

このスクリプトの主目的は、**AI の自己申告を実測に置き換えること**。
「本文中の『私は』は0回です」と言わせるのではなく、実際に数えて行番号ごと
提示する。文体ルールそのものは `docs/note/STYLE.md` にある。

書き手を縛る道具ではなく、見落としを拾う道具として作っている。そのため
投稿を止める `error` は「そのままでは投稿が成り立たないもの」だけに絞り、
文体上の指摘は `warning` に留める。すべてを厳格に扱いたいときは `--strict`。

使い方:
    uv run scripts/note_lint.py docs/note/kiji.md
    uv run scripts/note_lint.py docs/note/*.md --strict
    uv run scripts/note_lint.py docs/note/kiji.md --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field

from note_markdown import convert, split_front_matter, split_publishable

LEVEL_ERROR = "error"
LEVEL_WARNING = "warning"

# --------------------------------------------------------------------------
# 検査のしきい値
# --------------------------------------------------------------------------

# 本文の文字数の目安。既存記事の実測(記事01=3,963字 / 記事02=3,988字)に、
# 関連書籍セクション分の余裕を足した幅。
TARGET_MIN_CHARS: int | None = 3500
TARGET_MAX_CHARS: int | None = 5000

# note の記事一覧ではタイトルが途中で切れる。全角でこの程度が目安。
TITLE_MAX_CHARS = 40

# 一文がこれを超えたら見直す。
# 既存2本の実測では中央値が74〜76字、100字超が22〜33%あり、長い一文は
# このアカウントの文体そのもの。95パーセンタイルが145字前後だったため、
# 本当の外れ値だけを拾う 150 に置いている。短く切ることを促す値ではない。
SENTENCE_MAX_CHARS = 150

# 同じ文末がこの回数だけ続いたら単調とみなす。
SAME_ENDING_RUN = 3

# 一人称表現。「私は」は STYLE.md で原則0としているため別扱いにする。
FIRST_PERSON_PRIMARY = re.compile(r"私は")
# 「私たち」は読者を含む総称で使うことがあるので対象外。
# 「自分」も意図的に対象外にしている — 「当時の自分」は STYLE.md が
# 「私は」の置換先として推奨する表現であり、これを咎めると矛盾するため。
FIRST_PERSON_OTHER = re.compile(r"私(?!たち)[がのもにをと]|わたし(?!たち)|僕|俺")

# Amazon の商品リンクと、アソシエイト参加者としての表示。
# アソシエイトの専用リンクは URL に tag= が付く(通常リンクには付かない)。
AMAZON_LINK = re.compile(r"https?://(?:www\.)?(?:amazon\.co\.jp|amzn\.(?:to|asia))/[^\s)]*")
AMAZON_AFFILIATE_TAG = re.compile(r"[?&]tag=")
# 参加者表示は「アソシエイト」の語ではなく、**収益を得ているという主張**で判定する。
# 「アソシエイトを有効にした場合は明記します」のような将来形や、
# 「紹介料は発生しません」という否定形を誤検知しないため。
AFFILIATE_DISCLOSURE = re.compile(
    r"適格販売|収入を得て(?:い|お)|紹介料が入る|紹介料を得て"
)

# 自分向けのメモらしき見出し。これが公開範囲に残っていたら事故。
MEMO_HEADING = re.compile(r"投稿設定|見出し画像案|公開設定|メモ書き")

# note が解釈しない記法。書いてもそのまま文字として出てしまう。
UNSUPPORTED = [
    (re.compile(r"^\s*\|.*\|\s*$"), "表は note で崩れる。箇条書きか画像に置き換える"),
    (re.compile(r"\[\^[^\]]+\]"), "脚注記法は note で解釈されない"),
    (re.compile(r"^\s*<(?!toc|table[-_ ]of)[a-zA-Z][a-zA-Z0-9]*[ >/]"), "生の HTML タグは文字として出る"),
]

_FENCE_RE = re.compile(r"^\s*```")
_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.*)$")
_LIST_MARKER_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])[ \t]+")


@dataclass
class Finding:
    """検査で見つかった1件。"""

    path: str
    line: int
    level: str
    code: str
    message: str

    def format(self) -> str:
        """エディタから飛べる `ファイル:行: [レベル] 内容` の形にする。"""
        return f"{self.path}:{self.line}: [{self.level}] {self.message}"


@dataclass
class LintReport:
    """1ファイル分の検査結果。"""

    path: str
    findings: list[Finding] = field(default_factory=list)
    text_length: int = 0
    first_person_count: int = 0
    watashiwa_count: int = 0

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.level == LEVEL_ERROR]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.level == LEVEL_WARNING]


@dataclass
class _ProseLine:
    """検査対象になる散文の1行(元ファイルの行番号つき)。"""

    number: int
    text: str
    in_quote: bool
    is_heading: bool


# --------------------------------------------------------------------------
# 散文の抽出
# --------------------------------------------------------------------------

def _prose_lines(markdown: str) -> tuple[list[_ProseLine], dict[str, str], str | None]:
    """検査対象の行だけを、元ファイルの行番号つきで取り出す。

    除外するもの:
      - フロントマター(本文ではない)
      - コードブロックの中身(日本語の文体検査の対象外)
      - 先頭の `# 見出し`(タイトルとして扱われ本文には入らない)

    戻り値は (散文の行, フロントマター, タイトル)。
    """
    meta, body, offset = split_front_matter(markdown)
    # 「投稿しない」マーカー以降は公開されないので検査対象から外す
    body, _memo = split_publishable(body)
    lines = body.splitlines()

    title: str | None = meta.get("title") or None
    prose: list[_ProseLine] = []
    in_code = False
    title_taken = title is not None
    seen_content = False

    for i, raw in enumerate(lines):
        number = offset + i + 1  # 元ファイルの1始まり行番号
        stripped = raw.strip()

        if _FENCE_RE.match(stripped):
            in_code = not in_code
            continue
        if in_code or not stripped:
            continue

        heading = _HEADING_RE.match(stripped)
        # 本文先頭の `# 見出し` はタイトルになるので本文から除く
        if heading and not seen_content and not title_taken and len(heading.group(1)) == 1:
            title = heading.group(2).strip()
            title_taken = True
            seen_content = True
            continue

        seen_content = True
        in_quote = stripped.startswith(">")
        text = stripped.lstrip(">").strip() if in_quote else stripped
        text = _LIST_MARKER_RE.sub("", text)
        prose.append(
            _ProseLine(number=number, text=text, in_quote=in_quote, is_heading=bool(heading))
        )

    return prose, meta, title


def _sentences(prose: list[_ProseLine]) -> list[tuple[int, str]]:
    """句点で区切った文を (行番号, 文) で返す。

    見出し・引用は対象外。URL を含む行(参考資料の列挙など)も、句点で
    区切れず「一文が117字」のように誤検知するため外す。
    """
    out: list[tuple[int, str]] = []
    for line in prose:
        if line.is_heading or line.in_quote or "http" in line.text:
            continue
        for part in re.split(r"(?<=。)", line.text):
            part = part.strip()
            if part:
                out.append((line.number, part))
    return out


# --------------------------------------------------------------------------
# 個別の検査
# --------------------------------------------------------------------------

def _check_first_person(
    path: str, prose: list[_ProseLine], allowance: int
) -> tuple[list[Finding], int, int]:
    """一人称表現を数え、行番号つきで列挙する。

    `allowance` はフロントマターの `allow_first_person` で申告された
    「意図して残した数」。申告どおりなら指摘しない。
    """
    findings: list[Finding] = []
    watashiwa = 0
    other = 0

    for line in prose:
        if line.is_heading:
            continue  # 見出し・タイトルの一人称は許容している

        for m in FIRST_PERSON_PRIMARY.finditer(line.text):
            watashiwa += 1
            note = "(引用内)" if line.in_quote else ""
            findings.append(
                Finding(
                    path=path,
                    line=line.number,
                    level=LEVEL_WARNING,
                    code="first-person",
                    message=f"「私は」{note}: …{_excerpt(line.text, m.start())}…",
                )
            )
        for m in FIRST_PERSON_OTHER.finditer(line.text):
            other += 1
            note = "(引用内)" if line.in_quote else ""
            findings.append(
                Finding(
                    path=path,
                    line=line.number,
                    level=LEVEL_WARNING,
                    code="first-person-other",
                    message=f"一人称「{m.group(0)}」{note}: …{_excerpt(line.text, m.start())}…",
                )
            )

    total = watashiwa + other
    if allowance and total <= allowance:
        # 申告された数に収まっているので指摘を取り下げる
        return [], total, watashiwa
    if allowance:
        findings.insert(
            0,
            Finding(
                path=path,
                line=1,
                level=LEVEL_WARNING,
                code="first-person-over",
                message=f"一人称が申告 {allowance} 件を超えて {total} 件ある",
            ),
        )
    return findings, total, watashiwa


def _excerpt(text: str, at: int, width: int = 12) -> str:
    """該当箇所の前後を切り出す(どこの話か分かるように)。"""
    start = max(0, at - width // 2)
    return text[start : start + width]


def _check_length(
    path: str, length: int, min_chars: int | None, max_chars: int | None
) -> list[Finding]:
    """本文の文字数が目安の範囲にあるか見る。"""
    findings: list[Finding] = []
    if min_chars is not None and length < min_chars:
        findings.append(
            Finding(path, 1, LEVEL_WARNING, "too-short", f"本文 {length} 字。目安 {min_chars} 字に届いていない")
        )
    if max_chars is not None and length > max_chars:
        findings.append(
            Finding(path, 1, LEVEL_WARNING, "too-long", f"本文 {length} 字。目安 {max_chars} 字を超えている")
        )
    return findings


def _check_title(path: str, title: str | None) -> list[Finding]:
    """タイトルが決まるか、長すぎないかを見る。"""
    if not title:
        return [
            Finding(
                path,
                1,
                LEVEL_ERROR,
                "no-title",
                "タイトルが決まらない。先頭に `# 見出し` かフロントマターの `title:` を書く",
            )
        ]
    if len(title) > TITLE_MAX_CHARS:
        return [
            Finding(
                path,
                1,
                LEVEL_WARNING,
                "long-title",
                f"タイトルが {len(title)} 字。note の一覧では {TITLE_MAX_CHARS} 字あたりで切れる",
            )
        ]
    return []


def _check_headings(path: str, markdown: str) -> list[Finding]:
    """note が持たない見出しレベルを使っていないか見る。"""
    publishable, offset = _publishable(markdown)
    findings: list[Finding] = []
    in_code = False
    for i, raw in enumerate(publishable.splitlines(), start=1):
        stripped = raw.strip()
        if _FENCE_RE.match(stripped):
            in_code = not in_code
            continue
        if in_code:
            continue
        m = _HEADING_RE.match(stripped)
        if m and len(m.group(1)) >= 4:
            findings.append(
                Finding(
                    path,
                    offset + i,
                    LEVEL_WARNING,
                    "deep-heading",
                    f"`{m.group(1)}` は note に無いので h3 に丸められる",
                )
            )
    return findings


def _check_unsupported(path: str, markdown: str) -> list[Finding]:
    """note が解釈しない記法を拾う。"""
    publishable, offset = _publishable(markdown)
    findings: list[Finding] = []
    in_code = False
    for i, raw in enumerate(publishable.splitlines(), start=1):
        stripped = raw.strip()
        if _FENCE_RE.match(stripped):
            in_code = not in_code
            continue
        if in_code or not stripped:
            continue
        for pattern, message in UNSUPPORTED:
            if pattern.search(stripped):
                findings.append(
                    Finding(path, offset + i, LEVEL_WARNING, "unsupported", message)
                )
                break
    return findings


def _publishable(markdown: str) -> tuple[str, int]:
    """公開される本文と、元ファイルの行番号へ戻すためのずれを返す。

    フロントマターと「投稿しない」マーカー以降を除いた範囲が、実際に note へ
    出る部分。検査はすべてこの範囲に対して行う。
    """
    _meta, body, offset = split_front_matter(markdown)
    publishable, _memo = split_publishable(body)
    return publishable, offset


def _check_unpublished_section(path: str, markdown: str) -> list[Finding]:
    """自分向けのメモが公開範囲に残っていないか見る。

    推奨タグ・見出し画像案のようなメモを記事と同じファイルに置くのは便利だが、
    `<!-- 投稿しない -->` マーカーが無いと**そのまま公開記事の末尾に出る**。
    実際に既存2本がこの状態だった。
    """
    publishable, offset = _publishable(markdown)

    findings: list[Finding] = []
    in_code = False
    for i, raw in enumerate(publishable.splitlines()):
        stripped = raw.strip()
        if _FENCE_RE.match(stripped):
            in_code = not in_code
            continue
        if in_code:
            continue
        heading = _HEADING_RE.match(stripped)
        if heading and MEMO_HEADING.search(heading.group(2)):
            findings.append(
                Finding(
                    path,
                    offset + i + 1,
                    LEVEL_WARNING,
                    "unpublished-section",
                    f"「{heading.group(2).strip()}」は自分向けのメモに見える。"
                    "このままだと公開記事に出る。直前に <!-- 投稿しない --> を置く",
                )
            )
    return findings


def _check_affiliate(path: str, markdown: str) -> list[Finding]:
    """Amazon リンクとアソシエイト表示の整合を見る。

    見たいのは2つの食い違い。どちらも規約と事実に関わるので機械で拾う。

    1. 専用リンク(`tag=` 付き)を貼っているのに参加者表示が無い
       → Amazon アソシエイト運営規約が求める表示の欠落。
    2. 参加者表示があるのに専用リンクが無い
       → まだ参加していない段階で「収入を得ています」と書くことになり、
         事実に反する。通常リンクのまま公開する期間に起きやすい。
    """
    publishable, offset = _publishable(markdown)
    findings: list[Finding] = []
    has_affiliate_link = False
    disclosure_line: int | None = None
    link_line: int | None = None

    in_code = False
    for i, raw in enumerate(publishable.splitlines(), start=1):
        stripped = raw.strip()
        if _FENCE_RE.match(stripped):
            in_code = not in_code
            continue
        if in_code:
            continue
        for m in AMAZON_LINK.finditer(stripped):
            if link_line is None:
                link_line = offset + i
            if AMAZON_AFFILIATE_TAG.search(m.group(0)):
                has_affiliate_link = True
        if disclosure_line is None and AFFILIATE_DISCLOSURE.search(stripped):
            disclosure_line = offset + i

    if has_affiliate_link and disclosure_line is None:
        findings.append(
            Finding(
                path,
                link_line or 1,
                LEVEL_WARNING,
                "no-disclosure",
                "アソシエイトの専用リンクがあるのに参加者表示が無い(運営規約が表示を求めている)",
            )
        )
    if disclosure_line is not None and not has_affiliate_link:
        findings.append(
            Finding(
                path,
                disclosure_line,
                LEVEL_WARNING,
                "false-disclosure",
                "参加者表示があるのに専用リンク(tag= 付き)が無い。"
                "まだ参加していないなら、収入を得ているという記述は事実に反する",
            )
        )
    return findings


def _check_sentences(path: str, prose: list[_ProseLine]) -> list[Finding]:
    """一文の長さと、文末の単調さを見る。"""
    findings: list[Finding] = []
    sentences = _sentences(prose)

    for number, sentence in sentences:
        if len(sentence) > SENTENCE_MAX_CHARS:
            findings.append(
                Finding(
                    path,
                    number,
                    LEVEL_WARNING,
                    "long-sentence",
                    f"一文が {len(sentence)} 字。{SENTENCE_MAX_CHARS} 字を超えると読みにくい",
                )
            )

    # 文末3文字を署名にして、同じ終わり方が続いていないか見る
    run_ending: str | None = None
    run_start = 0
    run_length = 0
    for idx, (number, sentence) in enumerate(sentences):
        body = sentence.rstrip("。")
        ending = body[-3:] if len(body) >= 3 else body
        if ending and ending == run_ending:
            run_length += 1
        else:
            run_ending, run_length, run_start = ending, 1, idx
        if run_length == SAME_ENDING_RUN:
            findings.append(
                Finding(
                    path,
                    sentences[run_start][0],
                    LEVEL_WARNING,
                    "monotonous",
                    f"文末「{ending}」が {SAME_ENDING_RUN} 文続いている",
                )
            )
    return findings


# --------------------------------------------------------------------------
# まとめ
# --------------------------------------------------------------------------

def lint_text(
    markdown: str,
    *,
    path: str = "<text>",
    min_chars: int | None = TARGET_MIN_CHARS,
    max_chars: int | None = TARGET_MAX_CHARS,
) -> LintReport:
    """Markdown 文字列を検査する。"""
    prose, meta, title = _prose_lines(markdown)

    try:
        allowance = int(meta.get("allow_first_person", "0"))
    except ValueError:
        allowance = 0

    # 文字数は投稿時と同じ数え方をする(別々に数えると値がずれるため)
    converted = convert(markdown)
    report = LintReport(path=path, text_length=converted.text_length)

    fp_findings, fp_total, watashiwa = _check_first_person(path, prose, allowance)
    report.first_person_count = fp_total
    report.watashiwa_count = watashiwa

    report.findings.extend(_check_title(path, title))
    report.findings.extend(fp_findings)
    report.findings.extend(_check_length(path, report.text_length, min_chars, max_chars))
    report.findings.extend(_check_headings(path, markdown))
    report.findings.extend(_check_unsupported(path, markdown))
    report.findings.extend(_check_unpublished_section(path, markdown))
    report.findings.extend(_check_affiliate(path, markdown))
    report.findings.extend(_check_sentences(path, prose))
    report.findings.sort(key=lambda f: (f.line, f.code))
    return report


def lint_file(
    path: str,
    *,
    min_chars: int | None = TARGET_MIN_CHARS,
    max_chars: int | None = TARGET_MAX_CHARS,
) -> LintReport:
    """Markdown ファイルを検査する。"""
    with open(path, encoding="utf-8") as f:
        return lint_text(f.read(), path=path, min_chars=min_chars, max_chars=max_chars)


def format_report(report: LintReport) -> str:
    """人が読む形に整える。"""
    lines = [
        f"{report.path}",
        f"  本文 {report.text_length} 字 / "
        f"一人称 {report.first_person_count} 件(うち「私は」{report.watashiwa_count} 件)",
    ]
    if report.findings:
        lines.append("")
        lines.extend("  " + f.format() for f in report.findings)
    else:
        lines.append("  指摘なし")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """エントリポイント。"""
    parser = argparse.ArgumentParser(
        prog="note_lint.py", description="note 記事の文体を機械的に検査する"
    )
    parser.add_argument("files", nargs="+", help="検査する Markdown ファイル")
    parser.add_argument("--min-chars", type=int, default=TARGET_MIN_CHARS, help="本文の下限の目安")
    parser.add_argument("--max-chars", type=int, default=TARGET_MAX_CHARS, help="本文の上限の目安")
    parser.add_argument(
        "--strict", action="store_true", help="warning も失敗として扱う(終了コードを非ゼロにする)"
    )
    parser.add_argument("--json", action="store_true", help="結果を JSON で出力する")
    args = parser.parse_args(argv)

    reports = [
        lint_file(p, min_chars=args.min_chars, max_chars=args.max_chars) for p in args.files
    ]

    if args.json:
        print(
            json.dumps(
                [
                    {
                        "path": r.path,
                        "text_length": r.text_length,
                        "first_person_count": r.first_person_count,
                        "watashiwa_count": r.watashiwa_count,
                        "findings": [
                            {"line": f.line, "level": f.level, "code": f.code, "message": f.message}
                            for f in r.findings
                        ],
                    }
                    for r in reports
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print("\n\n".join(format_report(r) for r in reports))

    total_errors = sum(len(r.errors) for r in reports)
    total_warnings = sum(len(r.warnings) for r in reports)
    if total_errors or (args.strict and total_warnings):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
