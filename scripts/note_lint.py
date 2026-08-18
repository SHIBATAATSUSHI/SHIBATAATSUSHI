#!/usr/bin/env python3
"""note 原稿の公開前チェッカー(note.com/19770104 専用)。

大学病院に勤務する理学療法士が書く記事を前提に、公開前に人間が必ず見るべき箇所を洗い出す。
判定は「疑わしきは指摘する」方針で、最終判断は人間が行う前提。

注意: ルールは医療・リハビリのテーマに合わせてある。創作・エッセイなど別ジャンルの原稿に
当てると、登場人物の年齢・性別の描写を患者情報と誤判定するなど、正しく機能しない。

重大度:
  high   … 公開を止めるべき。医学的断定・患者情報・所属特定など。
  medium … 公開前に必ず確認する。出典の欠落、誇張、判断主体の曖昧さなど。
  low    … 推敲の余地。AIっぽい定型句、主語の反復、文の長さなど。

単体でも使える:
  python scripts/note_lint.py posts/example.md
  python scripts/note_lint.py posts/example.md --strict --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

SEVERITY_ORDER = {"low": 1, "medium": 2, "high": 3}
SEVERITY_LABEL = {"high": "重大", "medium": "要確認", "low": "推敲"}


@dataclass
class Finding:
    """指摘1件。"""

    rule: str
    severity: str
    line: int
    excerpt: str
    message: str
    hint: str = ""


# ---------------------------------------------------------------------------
# 正規表現ベースのルール
#
# (ルールID, 重大度, パターン, 指摘内容, 対処のヒント)
# ---------------------------------------------------------------------------

PATTERN_RULES: list[tuple[str, str, str, str, str]] = [
    # --- 医学的な断定・医療広告に触れる表現 -------------------------------
    (
        "medical/cure",
        "high",
        r"(治りま[すし]|完治しま?す|治せま?す|必ず改善|確実に改善|効果は絶大)",
        "治癒・改善を断定している。医療広告および医学的妥当性の観点で危険。",
        "「〜という報告がある」「個人差がある」等、断定を避けた表現に直す。",
    ),
    (
        "medical/diagnose",
        "high",
        r"(自己診断|診断できま?す|診断してくださ|受診は不要|病院に行かなくて)",
        "診断行為や受診回避を促す表現。医師法・安全性の観点で不可。",
        "判断は主治医・担当医の領分であることを明記する。",
    ),
    (
        "medical/medication",
        "high",
        r"(薬を(やめ|止め|減らし|中止)|服薬を(やめ|中止|調整)|処方を変え)",
        "服薬の変更を示唆している。医療者の記事として重大なリスク。",
        "服薬に関する記述は削除するか、医師の判断による旨を明記する。",
    ),
    (
        "medical/absolute",
        "medium",
        r"(必ず|絶対に|100[%％]|誰でも|全員が|例外なく|間違いなく)",
        "断定・一般化の表現。臨床では例外が生じるため誇張になりやすい。",
        "条件や対象を限定する(「〜の場合は」「多くは」)。",
    ),
    (
        "medical/superlative",
        "medium",
        r"(日本初|世界初|No\.?1|ナンバーワン|最高の|最強の|唯一の方法)",
        "最上級・優位性の表現。医療広告ガイドラインで問題になりやすい。",
        "客観的に検証できない優位性の主張は削る。",
    ),
    # --- 患者情報・所属の特定 ---------------------------------------------
    (
        "privacy/case",
        "high",
        r"(\d{1,3}\s*歳(代)?\s*(の)?(男性|女性|男児|女児)|症例[0-9A-Za-z]|患者[A-ZＡ-Ｚ]さん)",
        "個別症例と読める記述。患者が特定されうる。",
        "実症例は使わない。一般化した記述か、完全な仮想例である旨を明記する。",
    ),
    (
        "privacy/record",
        "high",
        r"(カルテ|診療録|電子カルテ|担当患者|受け持ち患者|入院中の患者)",
        "診療情報に触れる記述。院内規程・個人情報保護の確認が必須。",
        "診療情報は書かない。書く場合は一般論に置き換える。",
    ),
    (
        "privacy/affiliation",
        "high",
        r"(当院|勤務先の病院|所属する病院|うちの病院|自施設の)",
        "所属施設が推定できる記述。施設の広報規程に抵触する可能性。",
        "「大学病院に勤務する理学療法士として」程度の一般化に留める。",
    ),
    # --- 出典・エビデンス ---------------------------------------------------
    (
        "citation/vague",
        "medium",
        r"(研究によ(れば|ると)|論文で(は|も)|エビデンスがあ|科学的に(証明|実証)|一般的に言われ)",
        "研究・エビデンスに言及しているが、出典が近くに見当たらない。",
        "一次情報のURLを併記するか、記述を削る。",
    ),
    # --- Stop Slop(AIっぽさ)------------------------------------------------
    (
        "slop/cliche",
        "low",
        r"(いかがでした|まとめると|結論から(言|述べ)|ではないでしょうか|"
        r"と言えるでしょう|ぜひ.{0,10}してみてください|深掘り|本質的に|"
        r"言っても過言ではない|欠かせない|昨今|とはいえ、|重要なのは)",
        "AI生成文に頻出する定型句。",
        "その一文が無くても意味が通るなら削る。",
    ),
    (
        "slop/contrast",
        "low",
        r"(ではなく[、,]|だけでは(ない|なく)|単なる.{0,12}ではな|"
        r"重要なのは.{0,20}である)",
        "「AではなくB」型の対比構文。多用するとAIらしい単調さが出る。",
        "3回以上出るなら、具体例や手順の記述に置き換える。",
    ),
    (
        "slop/listicle",
        "low",
        r"(([0-9１-９])つのポイント|([0-9１-９])つのコツ|徹底解説|完全ガイド|保存版)",
        "量産系タイトルに典型的な言い回し。",
        "内容を具体的に示す語(手順・確認表・判断基準)に置き換える。",
    ),
    # --- 文体 ---------------------------------------------------------------
    (
        "style/emoji",
        "low",
        r"[\U0001F300-\U0001FAFF☀-➿]",
        "絵文字が含まれる。専門職向け記事では浮きやすい。",
        "削除するか、使う場所を決めて統一する。",
    ),
    (
        "style/exclaim",
        "low",
        r"[!！]{1,}",
        "感嘆符。専門記事ではトーンが崩れやすい。",
        "平叙文に直す。",
    ),
]

# 判断主体が曖昧になりやすい述語。件数だけ数えて確認を促す。
JUDGEMENT_PATTERN = re.compile(
    r"(と考えられ(る|ます)|とされ(る|ています|ます)|が求められ(る|ます)|"
    r"すべきであ(る|ります)|が望ましい|と判断され(る|ます)|必要があ(る|ります))"
)

FIRST_PERSON_PATTERN = re.compile(r"(私は|私が|私の|自分は)")

SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?])")


def _excerpt(text: str, limit: int = 56) -> str:
    """指摘表示用に1行を短く整形する。"""
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _iter_lines(body: str):
    """(行番号, 行文字列) を返す。行番号は本文先頭を1とする。"""
    for index, line in enumerate(body.splitlines(), start=1):
        yield index, line


def _is_skippable(line: str, in_code: bool) -> bool:
    """コードブロック・引用元URL行はチェック対象から外す。"""
    if in_code:
        return True
    stripped = line.strip()
    return stripped.startswith(("```", "> 出典", "参考:", "出典:"))


def _check_patterns(body: str) -> list[Finding]:
    """正規表現ルールを本文に適用する。"""
    findings: list[Finding] = []
    in_code = False
    for line_no, line in _iter_lines(body):
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if _is_skippable(line, in_code):
            continue
        for rule, severity, pattern, message, hint in PATTERN_RULES:
            if re.search(pattern, line):
                findings.append(
                    Finding(rule, severity, line_no, _excerpt(line), message, hint)
                )
    return findings


def _check_citations(body: str) -> list[Finding]:
    """数値の主張が出てくる段落に出典URLがあるかを見る。"""
    findings: list[Finding] = []
    line_no = 0
    for block in body.split("\n\n"):
        block_start = line_no + 1
        line_no += len(block.split("\n")) + 1
        if block.strip().startswith("```"):
            continue
        has_number_claim = re.search(r"\d+(\.\d+)?\s*[%％倍]|\d{4}年の(研究|調査|報告)", block)
        if has_number_claim and "http" not in block:
            findings.append(
                Finding(
                    "citation/number",
                    "medium",
                    block_start,
                    _excerpt(block.replace("\n", " ")),
                    "数値を伴う主張に出典URLが見当たらない。",
                    "一次情報(公的機関・原著論文)のURLを併記する。",
                )
            )
    return findings


def _check_first_person(body: str) -> list[Finding]:
    """「私は」の反復を数える(本人の希望で主語は省く方針)。"""
    hits: list[tuple[int, str]] = []
    for line_no, line in _iter_lines(body):
        for _ in FIRST_PERSON_PATTERN.finditer(line):
            hits.append((line_no, line))
    if not hits:
        return []
    line_numbers = sorted({line_no for line_no, _ in hits})
    severity = "medium" if len(hits) >= 3 else "low"
    line_no, line = hits[0]
    return [
        Finding(
            "voice/first-person",
            severity,
            line_no,
            _excerpt(line),
            f"一人称の主語が{len(hits)}箇所ある(行: "
            f"{', '.join(str(n) for n in line_numbers[:10])})。",
            "主語を省いても誰の判断か伝わるよう、文脈側で示す。",
        )
    ]


def _check_judgement_subject(body: str) -> list[Finding]:
    """判断主体が曖昧になりやすい述語の件数を確認項目として出す。"""
    hits = [
        (line_no, line)
        for line_no, line in _iter_lines(body)
        if JUDGEMENT_PATTERN.search(line)
    ]
    if len(hits) < 4:
        return []
    line_no, line = hits[0]
    return [
        Finding(
            "voice/subject",
            "low",
            line_no,
            _excerpt(line),
            f"主体を書かない評価・判断の述語が{len(hits)}箇所ある。",
            "誰の判断か(理学療法士/主治医/所属施設/筆者)を明示できているか確認する。",
        )
    ]


def _collect_sentences(body: str) -> list[tuple[int, str]]:
    """本文から (行番号, 文) の一覧を作る。見出し・箇条書き・コードは除く。"""
    sentences: list[tuple[int, str]] = []
    in_code = False
    for line_no, line in _iter_lines(body):
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code or line.strip().startswith(("#", "-", "*", ">", "|")):
            continue
        for sentence in SENTENCE_SPLIT.split(line):
            if len(sentence.strip()) > 4:
                sentences.append((line_no, sentence.strip()))
    return sentences


def _check_monotony(body: str) -> list[Finding]:
    """同じ書き出しの文が続いていないかを見る。

    「私は〜。私は〜。私は〜。」のような反復は、本人が最も嫌う単調さ。
    """
    findings: list[Finding] = []
    sentences = _collect_sentences(body)

    run_start = 0
    for index in range(1, len(sentences) + 1):
        same = index < len(sentences) and (
            sentences[index][1][:3] == sentences[run_start][1][:3]
        )
        if not same:
            run = index - run_start
            if run >= 3:
                line_no, sentence = sentences[run_start]
                findings.append(
                    Finding(
                        "slop/repeated-opening",
                        "low",
                        line_no,
                        _excerpt(sentence),
                        f"「{sentence[:3]}」で始まる文が{run}連続している。",
                        "書き出しを変える。主語を省く、条件節から始める、体言止めを挟む。",
                    )
                )
            run_start = index
    return findings


def _check_rhythm(body: str) -> list[Finding]:
    """文の長さが揃いすぎていないかを見る(AI生成文にありがちな均質さ)。"""
    sentences = _collect_sentences(body)
    if len(sentences) < 8:
        return []
    window = 6
    for start in range(len(sentences) - window + 1):
        chunk = [len(s) for _, s in sentences[start : start + window]]
        mean = sum(chunk) / window
        if mean < 20:
            continue
        if max(chunk) - min(chunk) <= mean * 0.25:
            line_no, sentence = sentences[start]
            return [
                Finding(
                    "slop/uniform-rhythm",
                    "low",
                    line_no,
                    _excerpt(sentence),
                    f"{window}文が同じくらいの長さ(平均{mean:.0f}文字)で並んでいる。",
                    "短い文を混ぜて緩急をつける。",
                )
            ]
    return []


def _check_length(body: str) -> list[Finding]:
    """長すぎる一文を拾う。"""
    findings: list[Finding] = []
    for line_no, line in _iter_lines(body):
        if line.strip().startswith(("```", "|", ">")):
            continue
        for sentence in SENTENCE_SPLIT.split(line):
            if len(sentence.strip()) > 100:
                findings.append(
                    Finding(
                        "style/long-sentence",
                        "low",
                        line_no,
                        _excerpt(sentence),
                        f"一文が{len(sentence.strip())}文字ある。",
                        "2文に割る。読点で繋ぎすぎない。",
                    )
                )
    return findings


def _check_title(title: str) -> list[Finding]:
    """タイトル単体のチェック。"""
    findings: list[Finding] = []
    if len(title) > 60:
        findings.append(
            Finding(
                "title/length",
                "low",
                0,
                _excerpt(title),
                f"タイトルが{len(title)}文字。一覧で末尾が切れやすい。",
                "60文字以内、前半30文字で主旨が分かる形にする。",
            )
        )
    if FIRST_PERSON_PATTERN.search(title):
        findings.append(
            Finding(
                "title/first-person",
                "medium",
                0,
                _excerpt(title),
                "タイトルに一人称の主語が入っている。",
                "主語を省いた形に直す。",
            )
        )
    for rule, severity, pattern, message, hint in PATTERN_RULES:
        if rule.startswith(
            ("medical/", "slop/listicle", "style/exclaim", "style/emoji")
        ) and re.search(pattern, title):
            findings.append(
                Finding(f"title:{rule}", severity, 0, _excerpt(title), message, hint)
            )
    return findings


def lint(title: str, body: str) -> list[Finding]:
    """タイトルと本文をチェックし、重大度順に並べた指摘を返す。"""
    findings: list[Finding] = []
    findings += _check_title(title)
    findings += _check_patterns(body)
    findings += _check_citations(body)
    findings += _check_first_person(body)
    findings += _check_judgement_subject(body)
    findings += _check_monotony(body)
    findings += _check_rhythm(body)
    findings += _check_length(body)
    findings.sort(key=lambda f: (-SEVERITY_ORDER[f.severity], f.line))
    return findings


def worst_severity(findings: list[Finding]) -> str | None:
    """最も重い重大度を返す。指摘が無ければ None。"""
    if not findings:
        return None
    return max(findings, key=lambda f: SEVERITY_ORDER[f.severity]).severity


def format_findings(findings: list[Finding]) -> str:
    """ターミナル表示用に整形する。"""
    if not findings:
        return "  指摘なし"
    lines = []
    for finding in findings:
        place = f"L{finding.line}" if finding.line else "タイトル"
        lines.append(
            f"  [{SEVERITY_LABEL[finding.severity]}] {place} {finding.rule}\n"
            f"      {finding.message}\n"
            f"      > {finding.excerpt}\n"
            f"      → {finding.hint}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 公開前確認票
# ---------------------------------------------------------------------------

MANUAL_CHECKS = [
    "医学的妥当性: 記述の根拠を確認したか(一次情報にあたったか)",
    "患者情報: 実在の患者・症例を推定できる記述が無いか",
    "所属規程: 勤務先の広報・SNS規程に触れていないか",
    "引用: 引用範囲が適切で、出典URLが生きているか",
    "経験: 自分の経験として書いた箇所が事実か(創作が混ざっていないか)",
    "読者: 想定読者(若手〜中堅リハ職)に向いた具体性があるか",
    "タイトル: 読むと何ができるようになるかが分かるか",
    "タグ・マガジン: 分類先が決まっているか",
    "無料/有料: 今回の位置づけ(無料何本目か、有料なら価格)が方針と合っているか",
]


def render_review_sheet(title: str, findings: list[Finding], source: str = "") -> str:
    """公開前確認票(Markdown)を生成する。"""
    from datetime import date

    lines = [
        f"# 公開前確認票: {title}",
        "",
        f"- 作成日: {date.today().isoformat()}",
        f"- 原稿: {source or '(未指定)'}",
        "",
        "## 自動チェック結果",
        "",
    ]
    if not findings:
        lines.append("指摘なし。")
    else:
        counts: dict[str, int] = {}
        for finding in findings:
            counts[finding.severity] = counts.get(finding.severity, 0) + 1
        summary = " / ".join(
            f"{SEVERITY_LABEL[sev]} {counts[sev]}件"
            for sev in ("high", "medium", "low")
            if sev in counts
        )
        lines += [summary, ""]
        lines.append("| 重大度 | 位置 | ルール | 指摘 | 対処 |")
        lines.append("| --- | --- | --- | --- | --- |")
        for finding in findings:
            place = f"L{finding.line}" if finding.line else "タイトル"
            message = finding.message.replace("|", "/")
            hint = finding.hint.replace("|", "/")
            lines.append(
                f"| {SEVERITY_LABEL[finding.severity]} | {place} | "
                f"{finding.rule} | {message} | {hint} |"
            )
    lines += ["", "## 人間が確認する項目", ""]
    lines += [f"- [ ] {item}" for item in MANUAL_CHECKS]
    lines += [
        "",
        "## 公開判断",
        "",
        "- [ ] 上記をすべて確認した",
        "- [ ] note の下書きを目視で確認した",
        "- [ ] 公開する",
        "",
        "確認者: ______________  日付: ____/__/__",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI(単体実行用)
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="note_lint.py", description="note 原稿の公開前チェック"
    )
    parser.add_argument("file", help="チェックする Markdown ファイル")
    parser.add_argument("--json", action="store_true", help="JSON で出力する")
    parser.add_argument(
        "--strict", action="store_true", help="要確認レベルでも異常終了する"
    )
    parser.add_argument("--review-sheet", type=Path, help="公開前確認票の出力先ファイル")
    args = parser.parse_args(argv)

    # note_post 側の front matter パーサを使い回す
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from note_post import NotePostError, load_article  # noqa: E402

    try:
        article = load_article(Path(args.file))
    except NotePostError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1

    findings = lint(article.title, article.body)

    if args.json:
        print(json.dumps([asdict(f) for f in findings], ensure_ascii=False, indent=2))
    else:
        print(f"チェック対象: {args.file}")
        print(f"タイトル    : {article.title}")
        print(format_findings(findings))

    if args.review_sheet:
        args.review_sheet.parent.mkdir(parents=True, exist_ok=True)
        args.review_sheet.write_text(
            render_review_sheet(article.title, findings, args.file), encoding="utf-8"
        )
        print(f"\n公開前確認票: {args.review_sheet}")

    worst = worst_severity(findings)
    if worst == "high":
        return 2
    if worst == "medium" and args.strict:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
