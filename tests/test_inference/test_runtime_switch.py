from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import ngram.entity as entity_module
from ngram.config import EntityCognition, HarnessConfig, InferenceSettings
from ngram.entity import Entity
from ngram.inference.control import InferenceControl


class StubProvider:
    def __init__(
        self,
        models: list[str],
        embedding_models: set[str] | None = None,
        embedding_dimensions: int = 2,
    ) -> None:
        self.models = models
        self.embedding_models = (
            {"nomic-embed-text"} if embedding_models is None else embedding_models
        )
        self.closed = False
        self.embed_calls: list[tuple[str, str]] = []
        self.embedding_dimensions = embedding_dimensions

    async def list_models(self) -> list[str]:
        return self.models

    async def close(self) -> None:
        self.closed = True

    async def embed(self, model: str, text: str) -> list[float]:
        self.embed_calls.append((model, text))
        if model not in self.embedding_models or not text:
            return []
        return [1.0, *([0.0] * (self.embedding_dimensions - 1))]


@pytest.mark.asyncio
async def test_runtime_switch_rebinds_every_long_lived_inference_consumer(monkeypatch, tmp_path) -> None:
    config = SimpleNamespace(
        name="test",
        harness=HarnessConfig(inference=InferenceSettings(provider="remote_gateway", base_url="https://private.invalid")),
        cognition=EntityCognition(reflex_model="local-model", deliberate_model="local-model"),
    )
    old = StubProvider(["local-model"])
    replacement = StubProvider(
        ["gpt-6-astra"], {"text-embedding-3-small"}, embedding_dimensions=768
    )
    seen = {}

    def build(harness, *, bearer_token=None):
        seen["provider"] = harness.inference.provider
        seen["token"] = bearer_token
        return replacement

    monkeypatch.setattr(entity_module, "build_inference_provider", build)
    entity = Entity.__new__(Entity)
    entity.config = config
    entity.client = old
    entity._embedding_client = old
    entity._startup_inference = InferenceSettings(provider="remote_gateway", base_url="https://private.invalid")
    entity._startup_reflex_model = "local-model"
    entity._startup_deliberate_model = "local-model"
    entity._startup_embedding_model = "nomic-embed-text"
    entity._turn_lock = asyncio.Lock()
    entity.inference_control = InferenceControl(tmp_path / "paused")
    entity._inference_switch_lock = asyncio.Lock()
    entity.knowledge = SimpleNamespace(client=old)
    entity.procedural = SimpleNamespace(client=old)
    entity.router = SimpleNamespace(client=old)
    entity.deliberate = SimpleNamespace(client=old)

    status = await entity.configure_inference({
        "mode": "frontier",
        "provider": "openai",
        "model": "gpt-6-astra",
        "apiKey": "runtime-secret",
        "embeddingMode": "provider",
        "embeddingModel": "text-embedding-3-small",
    })

    assert status == {
        "mode": "frontier",
        "provider": "openai",
        "model": "gpt-6-astra",
        "verified": True,
        "warning": "",
        "embeddingMode": "provider",
        "embeddingModel": "text-embedding-3-small",
        "embeddingDimensions": 768,
    }
    assert seen == {"provider": "openai", "token": "runtime-secret"}
    assert old.closed is True
    assert entity.client.provider is replacement
    remembered = await entity.client.embed("text-embedding-3-small", "remember")
    assert len(remembered) == 768
    assert remembered[0] == 1.0
    assert replacement.embed_calls[0][0] == "text-embedding-3-small"
    assert all(component.client is entity.client for component in (
        entity.knowledge, entity.procedural, entity.router, entity.deliberate
    ))
    assert config.cognition.deliberate_model == "gpt-6-astra"
    assert config.harness.models.embedding == "text-embedding-3-small"
    assert "runtime-secret" not in repr(config)


@pytest.mark.asyncio
async def test_runtime_switch_can_explicitly_keep_existing_embeddings(monkeypatch, tmp_path) -> None:
    config = SimpleNamespace(
        name="test",
        harness=HarnessConfig(inference=InferenceSettings(provider="local")),
        cognition=EntityCognition(reflex_model="local-model", deliberate_model="local-model"),
    )
    old = StubProvider(["local-model"])
    replacement = StubProvider(["hosted-model"], set())
    monkeypatch.setattr(entity_module, "build_inference_provider", lambda *_args, **_kwargs: replacement)
    entity = Entity.__new__(Entity)
    entity.config = config
    entity.client = old
    entity._embedding_client = old
    entity._startup_inference = InferenceSettings(provider="local")
    entity._startup_reflex_model = "local-model"
    entity._startup_deliberate_model = "local-model"
    entity._startup_embedding_model = "nomic-embed-text"
    entity._turn_lock = asyncio.Lock()
    entity.inference_control = InferenceControl(tmp_path / "paused")
    entity._inference_switch_lock = asyncio.Lock()
    entity.knowledge = SimpleNamespace(client=old)
    entity.procedural = SimpleNamespace(client=old)
    entity.router = SimpleNamespace(client=old)
    entity.deliberate = SimpleNamespace(client=old)

    status = await entity.configure_inference({
        "mode": "frontier",
        "provider": "openai",
        "model": "hosted-model",
        "apiKey": "secret",
        "embeddingMode": "existing",
    })

    assert status["embeddingMode"] == "existing"
    assert status["embeddingDimensions"] is None
    assert entity.client.chat_provider.provider is replacement
    assert entity.client.embedding_provider is old
    assert old.closed is False


@pytest.mark.asyncio
async def test_runtime_switch_rejects_bad_hosted_embeddings_without_mutating_entity(monkeypatch, tmp_path) -> None:
    config = SimpleNamespace(
        name="test",
        harness=HarnessConfig(inference=InferenceSettings(provider="local")),
        cognition=EntityCognition(reflex_model="local-model", deliberate_model="local-model"),
    )
    old = StubProvider(["local-model"])
    replacement = StubProvider(["hosted-model"], set())
    monkeypatch.setattr(entity_module, "build_inference_provider", lambda *_args, **_kwargs: replacement)
    entity = Entity.__new__(Entity)
    entity.config = config
    entity.client = old
    entity._embedding_client = old
    entity._startup_inference = InferenceSettings(provider="local")
    entity._startup_reflex_model = "local-model"
    entity._startup_deliberate_model = "local-model"
    entity._startup_embedding_model = "nomic-embed-text"
    entity._turn_lock = asyncio.Lock()
    entity.inference_control = InferenceControl(tmp_path / "paused")
    entity._inference_switch_lock = asyncio.Lock()
    entity.knowledge = SimpleNamespace(client=old)
    entity.procedural = SimpleNamespace(client=old)
    entity.router = SimpleNamespace(client=old)
    entity.deliberate = SimpleNamespace(client=old)

    with pytest.raises(RuntimeError, match="Hosted memory is not ready"):
        await entity.configure_inference({
            "mode": "frontier",
            "provider": "openai",
            "model": "hosted-model",
            "apiKey": "secret",
            "embeddingMode": "provider",
            "embeddingModel": "missing-embedding-model",
        })

    assert entity.client is old
    assert entity._embedding_client is old
    assert replacement.closed is True
    assert old.closed is False
