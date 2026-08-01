"""記録済みレースを読み込み、情報源ごとの較正スコアを出力する。

使い方:
    python3 analysis/keiba/report.py

不完全な記録は落とさずに、計算できる指標だけを出す。
「全馬のオッズが揃うまで何も出ない」設計にすると n が増えないため。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from calibration import (  # noqa: E402
    binomial_two_sided_p,
    brier,
    concentration,
    divergence_ratio,
    entropy,
    kl_divergence,
    log_loss,
)
from schema import InsufficientQuote, Race, load_races  # noqa: E402

RECORDS_DIR = Path(__file__).resolve().parent / "records"

# 事後の基準となる情報源。ここに対する相対でしか予想の良し悪しは語れない。
BASELINE_SOURCE = "確定"
# 較正リーダーボードの主対象。紙面がこれに勝てているかが最初の問い。
PAPER_SOURCE = "紙面"

DASH = "—"


def _fmt(value: float | None, digits: int = 3) -> str:
    return DASH if value is None else f"{value:.{digits}f}"


def evaluate_quote(race: Race, quote: Any) -> dict[str, Any]:
    """1つの見積もりについて、計算できる指標だけを埋めた辞書を返す。"""
    row: dict[str, Any] = {
        "source": quote.source,
        "captured_at": quote.captured_at,
        "n_odds": len(quote.odds),
        "overround": None,
        "basis": quote.overround_basis,
        "p_winner": None,
        "log_loss": None,
        "brier": None,
        "entropy": None,
        "concentration": None,
        "skip": None,
    }
    if not quote.odds and quote.declared_overround is None:
        row["skip"] = "未取得"
        return row

    try:
        row["overround"] = quote.overround(race.field_size)
    except InsufficientQuote as e:
        row["skip"] = str(e)
        row["overround"] = quote.overround_lower_bound()
        return row

    if race.result is None:
        row["skip"] = "結果未確定"
    else:
        winner = race.result.winner
        try:
            p = quote.probability(winner, race.field_size)
            row["p_winner"] = p
            row["log_loss"] = log_loss(p)
        except InsufficientQuote:
            row["skip"] = f"1着({winner}番)のオッズ未記録"

    try:
        probs = quote.probabilities(race.field_size)
    except InsufficientQuote:
        return row

    row["entropy"] = entropy(probs)
    row["concentration"] = concentration(probs)
    if race.result is not None:
        row["brier"] = brier(probs, race.result.winner)
    return row


def race_report(race: Race) -> list[str]:
    """1レース分の明細。"""
    head = f"{race.race_id}  {race.course}{race.race_no}R {race.name}".rstrip()
    # 未取得の項目は空欄を並べず、そもそも表示しない
    parts = [p for p in (race.klass, f"{race.surface}{race.distance_m}m" if race.distance_m else "") if p]
    parts.append(f"頭数{race.field_size}" if race.field_size else "頭数不明")
    lines = ["", head, f"  {' '.join(parts)} [{race.status}]"]

    if not race.quotes:
        lines.append(f"  (見積もりなし) {race.note}")
        return lines

    lines.append(
        f"  {'情報源':<10}{'逆数和':>8}{'根拠':>16}{'p(1着)':>10}{'logloss':>10}{'Brier':>9}{'entropy':>9}"
    )
    rows = []
    for quote in race.timeline():
        row = evaluate_quote(race, quote)
        rows.append(row)
        lines.append(
            f"  {row['source']:<10}"
            f"{_fmt(row['overround']):>8}"
            f"{row['basis']:>16}"
            f"{_fmt(row['p_winner'], 4):>10}"
            f"{_fmt(row['log_loss']):>10}"
            f"{_fmt(row['brier']):>9}"
            f"{_fmt(row['entropy']):>9}"
        )
        if row["skip"]:
            lines.append(f"      └ {row['skip']}")

    lines.extend(_race_comparisons(race))
    return lines


def _race_comparisons(race: Race) -> list[str]:
    """紙面と確定の突き合わせ。乖離倍率と分布間距離。"""
    paper = race.quote(PAPER_SOURCE) or (race.timeline()[0] if race.quotes else None)
    final = race.quote(BASELINE_SOURCE)
    if paper is None or final is None or race.result is None:
        return []

    lines = []
    winner = race.result.winner
    if winner in paper.odds and winner in final.odds:
        ratio = divergence_ratio(paper.odds[winner], final.odds[winner])
        direction = "過小評価" if ratio > 1 else "過大評価"
        lines.append(
            f"  乖離: 1着{winner}番 {paper.source}{paper.odds[winner]}倍 → "
            f"{final.source}{final.odds[winner]}倍 = {ratio:.2f}倍 ({paper.source}が{direction})"
        )
    try:
        p_final = final.probabilities(race.field_size)
        p_paper = paper.probabilities(race.field_size)
        lines.append(f"  KL(確定||{paper.source}) = {kl_divergence(p_final, p_paper):.4f}")
    except InsufficientQuote:
        pass
    return lines


def aggregate(races: list[Race]) -> list[str]:
    """全レース横断の集計。ここが台帳の本体。"""
    lines = ["", "=" * 78, "集計", "=" * 78]

    # --- 情報源ごとの平均スコア ---
    scores: dict[str, list[float]] = {}
    for race in races:
        for quote in race.quotes:
            row = evaluate_quote(race, quote)
            if row["log_loss"] is not None:
                scores.setdefault(quote.source, []).append(row["log_loss"])

    if scores:
        lines.append("")
        lines.append(f"  {'情報源':<12}{'n':>4}{'平均logloss':>14}")
        for source, values in sorted(scores.items(), key=lambda kv: -len(kv[1])):
            lines.append(f"  {source:<12}{len(values):>4}{sum(values) / len(values):>14.3f}")
        lines.append("  ※ 小さいほど良い。確定(市場)を上回れない予想は情報を足していない。")

    # --- 仮説H1: 紙面は1着馬を系統的に過小評価する ---
    lines.extend(_sign_test_underestimation(races))

    # --- 仮説H2: 乖離はクラスの情報厚みで決まる ---
    lines.extend(_divergence_by_class(races))

    lines.append("")
    lines.append("  未取得・保留:")
    pending = [r for r in races if r.status == "pending"]
    lines.extend(f"    {r.race_id} {r.name}: {r.note.splitlines()[0]}" for r in pending)
    if not pending:
        lines.append("    なし")
    return lines


def _sign_test_underestimation(races: list[Race]) -> list[str]:
    """H1: 紙面の分布は市場より平坦で、1着馬(=実際の上位人気)を過小評価する。

    各レースで p_紙面(1着) < p_確定(1着) かを数え、符号検定にかける。
    偏りがなければ半々になるはず、が帰無仮説。
    """
    hits, total, detail = 0, 0, []
    for race in races:
        paper, final = race.quote(PAPER_SOURCE), race.quote(BASELINE_SOURCE)
        if paper is None or final is None or race.result is None:
            continue
        try:
            pp = paper.probability(race.result.winner, race.field_size)
            pf = final.probability(race.result.winner, race.field_size)
        except InsufficientQuote:
            continue
        total += 1
        under = pp < pf
        hits += int(under)
        detail.append(f"    {race.race_id}: 紙面{pp:.4f} vs 確定{pf:.4f} → {'過小' if under else '過大'}")

    lines = ["", "  H1 紙面は1着馬を過小評価するか(符号検定)"]
    lines.extend(detail)
    if total == 0:
        lines.append("    判定可能なレースなし")
        return lines
    p = binomial_two_sided_p(hits, total)
    lines.append(f"    {hits}/{total} で過小評価、両側p={p:.4f}")
    if total < 5:
        lines.append(f"    ※ n={total}ではどう転んでも有意にならない。方向の記録として積むだけ。")
    return lines


def _divergence_by_class(races: list[Race]) -> list[str]:
    """H2: 情報層が厚いクラスほど紙面と市場の乖離は小さい。"""
    lines = ["", "  H2 乖離倍率とクラスの情報厚み"]
    rows = []
    for race in races:
        paper, final = race.quote(PAPER_SOURCE), race.quote(BASELINE_SOURCE)
        if paper is None or final is None or race.result is None:
            continue
        w = race.result.winner
        if w in paper.odds and w in final.odds:
            rows.append((race.klass or "不明", divergence_ratio(paper.odds[w], final.odds[w]), race.race_id))
    if not rows:
        lines.append("    判定可能なレースなし")
        return lines
    for klass, ratio, rid in sorted(rows, key=lambda r: -r[1]):
        lines.append(f"    {klass:<12}{ratio:>6.2f}倍  ({rid})")
    lines.append("    ※ キャリアの浅いクラスほど大きく出るはず、が仮説。共変量は career_starts に入れる。")
    return lines


def main() -> None:
    races = load_races(RECORDS_DIR)
    print("=" * 78)
    print(f"競馬予想 較正台帳  記録{len(races)}レース")
    print("=" * 78)
    for race in races:
        print("\n".join(race_report(race)))
    print("\n".join(aggregate(races)))


if __name__ == "__main__":
    main()
