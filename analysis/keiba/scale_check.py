"""数万レース規模で取り込みと集計が現実的な時間で終わるかを確かめる。

通常のテストからは外してある(数十秒かかるため)。目的は正しさの検証ではなく、
JSONの全読みからSQLiteに移した理由が実際に効いているかの確認。

    python3 analysis/keiba/scale_check.py --races 50000
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from market import iter_synthetic_races, report_lines, return_bins, scan, summarize  # noqa: E402
from store import connect, upsert_quote, upsert_race, upsert_results  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="規模検証")
    parser.add_argument("--races", type=int, default=50000)
    parser.add_argument("--field-size", type=int, default=12)
    parser.add_argument("--flatten", type=float, default=0.92, help="1.0で無バイアス")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "scale.sqlite3"
        conn = connect(db)

        t0 = time.perf_counter()
        n = 0
        for race in iter_synthetic_races(
            args.races, seed=1, field_size=args.field_size, flatten=args.flatten
        ):
            upsert_race(
                conn,
                {
                    "race_id": race.race_id,
                    "date": "2025-01-01",
                    "course": "合成",
                    "race_no": 1,
                    "klass": race.klass,
                    "surface": "芝",
                    "distance_m": 1600,
                    "field_size": race.field_size,
                    "origin": "synthetic",
                },
            )
            upsert_quote(conn, race.race_id, "確定", race.odds)
            upsert_results(
                conn,
                race.race_id,
                {h: (1 if h == race.winner else None) for h in race.odds},
            )
            n += 1
        conn.commit()
        t_insert = time.perf_counter() - t0

        t0 = time.perf_counter()
        obs, summary = scan(conn)
        t_scan = time.perf_counter() - t0

        t0 = time.perf_counter()
        return_bins(obs, n_bins=10)
        t_bin = time.perf_counter() - t0

        size_mb = db.stat().st_size / 1024 / 1024
        print(f"レース数      {n:,}")
        print(f"観測数        {summary.runners:,}")
        print(f"DBサイズ      {size_mb:.1f} MB")
        print(f"取り込み      {t_insert:6.2f} 秒")
        print(f"集計スキャン  {t_scan:6.2f} 秒")
        print(f"層別集計      {t_bin:6.2f} 秒")
        print()
        print("\n".join(report_lines(obs, summary)))
        conn.close()


if __name__ == "__main__":
    main()
