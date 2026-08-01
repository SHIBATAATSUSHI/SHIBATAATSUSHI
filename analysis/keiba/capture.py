"""1時点のオッズを記録ファイルに書き込むCLI。

現場で素早く撮ることが最優先なので、JSONを手で開かずに済むようにする。
取得時刻を省略すると実行時刻が入る(基点を空欄のまま残さないため)。

例:
    # 全馬のオッズを撮った場合(逆数和は実測されるので指定不要)
    python3 analysis/keiba/capture.py 2026-08-02-niigata-11-ibis-summer-dash \\
        --source 締切5分前 --odds 6=2.2 16=4.1 3=7.8 --field-size 18

    # 一部しか撮れなかった場合(控除率からの仮定を明示する)
    python3 analysis/keiba/capture.py 2026-08-02-niigata-11-ibis-summer-dash \\
        --source 当日午前 --odds 6=2.4 --overround 1.25

    # 結果を入れる
    python3 analysis/keiba/capture.py 2026-08-02-niigata-11-ibis-summer-dash \\
        --result 6,16,3 --status complete
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from schema import BASIS_ASSUMED, BASIS_MEASURED, BASIS_PARTIAL  # noqa: E402

RECORDS_DIR = Path(__file__).resolve().parent / "records"


def _parse_odds(tokens: list[str]) -> dict[str, float]:
    """"6=2.2" 形式の並びを 馬番->オッズ の辞書にする。"""
    odds = {}
    for token in tokens:
        if "=" not in token:
            raise SystemExit(f"オッズの書式が不正: {token!r} (例: 6=2.2)")
        horse, value = token.split("=", 1)
        odds[str(int(horse))] = float(value)
    return odds


def _find_record(race_id: str) -> Path:
    path = RECORDS_DIR / f"{race_id}.json"
    if path.exists():
        return path
    matches = [p for p in RECORDS_DIR.glob("*.json") if race_id in p.stem]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise SystemExit(f"記録が見つからない: {race_id}")
    raise SystemExit(f"{race_id} に複数一致: {[p.stem for p in matches]}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="1時点のオッズを記録に書き込む")
    parser.add_argument("race_id", help="記録ファイル名(部分一致可)")
    parser.add_argument("--source", help="情報源名(例: 締切5分前)")
    parser.add_argument("--at", help="取得時刻 ISO8601。省略時は実行時刻")
    parser.add_argument("--odds", nargs="+", default=[], metavar="馬番=オッズ")
    parser.add_argument("--overround", type=float, help="全馬の逆数和。全馬撮れていれば不要")
    parser.add_argument(
        "--basis",
        choices=[BASIS_MEASURED, BASIS_ASSUMED, BASIS_PARTIAL],
        help="逆数和の出どころ。--overround 指定時は既定で assumed_takeout",
    )
    parser.add_argument("--covers", type=int, help="逆数和が何頭分か。部分和のときだけ指定")
    parser.add_argument("--note", default="", help="但し書き")
    parser.add_argument("--field-size", type=int, help="出走頭数")
    parser.add_argument("--result", help="着順をカンマ区切りで(例: 6,16,3)")
    parser.add_argument("--status", choices=["pending", "partial", "complete"])
    args = parser.parse_args(argv)

    path = _find_record(args.race_id)
    with path.open(encoding="utf-8") as f:
        record = json.load(f)

    if args.field_size is not None:
        record["field_size"] = args.field_size
    if args.status:
        record["status"] = args.status
    if args.result:
        order = [int(x) for x in args.result.split(",") if x.strip()]
        record.setdefault("result", None)
        payouts = (record.get("result") or {}).get("payouts", {})
        record["result"] = {"order": order, "payouts": payouts}
        print(f"着順を記録: {order}")

    if args.source:
        if not args.odds and args.overround is None:
            raise SystemExit("--source を指定するなら --odds か --overround が要る")
        odds = _parse_odds(args.odds)
        basis = args.basis or (BASIS_ASSUMED if args.overround is not None else BASIS_MEASURED)
        field_size = record.get("field_size")
        complete = field_size is not None and len(odds) == field_size

        quote = {
            "source": args.source,
            "captured_at": args.at or datetime.now().isoformat(timespec="seconds"),
            "odds": odds,
            "declared_overround": args.overround,
            "overround_basis": basis,
            "covers": args.covers,
            "partial_sums": {},
            "note": args.note,
        }

        quotes = record.setdefault("quotes", [])
        for i, existing in enumerate(quotes):
            if existing["source"] == args.source:
                # 既存の但し書きと部分和は、明示的に上書きしない限り残す
                quote["note"] = args.note or existing.get("note", "")
                quote["partial_sums"] = existing.get("partial_sums", {})
                quotes[i] = quote
                break
        else:
            quotes.append(quote)

        print(f"{args.source}: {len(odds)}頭分を記録 ({quote['captured_at']})")
        if complete:
            print("  全馬そろったので逆数和は実測される(Brier・エントロピーが解禁)")
        elif args.overround is None:
            print("  警告: 全馬に足りず逆数和も未指定。このままでは正規化できない")

    with path.open("w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"書き込み: {path.relative_to(Path.cwd()) if path.is_relative_to(Path.cwd()) else path}")


if __name__ == "__main__":
    main()
