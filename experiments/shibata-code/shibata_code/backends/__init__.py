"""プロバイダごとのバックエンド。

各プロバイダについて、公式SDKを使う版と、標準ライブラリだけで喋る版の
2つがある。後者は iOS のように SDK を入れられない環境のためのもの。
既定は auto で、SDK が入っていればそちらを、無ければ標準ライブラリ版を使う。
"""

from __future__ import annotations

import importlib.util

from ..providers import KIND_ANTHROPIC
from .anthropic_backend import AnthropicBackend, AnthropicHTTPBackend
from .base import (
    STOP_END_TURN,
    STOP_MAX_TOKENS,
    STOP_PAUSE_TURN,
    STOP_REFUSAL,
    STOP_TOOL_USE,
    Backend,
    BackendError,
    TurnResult,
)
from .openai_backend import OpenAICompatBackend, OpenAICompatHTTPBackend

__all__ = [
    "AnthropicBackend",
    "AnthropicHTTPBackend",
    "Backend",
    "BackendError",
    "OpenAICompatBackend",
    "OpenAICompatHTTPBackend",
    "STOP_END_TURN",
    "STOP_MAX_TOKENS",
    "STOP_PAUSE_TURN",
    "STOP_REFUSAL",
    "STOP_TOOL_USE",
    "TurnResult",
    "build_backend",
    "sdk_available",
]


def sdk_available(module: str) -> bool:
    """その SDK が入っているか。import せずに調べる。"""
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def build_backend(config, client=None) -> Backend:
    """設定に合うバックエンドを作る。"""
    transport = getattr(config, "transport", "auto")
    is_anthropic = config.model.provider_spec.kind == KIND_ANTHROPIC
    sdk_module = "anthropic" if is_anthropic else "openai"

    use_http = transport == "http" or (
        transport == "auto" and not sdk_available(sdk_module)
    )

    if is_anthropic:
        if use_http:
            return AnthropicHTTPBackend(config)
        return AnthropicBackend(config, client=client)

    if use_http:
        return OpenAICompatHTTPBackend(config)
    return OpenAICompatBackend(config, client=client)
