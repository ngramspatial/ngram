"""Inference provider protocol — brain side of the body/brain split."""

from __future__ import annotations

from typing import Any, AsyncIterator, Optional, Protocol, runtime_checkable

from ngram.inference.types import ChatCompletionResult


@runtime_checkable
class InferenceProvider(Protocol):
    async def chat_completion(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        tools: Optional[list[dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int | None = 1024,
        think: bool = False,
        stream: bool = False,
        num_ctx: int | None = None,
    ) -> ChatCompletionResult | AsyncIterator[str]:
        ...

    async def embed(self, model: str, text: str) -> list[float]:
        ...

    async def health(self) -> dict[str, Any]:
        """Structured status for startup and ops checks."""

    async def list_models(self) -> list[str]:
        ...

    async def close(self) -> None:
        ...
