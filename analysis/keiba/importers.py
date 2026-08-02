"""外部データをSQLiteへ取り込む。

入口をCSV1本に決めてあるのは、データの出所を問わない設計にするため。
本セッションからは jra.go.jp / keiba.go.jp が egress ポリシーで遮断されており、
取得は利用者の手元で行う前提になっている。形式さえ合っていればどこ由来でも入る。
"""

from __future__ import annotations

import csv
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from schema import BASIS_MEASURED, load_races
from store import upsert_quote, upsert_race, upsert_results

# CSVに必須の列。1行1頭。
REQUIRED_COLUMNS = {"race_id", "horse_no", "win_odds"}


@dataclass
class ImportReport:
    """取り込み結果。落としたレースは理由ごとに数える。

    黙って落とすと、母集団がいつのまにか歪んでいても気づけない。
    「撮れたレースだけを集める」のと同じ選択バイアスがここでも起きる。
    """

    imported: int = 0
    skipped: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def skip(self, reason: str) -> None:
        self.skipped[reason] += 1

    def lines(self) -> list[str]:
        out = [f"取り込み: {self.imported}レース"]
        for reason, n in sorted(self.skipped.items(), key=lambda kv: -kv[1]):
            out.append(f"  除外 {n:>6}  {reason}")
        return out


def import_csv(
    conn: sqlite3.Connection, path: Path | str, quote_source: str = "確定"
) -> ImportReport:
    """1行1頭のCSVを取り込む。

    期待する列: race_id, horse_no, win_odds が必須。
    date, course, race_no, klass, surface, distance_m, finish_pos は任意。
    出走頭数は行数から導く(別途 field_size 列を信じない)。
    """
    report = ImportReport()
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)

    with Path(path).open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise SystemExit(f"CSVに必須列がない: {sorted(missing)}")
        for row in reader:
            if row.get("race_id"):
                grouped[row["race_id"]].append(row)

    for race_id, rows in grouped.items():
        odds: dict[int, float] = {}
        finish: dict[int, int | None] = {}
        broken = False

        for row in rows:
            try:
                horse_no = int(row["horse_no"])
                value = float(row["win_odds"])
            except (TypeError, ValueError):
                broken = True
                break
            if value <= 0.0:
                broken = True
                break
            odds[horse_no] = value
            raw_pos = (row.get("finish_pos") or "").strip()
            finish[horse_no] = int(raw_pos) if raw_pos.isdigit() else None

        if broken:
            report.skip("単勝オッズが欠損または不正")
            continue
        if len(odds) < 2:
            report.skip("出走2頭未満")
            continue
        if sum(1 for p in finish.values() if p == 1) > 1:
            # 同着は単勝の払戻が変わり、勝率1件として数えると較正が歪む
            report.skip("1着同着")
            continue

        head = rows[0]
        upsert_race(
            conn,
            {
                "race_id": race_id,
                "date": head.get("date"),
                "course": head.get("course"),
                "race_no": _maybe_int(head.get("race_no")),
                "klass": head.get("klass"),
                "surface": head.get("surface"),
                "distance_m": _maybe_int(head.get("distance_m")),
                "field_size": len(odds),
                "origin": "csv",
            },
        )
        upsert_quote(conn, race_id, quote_source, odds, overround_basis=BASIS_MEASURED)
        upsert_results(conn, race_id, finish)
        report.imported += 1

    conn.commit()
    return report


def import_json_records(conn: sqlite3.Connection, records_dir: Path) -> ImportReport:
    """既存の records/*.py 精密記録をSQLiteにも入れる。

    実測4レースは全馬のオッズが揃っていないので、市場の較正の母集団からは
    自動的に外れる(store.iter_race_rows が頭数一致を要求するため)。
    それでも入れておくのは、2つの経路を同じ語彙で保持するため。
    """
    report = ImportReport()
    for race in load_races(records_dir):
        upsert_race(
            conn,
            {
                "race_id": race.race_id,
                "date": race.date,
                "course": race.course,
                "race_no": race.race_no,
                "klass": race.klass,
                "surface": race.surface,
                "distance_m": race.distance_m,
                "field_size": race.field_size,
                "origin": "json",
            },
        )
        for quote in race.quotes:
            if not quote.odds:
                continue
            upsert_quote(
                conn,
                race.race_id,
                quote.source,
                quote.odds,
                captured_at=quote.captured_at,
                declared_overround=quote.declared_overround,
                overround_basis=quote.overround_basis,
                covers=quote.covers,
            )
        if race.result is not None:
            upsert_results(
                conn,
                race.race_id,
                {horse: pos for pos, horse in enumerate(race.result.order, start=1)},
            )
        report.imported += 1

    conn.commit()
    return report


def _maybe_int(value: str | None) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except ValueError:
        return None
