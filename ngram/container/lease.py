"""Canonical writer leases for preventing split-brain entity activation."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from ngram.container.format import ContainerError
from ngram.container.postgres_snapshot import ConnectFactory, _default_connect

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS ngram_entity_leases (
    entity_id TEXT PRIMARY KEY,
    holder TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    generation BIGINT NOT NULL,
    acquired_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL
)
"""


@dataclass(frozen=True)
class CanonicalLease:
    entity_id: str
    holder: str
    generation: int
    acquired_at: datetime
    expires_at: datetime
    token: str

    def public_record(self, *, state: str = "active") -> dict[str, Any]:
        return {
            "type": "postgres-canonical-writer-v1",
            "state": state,
            "entity_id": self.entity_id,
            "holder": self.holder,
            "generation": self.generation,
            "acquired_at": self.acquired_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "token_included": False,
        }


def _validate_identity(entity_id: str, holder: str, ttl_seconds: int) -> None:
    if not entity_id.startswith("ng1:") or len(entity_id) < 8:
        raise ContainerError("canonical lease requires a valid ngram entity id")
    if not holder.strip() or len(holder) > 200:
        raise ContainerError("canonical lease requires a bounded holder name")
    if ttl_seconds < 15 or ttl_seconds > 86400:
        raise ContainerError("canonical lease TTL must be between 15 and 86400 seconds")


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _as_utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime.fromisoformat(str(value)).astimezone(UTC)


class PostgresLeaseManager:
    """Acquire, renew, release, and inspect one canonical writer lease."""

    def __init__(self, dsn: str, *, connect: ConnectFactory | None = None) -> None:
        if not dsn.strip():
            raise ContainerError("canonical lease requires a database URL")
        self.dsn = dsn
        self.connect = connect or _default_connect

    async def acquire(
        self,
        entity_id: str,
        holder: str,
        *,
        ttl_seconds: int = 120,
    ) -> CanonicalLease:
        _validate_identity(entity_id, holder, ttl_seconds)
        conn = await self.connect(self.dsn)
        try:
            await conn.execute(_CREATE_SQL)
            async with conn.transaction(isolation="serializable"):
                now = _as_utc(await conn.fetchval("SELECT CURRENT_TIMESTAMP"))
                row = await conn.fetchrow(
                    "SELECT * FROM ngram_entity_leases WHERE entity_id = $1 FOR UPDATE",
                    entity_id,
                )
                generation = 1
                acquired_at = now
                if row:
                    existing = dict(row)
                    expires_at = _as_utc(existing["expires_at"])
                    existing_holder = str(existing["holder"])
                    if expires_at > now and existing_holder != holder:
                        raise ContainerError(
                            f"entity already has an active canonical writer: {existing_holder}"
                        )
                    generation = int(existing["generation"])
                    if existing_holder != holder or expires_at <= now:
                        generation += 1
                    else:
                        acquired_at = _as_utc(existing["acquired_at"])
                token = secrets.token_urlsafe(32)
                expires_at = now + timedelta(seconds=ttl_seconds)
                await conn.execute(
                    """
                    INSERT INTO ngram_entity_leases
                        (entity_id, holder, token_hash, generation, acquired_at, expires_at)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    ON CONFLICT (entity_id) DO UPDATE SET
                        holder = EXCLUDED.holder,
                        token_hash = EXCLUDED.token_hash,
                        generation = EXCLUDED.generation,
                        acquired_at = EXCLUDED.acquired_at,
                        expires_at = EXCLUDED.expires_at
                    """,
                    entity_id,
                    holder,
                    _token_hash(token),
                    generation,
                    acquired_at,
                    expires_at,
                )
                return CanonicalLease(
                    entity_id=entity_id,
                    holder=holder,
                    generation=generation,
                    acquired_at=acquired_at,
                    expires_at=expires_at,
                    token=token,
                )
        finally:
            await conn.close()

    async def renew(self, lease: CanonicalLease, *, ttl_seconds: int = 120) -> CanonicalLease:
        _validate_identity(lease.entity_id, lease.holder, ttl_seconds)
        conn = await self.connect(self.dsn)
        try:
            async with conn.transaction(isolation="serializable"):
                now = _as_utc(await conn.fetchval("SELECT CURRENT_TIMESTAMP"))
                row = await conn.fetchrow(
                    "SELECT * FROM ngram_entity_leases WHERE entity_id = $1 FOR UPDATE",
                    lease.entity_id,
                )
                if not row:
                    raise ContainerError("canonical lease no longer exists")
                current = dict(row)
                valid = (
                    str(current["holder"]) == lease.holder
                    and int(current["generation"]) == lease.generation
                    and hmac.compare_digest(str(current["token_hash"]), _token_hash(lease.token))
                    and _as_utc(current["expires_at"]) > now
                )
                if not valid:
                    raise ContainerError("canonical lease was lost or superseded")
                expires_at = now + timedelta(seconds=ttl_seconds)
                await conn.execute(
                    "UPDATE ngram_entity_leases SET expires_at = $2 WHERE entity_id = $1",
                    lease.entity_id,
                    expires_at,
                )
                return CanonicalLease(
                    entity_id=lease.entity_id,
                    holder=lease.holder,
                    generation=lease.generation,
                    acquired_at=lease.acquired_at,
                    expires_at=expires_at,
                    token=lease.token,
                )
        finally:
            await conn.close()

    async def release(self, lease: CanonicalLease) -> dict[str, Any]:
        conn = await self.connect(self.dsn)
        try:
            async with conn.transaction(isolation="serializable"):
                row = await conn.fetchrow(
                    "SELECT * FROM ngram_entity_leases WHERE entity_id = $1 FOR UPDATE",
                    lease.entity_id,
                )
                if not row:
                    raise ContainerError("canonical lease no longer exists")
                current = dict(row)
                valid = (
                    str(current["holder"]) == lease.holder
                    and int(current["generation"]) == lease.generation
                    and hmac.compare_digest(str(current["token_hash"]), _token_hash(lease.token))
                )
                if not valid:
                    raise ContainerError("canonical lease was lost or superseded")
                await conn.execute(
                    "DELETE FROM ngram_entity_leases WHERE entity_id = $1",
                    lease.entity_id,
                )
                return lease.public_record(state="released")
        finally:
            await conn.close()

    async def inspect(self, entity_id: str) -> dict[str, Any] | None:
        conn = await self.connect(self.dsn)
        try:
            now = _as_utc(await conn.fetchval("SELECT CURRENT_TIMESTAMP"))
            row = await conn.fetchrow(
                "SELECT entity_id, holder, generation, acquired_at, expires_at "
                "FROM ngram_entity_leases WHERE entity_id = $1",
                entity_id,
            )
            if not row:
                return None
            value = dict(row)
            expires_at = _as_utc(value["expires_at"])
            return {
                "type": "postgres-canonical-writer-v1",
                "state": "active" if expires_at > now else "expired",
                "entity_id": str(value["entity_id"]),
                "holder": str(value["holder"]),
                "generation": int(value["generation"]),
                "acquired_at": _as_utc(value["acquired_at"]).isoformat(),
                "expires_at": expires_at.isoformat(),
                "token_included": False,
            }
        finally:
            await conn.close()
