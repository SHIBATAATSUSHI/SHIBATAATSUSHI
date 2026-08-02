"""市場そのものの較正を測る。

問いは「予想が市場に勝てるか」ではなく、その前段の「市場自身はどれだけ較正されているか」。
これが分からないまま市場を基準に使うと、予想の良し悪しを語る土台が空洞になる。

主表は較正曲線ではなく**オッズ層別の期待回収率**にする。控除率が分かっているぶん
基準値が一意に決まる(単勝なら払戻率0.80)ためで、較正曲線より解釈で揉める余地が小さい。
バイアスがなければ全ビンが0.80に張り付く。人気側が0.80近辺で穴側が大きく下回るなら、
それが favorite-longshot bias。
"""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from calibration import TAKEOUT, brier, log_loss, reliability_bins, wilson_interval
from store import RaceRows, iter_race_rows

WIN_PAYOUT_RATE = TAKEOUT["単勝"]


@dataclass(slots=True)
class Observation:
    """1レース1頭ぶんの観測。"""

    odds: float
    implied_p: float
    won: bool


@dataclass
class MarketSummary:
    """市場全体のスコア。予想はすべてこの数字を基準に語る。"""

    races: int = 0
    runners: int = 0
    skipped_no_winner: int = 0
    mean_overround: float = 0.0
    mean_log_loss: float = 0.0
    mean_brier: float = 0.0


def scan(
    conn: sqlite3.Connection, quote_source: str = "確定"
) -> tuple[list[Observation], MarketSummary]:
    """DBを1回なめて、観測列と全体スコアを同時に作る。"""
    return summarize(iter_race_rows(conn, quote_source))


def summarize(races: Iterable[RaceRows]) -> tuple[list[Observation], MarketSummary]:
    """レース列から観測とスコアを作る(DBに依存しないので合成データでも使える)。"""
    obs: list[Observation] = []
    summary = MarketSummary()
    overround_total = log_loss_total = brier_total = 0.0

    for race in races:
        if race.winner is None:
            summary.skipped_no_winner += 1
            continue
        z = sum(1.0 / o for o in race.odds.values())
        probs = {h: (1.0 / o) / z for h, o in race.odds.items()}

        for horse, odds in race.odds.items():
            obs.append(Observation(odds=odds, implied_p=probs[horse], won=horse == race.winner))

        summary.races += 1
        summary.runners += len(race.odds)
        overround_total += z
        log_loss_total += log_loss(probs[race.winner])
        brier_total += brier(probs, race.winner)

    if summary.races:
        summary.mean_overround = overround_total / summary.races
        summary.mean_log_loss = log_loss_total / summary.races
        summary.mean_brier = brier_total / summary.races
    return obs, summary


@dataclass
class ReturnBin:
    """オッズ層1つぶんの集計。"""

    n: int
    wins: int
    odds_min: float
    odds_max: float
    predicted: float
    observed: float
    lo: float
    hi: float
    mean_return: float
    return_se: float

    # 期待勝数がこれを下回る層は、較正を点で論じるだけの母数がない
    MIN_EXPECTED_WINS = 25.0

    @property
    def bias(self) -> float:
        """払戻率からのずれ。負なら、その層は市場に過大評価されている。"""
        return self.mean_return - WIN_PAYOUT_RATE

    @property
    def significant(self) -> bool:
        """ずれが標準誤差の2倍を超えるか。"""
        return self.return_se > 0.0 and abs(self.bias) > 2.0 * self.return_se

    @property
    def expected_wins(self) -> float:
        return self.predicted * self.n

    @property
    def underpowered(self) -> bool:
        """勝ち馬の実数が少なすぎて、この層の較正は何も言えない。

        最も穴の層では、件数が数千あっても期待勝数は一桁になりうる。
        件数の多さに引きずられて読むと、ただのポアソン雑音を発見だと誤認する。
        """
        return self.expected_wins < self.MIN_EXPECTED_WINS


def return_bins(obs: list[Observation], n_bins: int = 10) -> list[ReturnBin]:
    """実装確率で件数等分し、層ごとの期待回収率を出す。

    人気順(オッズの低い順)に並べて返す。
    """
    if not obs:
        return []
    bins = reliability_bins([(o.implied_p, o.won) for o in obs], n_bins=n_bins)
    ordered = sorted(obs, key=lambda o: o.implied_p)

    out = []
    cursor = 0
    for b in bins:
        chunk = ordered[cursor : cursor + int(b["n"])]
        cursor += int(b["n"])
        returns = [o.odds if o.won else 0.0 for o in chunk]
        mean = sum(returns) / len(returns)
        se = _stderr(returns, mean)
        out.append(
            ReturnBin(
                n=int(b["n"]),
                wins=int(b["wins"]),
                odds_min=min(o.odds for o in chunk),
                odds_max=max(o.odds for o in chunk),
                predicted=float(b["predicted"]),
                observed=float(b["observed"]),
                lo=float(b["lo"]),
                hi=float(b["hi"]),
                mean_return=mean,
                return_se=se,
            )
        )
    out.reverse()  # 実装確率の高い順 = 人気順
    return out


def _stderr(values: list[float], mean: float) -> float:
    if len(values) < 2:
        return 0.0
    var = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(var / len(values))


