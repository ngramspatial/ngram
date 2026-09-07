from __future__ import annotations

from types import SimpleNamespace

import pytest

from ngram.config import HarnessConfig, InferenceSettings
from ngram.health import check_inference


class Provider:
    def __init__(self, dimensions: int) -> None:
        self.dimensions = dimensions

    async def health(self):
        return {"ok": True, "backend": "test"}

    async def embed(self, model, text):
        return [0.0] * self.dimensions


@pytest.mark.asyncio
async def test_hosted_health_includes_embedding_readiness() -> None:
    entity = SimpleNamespace(
        harness=HarnessConfig(inference=InferenceSettings(provider="openai"))
    )
    entity.harness.models.embedding = "text-embedding-3-small"
    result = await check_inference(entity, Provider(768))
    assert result["ok"] is True
    assert result["embedding_dimensions"] == 768


@pytest.mark.asyncio
async def test_hosted_health_fails_closed_on_memory_width_mismatch() -> None:
    entity = SimpleNamespace(
        harness=HarnessConfig(inference=InferenceSettings(provider="custom"))
    )
    result = await check_inference(entity, Provider(12))
    assert result["ok"] is False
    assert "expected 768" in result["error"]
