"""PostgreSQL relational store (asyncpg) with SQLite-compatible conn shim."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta
from typing import Any

from ngram.container.format import ContainerError
from ngram.container.lease import CanonicalLease, PostgresLeaseManager
from ngram.memory import automations_repo
from ngram.memory.pg_translate import translate_sql
from ngram.memory.store import _unpack_embedding, cosine_sim
from ngram.models import EmotionCategory
from ngram.presence.automations.models import Automation

try:
    import asyncpg
except ImportError as e:  # pragma: no cover - optional dependency
    asyncpg = None  # type: ignore[misc, assignment]
    _ASYNCPG_ERR = e
else:
    _ASYNCPG_ERR = None

SCHEMA_PG = """
CREATE TABLE IF NOT EXISTS episodes (
    id TEXT PRIMARY KEY,
    timestamp DOUBLE PRECISION,
    summary TEXT,
    participants TEXT,
    emotional_imprint TEXT,
    emotional_intensity DOUBLE PRECISION,
    significance DOUBLE PRECISION,
    tags TEXT,
    raw_context TEXT,
    self_reflection TEXT,
    embedding BYTEA
);

CREATE TABLE IF NOT EXISTS relationships (
    person_id TEXT PRIMARY KEY,
    name TEXT,
    first_met DOUBLE PRECISION,
    last_interaction DOUBLE PRECISION,
    interaction_count INTEGER,
    familiarity DOUBLE PRECISION,
    warmth DOUBLE PRECISION,
    trust DOUBLE PRECISION,
    dynamic TEXT,
    notes TEXT,
    topics_shared TEXT,
    unresolved TEXT
);

CREATE TABLE IF NOT EXISTS relational_documents (
    person_id TEXT PRIMARY KEY,
    person_name TEXT,
    document TEXT,
    derived_scores TEXT,
    last_interaction DOUBLE PRECISION,
    last_reflection DOUBLE PRECISION,
    interaction_count INTEGER,
    significant_moments TEXT,
    meta TEXT,
    created_at DOUBLE PRECISION,
    updated_at DOUBLE PRECISION
);

CREATE TABLE IF NOT EXISTS beliefs (
    id TEXT PRIMARY KEY,
    category TEXT,
    content TEXT,
    confidence DOUBLE PRECISION,
    source TEXT,
    formed_at DOUBLE PRECISION,
    last_reinforced DOUBLE PRECISION,
    times_reinforced INTEGER,
    embedding BYTEA
);

CREATE TABLE IF NOT EXISTS imprints (
    id TEXT PRIMARY KEY,
    episode_id TEXT,
    emotion TEXT,
    intensity DOUBLE PRECISION,
    trigger TEXT,
    embedding BYTEA
);

CREATE TABLE IF NOT EXISTS narrative (
    id TEXT PRIMARY KEY,
    timestamp DOUBLE PRECISION,
    self_story TEXT,
    trait_snapshot TEXT
);

CREATE TABLE IF NOT EXISTS inner_voice (
    id TEXT PRIMARY KEY,
    timestamp DOUBLE PRECISION,
    summary TEXT,
    emotional_context TEXT,
    embedding BYTEA
);

CREATE TABLE IF NOT EXISTS entity_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evolution_log (
    id TEXT PRIMARY KEY,
    timestamp DOUBLE PRECISION,
    changes_json TEXT,
    reasoning TEXT
);

CREATE TABLE IF NOT EXISTS reminders (
    id TEXT PRIMARY KEY,
    message TEXT NOT NULL,
    due_at DOUBLE PRECISION NOT NULL,
    target_person TEXT,
    platform TEXT,
    channel TEXT,
    person_id TEXT,
    status TEXT NOT NULL,
    created_at DOUBLE PRECISION NOT NULL,
    fired_at DOUBLE PRECISION
);

