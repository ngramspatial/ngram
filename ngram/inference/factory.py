"""Build the active InferenceProvider from harness + environment."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from ngram.inference.openai_transport import OpenAICompatibleTransport, OpenAIResponsesTransport
from ngram.inference.providers import LocalRuntimeProvider, RemoteGatewayProvider
from ngram.inference.protocol import InferenceProvider

if TYPE_CHECKING:
    from ngram.config import HarnessConfig

DEFAULT_GATEWAY_KEY_ENV = "NGRAM_INFERENCE_GATEWAY_TOKEN"
CF_ACCESS_CLIENT_ID_ENV = "NGRAM_CF_ACCESS_CLIENT_ID"
CF_ACCESS_CLIENT_SECRET_ENV = "NGRAM_CF_ACCESS_CLIENT_SECRET"

# Hosted providers that expose an OpenAI-compatible Chat Completions surface.
# Keeping the path separate matters for providers such as Gemini and Groq.
_MANAGED_REMOTE_PRESETS: dict[str, dict[str, str]] = {
    "openai": {
        "default_base": "https://api.openai.com",
        "api_path": "/v1",
        "key_env": "OPENAI_API_KEY",
        "max_tokens_field": "max_completion_tokens",
    },
    "anthropic": {
        "default_base": "https://api.anthropic.com",
        "api_path": "/v1",
        "key_env": "ANTHROPIC_API_KEY",
    },
    "gemini": {
        "default_base": "https://generativelanguage.googleapis.com",
        "api_path": "/v1beta/openai",
        "key_env": "GEMINI_API_KEY",
    },
    "openrouter": {
        "default_base": "https://openrouter.ai/api",
        "api_path": "/v1",
        "key_env": "OPENROUTER_API_KEY",
    },
    "xai": {
        "default_base": "https://api.x.ai",
        "api_path": "/v1",
        "key_env": "XAI_API_KEY",
    },
    "groq": {
        "default_base": "https://api.groq.com",
        "api_path": "/openai/v1",
        "key_env": "GROQ_API_KEY",
    },
    "together": {
        "default_base": "https://api.together.xyz",
        "api_path": "/v1",
        "key_env": "TOGETHER_API_KEY",
    },
    "fireworks": {
        "default_base": "https://api.fireworks.ai",
        "api_path": "/inference/v1",
        "key_env": "FIREWORKS_API_KEY",
    },
    "mistral": {
        "default_base": "https://api.mistral.ai",
        "api_path": "/v1",
        "key_env": "MISTRAL_API_KEY",
    },
    "deepseek": {
        "default_base": "https://api.deepseek.com",
        "api_path": "/v1",
        "key_env": "DEEPSEEK_API_KEY",
    },
    "venice": {
        "default_base": "https://api.venice.ai/api",
        "api_path": "/v1",
        "key_env": "VENICE_API_KEY",
    },
}

INFERENCE_PROVIDERS = frozenset({"local", "remote_gateway", "custom", *_MANAGED_REMOTE_PRESETS})

# OpenAI's current embedding API can emit a chosen vector width. Keeping the
# harness width at 768 preserves storage compatibility. Different embedding
# models still produce different vector spaces; existing memories need migration.
DEFAULT_HOSTED_EMBEDDING_MODELS: dict[str, str] = {
    "openai": "text-embedding-3-small",
}


def default_embedding_model(provider_name: str) -> str:
    """Return a safe provider-native embedding default, when one is known."""
    return DEFAULT_HOSTED_EMBEDDING_MODELS.get((provider_name or "").strip().lower(), "")


def effective_inference_provider_name(harness: HarnessConfig) -> str:
    """Resolve a supported local, private-gateway, or hosted provider name."""
    p = (harness.inference.provider or "").strip().lower()
    if p in INFERENCE_PROVIDERS:
        return p
    mode = (harness.deployment.mode or "local").strip().lower()
    if mode == "hybrid_railway":
        return "remote_gateway"
    if mode == "cloud":
        return "openai"
    return "local"


def _effective_bearer_env(harness: HarnessConfig, preset: str) -> str:
    v = (harness.inference.api_key_env or "").strip()
    if not v or v == DEFAULT_GATEWAY_KEY_ENV:
        return preset
    return v


def inference_bearer_key_env(harness: HarnessConfig) -> str:
    """Environment variable name that should hold the Bearer token for the active remote brain."""
    name = effective_inference_provider_name(harness)
    if name in _MANAGED_REMOTE_PRESETS:
        return _effective_bearer_env(harness, _MANAGED_REMOTE_PRESETS[name]["key_env"])
    if name == "remote_gateway":
        return (harness.inference.api_key_env or "").strip() or DEFAULT_GATEWAY_KEY_ENV
    return DEFAULT_GATEWAY_KEY_ENV


def _bearer_token_for_env(harness: HarnessConfig, env_name: str) -> str:
    return (os.environ.get(env_name) or "").strip()


def _remote_gateway_headers(token: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    client_id = (
        os.environ.get(CF_ACCESS_CLIENT_ID_ENV) or os.environ.get("CF_ACCESS_CLIENT_ID") or ""
    ).strip()
    client_secret = (
        os.environ.get(CF_ACCESS_CLIENT_SECRET_ENV)
        or os.environ.get("CF_ACCESS_CLIENT_SECRET")
        or ""
    ).strip()
    if bool(client_id) != bool(client_secret):
        raise RuntimeError(
            "Cloudflare Access service authentication requires both "
            f"{CF_ACCESS_CLIENT_ID_ENV} and {CF_ACCESS_CLIENT_SECRET_ENV}"
        )
    if client_id:
        headers["CF-Access-Client-Id"] = client_id
        headers["CF-Access-Client-Secret"] = client_secret
    return headers


def _inference_base_url(harness: HarnessConfig, *, provider_name: str) -> str:
    u = (harness.inference.base_url or "").strip()
    if u:
        return u.rstrip("/")
    if provider_name in _MANAGED_REMOTE_PRESETS:
        return _MANAGED_REMOTE_PRESETS[provider_name]["default_base"]
    return harness.ollama.base_url.rstrip("/")


def _inference_api_path(harness: HarnessConfig, *, provider_name: str) -> str:
    """Return the suffix appended to the configured host/root.

    A custom provider's URL is treated as the complete OpenAI-compatible API
    prefix. Managed overrides may likewise provide a complete ``.../v1`` URL.
    Legacy OpenRouter/Venice ``.../api`` overrides retain their old behavior.
    """
    explicit = (harness.inference.base_url or "").strip().rstrip("/")
    if provider_name == "custom":
        return ""
    if provider_name not in _MANAGED_REMOTE_PRESETS:
        return "/v1"
    preset_path = _MANAGED_REMOTE_PRESETS[provider_name]["api_path"]
    if not explicit:
        return preset_path
    if explicit.endswith(preset_path):
        return ""
    if explicit.endswith("/v1") or explicit.endswith("/v1beta/openai"):
        return ""
    if provider_name in ("openrouter", "venice"):
        return "/v1"
    return preset_path


def build_inference_provider(
    harness: HarnessConfig,
    *,
    bearer_token: str | None = None,
) -> InferenceProvider:
    name = effective_inference_provider_name(harness)
    timeout = float(harness.inference.timeout or harness.ollama.timeout)
    retry_attempts = int(harness.ollama.retry_attempts)
    retry_delay = float(harness.ollama.retry_delay)
    base = _inference_base_url(harness, provider_name=name)
    api_path = _inference_api_path(harness, provider_name=name)

    if name == "remote_gateway":
        env_name = inference_bearer_key_env(harness)
        token = (
            bearer_token if bearer_token is not None else _bearer_token_for_env(harness, env_name)
        )
        extra = _remote_gateway_headers(token)
        transport = OpenAICompatibleTransport(
            base,
            api_path=api_path,
            gemma_shaping=True,
            timeout=timeout,
            retry_attempts=retry_attempts,
            retry_delay=retry_delay,
            extra_headers=extra,
        )
        return RemoteGatewayProvider(
            transport,
            gateway_public_base=base,
            gateway_token=token,
            gateway_headers=extra,
            health_timeout=min(30.0, timeout),
        )

    if name in _MANAGED_REMOTE_PRESETS or name == "custom":
        env_name = inference_bearer_key_env(harness)
        token = (
            bearer_token if bearer_token is not None else _bearer_token_for_env(harness, env_name)
        )
        extra = {"Authorization": f"Bearer {token}"} if token else None
        transport_class = (
            OpenAIResponsesTransport if name == "openai" else OpenAICompatibleTransport
        )
        transport = transport_class(
            base,
            api_path=api_path,
            gemma_shaping=False,
            max_tokens_field=_MANAGED_REMOTE_PRESETS.get(name, {}).get(
                "max_tokens_field", "max_tokens"
            ),
            pass_temperature=name != "openai",
            timeout=timeout,
            retry_attempts=retry_attempts,
            retry_delay=retry_delay,
            extra_headers=extra,
            embedding_dimensions=(
                int(harness.memory.embedding_dimensions) if name in {"openai", "venice"} else None
            ),
        )
        return LocalRuntimeProvider(transport)

    transport = OpenAICompatibleTransport(
        base,
        api_path=api_path,
        gemma_shaping=True,
        timeout=timeout,
        retry_attempts=retry_attempts,
        retry_delay=retry_delay,
    )
    return LocalRuntimeProvider(transport)


async def validate_inference_models(
    provider: Any,
    reflex_model: str,
    deliberate_model: str,
    harness_default_deliberate: str,
) -> tuple[bool, list[str]]:
    fn = getattr(provider, "ensure_models", None)
    if not callable(fn):
        return True, []
    return await fn(reflex_model, deliberate_model, harness_default_deliberate)
