"""Startup / liveness checks for API and operator tooling."""

from __future__ import annotations

import math
from typing import Any

from ngram.config import EntityConfig
from ngram.inference.factory import effective_inference_provider_name


async def check_inference(entity: EntityConfig, client: Any) -> dict[str, Any]:
    h = await client.health()
    result = {"subsystem": "inference", **h}
    if not result.get("ok") or effective_inference_provider_name(entity.harness) == "local":
        return result
    try:
        vector = await client.embed(
            entity.harness.models.embedding,
            "ngram hosted memory startup probe",
        )
        expected = int(entity.harness.memory.embedding_dimensions)
        if len(vector) != expected or any(not math.isfinite(float(value)) for value in vector):
            result.update({
                "ok": False,
                "error": (
                    f"embedding readiness failed: received {len(vector)} dimensions; "
                    f"expected {expected}"
                ),
            })
        else:
            result.update({
                "embedding_model": entity.harness.models.embedding,
                "embedding_dimensions": len(vector),
            })
    except Exception as exc:
        result.update({"ok": False, "error": f"embedding readiness failed: {str(exc)[:300]}"})
    return result


async def check_database(store: Any) -> dict[str, Any]:
    try:
        async with store.session() as conn:
            n = await store.count_episodes(conn)
        return {"subsystem": "database", "ok": True, "dialect": getattr(store, "dialect", "unknown"), "episodes": n}
    except Exception as e:
        return {"subsystem": "database", "ok": False, "error": str(e)[:400]}
