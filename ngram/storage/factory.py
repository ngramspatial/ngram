"""Choose SQLite file vs Postgres from entity config + DATABASE_URL."""

from __future__ import annotations

import os
import socket
from typing import TYPE_CHECKING

from ngram.memory.postgres_store import PostgresMemoryStore
from ngram.memory.store import MemoryStore

if TYPE_CHECKING:
    from ngram.config import EntityConfig

MemoryStoreBackend = MemoryStore | PostgresMemoryStore


def create_memory_store(entity: EntityConfig) -> MemoryStoreBackend:
    url = entity.database_url()
    if url.startswith(("postgresql://", "postgres://")):
        required = (os.environ.get("NGRAM_CANONICAL_LEASE_REQUIRED") or "").strip().lower()
        lease_required = required in {"1", "true", "yes", "on"}
        continuity = (
            entity.raw.get("continuity") if isinstance(entity.raw.get("continuity"), dict) else {}
        )
        entity_id = (os.environ.get("NGRAM_ENTITY_ID") or continuity.get("entity_id") or "").strip()
        deployment = (
            os.environ.get("RAILWAY_DEPLOYMENT_ID")
            or os.environ.get("NGRAM_CANONICAL_LEASE_HOLDER")
            or ""
        ).strip()
        holder = deployment or f"{socket.gethostname()}/{os.getpid()}"
        return PostgresMemoryStore(
            url,
            display_name="postgresql",
            canonical_lease_required=lease_required,
            entity_id=entity_id,
            lease_holder=holder,
        )
    return MemoryStore(entity.db_path())
