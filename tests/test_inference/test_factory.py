"""Inference factory: hosted OpenRouter / Venice presets."""

from __future__ import annotations

import pytest

from ngram.config import (
    DeploymentSettings,
    HarnessConfig,
    InferenceSettings,
    apply_harness_env_overrides,
)
from ngram.inference.factory import (
    build_inference_provider,
    default_embedding_model,
    effective_inference_provider_name,
    inference_bearer_key_env,
)
from ngram.inference.openai_transport import OpenAIResponsesTransport
from ngram.inference.providers import LocalRuntimeProvider, RemoteGatewayProvider


def _harness(**inf_kwargs: object) -> HarnessConfig:
    return HarnessConfig(inference=InferenceSettings(**inf_kwargs))


def test_effective_provider_openrouter_explicit() -> None:
    h = _harness(provider="openrouter")
    assert effective_inference_provider_name(h) == "openrouter"


def test_effective_provider_hybrid_defaults_to_gateway() -> None:
    h = HarnessConfig(
        deployment=DeploymentSettings(mode="hybrid_railway"),
        inference=InferenceSettings(provider=""),
    )
    assert effective_inference_provider_name(h) == "remote_gateway"


def test_inference_bearer_key_env_presets() -> None:
    assert inference_bearer_key_env(_harness(provider="openrouter")) == "OPENROUTER_API_KEY"
    assert inference_bearer_key_env(_harness(provider="venice")) == "VENICE_API_KEY"


def test_inference_bearer_custom_api_key_env() -> None:
    h = _harness(provider="openrouter", api_key_env="MY_OPENROUTER")
    assert inference_bearer_key_env(h) == "MY_OPENROUTER"


def test_build_openrouter_default_base(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    p = build_inference_provider(_harness(provider="openrouter", base_url=""))
    assert isinstance(p, LocalRuntimeProvider)
    assert p._t.base_url == "https://openrouter.ai/api"  # noqa: SLF001


def test_openai_has_provider_native_embedding_default_and_width() -> None:
    assert default_embedding_model("openai") == "text-embedding-3-small"
    p = build_inference_provider(_harness(provider="openai"), bearer_token="test")
    assert p._t.embedding_dimensions == 768  # noqa: SLF001


def test_venice_requests_configured_embedding_width() -> None:
    h = _harness(provider="venice")
    h.memory.embedding_dimensions = 512
    p = build_inference_provider(h, bearer_token="test")
    assert p._t.embedding_dimensions == 512  # noqa: SLF001


def test_hosted_env_overrides_chat_and_embedding_models(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NGRAM_INFERENCE_PROVIDER", "openai")
    monkeypatch.setenv("NGRAM_INFERENCE_MODEL", "gpt-hosted")
    monkeypatch.delenv("NGRAM_EMBEDDING_MODEL", raising=False)
    h = HarnessConfig()
    apply_harness_env_overrides(h)
    assert h.inference.model == "gpt-hosted"
    assert h.models.reflex == "gpt-hosted"
    assert h.models.deliberate == "gpt-hosted"
    assert h.models.embedding == "text-embedding-3-small"


def test_hosted_env_overrides_embedding_dimensions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NGRAM_EMBEDDING_DIMENSIONS", "768")
    h = HarnessConfig()
    h.memory.embedding_dimensions = 1024
    apply_harness_env_overrides(h)
    assert h.memory.embedding_dimensions == 768


def test_build_openrouter_custom_base(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "x")
    p = build_inference_provider(
        _harness(provider="openrouter", base_url="https://example.com/custom")
    )
    assert p._t.base_url == "https://example.com/custom"  # noqa: SLF001


def test_build_venice_default_base(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VENICE_API_KEY", "x")
    p = build_inference_provider(_harness(provider="venice"))
    assert isinstance(p, LocalRuntimeProvider)
    assert p._t.base_url == "https://api.venice.ai/api"  # noqa: SLF001


@pytest.mark.parametrize(
    ("provider", "expected_api", "key_env"),
    [
        ("openai", "https://api.openai.com/v1", "OPENAI_API_KEY"),
        ("anthropic", "https://api.anthropic.com/v1", "ANTHROPIC_API_KEY"),
        ("gemini", "https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY"),
        ("xai", "https://api.x.ai/v1", "XAI_API_KEY"),
        ("groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY"),
        ("together", "https://api.together.xyz/v1", "TOGETHER_API_KEY"),
        ("fireworks", "https://api.fireworks.ai/inference/v1", "FIREWORKS_API_KEY"),
        ("mistral", "https://api.mistral.ai/v1", "MISTRAL_API_KEY"),
        ("deepseek", "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY"),
    ],
)
def test_managed_frontier_provider_endpoints(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    expected_api: str,
    key_env: str,
) -> None:
    monkeypatch.setenv(key_env, "secret")
    p = build_inference_provider(_harness(provider=provider))
    assert isinstance(p, LocalRuntimeProvider)
    assert p._t.api == expected_api  # noqa: SLF001
    assert p._t.extra_headers == {"Authorization": "Bearer secret"}  # noqa: SLF001


def test_runtime_bearer_override_does_not_modify_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    p = build_inference_provider(
        _harness(provider="openai"),
        bearer_token="runtime-only",
    )
    assert p._t.extra_headers == {"Authorization": "Bearer runtime-only"}  # noqa: SLF001
    assert "OPENAI_API_KEY" not in __import__("os").environ
    assert isinstance(p._t, OpenAIResponsesTransport)  # noqa: SLF001


def test_build_remote_gateway_class(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NGRAM_INFERENCE_GATEWAY_TOKEN", "tok")
    monkeypatch.delenv("NGRAM_CF_ACCESS_CLIENT_ID", raising=False)
    monkeypatch.delenv("NGRAM_CF_ACCESS_CLIENT_SECRET", raising=False)
    p = build_inference_provider(
        _harness(provider="remote_gateway", base_url="https://gw.example.com")
    )
    assert isinstance(p, RemoteGatewayProvider)


def test_remote_gateway_adds_cloudflare_access_service_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NGRAM_INFERENCE_GATEWAY_TOKEN", "tok")
    monkeypatch.setenv("NGRAM_CF_ACCESS_CLIENT_ID", "access-id")
    monkeypatch.setenv("NGRAM_CF_ACCESS_CLIENT_SECRET", "access-secret")
    p = build_inference_provider(
        _harness(provider="remote_gateway", base_url="https://gw.example.com")
    )
    assert p._t.extra_headers == {  # noqa: SLF001
        "Authorization": "Bearer tok",
        "CF-Access-Client-Id": "access-id",
        "CF-Access-Client-Secret": "access-secret",
    }


def test_remote_gateway_rejects_partial_cloudflare_access_service_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NGRAM_INFERENCE_GATEWAY_TOKEN", "tok")
    monkeypatch.setenv("NGRAM_CF_ACCESS_CLIENT_ID", "access-id")
    monkeypatch.delenv("NGRAM_CF_ACCESS_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("CF_ACCESS_CLIENT_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="requires both"):
        build_inference_provider(
            _harness(provider="remote_gateway", base_url="https://gw.example.com")
        )
