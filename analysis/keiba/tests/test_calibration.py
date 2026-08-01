"""較正指標と記録スキーマの検証。

期待値は実測データ(2026/8/1 札幌7R・8R)から手計算したものを固定してある。
指標の定義を後から変えたらここが落ちる、という関門にするのが目的。
"""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

KEIBA_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KEIBA_DIR))

from calibration import (  # noqa: E402
    binomial_two_sided_p,
    brier,
    concentration,
    entropy,
    kl_divergence,
    log_loss,
    normalize,
    overround,
)
from schema import BASIS_ASSUMED, InsufficientQuote, Quote, load_races  # noqa: E402


class TestNormalization(unittest.TestCase):
    """オッズは確率ではない。正規化して初めて確率になる。"""

    def test_overround_matches_takeout(self):
        """払戻率80%の市場なら逆数和は1.25に近くなる。"""
        odds = {1: 2.5, 2: 5.0, 3: 10.0, 4: 20.0}
        self.assertAlmostEqual(overround(odds), 0.4 + 0.2 + 0.1 + 0.05, places=10)

    def test_normalize_sums_to_one(self):
        odds = {1: 2.5, 2: 5.0, 3: 10.0, 4: 20.0}
        probs = normalize(odds)
        self.assertAlmostEqual(sum(probs.values()), 1.0, places=12)

    def test_normalize_with_declared_overround(self):
        """逆数和を外から与えた場合、総和は1にならない(与えた値との比になる)。"""
        odds = {1: 2.0}
        self.assertAlmostEqual(normalize(odds, z=1.25)[1], 0.4, places=12)


class TestScores(unittest.TestCase):
    def test_brier_is_sum_form(self):
        """多クラスBrier = sum_i (p_i - y_i)^2。手計算値と一致すること。"""
        probs = {1: 0.5, 2: 0.3, 3: 0.2}
        self.assertAlmostEqual(brier(probs, winner=1), 0.25 + 0.09 + 0.04, places=12)

    def test_brier_bounds(self):
        """完璧な予想は0、確率0を置いた馬が勝てば上限2に近づく。"""
        self.assertAlmostEqual(brier({1: 1.0, 2: 0.0}, winner=1), 0.0, places=12)
        self.assertAlmostEqual(brier({1: 0.0, 2: 1.0}, winner=1), 2.0, places=12)

    def test_brier_rejects_unknown_winner(self):
        with self.assertRaises(ValueError):
            brier({1: 0.6, 2: 0.4}, winner=3)

    def test_log_loss_on_real_races(self):
        """2026/8/1 札幌7R・8Rの実測値から手計算した対数損失を固定する。

        7R: 紙面31.2倍/逆数和1.24 -> 0.02585、確定5.5倍/1.25 -> 0.14545
        8R: 紙面 2.6倍/逆数和1.24 -> 0.31017、確定1.4倍/1.25 -> 0.57143
        両レースとも確定(市場)が紙面より小さい = 市場の勝ち。
        """
        cases = [
            ((1 / 31.2) / 1.24, 3.6553),  # 7R 紙面
            ((1 / 5.5) / 1.25, 1.9279),  # 7R 確定
            ((1 / 2.6) / 1.24, 1.1705),  # 8R 紙面
            ((1 / 1.4) / 1.25, 0.5596),  # 8R 確定
        ]
        for p, expected in cases:
            with self.subTest(p=p):
                self.assertAlmostEqual(log_loss(p), expected, places=3)

    def test_log_loss_punishes_zero(self):
        """確率0を置いた馬が勝ったら無限大。帯を置く予想の危うさがここに出る。"""
        self.assertEqual(log_loss(0.0), math.inf)

    def test_kl_divergence_is_zero_for_identical(self):
        p = {1: 0.5, 2: 0.5}
        self.assertAlmostEqual(kl_divergence(p, p), 0.0, places=12)


class TestFlatness(unittest.TestCase):
    """H1「紙面は市場より平坦」を測る道具の健全性。"""

    def test_flat_distribution_has_higher_entropy(self):
        flat = {1: 0.25, 2: 0.25, 3: 0.25, 4: 0.25}
        peaked = {1: 0.70, 2: 0.10, 3: 0.10, 4: 0.10}
        self.assertGreater(entropy(flat), entropy(peaked))
        self.assertAlmostEqual(entropy(flat), math.log(4), places=12)

    def test_concentration_moves_opposite_to_entropy(self):
        flat = {1: 0.25, 2: 0.25, 3: 0.25, 4: 0.25}
        peaked = {1: 0.70, 2: 0.10, 3: 0.10, 4: 0.10}
        self.assertLess(concentration(flat), concentration(peaked))