CREATE TABLE IF NOT EXISTS automations (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    schedule_natural TEXT NOT NULL,
    cron_expression TEXT NOT NULL,
    origin TEXT NOT NULL DEFAULT 'user',
    created_by TEXT NOT NULL,
    created_at DOUBLE PRECISION NOT NULL,
    deliver_to TEXT,
    deliver_platform TEXT,
    tools_hint TEXT DEFAULT '[]',
    enabled INTEGER DEFAULT 1,
    last_run DOUBLE PRECISION,
    last_result_summary TEXT,
    next_run DOUBLE PRECISION,
    run_count INTEGER DEFAULT 0,
    context TEXT DEFAULT '',
    priority TEXT DEFAULT 'normal',
    consecutive_failures INTEGER DEFAULT 0,
    max_failures INTEGER DEFAULT 5,
    self_destruct_condition TEXT,
    tags TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS automation_runs (
    id TEXT PRIMARY KEY,
    automation_id TEXT NOT NULL,
    started_at DOUBLE PRECISION NOT NULL,
    completed_at DOUBLE PRECISION,
    success INTEGER,
    result_summary TEXT,
    emotional_state_before TEXT,
    emotional_state_after TEXT,
    tools_used TEXT DEFAULT '[]',
    delivered_to TEXT,
    self_modified INTEGER DEFAULT 0,
    modification_description TEXT
);

CREATE TABLE IF NOT EXISTS seed_log (
    id TEXT PRIMARY KEY,
    entity_name TEXT NOT NULL,
    tick_timestamp DOUBLE PRECISION NOT NULL,
    source_type TEXT NOT NULL,
    source_detail TEXT,
    seed_text TEXT NOT NULL,
    consumed INTEGER DEFAULT 0,
    fragment_produced TEXT,
    fragment_tags TEXT,
    trace_id TEXT
);

CREATE TABLE IF NOT EXISTS turn_outcomes (
    id TEXT PRIMARY KEY,
    timestamp DOUBLE PRECISION,
    route TEXT,
    faculty TEXT,
    tools_used TEXT,
    tool_success_rate DOUBLE PRECISION,
    user_signal TEXT,
    outcome_score DOUBLE PRECISION,
    input_hash TEXT,
    embedding BYTEA
);
"""

_EXPERIENCE_TABLES = (
    "imprints",
    "episodes",
    "relationships",
    "relational_documents",
    "beliefs",
    "narrative",
    "inner_voice",
    "entity_state",
    "evolution_log",
    "reminders",
    "automation_runs",
    "automations",
    "seed_log",
    "turn_outcomes",
)


class PgCursor:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    async def fetchall(self) -> list[tuple[Any, ...]]:
        return [tuple(r) for r in self._rows]

    async def fetchone(self) -> tuple[Any, ...] | None:
        if not self._rows:
            return None
        return tuple(self._rows[0])


class PgShimConnection:
    def __init__(self, raw: Any) -> None:
        self._c = raw

    async def execute(self, sql: str, params: tuple[Any, ...] | list[Any] = ()) -> PgCursor:
        sql_t = translate_sql(sql)
        p = tuple(params)
        head = sql_t.lstrip().split(None, 1)[0].upper() if sql_t.strip() else ""
        if head in ("SELECT", "WITH"):
            rows = await self._c.fetch(sql_t, *p)
            return PgCursor(list(rows))
        await self._c.execute(sql_t, *p)
        return PgCursor([])

    async def commit(self) -> None:
        return None


class PostgresMemoryStore:
    dialect: str = "postgres"

    def __init__(
        self,
        database_url: str,
        *,
        display_name: str | None = None,
        canonical_lease_required: bool = False,
        entity_id: str = "",
        lease_holder: str = "",
        lease_manager: PostgresLeaseManager | None = None,
        lease_ttl_seconds: int = 300,
    ) -> None:
        if asyncpg is None:
            raise RuntimeError(
                "PostgreSQL backend requires asyncpg. Install with: pip install ngram[railway]"
            ) from _ASYNCPG_ERR
        self._dsn = database_url
        self.db_path = display_name or "[postgresql]"
        self._pool: asyncpg.Pool | None = None
        self._canonical_lease_required = bool(canonical_lease_required)
        self._entity_id = entity_id.strip()
        self._lease_holder = lease_holder.strip()
        self._lease_ttl_seconds = max(30, int(lease_ttl_seconds))
        self._lease_manager = lease_manager or PostgresLeaseManager(database_url)
        self._canonical_lease: CanonicalLease | None = None
        if self._canonical_lease_required and (not self._entity_id or not self._lease_holder):
            raise ContainerError(
                "canonical Postgres writer requires NGRAM_ENTITY_ID and a lease holder"
            )

    async def _ensure_canonical_lease(self) -> None:
        if not self._canonical_lease_required:
            return
        lease = self._canonical_lease
        if lease is None:
            self._canonical_lease = await self._lease_manager.acquire(
                self._entity_id,
                self._lease_holder,
                ttl_seconds=self._lease_ttl_seconds,
            )
            return
        renew_at = lease.expires_at - timedelta(seconds=max(15, self._lease_ttl_seconds // 3))
        if datetime.now(UTC) >= renew_at:
            self._canonical_lease = await self._lease_manager.renew(
                lease,
                ttl_seconds=self._lease_ttl_seconds,
            )

    async def _ensure_pool(self) -> asyncpg.Pool:
        await self._ensure_canonical_lease()
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=10)
            async with self._pool.acquire() as conn:
                await conn.execute(SCHEMA_PG)
        return self._pool

    @asynccontextmanager
    async def session(self) -> AsyncIterator[PgShimConnection]:
        pool = await self._ensure_pool()
        async with pool.acquire() as raw, raw.transaction():
            yield PgShimConnection(raw)

    async def wipe_experience_tables(self, conn: PgShimConnection) -> None:
        for table in _EXPERIENCE_TABLES:
            await conn.execute(f"DELETE FROM {table}")

    async def aclose(self) -> None:
        lease = self._canonical_lease
        self._canonical_lease = None
        if lease is not None:
            with suppress(Exception):
                await self._lease_manager.release(lease)
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def search_episodes_by_embedding(
        self,
        conn: PgShimConnection,
        query_emb: list[float],
        limit: int = 5,
        min_significance: float = 0.0,
    ) -> list[tuple[str, float]]:
        cur = await conn.execute(
            "SELECT id, significance, embedding FROM episodes WHERE embedding IS NOT NULL"
        )
        rows = await cur.fetchall()
        scored: list[tuple[str, float]] = []
        for eid, sig, emb_blob in rows:
            if sig is not None and float(sig) < min_significance:
                continue
            emb = _unpack_embedding(bytes(emb_blob) if emb_blob is not None else None)
            if emb:
                scored.append((eid, cosine_sim(query_emb, emb)))
        scored.sort(key=lambda x: -x[1])
        return scored[:limit]

    async def search_episodes_by_embedding_biased(
        self,
        conn: PgShimConnection,
        query_emb: list[float],
        mood: EmotionCategory,
        now: float,
        *,
        imprint_weight: float,
        imprint_half_life: float,
        limit: int = 10,
        min_significance: float = 0.0,
    ) -> list[tuple[str, float]]:
        from ngram.memory.imprints import affinity_multiplier_for_recall

        cur = await conn.execute(
            "SELECT id, timestamp, significance, embedding FROM episodes WHERE embedding IS NOT NULL"
        )
        rows = await cur.fetchall()
        eids = [r[0] for r in rows]
        imprint_map: dict[str, list[tuple[str, float]]] = {e: [] for e in eids}
        if eids:
            ph = ",".join(f"${i + 1}" for i in range(len(eids)))
            sql = f"SELECT episode_id, emotion, intensity FROM imprints WHERE episode_id IN ({ph})"
            icur = await conn.execute(sql, eids)
            for ep_id, emo, intens in await icur.fetchall():
                imprint_map.setdefault(ep_id, []).append((str(emo), float(intens)))

        scored: list[tuple[str, float]] = []
        for eid, ts, sig, emb_blob in rows:
            if sig is not None and float(sig) < min_significance:
                continue
            emb = _unpack_embedding(bytes(emb_blob) if emb_blob is not None else None)
            if not emb:
                continue
            base = cosine_sim(query_emb, emb)
            age = max(0.0, now - float(ts))
            mem_decay = 0.5 ** (age / imprint_half_life) if imprint_half_life > 0 else 1.0
            imprint_bonus = 0.0
            for emo_str, intens in imprint_map.get(eid, []):
                aff = affinity_multiplier_for_recall(mood, emo_str)
                imprint_bonus += float(intens) * (aff - 1.0) * mem_decay
            imprint_bonus = min(1.5, imprint_bonus)
            adj = base * (1.0 + imprint_weight * imprint_bonus)
            scored.append((eid, adj))
        scored.sort(key=lambda x: -x[1])
        return scored[:limit]

    async def count_episodes(self, conn: PgShimConnection) -> int:
        cur = await conn.execute("SELECT COUNT(*) FROM episodes")
        row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def count_relationships(self, conn: PgShimConnection) -> int:
        cur = await conn.execute("SELECT COUNT(*) FROM relationships")
        row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def min_episode_timestamp(self, conn: PgShimConnection) -> float | None:
        cur = await conn.execute("SELECT MIN(timestamp) FROM episodes")
        row = await cur.fetchone()
        if row and row[0] is not None:
            return float(row[0])
        return None

    async def save_automation(self, auto: Automation) -> None:
        async with self.session() as conn:
            await automations_repo.save_automation(conn, auto)

    async def get_automation(self, auto_id: str) -> Automation | None:
        async with self.session() as conn:
            return await automations_repo.get_automation(conn, auto_id)

    async def list_automations(self, enabled_only: bool = True) -> list[Automation]:
        async with self.session() as conn:
            return await automations_repo.list_automations(conn, enabled_only=enabled_only)

    async def update_automation(self, auto_id: str, **fields: object) -> None:
        async with self.session() as conn:
            await automations_repo.update_automation(conn, auto_id, **fields)

    async def delete_automation(self, auto_id: str) -> None:
        async with self.session() as conn:
            await automations_repo.delete_automation(conn, auto_id)

    async def save_automation_run(self, run: dict) -> None:
        async with self.session() as conn:
            await automations_repo.save_automation_run(conn, run)

    async def get_automation_runs(self, auto_id: str, limit: int = 10) -> list[dict]:
        async with self.session() as conn:
            return await automations_repo.get_automation_runs(conn, auto_id, limit=limit)

    async def count_automations(self) -> int:
        async with self.session() as conn:
            return await automations_repo.count_automations(conn)
