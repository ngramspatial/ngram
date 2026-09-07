from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ngram.container import ContainerError
from ngram.container.lease import PostgresLeaseManager


class _Transaction:
    def __init__(self, connection, options):
        self.connection = connection
        self.options = options

    async def __aenter__(self):
        self.connection.store.transactions.append(self.options)

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _LeaseStore:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
        self.row = None
        self.transactions = []
        self.closed = 0

    async def connect(self, _dsn):
        return _Connection(self)


class _Connection:
    def __init__(self, store):
        self.store = store

    def transaction(self, **options):
        return _Transaction(self, options)

    async def fetchval(self, sql, *args):
        assert sql == "SELECT CURRENT_TIMESTAMP"
        assert not args
        return self.store.now

    async def fetchrow(self, sql, *args):
        if "FROM ngram_entity_leases" not in sql:
            raise AssertionError(sql)
        if self.store.row and self.store.row["entity_id"] == args[0]:
            return dict(self.store.row)
        return None

    async def execute(self, sql, *args):
        if "CREATE TABLE IF NOT EXISTS" in sql:
            return
        if "INSERT INTO ngram_entity_leases" in sql:
            self.store.row = {
                "entity_id": args[0],
                "holder": args[1],
                "token_hash": args[2],
                "generation": args[3],
                "acquired_at": args[4],
                "expires_at": args[5],
            }
            return
        if sql.startswith("UPDATE ngram_entity_leases"):
            self.store.row["expires_at"] = args[1]
            return
        if sql.startswith("DELETE FROM ngram_entity_leases"):
            self.store.row = None
            return
        raise AssertionError(sql)

    async def close(self):
        self.store.closed += 1


@pytest.mark.asyncio
async def test_canonical_lease_blocks_competing_writer_and_releases_without_token_leak() -> None:
    store = _LeaseStore()
    manager = PostgresLeaseManager("postgresql://fixture", connect=store.connect)
    entity_id = "ng1:" + "a" * 40

    first = await manager.acquire(entity_id, "railway/worker-a", ttl_seconds=120)
    assert first.generation == 1
    assert first.token
    public = first.public_record()
    assert public["token_included"] is False
    assert first.token not in str(public)

    with pytest.raises(ContainerError, match="active canonical writer"):
        await manager.acquire(entity_id, "local/worker-b", ttl_seconds=120)

    renewed = await manager.renew(first, ttl_seconds=300)
    assert renewed.generation == first.generation
    assert renewed.expires_at > first.expires_at
    inspected = await manager.inspect(entity_id)
    assert inspected["holder"] == "railway/worker-a"
    assert inspected["token_included"] is False

    released = await manager.release(renewed)
    assert released["state"] == "released"
    assert await manager.inspect(entity_id) is None


@pytest.mark.asyncio
async def test_expired_lease_can_be_taken_over_with_new_generation() -> None:
    store = _LeaseStore()
    manager = PostgresLeaseManager("postgresql://fixture", connect=store.connect)
    entity_id = "ng1:" + "b" * 40
    first = await manager.acquire(entity_id, "railway/worker-a", ttl_seconds=15)
    store.now = first.expires_at + timedelta(seconds=1)

    second = await manager.acquire(entity_id, "railway/worker-b", ttl_seconds=120)
    assert second.generation == first.generation + 1
    assert second.holder == "railway/worker-b"
    with pytest.raises(ContainerError, match="lost or superseded"):
        await manager.renew(first)
