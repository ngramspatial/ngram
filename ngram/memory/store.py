"""SQLite storage + numpy cosine similarity for embeddings."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Optional

import aiosqlite
import numpy as np

from ngram.memory import automations_repo
from ngram.models import EmotionCategory
from ngram.presence.automations.models import Automation

SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
    id TEXT PRIMARY KEY,
    timestamp REAL,
    summary TEXT,
    participants TEXT,
    emotional_imprint TEXT,
    emotional_intensity REAL,
    significance REAL,
    tags TEXT,
    raw_context TEXT,
    self_reflection TEXT,
    embedding BLOB
);

CREATE TABLE IF NOT EXISTS relationships (
    person_id TEXT PRIMARY KEY,
    name TEXT,
    first_met REAL,
    last_interaction REAL,
    interaction_count INTEGER,
    familiarity REAL,
    warmth REAL,
    trust REAL,
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
    last_interaction REAL,
    last_reflection REAL,
    interaction_count INTEGER,
    significant_moments TEXT,
    meta TEXT,
    created_at REAL,
    updated_at REAL
);

CREATE TABLE IF NOT EXISTS beliefs (
    id TEXT PRIMARY KEY,
    category TEXT,
    content TEXT,
    confidence REAL,
    source TEXT,
    formed_at REAL,
    last_reinforced REAL,
    times_reinforced INTEGER,
    embedding BLOB
);

CREATE TABLE IF NOT EXISTS imprints (
    id TEXT PRIMARY KEY,
    episode_id TEXT,
    emotion TEXT,
    intensity REAL,
    trigger TEXT,
    embedding BLOB
);

CREATE TABLE IF NOT EXISTS narrative (
    id TEXT PRIMARY KEY,
    timestamp REAL,
    self_story TEXT,
    trait_snapshot TEXT
);

CREATE TABLE IF NOT EXISTS inner_voice (
    id TEXT PRIMARY KEY,
    timestamp REAL,
    summary TEXT,
    emotional_context TEXT,
    embedding BLOB
);

CREATE TABLE IF NOT EXISTS entity_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evolution_log (
    id TEXT PRIMARY KEY,
    timestamp REAL,
    changes_json TEXT,
    reasoning TEXT
);

CREATE TABLE IF NOT EXISTS reminders (
    id TEXT PRIMARY KEY,
    message TEXT NOT NULL,
    due_at REAL NOT NULL,
    target_person TEXT,
    platform TEXT,
    channel TEXT,
    person_id TEXT,
    status TEXT NOT NULL,
    created_at REAL NOT NULL,
    fired_at REAL
);

CREATE TABLE IF NOT EXISTS automations (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    schedule_natural TEXT NOT NULL,
    cron_expression TEXT NOT NULL,
    origin TEXT NOT NULL DEFAULT 'user',
    created_by TEXT NOT NULL,
    created_at REAL NOT NULL,
    deliver_to TEXT,
    deliver_platform TEXT,
    tools_hint TEXT DEFAULT '[]',
    enabled INTEGER DEFAULT 1,
    last_run REAL,
    last_result_summary TEXT,
    next_run REAL,
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
    started_at REAL NOT NULL,
    completed_at REAL,
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
    tick_timestamp REAL NOT NULL,
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
    timestamp REAL,
    route TEXT,
    faculty TEXT,
    tools_used TEXT,
    tool_success_rate REAL,
    user_signal TEXT,
    outcome_score REAL,
    input_hash TEXT,
    embedding BLOB
);
"""


def _pack_embedding(vec: list[float]) -> bytes:
    arr = np.array(vec, dtype=np.float32)
    return arr.tobytes()


def _unpack_embedding(blob: bytes | None) -> Optional[list[float]]:
    if not blob:
        return None
    arr = np.frombuffer(blob, dtype=np.float32)
    return arr.tolist()


def cosine_sim(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    va = np.array(a, dtype=np.float64)
    vb = np.array(b, dtype=np.float64)
    na = np.linalg.norm(va)
    nb = np.linalg.norm(vb)
    if na < 1e-9 or nb < 1e-9:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))


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


