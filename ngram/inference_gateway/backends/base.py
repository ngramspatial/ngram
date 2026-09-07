"""Backend adapters: local model runtimes (Ollama, OpenAI-compatible servers)."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class InferenceBackend(Protocol):
    base_url: str

    async def get_models(self) -> dict[str, Any]:
        """Return OpenAI-style ``{"data": [{"id": "..."}]}``."""

    async def post_chat_completions(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any] | str]:
        """Return (status_code, json_dict or raw text for stream)."""

    async def post_embeddings(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        ...

    async def aclose(self) -> None:
        ...
