"""競馬の事前価格(予想オッズ・発売オッズ)と事後結果を記録するデータ構造。

設計方針は3つ。

1. 生値だけを保存する。正規化確率もスコアもすべて派生値として計算する。
   一度正規化した数字を保存すると、控除率の仮定を後から差し替えられなくなる。
2. 不完全な記録を一級市民として扱う。現実の記録は
   「勝ち馬と本命の単勝だけ撮れた」「上位10頭の逆数和しか分からない」
   という状態から始まる。完全な記録が揃うまで待つと n は永遠に増えない。
3. 逆数和(オーバーラウンド)がどこから来たのかを必ず明示する。
   実測なのか、控除率からの仮定なのか、一部頭数の和にすぎないのかで、
   使える指標が変わるため。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class InsufficientQuote(Exception):
    """正規化に必要な情報が揃っていない見積もりを使おうとしたときに送出する。"""


# 逆数和の出どころ
BASIS_MEASURED = "measured"  # 全馬のオッズから実測した
BASIS_ASSUMED = "assumed_takeout"  # 控除率から仮定した(例: 単勝80% -> 1.25)
BASIS_PARTIAL = "partial_field"  # 一部頭数だけの和。全体の下限にしかならない


@dataclass(frozen=True)
class Quote:
    """ある情報源が、ある時点で提示した単勝価格の一式。

    Attributes:
        source: 情報源の名前(例: "紙面", "前売り", "締切5分前", "確定")。
        odds: 馬番 -> 単勝オッズ。取得できた馬だけでよい。
        captured_at: 取得時刻(ISO8601文字列)。時系列を刻むときに必須。
        declared_overround: 逆数和。odds が全馬揃っていない場合はここで補う。
        overround_basis: declared_overround の出どころ。BASIS_* のいずれか。
        covers: declared_overround が何頭分の和か。None なら全馬分。
        partial_sums: 部分的に実測した逆数和(例: {"top10": 1.154})。
            正規化には使わないが、declared_overround の妥当性検査に使う。
        note: 記録上の但し書き。
    """

    source: str
    odds: dict[int, float] = field(default_factory=dict)
    captured_at: str | None = None
    declared_overround: float | None = None
    overround_basis: str = BASIS_MEASURED
    covers: int | None = None
    partial_sums: dict[str, float] = field(default_factory=dict)
    note: str = ""

    def is_complete(self, field_size: int | None) -> bool:
        """全出走馬のオッズが揃っているか。出走頭数が不明なら常に False。"""
        return field_size is not None and len(self.odds) == field_size

    def overround(self, field_size: int | None) -> float:
        """全馬の単勝オッズ逆数和を返す。

        払戻率の逆数(単勝なら 1/0.8 = 1.25)に一致するのが正常な市場価格。
        紙面の予想オッズもこれに一致するなら、控除率を織り込んだ
        「模擬市場価格」として設計されていることになる。

        Raises:
            InsufficientQuote: 一部頭数の和しか分かっておらず正規化できないとき。
        """
        if self.is_complete(field_size):
            return sum(1.0 / o for o in self.odds.values())
        if self.declared_overround is None:
            raise InsufficientQuote(
                f"{self.source}: オッズが{len(self.odds)}頭分しかなく、"
                "逆数和も宣言されていないため正規化できない"
            )
        if self.covers is not None and (field_size is None or self.covers < field_size):
            raise InsufficientQuote(
                f"{self.source}: 宣言された逆数和 {self.declared_overround} は"
                f"{self.covers}頭分の部分和にすぎない。"
                "残りの馬の分だけ過小なので、これで割ると確率を過大評価する"
            )
        return self.declared_overround

    def overround_lower_bound(self) -> float | None:
        """部分和しかない場合の逆数和の下限。全体像の当たりをつけるためだけに使う。"""
        candidates = [v for v in self.partial_sums.values()]
        if self.odds:
            candidates.append(sum(1.0 / o for o in self.odds.values()))
        if self.declared_overround is not None and self.covers is not None:
            candidates.append(self.declared_overround)
        return max(candidates) if candidates else None

    def probability(self, horse: int, field_size: int | None) -> float:
        """指定した馬の正規化済み勝率。1頭分だけ分かれば計算できる。"""
        if horse not in self.odds:
            raise InsufficientQuote(f"{self.source}: {horse}番のオッズが記録されていない")
        return (1.0 / self.odds[horse]) / self.overround(field_size)

    def probabilities(self, field_size: int | None) -> dict[int, float]:
        """全馬の正規化済み勝率。全馬のオッズが揃っている場合のみ。"""
        if not self.is_complete(field_size):
            raise InsufficientQuote(
                f"{self.source}: 全馬のオッズが揃っていないため分布を構成できない"
                f"({len(self.odds)}頭 / 出走頭数{field_size})"
            )
        z = self.overround(field_size)
        return {h: (1.0 / o) / z for h, o in self.odds.items()}


@dataclass(frozen=True)
class Result:
    """レース結果。

    Attributes:
        order: 着順に並べた馬番。取得できた範囲でよい(1着だけでもスコアは出る)。
        payouts: 配当。参考情報であってスコアには使わない。
            配当額は組合せ空間の大きさと券種の払戻率で決まるため、
            予測精度の指標にはならない。
    """

    order: list[int]
    payouts: dict[str, Any] = field(default_factory=dict)

    @property
    def winner(self) -> int:
        return self.order[0]


@dataclass
class Race:
    """1レース分の記録。

    Attributes:
        field_size: 出走頭数。不明なら None(正規化できる指標が制限される)。
        career_starts: 馬番 -> 出走キャリア戦数。情報厚みの共変量。
        status: "complete"(必要な値が揃った) / "partial" / "pending"(未取得)。
    """

    race_id: str
    date: str
    course: str
    race_no: int
    name: str = ""
    klass: str = ""
    surface: str = ""
    distance_m: int = 0
    field_size: int | None = None
    quotes: list[Quote] = field(default_factory=list)
    result: Result | None = None
    career_starts: dict[int, int] = field(default_factory=dict)
    status: str = "pending"
    note: str = ""

    def quote(self, source: str) -> Quote | None:
        """情報源名で見積もりを引く。同名が複数あれば最初のもの。"""
        for q in self.quotes:
            if q.source == source:
                return q
        return None

    def timeline(self) -> list[Quote]:
        """取得時刻順に並べた見積もり。時刻のないものは末尾に回す。"""
        return sorted(self.quotes, key=lambda q: (q.captured_at is None, q.captured_at or ""))


def _quote_from_dict(d: dict[str, Any]) -> Quote:
    return Quote(
        source=d["source"],
        odds={int(k): float(v) for k, v in (d.get("odds") or {}).items()},
        captured_at=d.get("captured_at"),
        declared_overround=d.get("declared_overround"),
        overround_basis=d.get("overround_basis", BASIS_MEASURED),
        covers=d.get("covers"),
        partial_sums={k: float(v) for k, v in (d.get("partial_sums") or {}).items()},
        note=d.get("note", ""),
    )


def race_from_dict(d: dict[str, Any]) -> Race:
    """JSON由来の辞書から Race を組み立てる。"""
    result = None
    if d.get("result"):
        result = Result(
            order=[int(x) for x in d["result"]["order"]],
            payouts=d["result"].get("payouts", {}),
        )
    field_size = d.get("field_size")
    return Race(
        race_id=d["race_id"],
        date=d["date"],
        course=d["course"],
        race_no=int(d["race_no"]),
        name=d.get("name", ""),
        klass=d.get("klass", ""),
        surface=d.get("surface", ""),
        distance_m=int(d.get("distance_m") or 0),
        field_size=int(field_size) if field_size is not None else None,
        quotes=[_quote_from_dict(q) for q in d.get("quotes", [])],
        result=result,
        career_starts={int(k): int(v) for k, v in (d.get("career_starts") or {}).items()},
        status=d.get("status", "pending"),
        note=d.get("note", ""),
    )


def load_races(records_dir: Path) -> list[Race]:
    """記録ディレクトリ配下の *.json をすべて読み込む。ファイル名順。"""
    races = []
    for path in sorted(records_dir.glob("*.json")):
        with path.open(encoding="utf-8") as f:
            races.append(race_from_dict(json.load(f)))
    return races
