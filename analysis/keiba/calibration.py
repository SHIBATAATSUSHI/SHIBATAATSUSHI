"""確率としての予想を評価する指標群。

前提: 単勝オッズは確率ではない。逆数和が払戻率の逆数(単勝なら1.25)に
なるよう控除率が織り込まれた価格である。確率として扱うには必ず正規化する。

指標の使い分け:
- log_loss   1着馬の確率さえ分かれば計算できる。記録が不完全でも使える主力。
- brier      全馬の分布が必要。外した先の分布まで含めて評価できる。
- entropy    分布の平坦さ。「紙面は市場より平坦」という仮説を測る道具。
- kl_divergence  2つの分布の隔たり。紙面と確定の距離。

標準ライブラリのみで完結させている(依存を増やさないため)。
"""

from __future__ import annotations

import math

# 券種ごとの払戻率。逆数がその市場の正常な逆数和になる。
TAKEOUT = {
    "単勝": 0.80,
    "複勝": 0.80,
    "馬連": 0.775,
    "馬単": 0.775,
    "ワイド": 0.775,
    "3連複": 0.775,
    "3連単": 0.725,
}


def overround(odds: dict[int, float]) -> float:
    """オッズ一式の逆数和。控除率が正しく織り込まれていれば 1/払戻率 に一致する。"""
    return sum(1.0 / o for o in odds.values())


def normalize(odds: dict[int, float], z: float | None = None) -> dict[int, float]:
    """オッズを確率分布へ正規化する。z を渡さなければ実測の逆数和を使う。"""
    z = overround(odds) if z is None else z
    return {h: (1.0 / o) / z for h, o in odds.items()}


def log_loss(p_winner: float) -> float:
    """1着馬に与えていた確率の対数損失。小さいほど良い。

    確率0を置いた予想は無限大に罰される。1頭分の確率だけで計算できるため、
    記録が不完全なレースでも蓄積できるのが利点。
    """
    if p_winner <= 0.0:
        return math.inf
    return -math.log(p_winner)


def brier(probs: dict[int, float], winner: int) -> float:
    """多クラスBrierスコア(総和形)。0が完璧、上限は2。小さいほど良い。

    sum_i (p_i - y_i)^2 で、y は1着馬だけ1のワンホット。
    全馬の確率が必要なので、完全な記録でのみ計算できる。
    """
    if winner not in probs:
        raise ValueError(f"1着の{winner}番が分布に含まれていない")
    return sum((p - (1.0 if h == winner else 0.0)) ** 2 for h, p in probs.items())


def entropy(probs: dict[int, float]) -> float:
    """分布のシャノンエントロピー(nat)。大きいほど平坦=どの馬も同じくらい、の意。"""
    return -sum(p * math.log(p) for p in probs.values() if p > 0.0)


def concentration(probs: dict[int, float]) -> float:
    """ハーフィンダール指数 sum p^2。大きいほど分布が尖っている。"""
    return sum(p * p for p in probs.values())


def kl_divergence(p: dict[int, float], q: dict[int, float]) -> float:
    """KL(p||q)。p を真の分布とみなしたときの q の隔たり。"""
    total = 0.0
    for h, pi in p.items():
        if pi <= 0.0:
            continue
        qi = q.get(h, 0.0)
        if qi <= 0.0:
            return math.inf
        total += pi * math.log(pi / qi)
    return total


def divergence_ratio(odds_before: float, odds_after: float) -> float:
    """乖離倍率。事前価格が事後価格の何倍だったか。

    31.2倍が5.5倍になったなら5.7倍。1より大きいほど事前に過小評価していた。
    """
    return odds_before / odds_after


def log_gap(p_from: float, p_to: float) -> float:
    """2時点間の確率の対数差 ln(p_to / p_from)。

    乖離倍率(比)ではなく対数を使う理由は2つ。加法的なので時点をまたいで足せること、
    そして倍率が「2倍」と「0.5倍」で非対称になるのを避けられること。
    符号が向きを表す。正なら事前が過小評価していた。
    """
    if p_from <= 0.0 or p_to <= 0.0:
        return math.inf
    return math.log(p_to / p_from)


def movement_share(p_start: float, p_at: float, p_final: float) -> float | None:
    """最終値までの動きのうち、その時点までに何割が済んでいたか。

    情報流入曲線の縦軸。0なら出発点のまま、1なら確定値に到達済み。
    1を超えることもある(行き過ぎて戻る)。始点と終点が同じなら測定不能でNone。
    """
    total = log_gap(p_start, p_final)
    if total == 0.0 or math.isinf(total):
        return None
    return log_gap(p_start, p_at) / total


def binomial_two_sided_p(k: int, n: int, p: float = 0.5) -> float:
    """符号検定用の両側二項検定p値。

    n が小さいうちは正規近似が効かないので厳密に数え上げる。
    「紙面は市場より一貫して平坦か」を n=5 程度から評価するために使う。
    """
    if n == 0:
        return 1.0

    def pmf(i: int) -> float:
        return math.comb(n, i) * (p**i) * ((1.0 - p) ** (n - i))

    threshold = pmf(k) * (1.0 + 1e-9)
    return min(1.0, sum(pmf(i) for i in range(n + 1) if pmf(i) <= threshold))
