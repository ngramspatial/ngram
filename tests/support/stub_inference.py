"""Test doubles for ``InferenceProvider`` (no network)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from ngram.inference.types import ChatCompletionResult


class StubInferenceProvider:
    """Minimal provider for entity integration tests."""

    def __init__(self, *, embed_dim: int = 768) -> None:
        self.embed_dim = embed_dim

    async def chat_completion(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        think: bool = False,
        stream: bool = False,
        num_ctx: int | None = None,
    ) -> ChatCompletionResult | AsyncIterator[str]:
        if stream:

            async def gen() -> AsyncIterator[str]:
                for ch in "Stub stream reply.":
                    yield ch

            return gen()

        sys0 = str((messages[0] or {}).get("content") or "") if messages else ""
        if "REFLEX or DELIBERATE" in sys0:
            return ChatCompletionResult(content="REFLEX")
        return ChatCompletionResult(content="Stub integrated reply.")

    async def embed(self, model: str, text: str) -> list[float]:
        return [0.01] * self.embed_dim

    async def health(self) -> dict[str, Any]:
        return {"ok": True, "backend": "stub"}

    async def list_models(self) -> list[str]:
        return ["stub-model"]

    async def close(self) -> None:
        pass

    async def ensure_models(self, *names: str) -> tuple[bool, list[str]]:
        return True, []


class UnreachableInferenceProvider:
    """Simulates tunnel/gateway down (connection errors during model checks)."""

    async def chat_completion(self, *args: Any, **kwargs: Any) -> ChatCompletionResult:
        raise ConnectionError("simulated inference unreachable")

    async def embed(self, *args: Any, **kwargs: Any) -> list[float]:
        raise ConnectionError("simulated inference unreachable")

    async def health(self) -> dict[str, Any]:
        return {"ok": False, "error": "unreachable"}

    async def list_models(self) -> list[str]:
        raise ConnectionError("simulated inference unreachable")

    async def close(self) -> None:
        pass

    async def ensure_models(self, *names: str) -> tuple[bool, list[str]]:
        raise ConnectionError("simulated gateway unavailable")