class MemoryStore:
    dialect: str = "sqlite"

    def __init__(self, db_path: str) -> None:
        self.db_path = str(Path(db_path).expanduser())
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialized = False

    async def wipe_experience_tables(self, conn: aiosqlite.Connection) -> None:
        """Delete all lived-memory rows; schema remains. Order safe without FK enforcement."""
        for table in _EXPERIENCE_TABLES:
            await conn.execute(f"DELETE FROM {table}")
        await conn.commit()

    async def connect(self) -> aiosqlite.Connection:
        conn = await aiosqlite.connect(self.db_path)
        if not self._initialized:
            await conn.execute("PRAGMA journal_mode=DELETE")
            await conn.executescript(SCHEMA)
            await conn.commit()
            self._initialized = True
        return conn

    @asynccontextmanager
    async def session(self) -> AsyncIterator[aiosqlite.Connection]:
        """Open DB and always close — including on asyncio cancellation (Ctrl+C / task cancel)."""
        conn = await self.connect()
        try:
            yield conn
        finally:
            try:
                await conn.close()
            except Exception:
                pass

    async def aclose(self) -> None:
        """Best-effort WAL checkpoint on shutdown so Ctrl+C leaves a clean file on disk."""
        p = Path(self.db_path)
        if not p.is_file():
            return
        try:
            conn = await self.connect()
            try:
                await conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                await conn.commit()
            finally:
                await conn.close()
        except Exception:
            pass

    async def search_episodes_by_embedding(
        self,
        conn: aiosqlite.Connection,
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
            emb = _unpack_embedding(emb_blob)
            if emb:
                scored.append((eid, cosine_sim(query_emb, emb)))
        scored.sort(key=lambda x: -x[1])
        return scored[:limit]

    async def search_episodes_by_embedding_biased(
        self,
        conn: aiosqlite.Connection,
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
            ph = ",".join("?" * len(eids))
            icur = await conn.execute(
                f"SELECT episode_id, emotion, intensity FROM imprints WHERE episode_id IN ({ph})",
                eids,
            )
            for ep_id, emo, intens in await icur.fetchall():
                imprint_map.setdefault(ep_id, []).append((str(emo), float(intens)))

        scored: list[tuple[str, float]] = []
        for eid, ts, sig, emb_blob in rows:
            if sig is not None and float(sig) < min_significance:
                continue
            emb = _unpack_embedding(emb_blob)
            if not emb:
                continue
            base = cosine_sim(query_emb, emb)
            age = max(0.0, now - float(ts))
            mem_decay = (
                0.5 ** (age / imprint_half_life) if imprint_half_life > 0 else 1.0
            )
            imprint_bonus = 0.0
            for emo_str, intens in imprint_map.get(eid, []):
                aff = affinity_multiplier_for_recall(mood, emo_str)
                imprint_bonus += float(intens) * (aff - 1.0) * mem_decay
            imprint_bonus = min(1.5, imprint_bonus)
            adj = base * (1.0 + imprint_weight * imprint_bonus)
            scored.append((eid, adj))
        scored.sort(key=lambda x: -x[1])
        return scored[:limit]

    async def count_episodes(self, conn: aiosqlite.Connection) -> int:
        cur = await conn.execute("SELECT COUNT(*) FROM episodes")
        row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def count_relationships(self, conn: aiosqlite.Connection) -> int:
        cur = await conn.execute("SELECT COUNT(*) FROM relationships")
        row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def min_episode_timestamp(self, conn: aiosqlite.Connection) -> float | None:
        cur = await conn.execute("SELECT MIN(timestamp) FROM episodes")
        row = await cur.fetchone()
        if row and row[0] is not None:
            return float(row[0])
        return None

    async def save_automation(self, auto: Automation) -> None:
        async with self.session() as conn:
            await automations_repo.save_automation(conn, auto)

    async def get_automation(self, auto_id: str) -> Optional[Automation]:
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
