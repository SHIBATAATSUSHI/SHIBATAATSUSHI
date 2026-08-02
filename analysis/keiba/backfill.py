"""大量記録の取り込みと、市場の較正の集計を行うCLI。

    python3 analysis/keiba/backfill.py import-csv data/2025.csv
    python3 analysis/keiba/backfill.py import-json
    python3 analysis/keiba/backfill.py analyze
    python3 analysis/keiba/backfill.py stats
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from importers import import_csv, import_json_records  # noqa: E402
from market import report_lines, scan  # noqa: E402
from store import DEFAULT_DB, connect, counts  # noqa: E402

RECORDS_DIR = Path(__file__).resolve().parent / "records"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="市場の較正を数万レース規模で測る")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLiteのパス")
    sub = parser.add_subparsers(dest="command", required=True)

    p_csv = sub.add_parser("import-csv", help="1行1頭のCSVを取り込む")
    p_csv.add_argument("path")
    p_csv.add_argument("--source", default="確定", help="価格の情報源名")

    sub.add_parser("import-json", help="records/*.json の精密記録も取り込む")

    p_analyze = sub.add_parser("analyze", help="較正曲線とfavorite-longshot biasを出す")
    p_analyze.add_argument("--source", default="確定")
    p_analyze.add_argument("--bins", type=int, default=10)

    sub.add_parser("stats", help="取り込み状況")

    args = parser.parse_args(argv)
    conn = connect(args.db)

    if args.command == "import-csv":
        print("\n".join(import_csv(conn, args.path, args.source).lines()))
    elif args.command == "import-json":
        print("\n".join(import_json_records(conn, RECORDS_DIR).lines()))
    elif args.command == "analyze":
        obs, summary = scan(conn, args.source)
        if summary.races == 0:
            print(
                f"情報源 {args.source!r} で全馬そろったレースが0件。\n"
                "部分的な価格からは正規化できないため集計しない"
                "(逆数和が過小になり確率を過大評価するため)。"
            )
            return
        print("\n".join(report_lines(obs, summary, n_bins=args.bins)))
    elif args.command == "stats":
        for key, value in counts(conn).items():
            print(f"  {key:<16}{value:>10,}")

    conn.close()


if __name__ == "__main__":
    main()
