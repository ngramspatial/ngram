"""Protocols for storage backends (relational + blobs)."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class RelationalStore(Protocol):
    """Minimal surface used across memory modules."""

    dialect: str
    db_path: str

    def session(self) -> AbstractAsyncContextManager[Any]:
        ...

    async def wipe_experience_tables(self, conn: Any) -> None:
        ...

    async def aclose(self) -> None:
        ...

    async def search_episodes_by_embedding(
        self,
        conn: Any,
        query_emb: list[float],
        limit: int = 5,
        min_significance: float = 0.0,
    ) -> list[tuple[str, float]]:
        ...

    async def search_episodes_by_embedding_biased(
        self,
        conn: Any,
        query_emb: list[float],
        mood: Any,
        now: float,
        *,
        imprint_weight: float,
        imprint_half_life: float,
        limit: int = 10,
        min_significance: float = 0.0,
    ) -> list[tuple[str, float]]:
        ...

    async def count_episodes(self, conn: Any) -> int:
        ...

    async def count_relationships(self, conn: Any) -> int:
        ...

    async def min_episode_timestamp(self, conn: Any) -> float | None:
        ...


@runtime_checkable
class AttachmentBlobStore(Protocol):
    async def put(self, key: str, data: bytes, content_type: str | None) -> str:
        """Persist bytes; return canonical URI or path reference for metadata."""

    async def get(self, key: str) -> bytes | None:
        ...
