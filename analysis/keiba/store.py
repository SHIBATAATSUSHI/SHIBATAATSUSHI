"""大量の粗い記録を置くためのSQLite層。

records/*.json は5時点を刻む精密記録には最適だが、数万レースでは破綻する
(集計のたびに全ファイルを開いて読む)。そこで経路を2本に分ける。

- records/*.json … 少数精密。紙面・5時点・H1/H3。report.py が担当
- SQLite        … 大量粗粒度。確定オッズと着順だけ。市場自身の較正とFLB

語彙は JSON 側と揃えてある(情報源ごとの価格一式 = quote、その逆数和の出どころ = basis)。
将来どちらかに寄せるとしても、意味の対応がついたまま移せるようにするため。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent / "keiba.sqlite3"

SCHEMA = """
CREATE TABLE IF NOT EXISTS races (
    race_id     TEXT PRIMARY KEY,
    date        TEXT,
    course      TEXT,
    race_no     INTEGER,
    klass       TEXT,
    surface     TEXT,
    distance_m  INTEGER,
    field_size  INTEGER,
    origin      TEXT               -- 取り込み元(csv / json)
);

CREATE TABLE IF NOT EXISTS quotes (
    race_id            TEXT NOT NULL,
    quote_source       TEXT NOT NULL,   -- 紙面 / 前売り / 確定 など
    captured_at        TEXT,
    declared_overround REAL,
    overround_basis    TEXT,
    covers             INTEGER,
    PRIMARY KEY (race_id, quote_source)
);

CREATE TABLE IF NOT EXISTS prices (
    race_id      TEXT NOT NULL,
    quote_source TEXT NOT NULL,
    horse_no     INTEGER NOT NULL,
    odds         REAL NOT NULL,
    PRIMARY KEY (race_id, quote_source, horse_no)
);

CREATE TABLE IF NOT EXISTS results (
    race_id     TEXT NOT NULL,
    horse_no    INTEGER NOT NULL,
    finish_pos  INTEGER,
    PRIMARY KEY (race_id, horse_no)
);

CREATE INDEX IF NOT EXISTS idx_prices_source ON prices (quote_source, race_id);
CREATE INDEX IF NOT EXISTS idx_results_race ON results (race_id);
"""


@dataclass
class RaceRows:
    """1レース分の、確定価格と着順をそろえた塊。

    Attributes:
        odds: 馬番 -> 単勝オッズ。取り込み時に全馬そろっていることを保証済み。
        winner: 1着の馬番。着順未確定なら None。
    """

    race_id: str
    klass: str
    odds: dict[int, float]
    winner: int | None

    @property
    def field_size(self) -> int:
        return len(self.odds)


def connect(path: Path | str = DEFAULT_DB) -> sqlite3.Connection:
    """DBを開き、スキーマがなければ作る。"""
    conn = sqlite3.connect(str(path))
    conn.executescript(SCHEMA)
    return conn


def upsert_race(conn: sqlite3.Connection, race: dict[str, object]) -> None:
    """レースの属性を書き込む(同じrace_idなら上書き)。"""
    conn.execute(
        """INSERT INTO races (race_id, date, course, race_no, klass, surface,
                              distance_m, field_size, origin)
           VALUES (:race_id, :date, :course, :race_no, :klass, :surface,
                   :distance_m, :field_size, :origin)
           ON CONFLICT(race_id) DO UPDATE SET
               date=excluded.date, course=excluded.course, race_no=excluded.race_no,
               klass=excluded.klass, surface=excluded.surface,
               distance_m=excluded.distance_m, field_size=excluded.field_size,
               origin=excluded.origin""",
        race,
    )


def upsert_quote(
    conn: sqlite3.Connection,
    race_id: str,
    quote_source: str,
    odds: dict[int, float],
    captured_at: str | None = None,
    declared_overround: float | None = None,
    overround_basis: str = "measured",
    covers: int | None = None,
) -> None:
    """ある情報源の価格一式を書き込む。"""
    conn.execute(
        """INSERT INTO quotes (race_id, quote_source, captured_at,
                               declared_overround, overround_basis, covers)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(race_id, quote_source) DO UPDATE SET
               captured_at=excluded.captured_at,
               declared_overround=excluded.declared_overround,
               overround_basis=excluded.overround_basis,
               covers=excluded.covers""",
        (race_id, quote_source, captured_at, declared_overround, overround_basis, covers),
    )
    conn.executemany(
        """INSERT INTO prices (race_id, quote_source, horse_no, odds) VALUES (?, ?, ?, ?)
           ON CONFLICT(race_id, quote_source, horse_no) DO UPDATE SET odds=excluded.odds""",
        [(race_id, quote_source, h, o) for h, o in odds.items()],
    )


def upsert_results(conn: sqlite3.Connection, race_id: str, finish: dict[int, int | None]) -> None:
    """着順を書き込む。値が None の馬(取消・除外など)も行としては残す。"""
    conn.executemany(
        """INSERT INTO results (race_id, horse_no, finish_pos) VALUES (?, ?, ?)
           ON CONFLICT(race_id, horse_no) DO UPDATE SET finish_pos=excluded.finish_pos""",
        [(race_id, h, p) for h, p in finish.items()],
    )


def iter_race_rows(
    conn: sqlite3.Connection, quote_source: str = "確定", require_full_field: bool = True
) -> Iterator[RaceRows]:
    """レース単位で価格と着順を束ねて流す。

    レースをまたいでストリームするので、数万レースでもメモリに全部載せない。

    Args:
        require_full_field: races.field_size と価格の頭数が一致するレースだけを流す。
            部分的な価格から正規化すると確率を過大評価するため、既定で有効。
    """
    cur = conn.execute(
        """SELECT p.race_id, r.klass, r.field_size, p.horse_no, p.odds, res.finish_pos
             FROM prices p
             JOIN races r ON r.race_id = p.race_id
             LEFT JOIN results res
                    ON res.race_id = p.race_id AND res.horse_no = p.horse_no
            WHERE p.quote_source = ?
            ORDER BY p.race_id, p.horse_no""",
        (quote_source,),
    )

    current: str | None = None
    klass = ""
    declared_size = 0
    odds: dict[int, float] = {}
    winner: int | None = None

    def finish() -> RaceRows | None:
        if current is None:
            return None
        if require_full_field and (declared_size or 0) != len(odds):
            return None
        return RaceRows(race_id=current, klass=klass, odds=dict(odds), winner=winner)

    for race_id, race_klass, field_size, horse_no, horse_odds, finish_pos in cur:
        if race_id != current:
            done = finish()
            if done is not None:
                yield done
            current, klass, declared_size = race_id, race_klass or "", field_size
            odds, winner = {}, None
        odds[horse_no] = horse_odds
        if finish_pos == 1:
            winner = horse_no

    done = finish()
    if done is not None:
        yield done


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    """取り込み状況の概況。"""
    q = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    return {
        "races": q("SELECT COUNT(*) FROM races"),
        "prices": q("SELECT COUNT(*) FROM prices"),
        "results": q("SELECT COUNT(*) FROM results"),
        "quote_sources": q("SELECT COUNT(DISTINCT quote_source) FROM prices"),
    }
