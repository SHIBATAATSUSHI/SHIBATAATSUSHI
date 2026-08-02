"""市場の較正を測る道具の検証。

中心は**既知バイアスの回収**。真の勝率から合成レースを作り、既知の歪みを注入して、
道具がそれを向きと大きさで検出できるかを見る。同じくらい大事なのが対照群で、
歪みを入れていないデータで道具がバイアスを「見つけて」しまわないことを確かめる。
存在しないものを検出する道具は、存在するものを検出する道具より害が大きい。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

KEIBA_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KEIBA_DIR))

from calibration import reliability_bins, wilson_interval  # noqa: E402
from importers import import_csv, import_json_records  # noqa: E402
from market import (  # noqa: E402
    WIN_PAYOUT_RATE,
    flb_verdict,
    iter_synthetic_races,
    return_bins,
    scan,
    summarize,
)
from store import connect, iter_race_rows  # noqa: E402

RACES = 8000

# 対照群で「バイアスなし」を確認するときの許容幅(標準誤差の何倍か)。
#
# ここは推論のための有意水準ではなく、回帰テストの門である。10層に素の2SEを
# 課すと、完全に較正された市場でも4割方どこかが引っかかる。多重比較を
# ファミリーワイズ5%に補正して z=2.81 にしても、seedの2割弱で落ちる。
# テストに求められる偽陽性率は5%ではなく実質ゼロなので、4SEまで広げる。
# 注入するバイアスは8SE前後の信号なので、これでも分離は十分に効く。
CONTROL_Z = 4.0


class TestWilson(unittest.TestCase):
    def test_interval_stays_inside_zero_one(self):
        """勝率0のビンでも区間が0未満にならない。単純な正規近似はここで壊れる。"""
        lo, hi = wilson_interval(0, 500)
        self.assertGreaterEqual(lo, 0.0)
        self.assertGreater(hi, 0.0)
        self.assertLess(hi, 0.05)

    def test_interval_narrows_with_n(self):
        narrow = wilson_interval(50, 1000)
        wide = wilson_interval(5, 100)
        self.assertLess(narrow[1] - narrow[0], wide[1] - wide[0])

    def test_no_data_is_maximally_uncertain(self):
        self.assertEqual(wilson_interval(0, 0), (0.0, 1.0))


class TestReliabilityBins(unittest.TestCase):
    def test_bins_are_equal_count(self):
        points = [(i / 100, i % 3 == 0) for i in range(100)]
        bins = reliability_bins(points, n_bins=10)
        self.assertEqual(len(bins), 10)
        self.assertEqual({b["n"] for b in bins}, {10})

    def test_bins_cover_every_point(self):
        points = [(i / 97, False) for i in range(97)]
        bins = reliability_bins(points, n_bins=10)
        self.assertEqual(sum(int(b["n"]) for b in bins), 97)

    def test_empty_input(self):
        self.assertEqual(reliability_bins([], n_bins=10), [])


class TestCalibratedControl(unittest.TestCase):
    """対照群: 歪みのない市場では、道具はバイアスを検出してはいけない。"""

    @classmethod
    def setUpClass(cls):
        races = iter_synthetic_races(RACES, seed=11, flatten=1.0)
        cls.obs, cls.summary = summarize(races)
        cls.bins = return_bins(cls.obs, n_bins=10)

    def test_overround_matches_payout_rate(self):
        """設計どおり逆数和は払戻率の逆数になる。"""
        self.assertAlmostEqual(self.summary.mean_overround, 1 / WIN_PAYOUT_RATE, places=9)

    def test_overall_return_matches_payout_rate(self):
        """市場全体の期待回収率が払戻率に一致する。

        層別より先にこれを見る。n が全観測ぶんあるので標準誤差が小さく、
        層別のどの検定より検出力が高い。ここがずれていたら生成器か指標のバグ。
        """
        returns = [o.odds if o.won else 0.0 for o in self.obs]
        mean = sum(returns) / len(returns)
        var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
        se = (var / len(returns)) ** 0.5
        self.assertLessEqual(abs(mean - WIN_PAYOUT_RATE), 3.0 * se)

    def test_no_bin_deviates_beyond_multiplicity_adjusted_bound(self):
        """層別の回収率が、多重比較を補正した限界を超えない。

        10層すべてに素の2SEを課すのは誤り。完全に較正された市場でも
        「どこか1層が2SEを超える」確率は4割程度あり、そういう検定は
        存在しないバイアスを定期的に報告する。回帰テストとして
        偽陽性を実質ゼロにするため、4SEを門にする(下の CONTROL_Z の注記を参照)。
        """
        for b in self.bins:
            if b.underpowered:
                continue
            with self.subTest(odds=f"{b.odds_min:.1f}-{b.odds_max:.1f}"):
                self.assertLessEqual(abs(b.bias), CONTROL_Z * b.return_se + 1e-9)

    def test_predicted_matches_observed(self):
        """較正されているので、実装確率と実測勝率の区間が重なる。

        期待勝数が足りない層は対象外にする。件数が数千あっても勝ち馬が数件しか
        出ない層では、実測勝率はポアソン雑音そのものであって較正の検定にならない。
        区間はここでも同じ理由で広げる。
        """
        checked = 0
        for b in self.bins:
            if b.underpowered:
                continue
            lo, hi = wilson_interval(b.wins, b.n, z=CONTROL_Z)
            with self.subTest(odds=f"{b.odds_min:.1f}-{b.odds_max:.1f}"):
                self.assertGreaterEqual(b.predicted, lo)
                self.assertLessEqual(b.predicted, hi)
            checked += 1
        self.assertGreaterEqual(checked, 8, "検出力のある層が少なすぎて検証にならない")

    def test_realistic_odds_range(self):
        """生成される単勝オッズが現実的な範囲に収まっている。

        裾が重すぎる分布だと1000倍級の馬が生まれ、最も穴の層が
        雑音だけになって検証が空回りする。
        """
        longest = max(o.odds for o in self.obs)
        self.assertLess(longest, 500.0)

    def test_verdict_reports_no_bias(self):
        self.assertIn("誤差の範囲", flb_verdict(self.bins))


class TestInjectedBias(unittest.TestCase):
    """既知のFLBを注入して、向きと大きさで回収できるかを見る。"""

    @classmethod
    def setUpClass(cls):
        # flatten<1 は分布を平坦にする方向 = 穴馬の実装確率を真の勝率より高く見積もる
        races = iter_synthetic_races(RACES, seed=11, flatten=0.80)
        cls.obs, cls.summary = summarize(races)
        cls.bins = return_bins(cls.obs, n_bins=10)

    def test_favourites_beat_longshots(self):
        favourite, longshot = self.bins[0], self.bins[-1]
        self.assertGreater(favourite.mean_return, longshot.mean_return)

    def test_longshots_are_overrated_by_the_market(self):
        """穴層は実測勝率が実装確率を下回る = 市場が買われすぎている。"""
        longshot = self.bins[-1]
        self.assertLess(longshot.observed, longshot.predicted)
        self.assertLess(longshot.mean_return, WIN_PAYOUT_RATE)

    def test_favourites_are_underrated_by_the_market(self):
        favourite = self.bins[0]
        self.assertGreater(favourite.observed, favourite.predicted)
        self.assertGreater(favourite.mean_return, WIN_PAYOUT_RATE)

    def test_verdict_names_flb(self):
        verdict = flb_verdict(self.bins)
        self.assertIn("favorite-longshot bias", verdict)
        self.assertNotIn("誤差の範囲", verdict)

    def test_bias_is_detected_as_significant(self):
        """歪めた両端の層は、基準からのずれが誤差を超えて検出される。"""
        self.assertTrue(self.bins[0].significant)
        self.assertTrue(self.bins[-1].significant)

    def test_overround_is_unchanged(self):
        """総量(逆数和)は正しいまま配分だけが歪む、という構図を保っている。"""
        self.assertAlmostEqual(self.summary.mean_overround, 1 / WIN_PAYOUT_RATE, places=9)


class TestUnderpoweredBins(unittest.TestCase):
    """勝ち馬が少なすぎる層を、道具が黙って通さないこと。"""

    def test_tail_bin_is_flagged_when_field_is_extreme(self):
        """極端に細かく割ると穴側の層は検出力を失い、そう申告される。"""
        obs, _ = summarize(iter_synthetic_races(1500, seed=3, flatten=1.0))
        bins = return_bins(obs, n_bins=20)
        self.assertTrue(bins[-1].underpowered)
        self.assertIn("検出力不足", "\n".join(_report(obs)))

    def test_verdict_warns_when_longshot_bin_is_thin(self):
        obs, _ = summarize(iter_synthetic_races(1500, seed=3, flatten=1.0))
        bins = return_bins(obs, n_bins=20)
        self.assertIn("正規近似は当てにならない", flb_verdict(bins))

    def test_powered_bin_is_not_flagged(self):
        obs, _ = summarize(iter_synthetic_races(RACES, seed=3, flatten=1.0))
        bins = return_bins(obs, n_bins=10)
        self.assertFalse(bins[0].underpowered)


def _report(obs):
    from market import MarketSummary, report_lines

    summary = MarketSummary(races=len(obs) // 12, runners=len(obs))
    return report_lines(obs, summary, n_bins=20)


class TestStoreRoundTrip(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "test.sqlite3"
        self.conn = connect(self.db)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _write_csv(self, text: str) -> Path:
        path = Path(self.tmp.name) / "in.csv"
        path.write_text(text, encoding="utf-8")
        return path

    def test_csv_round_trip(self):
        path = self._write_csv(
            "race_id,date,course,race_no,klass,horse_no,win_odds,finish_pos\n"
            "r1,2025-08-03,新潟,11,GIII,1,2.0,1\n"
            "r1,2025-08-03,新潟,11,GIII,2,4.0,2\n"
            "r1,2025-08-03,新潟,11,GIII,3,8.0,3\n"
        )
        report = import_csv(self.conn, path)
        self.assertEqual(report.imported, 1)

        rows = list(iter_race_rows(self.conn))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].winner, 1)
        self.assertEqual(rows[0].field_size, 3)

        obs, summary = scan(self.conn)
        self.assertEqual(summary.races, 1)
        self.assertEqual(len(obs), 3)

    def test_dead_heat_is_skipped(self):
        """1着同着は単勝の払戻構造が変わるので母集団から外す。"""
        path = self._write_csv(
            "race_id,horse_no,win_odds,finish_pos\n"
            "r1,1,2.0,1\n"
            "r1,2,4.0,1\n"
            "r1,3,8.0,3\n"
        )
        report = import_csv(self.conn, path)
        self.assertEqual(report.imported, 0)
        self.assertEqual(report.skipped["1着同着"], 1)

    def test_broken_odds_are_skipped_with_reason(self):
        path = self._write_csv(
            "race_id,horse_no,win_odds,finish_pos\nr1,1,,1\nr1,2,4.0,2\n"
        )
        report = import_csv(self.conn, path)
        self.assertEqual(report.imported, 0)
        self.assertEqual(sum(report.skipped.values()), 1)

    def test_race_without_winner_is_stored_but_not_scored(self):
        """着順未確定でも記録は残す。ただしスコアの母集団には入れない。"""
        path = self._write_csv(
            "race_id,horse_no,win_odds,finish_pos\nr1,1,2.0,\nr1,2,4.0,\n"
        )
        self.assertEqual(import_csv(self.conn, path).imported, 1)
        _, summary = scan(self.conn)
        self.assertEqual(summary.races, 0)
        self.assertEqual(summary.skipped_no_winner, 1)

    def test_partial_records_are_excluded_from_market_population(self):
        """実測4レースは全馬そろっていないので市場の較正には使えない。

        部分的な価格から正規化すると逆数和が過小になり確率を過大評価する。
        JSON経路には残しつつ、SQLiteの集計からは自動的に外れることを確認する。
        """
        report = import_json_records(self.conn, KEIBA_DIR / "records")
        self.assertGreaterEqual(report.imported, 4)
        self.assertEqual(list(iter_race_rows(self.conn, "確定")), [])

        all_rows = list(iter_race_rows(self.conn, "確定", require_full_field=False))
        self.assertGreater(len(all_rows), 0)


if __name__ == "__main__":
    unittest.main()
