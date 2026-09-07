"""Read-only context counters for connected bodies; never runs inference."""

import time

from ngram.cognition.history_compression import estimate_context_tokens
from ngram.inference.factory import effective_inference_provider_name


def context_snapshot(entity) -> dict:
    config = entity.config
    model = config.effective_deliberate_model()
    provider = effective_inference_provider_name(config.harness)
    budget = config.effective_context_tokens()
    latest = getattr(getattr(entity, "deliberate", None), "latest_context_status", None)
    if latest and latest.get("model") == model and latest.get("provider") == provider:
        return {**latest, "inputBudgetTokens": budget}
    # After a worker restart there is no measured prompt yet. Report the
    # persisted history with its narrower scope, not a fabricated full prompt.
    return {
        "phase": "usage", "source": "history", "model": model, "provider": provider,
        "estimatedTokens": estimate_context_tokens(
            getattr(entity, "_history_rolling_summary", "") or "",
            list(getattr(entity, "_history", [])),
        ),
        "inputBudgetTokens": budget, "observedAt": int(time.time() * 1000),
    }
