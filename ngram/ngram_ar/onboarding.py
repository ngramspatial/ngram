"""Authenticated setup metadata, without credentials or personal memory content."""

from __future__ import annotations

import os
from pathlib import Path

from ngram.inference.factory import effective_inference_provider_name


async def setup_snapshot(entity) -> dict:
    config = entity.config
    has_memories = None
    try:
        async with entity.store.session() as conn:
            # Check every common embedded memory table, not just chat history.
            has_memories = False
            for table in ("episodes", "beliefs", "imprints", "inner_voice"):
                cursor = await conn.execute(f"SELECT COUNT(*) FROM {table}")
                row = await cursor.fetchone()
                if row and row[0]:
                    has_memories = True
                    break
    except Exception:
        # Unknown is not permission to change an existing vector space.
        has_memories = None
    workspace = os.environ.get("NGRAM_EXECUTION_WORKSPACE_DIR", "")
    volume = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "")
    volume_mounted = bool(workspace and volume and (
        Path(workspace) == Path(volume) or Path(volume) in Path(workspace).parents
    ))
    return {
        "entityName": config.name,
        "provider": effective_inference_provider_name(config.harness),
        "model": config.effective_deliberate_model(),
        "embeddingModel": config.harness.models.embedding,
        "embeddingDimensions": config.harness.memory.embedding_dimensions,
        "hasMemories": has_memories,
        "memoryStore": "postgres" if config.database_url() else "sqlite",
        "volumeMounted": volume_mounted,
        "relationships": bool(config.harness.memory.relational.enabled),
    }
