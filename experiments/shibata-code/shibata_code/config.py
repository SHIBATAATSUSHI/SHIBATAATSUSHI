"""設定とモデルプリセット。

モデルごとに送ってよいAPIパラメータが違う(Anthropic の adaptive thinking と
effort、OpenAI 系の reasoning_effort など)ため、モデルの素性を `ModelSpec` に
まとめて持ち、リクエスト組み立て時に参照する。

モデルIDはプロバイダ側の都合で頻繁に変わる。プリセットはあくまで近道で、
`--model プロバイダ:モデルID` で任意のIDを直接指定できるようにしてある。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

from .providers import DEFAULT_PROVIDER, PROVIDERS, ProviderSpec, resolve_provider

PERMISSION_MODES = ("ask", "auto", "yolo")
THINKING_DISPLAYS = ("summarized", "omitted")
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")

# 通信経路。auto は SDK があればそれを、無ければ標準ライブラリ版を使う。
# http を選ぶと SDK を入れずに動く(iOS 等で必要)。
TRANSPORTS = ("auto", "sdk", "http")

# OpenAI 系の reasoning_effort が受け付ける段階。Anthropic の effort より粗い。
OPENAI_EFFORTS = {"low": "low", "medium": "medium", "high": "high", "xhigh": "high", "max": "high"}


@dataclass(frozen=True)
class ModelSpec:
    """モデル1つ分の素性。価格は 100万トークンあたりのUSD(0 は未設定)。"""

    provider: str
    id: str
    label: str
    max_output: int = 32_000
    # 入力の上限。コンパクションの発動判断に使う。不明なら控えめな値にする
    context_window: int = 128_000
    input_price: float = 0.0
    output_price: float = 0.0
    cache_write_price: float = 0.0
    cache_read_price: float = 0.0
    # Anthropic: thinking={"type":"adaptive"} を送ってよいか
    supports_adaptive_thinking: bool = False
    # Anthropic: output_config.effort を送ってよいか
    supports_effort: bool = False
    # OpenAI 系: reasoning_effort を送ってよいか
    supports_reasoning_effort: bool = False
    # Anthropic: 安全性分類器の refusal をサーバ側で救済できるか
    supports_fallbacks: bool = False
    # 出力上限を指定するパラメータ名(OpenAI 系で分岐する)
    max_tokens_param: str = "max_tokens"

    @property
    def provider_spec(self) -> ProviderSpec:
        return PROVIDERS[self.provider]

    @property
    def has_pricing(self) -> bool:
        return bool(self.input_price or self.output_price)

    def display(self) -> str:
        """端末表示用の名前。"""
        return f"{self.label} ({self.provider}:{self.id})"


def _anthropic(**kwargs) -> ModelSpec:
    """Anthropic モデルの共通設定。"""
    return ModelSpec(
        provider="anthropic",
        max_output=128_000,
        context_window=1_000_000,
        supports_adaptive_thinking=True,
        supports_effort=True,
        **kwargs,
    )


# よく使うモデルの短縮名。`--model opus` のように指定できる。
# 価格が 0 のものは未設定で、コスト概算は出ない(トークン数だけ出る)。
MODEL_PRESETS: dict[str, ModelSpec] = {
    # --- Anthropic ---------------------------------------------------
    "fable": _anthropic(
        id="claude-fable-5",
        label="Claude Fable 5",
        input_price=10.0,
        output_price=50.0,
        cache_write_price=12.5,
        cache_read_price=1.0,
        supports_fallbacks=True,
    ),
    "mythos": _anthropic(
        id="claude-mythos-5",
        label="Claude Mythos 5",
        input_price=10.0,
        output_price=50.0,
        cache_write_price=12.5,
        cache_read_price=1.0,
        supports_fallbacks=True,
    ),
    "opus": _anthropic(
        id="claude-opus-5",
        label="Claude Opus 5",
        input_price=5.0,
        output_price=25.0,
        cache_write_price=6.25,
        cache_read_price=0.5,
    ),
    "sonnet": _anthropic(
        id="claude-sonnet-5",
        label="Claude Sonnet 5",
        input_price=3.0,
        output_price=15.0,
        cache_write_price=3.75,
        cache_read_price=0.3,
    ),
    "haiku": ModelSpec(
        provider="anthropic",
        id="claude-haiku-4-5",
        label="Claude Haiku 4.5",
        max_output=64_000,
        context_window=200_000,
        input_price=1.0,
        output_price=5.0,
        cache_write_price=1.25,
        cache_read_price=0.1,
        # Haiku 4.5 は adaptive thinking と effort に非対応。
        supports_adaptive_thinking=False,
        supports_effort=False,
    ),
    # --- OpenAI ------------------------------------------------------
    "gpt": ModelSpec(
        provider="openai",
        id="gpt-5.4",
        label="GPT-5.4",
        max_output=64_000,
        supports_reasoning_effort=True,
        max_tokens_param="max_completion_tokens",
    ),
    "gpt-mini": ModelSpec(
        provider="openai",
        id="gpt-5.4-mini",
        label="GPT-5.4 mini",
        max_output=64_000,
        supports_reasoning_effort=True,
        max_tokens_param="max_completion_tokens",
    ),
    # --- Google ------------------------------------------------------
    "gemini": ModelSpec(
        provider="google",
        id="gemini-2.5-pro",
        label="Gemini 2.5 Pro",
        max_output=64_000,
        context_window=1_000_000,
    ),
    "gemini-flash": ModelSpec(
        provider="google",
        id="gemini-2.5-flash",
        label="Gemini 2.5 Flash",
        max_output=64_000,
        context_window=1_000_000,
    ),
    # --- DeepSeek ----------------------------------------------------
    "deepseek": ModelSpec(
        provider="deepseek",
        id="deepseek-chat",
        label="DeepSeek Chat",
        max_output=32_000,
    ),
    "deepseek-reasoner": ModelSpec(
        provider="deepseek",
        id="deepseek-reasoner",
        label="DeepSeek Reasoner",
        max_output=32_000,
    ),
    # --- Moonshot (Kimi) ---------------------------------------------
    "kimi": ModelSpec(
        provider="moonshot",
        id="kimi-k2-turbo-preview",
        label="Kimi K2",
        max_output=32_000,
    ),
    # --- Qwen --------------------------------------------------------
    "qwen": ModelSpec(
        provider="qwen",
        id="qwen-plus",
        label="Qwen Plus",
        max_output=32_000,
    ),
    "qwen-max": ModelSpec(
        provider="qwen",
        id="qwen-max",
        label="Qwen Max",
        max_output=32_000,
    ),
}

DEFAULT_MODEL_KEY = "opus"


def resolve_model(name: str | None) -> ModelSpec:
    """指定からモデルを解決する。

    受け付ける書き方は3通り。

    1. プリセットの短縮名   … `opus` / `gemini` / `deepseek`
    2. `プロバイダ:モデルID` … `openrouter:z-ai/glm-5.2` / `deepseek:deepseek-reasoner`
    3. Anthropic のモデルID  … `claude-opus-5`

    プリセットのIDは古くなることがあるので、確実なのは 2 の書き方。
    """
    if not name:
        return MODEL_PRESETS[DEFAULT_MODEL_KEY]

    raw = name.strip()
    key = raw.lower()

    if key in MODEL_PRESETS:
        return MODEL_PRESETS[key]

    # プロバイダ:モデルID の形
    if ":" in raw:
        provider_key, _, model_id = raw.partition(":")
        provider = resolve_provider(provider_key)
        model_id = model_id.strip()
        if not model_id:
            raise ValueError(f"モデルIDが空: {raw}")
        return _spec_for(provider, model_id)

    # プリセットと同じIDを完全形で書いた場合
    for spec in MODEL_PRESETS.values():
        if spec.id == raw:
            return spec

    # 素のIDは Anthropic とみなす(既定プロバイダ)
    return _spec_for(PROVIDERS[DEFAULT_PROVIDER], raw)


def _spec_for(provider: ProviderSpec, model_id: str) -> ModelSpec:
    """プリセットに無いモデルIDのための暫定スペックを作る。"""
    if provider.key == "anthropic":
        lowered = model_id.lower()
        return ModelSpec(
            provider="anthropic",
            id=model_id,
            label=model_id,
            max_output=128_000,
            context_window=1_000_000,
            # 現行世代とみなす。旧世代を指定した場合は 400 が返るので分かる。
            supports_adaptive_thinking=True,
            supports_effort=True,
            supports_fallbacks=lowered.startswith(("claude-fable", "claude-mythos")),
        )
    return ModelSpec(
        provider=provider.key,
        id=model_id,
        label=model_id,
        max_output=32_000,
        # OpenAI 系は reasoning_effort を受けないモデルも多いので既定は無効。
        # 必要なら --reasoning-effort で明示的に有効化する。
        supports_reasoning_effort=False,
        max_tokens_param=(
            "max_completion_tokens" if provider.key == "openai" else "max_tokens"
        ),
    )


@dataclass(frozen=True)
class Config:
    """1セッション分の実行設定。"""

    model: ModelSpec
    workspace: Path
    effort: str = "high"
    # 1リクエストあたりの出力上限。Anthropic では思考トークンもここに含まれる。
    max_tokens: int = 32_000
    thinking_display: str = "summarized"
    permission_mode: str = "ask"
    # 1ターンで許すツール往復の上限(暴走の保険)
    max_iterations: int = 60
    bash_timeout: int = 120
    color: bool = True
    # OpenAI 互換の接続先を明示的に上書きするとき(ローカルモデル等)
    base_url: str | None = None
    # OpenAI 系で reasoning_effort を強制的に送るか
    force_reasoning_effort: bool = False
    # 通信経路(auto / sdk / http)
    transport: str = "auto"
    # 履歴が長くなったら自動で要約して畳むか
    compaction: bool = True
    # コンテキスト長のうち、何割まで使ったら畳むか
    compact_ratio: float = 0.7
    # 畳むときに手つかずで残す直近メッセージ数
    compact_keep_recent: int = 6
    # モデルのコンテキスト長を明示的に上書きする
    context_window: int | None = None

    @property
    def provider(self) -> ProviderSpec:
        return self.model.provider_spec

    def resolved_base_url(self) -> str | None:
        """実際に使う接続先。"""
        return self.base_url or self.model.provider_spec.base_url

    def resolved_context_window(self) -> int:
        """実際に使うコンテキスト長。"""
        return self.context_window or self.model.context_window

    def compact_threshold(self) -> int:
        """このトークン数を超えたら履歴を畳む。"""
        return int(self.resolved_context_window() * self.compact_ratio)

    def with_model(self, name: str) -> "Config":
        """モデルだけ差し替えた新しい設定を返す。"""
        spec = resolve_model(name)
        return replace(self, model=spec, max_tokens=min(self.max_tokens, spec.max_output))

    def with_effort(self, effort: str) -> "Config":
        if effort not in EFFORT_LEVELS:
            raise ValueError(f"effort は {'/'.join(EFFORT_LEVELS)} のいずれか: {effort}")
        return replace(self, effort=effort)

    def with_permission_mode(self, mode: str) -> "Config":
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
    base_url: str | None = None,
    force_reasoning_effort: bool = False,
    transport: str = "auto",
    compaction: bool = True,
    context_window: int | None = None,
) -> Config:
    """CLI 引数から `Config` を組み立てる。"""
    spec = resolve_model(model)

    if transport not in TRANSPORTS:
        raise ValueError(f"transport は {'/'.join(TRANSPORTS)} のいずれか: {transport}")

    if effort not in EFFORT_LEVELS:
        raise ValueError(f"effort は {'/'.join(EFFORT_LEVELS)} のいずれか: {effort}")
    if thinking_display not in THINKING_DISPLAYS:
        raise ValueError(
            f"thinking-display は {'/'.join(THINKING_DISPLAYS)} のいずれか: {thinking_display}"
        )
    if permission_mode not in PERMISSION_MODES:
        raise ValueError(f"権限モードは {'/'.join(PERMISSION_MODES)} のいずれか: {permission_mode}")

    root = Path(workspace).expanduser().resolve() if workspace else Path.cwd().resolve()
    if not root.is_dir():
        raise ValueError(f"作業ディレクトリが存在しない: {root}")

    if color is None:
        color = os.environ.get("NO_COLOR") is None

    return Config(
        model=spec,
        workspace=root,
        effort=effort,
        max_tokens=min(max_tokens or 32_000, spec.max_output),
        thinking_display=thinking_display,
        permission_mode=permission_mode,
        color=color,
        base_url=base_url or os.environ.get("SHIBATA_CODE_BASE_URL"),
        force_reasoning_effort=force_reasoning_effort,
        transport=transport,
        compaction=compaction,
        context_window=context_window,
    )
