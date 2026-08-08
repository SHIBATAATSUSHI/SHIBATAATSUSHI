"""Mytos の設定とモデルプリセット。

モデルごとに使えるAPIパラメータが違う(adaptive thinking / effort / サーバ側
フォールバック)ため、モデルの素性を `ModelSpec` にまとめて持っておき、
リクエスト組み立て時に参照する。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

# 許可する権限モード。
#   ask     … 書き込み・コマンド実行のたびに確認する(既定)
#   auto    … ファイル編集は自動承認、bash は確認する
#   yolo    … すべて自動承認(信頼できる環境でのみ)
PERMISSION_MODES = ("ask", "auto", "yolo")

# 思考の出力レベル。summarized は要約された思考、off は思考を表示しない。
THINKING_DISPLAYS = ("summarized", "omitted")

EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")


@dataclass(frozen=True)
class ModelSpec:
    """モデル1つ分の素性。価格は 100万トークンあたりのUSD。"""

    id: str
    label: str
    input_price: float
    output_price: float
    cache_write_price: float
    cache_read_price: float
    max_output: int
    # adaptive thinking を送ってよいか(旧世代モデルは 400 になる)
    supports_adaptive_thinking: bool = True
    # output_config.effort を送ってよいか
    supports_effort: bool = True
    # 安全性分類器による refusal に対するサーバ側フォールバックを使えるか
    supports_fallbacks: bool = False


# よく使うモデルの短縮名。`--model opus` のように指定できる。
MODEL_PRESETS: dict[str, ModelSpec] = {
    "fable": ModelSpec(
        id="claude-fable-5",
        label="Claude Fable 5",
        input_price=10.0,
        output_price=50.0,
        cache_write_price=12.5,
        cache_read_price=1.0,
        max_output=128_000,
        supports_fallbacks=True,
    ),
    "mythos": ModelSpec(
        id="claude-mythos-5",
        label="Claude Mythos 5",
        input_price=10.0,
        output_price=50.0,
        cache_write_price=12.5,
        cache_read_price=1.0,
        max_output=128_000,
        supports_fallbacks=True,
    ),
    "opus": ModelSpec(
        id="claude-opus-5",
        label="Claude Opus 5",
        input_price=5.0,
        output_price=25.0,
        cache_write_price=6.25,
        cache_read_price=0.5,
        max_output=128_000,
    ),
    "sonnet": ModelSpec(
        id="claude-sonnet-5",
        label="Claude Sonnet 5",
        input_price=3.0,
        output_price=15.0,
        cache_write_price=3.75,
        cache_read_price=0.3,
        max_output=128_000,
    ),
    "haiku": ModelSpec(
        id="claude-haiku-4-5",
        label="Claude Haiku 4.5",
        input_price=1.0,
        output_price=5.0,
        cache_write_price=1.25,
        cache_read_price=0.1,
        max_output=64_000,
        # Haiku 4.5 は adaptive thinking と effort に非対応。
        supports_adaptive_thinking=False,
        supports_effort=False,
    ),
}

DEFAULT_MODEL_KEY = "opus"


def resolve_model(name: str | None) -> ModelSpec:
    """短縮名またはモデルIDから `ModelSpec` を得る。

    未知のIDが渡された場合は、現行世代のモデルとみなして adaptive thinking と
    effort を有効にした暫定のスペックを返す(価格は不明なので 0 とする)。
    """
    if not name:
        return MODEL_PRESETS[DEFAULT_MODEL_KEY]

    key = name.strip().lower()
    if key in MODEL_PRESETS:
        return MODEL_PRESETS[key]

    # 完全なモデルIDで指定された場合、プリセットに一致すればそれを使う。
    for spec in MODEL_PRESETS.values():
        if spec.id == key:
            return spec

    return ModelSpec(
        id=name.strip(),
        label=name.strip(),
        input_price=0.0,
        output_price=0.0,
        cache_write_price=0.0,
        cache_read_price=0.0,
        max_output=64_000,
        supports_fallbacks=key.startswith(("claude-fable", "claude-mythos")),
    )


@dataclass(frozen=True)
class Config:
    """1セッション分の実行設定。"""

    model: ModelSpec
    workspace: Path
    effort: str = "high"
    # 1リクエストあたりの出力上限。思考トークンもここに含まれる点に注意。
    max_tokens: int = 32_000
    thinking_display: str = "summarized"
    permission_mode: str = "ask"
    # 1ターンで許すツール往復の上限(暴走の保険)
    max_iterations: int = 60
    # bash の既定タイムアウト秒
    bash_timeout: int = 120
    color: bool = True

    def with_model(self, name: str) -> "Config":
        """モデルだけ差し替えた新しい設定を返す。"""
        spec = resolve_model(name)
        max_tokens = min(self.max_tokens, spec.max_output)
        return replace(self, model=spec, max_tokens=max_tokens)

    def with_effort(self, effort: str) -> "Config":
        """effort だけ差し替えた新しい設定を返す。"""
        if effort not in EFFORT_LEVELS:
            raise ValueError(f"effort は {'/'.join(EFFORT_LEVELS)} のいずれか: {effort}")
        return replace(self, effort=effort)

    def with_permission_mode(self, mode: str) -> "Config":
        """権限モードだけ差し替えた新しい設定を返す。"""
        if mode not in PERMISSION_MODES:
            raise ValueError(f"権限モードは {'/'.join(PERMISSION_MODES)} のいずれか: {mode}")
        return replace(self, permission_mode=mode)


def build_config(
    *,
    model: str | None = None,
    workspace: str | os.PathLike[str] | None = None,
    effort: str = "high",
    max_tokens: int | None = None,
    thinking_display: str = "summarized",
    permission_mode: str = "ask",
    color: bool | None = None,
) -> Config:
    """CLI 引数から `Config` を組み立てる。"""
    spec = resolve_model(model)

    if effort not in EFFORT_LEVELS:
        raise ValueError(f"effort は {'/'.join(EFFORT_LEVELS)} のいずれか: {effort}")
    if thinking_display not in THINKING_DISPLAYS:
        raise ValueError(
            f"thinking-display は {'/'.join(THINKING_DISPLAYS)} のいずれか: {thinking_display}"
        )
    if permission_mode not in PERMISSION_MODES:
        raise ValueError(
            f"権限モードは {'/'.join(PERMISSION_MODES)} のいずれか: {permission_mode}"
        )

    root = Path(workspace).expanduser().resolve() if workspace else Path.cwd().resolve()
    if not root.is_dir():
        raise ValueError(f"作業ディレクトリが存在しない: {root}")

    resolved_max_tokens = min(max_tokens or 32_000, spec.max_output)

    if color is None:
        color = os.environ.get("NO_COLOR") is None

    return Config(
        model=spec,
        workspace=root,
        effort=effort,
        max_tokens=resolved_max_tokens,
        thinking_display=thinking_display,
        permission_mode=permission_mode,
        color=color,
    )