def flb_verdict(bins: list[ReturnBin]) -> str:
    """人気層と穴層の回収率差から、FLBの有無を一文で述べる。"""
    if len(bins) < 2:
        return "層が足りず判定不能"
    fav, longshot = bins[0], bins[-1]
    gap = fav.mean_return - longshot.mean_return
    se = math.sqrt(fav.return_se**2 + longshot.return_se**2)
    if se <= 0.0:
        return "標準誤差が計算できず判定不能"
    zscore = gap / se
    caveat = ""
    if longshot.underpowered:
        caveat = (
            f"  ただし穴層の期待勝数は{longshot.expected_wins:.0f}件しかなく、"
            "z値の正規近似は当てにならない"
        )
    if abs(zscore) < 2.0:
        return f"人気層と穴層の回収率差 {gap:+.3f} (z={zscore:.2f})。差は誤差の範囲{caveat}"
    direction = "人気層が有利" if gap > 0 else "穴層が有利"
    tag = "favorite-longshot biasと整合" if gap > 0 else "FLBとは逆向き"
    return f"人気層と穴層の回収率差 {gap:+.3f} (z={zscore:.2f})。{direction}で、{tag}{caveat}"


def report_lines(obs: list[Observation], summary: MarketSummary, n_bins: int = 10) -> list[str]:
    """人が読む形に整える。"""
    lines = [
        "=" * 86,
        "市場そのものの較正",
        "=" * 86,
        f"  母集団: {summary.races:,}レース / {summary.runners:,}頭",
        f"  平均逆数和: {summary.mean_overround:.4f}  (単勝払戻率{WIN_PAYOUT_RATE}の逆数は"
        f"{1 / WIN_PAYOUT_RATE:.4f})",
        f"  市場のlog loss: {summary.mean_log_loss:.4f}   Brier: {summary.mean_brier:.4f}",
    ]
    if summary.skipped_no_winner:
        lines.append(f"  着順未確定のため除外: {summary.skipped_no_winner:,}レース")

    bins = return_bins(obs, n_bins=n_bins)
    if not bins:
        lines.append("\n  観測がないため層別集計はなし")
        return lines

    lines += [
        "",
        f"  オッズ層別の期待回収率(基準={WIN_PAYOUT_RATE})",
        f"  {'オッズ帯':<20}{'件数':>9}{'実装確率':>10}{'実測勝率':>10}"
        f"{'95%区間':>18}{'回収率':>10}{'±2SE':>9}",
    ]
    for b in bins:
        band = f"{b.odds_min:.1f}〜{b.odds_max:.1f}倍"
        ci = f"{b.lo:.3f}-{b.hi:.3f}"
        mark = " *" if b.significant else ""
        if b.underpowered:
            mark += " (検出力不足)"
        lines.append(
            f"  {band:<20}{b.n:>9,}{b.predicted:>10.4f}{b.observed:>10.4f}"
            f"{ci:>18}{b.mean_return:>10.3f}{2 * b.return_se:>9.3f}{mark}"
        )
    lines.append("  * = 回収率の基準からのずれが標準誤差の2倍を超える層")
    lines.append(
        f"  (検出力不足) = 期待勝数が{ReturnBin.MIN_EXPECTED_WINS:.0f}件未満。"
        "件数が多くても勝ち馬の実数が少なく、この層の較正は読めない"
    )
    lines.append("")
    lines.append(f"  判定: {flb_verdict(bins)}")
    return lines


def iter_synthetic_races(
    n_races: int,
    seed: int = 0,
    field_size: int = 12,
    flatten: float = 1.0,
    payout_rate: float = WIN_PAYOUT_RATE,
) -> Iterator[RaceRows]:
    """検証用の合成レースを生成する。

    真の勝率 q から市場の実装確率を p ∝ q^flatten として作り、
    オッズを payout_rate / p で与える。勝ち馬は q から抽く。

    flatten=1.0 なら市場は完全に較正され、どの層でも期待回収率は payout_rate に一致する。
    flatten<1.0 は分布を平坦にする方向、つまり穴馬の実装確率を真の勝率より高く見積もる
    ことになり、これが favorite-longshot bias そのものになる。

    「既知のバイアスを注入して、道具がそれを検出できるか」を確かめるために使う。
    実測値は records/ とDBにしか置かない。
    """
    import random

    rng = random.Random(seed)
    for i in range(n_races):
        # 対数正規。実際の1レース内の勝率分布(1番人気3割前後、最低人気1%前後)に
        # 近い裾の広さになるよう sigma を選んである。裾が重すぎる分布だと
        # 単勝1000倍のような現実に存在しない馬が生まれ、検証の意味が薄れる。
        weights = [math.exp(rng.gauss(0.0, 1.2)) for _ in range(field_size)]
        total = sum(weights)
        true_p = [w / total for w in weights]

        # 勝率に下限を置く。実際の単勝プールでは、どんな最低人気でも
        # 実装確率が0.3%を割ることはまずない(数千倍のオッズは成立しない)。
        # 下限を置かないと生成器だけが現実に存在しない超長打を作り、
        # 最も穴の層が雑音だけになって検証が空回りする。
        floored = [max(p, 0.004) for p in true_p]
        ftotal = sum(floored)
        true_p = [p / ftotal for p in floored]

        distorted = [p**flatten for p in true_p]
        dz = sum(distorted)
        implied = [d / dz for d in distorted]
        odds = {h + 1: payout_rate / implied[h] for h in range(field_size)}

        draw = rng.random()
        cumulative = 0.0
        winner = field_size
        for h, p in enumerate(true_p, start=1):
            cumulative += p
            if draw <= cumulative:
                winner = h
                break

        yield RaceRows(race_id=f"synth-{i:07d}", klass="合成", odds=odds, winner=winner)
