"""Per-call runtime context for tool execution."""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any

from ngram.models import Input
from ngram.presence.platforms.base import Platform


@dataclass
class ToolRuntimeContext:
    entity: Any
    inp: Input | None = None
    platform: Platform | None = None
    state: dict[str, Any] = field(default_factory=dict)


_RUNTIME_CTX: ContextVar[ToolRuntimeContext | None] = ContextVar(
    "ngram_tool_runtime_context",
    default=None,
)


def set_tool_runtime(ctx: ToolRuntimeContext) -> Token[ToolRuntimeContext | None]:
    return _RUNTIME_CTX.set(ctx)


def reset_tool_runtime(token: Token[ToolRuntimeContext | None]) -> None:
    _RUNTIME_CTX.reset(token)


def get_tool_runtime() -> ToolRuntimeContext | None:
    return _RUNTIME_CTX.get()


def require_tool_runtime() -> ToolRuntimeContext:
    ctx = get_tool_runtime()
    if ctx is None:
        raise RuntimeError("Tool runtime context unavailable.")
    return ctx
