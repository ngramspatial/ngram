from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from ngram.container.lease import CanonicalLease
from ngram.memory import postgres_store
from ngram.storage import factory


def test_factory_configures_authoritative_worker_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeStore:
        def __init__(self, url: str, **kwargs: object) -> None:
            captured.update({"url": url, **kwargs})

    entity = SimpleNamespace(
        database_url=lambda: "postgresql://fixture",
        raw={"continuity": {"entity_id": "ng1:" + "a" * 40}},
    )
    monkeypatch.setattr(factory, "PostgresMemoryStore", FakeStore)
    monkeypatch.setenv("NGRAM_CANONICAL_LEASE_REQUIRED", "true")
    monkeypatch.setenv("RAILWAY_DEPLOYMENT_ID", "deployment-123")

    factory.create_memory_store(entity)

    assert captured["url"] == "postgresql://fixture"
    assert captured["canonical_lease_required"] is True
    assert captured["entity_id"] == "ng1:" + "a" * 40
    assert captured["lease_holder"] == "deployment-123"


class _AcquireContext:
    def __init__(self, value: object) -> None:
        self.value = value

    async def __aenter__(self) -> object:
        return self.value

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class _Pool:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def acquire(self) -> _AcquireContext:
        return _AcquireContext(self)

    async def execute(self, _sql: str) -> None:
        self.events.append("schema")

    async def close(self) -> None:
        self.events.append("pool-close")


class _LeaseManager:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def acquire(self, entity_id: str, holder: str, *, ttl_seconds: int) -> CanonicalLease:
        self.events.append("lease-acquire")
        now = datetime.now(UTC)
        return CanonicalLease(
            entity_id=entity_id,
            holder=holder,
            generation=1,
            acquired_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
            token="secret-token",
        )

    async def release(self, _lease: CanonicalLease) -> dict[str, object]:
        self.events.append("lease-release")
        return {}


@pytest.mark.asyncio
async def test_postgres_store_holds_lease_before_initializing_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    pool = _Pool(events)

    async def create_pool(*_args: object, **_kwargs: object) -> _Pool:
        events.append("pool-create")
        return pool

    monkeypatch.setattr(
        postgres_store,
        "asyncpg",
        SimpleNamespace(create_pool=create_pool),
    )
    store = postgres_store.PostgresMemoryStore(
        "postgresql://fixture",
        canonical_lease_required=True,
        entity_id="ng1:" + "b" * 40,
        lease_holder="worker-1",
        lease_manager=_LeaseManager(events),
    )

    assert await store._ensure_pool() is pool
    assert events[:3] == ["lease-acquire", "pool-create", "schema"]
    await store.aclose()
    assert events[-2:] == ["lease-release", "pool-close"]
