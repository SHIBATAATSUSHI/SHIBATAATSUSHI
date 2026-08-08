"""プロバイダごとのバックエンド。"""

from __future__ import annotations

from ..providers import KIND_ANTHROPIC
from .anthropic_backend import AnthropicBackend
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
from .openai_backend import OpenAICompatBackend

__all__ = [
    "AnthropicBackend",
    "Backend",
    "BackendError",
    "OpenAICompatBackend",
    "STOP_END_TURN",
    "STOP_MAX_TOKENS",
    "STOP_PAUSE_TURN",
    "STOP_REFUSAL",
    "STOP_TOOL_USE",
    "TurnResult",
    "build_backend",
]


def build_backend(config, client=None) -> Backend:
    """設定に合うバックエンドを作る。"""
    if config.model.provider_spec.kind == KIND_ANTHROPIC:
        return AnthropicBackend(config, client=client)
    return OpenAICompatBackend(config, client=client)
