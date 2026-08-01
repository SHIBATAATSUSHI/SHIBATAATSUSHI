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
    log_gap,
    log_loss,
    movement_share,
)
from schema import InsufficientQuote, Quote, Race, load_races  # noqa: E402

RECORDS_DIR = Path(__file__).resolve().parent / "records"

# 事後の基準となる情報源。ここに対する相対でしか予想の良し悪しは語れない。
BASELINE_SOURCE = "確定"
# 較正リーダーボードの主対象。紙面がこれに勝てているかが最初の問い。
PAPER_SOURCE = "紙面"
# 市場が開いた最初の時点。ここから確定までが「同一市場内の情報流入」。
MARKET_OPEN_SOURCE = "前売り"

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
    lines.extend(_inflow_section(race))
    return lines


def focus_horse(race: Race) -> int | None:
    """情報流入曲線を追う対象馬。

    結果が出ていれば1着馬。まだなら最初の時点での1番人気(=最も金が集まった馬)。
    結果を待たずに曲線を引けるようにするため、事前に決め打ちできる規則にしてある。
    """
    if race.result is not None:
        return race.result.winner
    for quote in race.timeline():
        if quote.odds:
            return min(quote.odds, key=lambda h: quote.odds[h])
    return None


def _inflow_section(race: Race) -> list[str]:
    """情報流入曲線。同一市場内で確定値までの動きがいつ入ったかを見る。

    3時点以上そろって初めて「曲線」になるので、それ未満では出さない。
    """
    horse = focus_horse(race)
    final = race.quote(BASELINE_SOURCE)
    if horse is None or final is None or horse not in final.odds:
        return []

    series = [q for q in race.timeline() if horse in q.odds and q.source != PAPER_SOURCE]
    if len(series) < 3:
        return []

    try:
        p_final = final.probability(horse, race.field_size)
        p_start = series[0].probability(horse, race.field_size)
    except InsufficientQuote:
        return []

    lines = ["", f"  情報流入曲線 ({horse}番) — 確定までの動きの何割が済んでいたか"]
    for quote in series:
        try:
            p = quote.probability(horse, race.field_size)
        except InsufficientQuote:
            continue
        share = movement_share(p_start, p, p_final)
        stamp = quote.captured_at or "時刻未記録"
        lines.append(
            f"    {quote.source:<10}{stamp:<22}{quote.odds[horse]:>7.1f}倍"
            f"  p={p:.4f}  進捗={'—' if share is None else f'{share * 100:5.1f}%'}"
        )
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

    # --- 仮説H3: 同一市場内の情報流入は紙面→確定より小さい ---
    lines.extend(_h3_market_vs_paper(races))

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


def _gap(quote: Quote | None, final: Quote, horse: int, field_size: int | None) -> float | None:
    """ある時点から確定までの対数ギャップの大きさ。測れなければ None。"""
    if quote is None or horse not in quote.odds or horse not in final.odds:
        return None
    try:
        return abs(log_gap(quote.probability(horse, field_size), final.probability(horse, field_size)))
    except InsufficientQuote:
        return None


def _h3_market_vs_paper(races: list[Race]) -> list[str]:
    """H3: 同一市場内の情報流入は、紙面→確定の乖離より小さい。

    必ず同一レース内で比べる。紙面→確定を別レース(別クラス)の値と比べると、
    情報厚みの差(H2)と区別がつかなくなり、何を測ったのか言えなくなる。
    """
    lines = ["", "  H3 同一市場内の情報流入 vs 紙面→確定(同一レース内で比較)"]
    testable, confounded = [], []

    for race in races:
        final = race.quote(BASELINE_SOURCE)
        horse = focus_horse(race)
        if final is None or horse is None:
            continue
        field_size = race.field_size
        d_paper = _gap(race.quote(PAPER_SOURCE), final, horse, field_size)
        d_market = _gap(race.quote(MARKET_OPEN_SOURCE), final, horse, field_size)

        if d_paper is not None and d_market is not None:
            testable.append((race, horse, d_paper, d_market))
        elif d_paper is not None or d_market is not None:
            have = "紙面のみ" if d_paper is not None else "前売りのみ"
            confounded.append((race, have))

    for race, horse, d_paper, d_market in testable:
        holds = d_market < d_paper
        lines.append(
            f"    {race.race_id} ({horse}番): "
            f"|紙面→確定|={d_paper:.3f}  |前売り→確定|={d_market:.3f}  "
            f"→ H3{'と整合' if holds else 'に反する'}"
        )
    if testable:
        k = sum(1 for _, _, dp, dm in testable if dm < dp)
        n = len(testable)
        lines.append(f"    {k}/{n} で整合、両側p={binomial_two_sided_p(k, n):.4f}")

    for race, have in confounded:
        lines.append(
            f"    {race.race_id}: {have}のため判定不能。"
            "他レースの紙面と比べるとH2(情報厚み)と交絡するので比較しない"
        )
    if not testable and not confounded:
        lines.append("    判定可能なレースなし")
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
