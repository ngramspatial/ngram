"""Embedding helper using Ollama."""

from __future__ import annotations

from ngram.inference.protocol import InferenceProvider


async def embed_text(client: InferenceProvider, model: str, text: str) -> list[float]:
    return await client.embed(model, text)
