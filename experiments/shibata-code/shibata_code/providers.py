"""モデル提供元(プロバイダ)の定義。

Anthropic だけは専用SDKを使う。それ以外は軒並み OpenAI 互換の
`/v1/chat/completions` を出しているので、base_url を差し替えるだけで
同じ実装を使い回せる。ここではその接続先と、APIキーを探す環境変数を持つ。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# バックエンドの種類。どちらの実装で喋るかを決める。
KIND_ANTHROPIC = "anthropic"
KIND_OPENAI_COMPAT = "openai_compat"


@dataclass(frozen=True)
class ProviderSpec:
    """プロバイダ1つ分の接続情報。"""

    key: str
    label: str
    kind: str
    # OpenAI 互換の場合の接続先。Anthropic は SDK 既定に任せるので None。
    base_url: str | None
    # APIキーを探す環境変数(先に見つかったものを使う)
    api_key_envs: tuple[str, ...]
    # 新しいモデルIDを調べるときの参照先
    docs: str = ""

    def find_api_key(self) -> str | None:
        """環境変数からAPIキーを探す。"""
        for name in self.api_key_envs:
            value = os.environ.get(name)
            if value:
                return value
        return None

    def env_hint(self) -> str:
        """未設定時に案内する環境変数名。"""
        return " または ".join(self.api_key_envs)


PROVIDERS: dict[str, ProviderSpec] = {
    "anthropic": ProviderSpec(
        key="anthropic",
        label="Anthropic",
        kind=KIND_ANTHROPIC,
        base_url=None,
        api_key_envs=("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"),
        docs="https://platform.claude.com/docs/en/about-claude/models/overview",
    ),
    "openai": ProviderSpec(
        key="openai",
        label="OpenAI",
        kind=KIND_OPENAI_COMPAT,
        base_url="https://api.openai.com/v1",
        api_key_envs=("OPENAI_API_KEY",),
        docs="https://platform.openai.com/docs/models",
    ),
    "google": ProviderSpec(
        key="google",
        label="Google Gemini",
        kind=KIND_OPENAI_COMPAT,
        # Gemini は OpenAI 互換エンドポイントを別パスで出している
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        api_key_envs=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        docs="https://ai.google.dev/gemini-api/docs/models",
    ),
    "deepseek": ProviderSpec(
        key="deepseek",
        label="DeepSeek",
        kind=KIND_OPENAI_COMPAT,
        base_url="https://api.deepseek.com/v1",
        api_key_envs=("DEEPSEEK_API_KEY",),
        docs="https://api-docs.deepseek.com/",
    ),
    "moonshot": ProviderSpec(
        key="moonshot",
        label="Moonshot (Kimi)",
        kind=KIND_OPENAI_COMPAT,
        base_url="https://api.moonshot.ai/v1",
        api_key_envs=("MOONSHOT_API_KEY", "KIMI_API_KEY"),
        docs="https://platform.moonshot.ai/docs",
    ),
    "qwen": ProviderSpec(
        key="qwen",
        label="Qwen (DashScope)",
        kind=KIND_OPENAI_COMPAT,
        base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        api_key_envs=("DASHSCOPE_API_KEY", "QWEN_API_KEY"),
        docs="https://www.alibabacloud.com/help/en/model-studio/",
    ),
    "openrouter": ProviderSpec(
        key="openrouter",
        label="OpenRouter",
        kind=KIND_OPENAI_COMPAT,
        base_url="https://openrouter.ai/api/v1",
        api_key_envs=("OPENROUTER_API_KEY",),
        docs="https://openrouter.ai/models",
    ),
    "local": ProviderSpec(
        key="local",
        label="ローカル / 自前ホスト",
        kind=KIND_OPENAI_COMPAT,
        # Ollama や LM Studio の既定。--base-url で上書きできる。
        base_url="http://localhost:11434/v1",
        api_key_envs=("LOCAL_API_KEY",),
        docs="https://docs.ollama.com/openai",
    ),
}

DEFAULT_PROVIDER = "anthropic"


def resolve_provider(key: str) -> ProviderSpec:
    """プロバイダ名から `ProviderSpec` を得る。"""
    normalized = key.strip().lower()
    if normalized not in PROVIDERS:
        known = ", ".join(PROVIDERS)
        raise ValueError(f"未知のプロバイダ: {key}(使えるのは {known})")
    return PROVIDERS[normalized]
