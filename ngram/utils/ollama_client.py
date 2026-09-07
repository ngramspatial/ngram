"""Backward-compatible imports; prefer ``ngram.inference`` for new code."""

from __future__ import annotations

from ngram.inference.openai_transport import OpenAICompatibleTransport
from ngram.inference.types import ChatCompletionResult, ToolCallSpec

# Historical name — same implementation as the OpenAI-compatible transport.
OllamaClient = OpenAICompatibleTransport

__all__ = ["ChatCompletionResult", "OllamaClient", "ToolCallSpec", "OpenAICompatibleTransport"]