class TestSignTest(unittest.TestCase):
    def test_n2_cannot_reach_significance(self):
        """n=2で2連続同方向でも両側p=0.5。逸話は逸話のまま、と道具が自己申告する。"""
        self.assertAlmostEqual(binomial_two_sided_p(2, 2), 0.5, places=12)

    def test_n5_all_same_direction(self):
        """5連続同方向でようやくp=0.0625。ここが実質的な最小サンプル数。"""
        self.assertAlmostEqual(binomial_two_sided_p(5, 5), 2 * (1 / 32), places=12)

    def test_empty_sample(self):
        self.assertEqual(binomial_two_sided_p(0, 0), 1.0)


class TestQuote(unittest.TestCase):
    def test_partial_sum_is_rejected_for_normalization(self):
        """上位10頭の逆数和で18頭立てを正規化してはいけない。

        1.154で割ると残り8頭の逆数質量を無視するぶん確率を過大評価する。
        """
        q = Quote(
            source="前売り",
            odds={6: 2.2},
            declared_overround=1.154,
            covers=10,
        )
        with self.assertRaises(InsufficientQuote):
            q.overround(field_size=18)

    def test_full_field_assumption_normalizes(self):
        """控除率からの仮定(全馬分)なら正規化できる。"""
        q = Quote(
            source="前売り",
            odds={6: 2.2},
            declared_overround=1.25,
            overround_basis=BASIS_ASSUMED,
        )
        self.assertAlmostEqual(q.probability(6, field_size=18), (1 / 2.2) / 1.25, places=12)

    def test_missing_horse_is_reported(self):
        q = Quote(source="紙面", odds={1: 3.0}, declared_overround=1.25)
        with self.assertRaises(InsufficientQuote):
            q.probability(9, field_size=12)

    def test_probabilities_requires_full_field(self):
        q = Quote(source="紙面", odds={1: 3.0}, declared_overround=1.25)
        with self.assertRaises(InsufficientQuote):
            q.probabilities(field_size=12)

    def test_measured_overround_wins_over_declared(self):
        """全馬揃っていれば実測を使う。宣言値は無視される。"""
        q = Quote(source="確定", odds={1: 2.0, 2: 2.0}, declared_overround=99.0)
        self.assertAlmostEqual(q.overround(field_size=2), 1.0, places=12)


class TestRecords(unittest.TestCase):
    """記録ファイルが壊れていないことと、実データのスコアの再現。"""

    @classmethod
    def setUpClass(cls):
        cls.races = load_races(KEIBA_DIR / "records")

    def test_records_load(self):
        self.assertGreaterEqual(len(self.races), 4)

    def test_sapporo_7r_divergence(self):
        """7R: 紙面31.2倍 -> 確定5.5倍 = 5.7倍の乖離。"""
        race = next(r for r in self.races if r.race_id == "2026-08-01-sapporo-07")
        paper, final = race.quote("紙面"), race.quote("確定")
        winner = race.result.winner
        self.assertEqual(winner, 3)
        self.assertAlmostEqual(paper.odds[winner] / final.odds[winner], 5.67, places=2)
        self.assertAlmostEqual(log_loss(paper.probability(winner, race.field_size)), 3.6553, places=3)
        self.assertAlmostEqual(log_loss(final.probability(winner, race.field_size)), 1.9279, places=3)

    def test_paper_underestimates_winner_in_both_races(self):
        """H1の方向: 両レースとも紙面が1着馬を過小評価している。"""
        for race_id in ("2026-08-01-sapporo-07", "2026-08-01-sapporo-08"):
            with self.subTest(race_id=race_id):
                race = next(r for r in self.races if r.race_id == race_id)
                w = race.result.winner
                p_paper = race.quote("紙面").probability(w, race.field_size)
                p_final = race.quote("確定").probability(w, race.field_size)
                self.assertLess(p_paper, p_final)

    def test_pending_race_is_kept_as_missing(self):
        """9Rは事前価格がないので欠測として残す。事後だけ足すと選択バイアスが入る。"""
        race = next(r for r in self.races if r.race_id == "2026-08-01-sapporo-09")
        self.assertEqual(race.status, "pending")
        self.assertEqual(race.quotes, [])
        self.assertIsNone(race.result)


if __name__ == "__main__":
    unittest.main()
