"""Shared inference result types (body ↔ brain boundary)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ToolCallSpec:
    name: str
    arguments: dict[str, Any]
    id: str = ""


@dataclass
class ChatCompletionResult:
    content: str
    thinking: Optional[str] = None
    tool_calls: list[ToolCallSpec] = field(default_factory=list)
    finish_reason: Optional[str] = None
    raw_message: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, int] = field(default_factory=dict)
    raw_assistant_text: str = ""
