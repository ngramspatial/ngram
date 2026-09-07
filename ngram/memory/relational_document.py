"""Per-person relationship documents — prose-first portraits with derived scalar summaries.

Legacy scalar tracking remains in ``relationships`` (see ``relational.py``); this module
stores the living document and keeps derived scores in sync for thresholds and blurbs.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import structlog

from ngram.models import Relationship

if TYPE_CHECKING:
    from ngram.config import EntityConfig, RelationalDocumentSettings

log = structlog.get_logger("ngram.memory.relational_document")


def _default_derived() -> dict[str, float]:
    return {
        "familiarity": 0.2,
        "warmth": 0.5,
        "trust": 0.35,
        "tension": 0.1,
        "investment": 0.4,
    }


@dataclass
class RelationalDocument:
    person_id: str
    person_name: str
    document: str
    derived_scores: dict[str, float] = field(default_factory=_default_derived)
    last_interaction: float = 0.0
    last_reflection: float = 0.0
    interaction_count: int = 0
    significant_moments: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0


def compute_health_snapshot(doc: RelationalDocument) -> dict[str, Any]:
    """Compute relationship stage, health score, trajectory, and cadence.

    Returns a dict with:
        stage: str           — "forming" | "developing" | "established" | "deep"
        stage_emoji: str     — 🌱 | 🌿 | 🌳 | 🏛️
        health: float        — 0..1 composite score
        health_label: str    — "fragile" | "uncertain" | "steady" | "strong" | "thriving"
        trajectory: str      — "accelerating" | "steady" | "fading" | "dormant" | "new"
        trajectory_emoji: str — 📈 | ➡️ | 📉 | 💤 | 🆕
        cadence_label: str   — human-readable interaction cadence
        days_known: float    — days since first interaction
        silence_hours: float — hours since last interaction
    """
    now = time.time()
    ds = doc.derived_scores
    n = doc.interaction_count

    # Stage — based on interaction count
    if n < 10:
        stage, stage_emoji = "forming", "🌱"
    elif n < 50:
        stage, stage_emoji = "developing", "🌿"
    elif n < 150:
        stage, stage_emoji = "established", "🌳"
    else:
        stage, stage_emoji = "deep", "🏛️"

    # Health — composite of warmth + trust + investment - tension
    warmth = float(ds.get("warmth", 0.5))
    trust = float(ds.get("trust", 0.35))
    investment = float(ds.get("investment", 0.4))
    tension_val = float(ds.get("tension", 0.1))
    health = max(0.0, min(1.0, (warmth + trust + investment - tension_val * 0.5) / 2.5))

    if health < 0.25:
        health_label = "fragile"
    elif health < 0.40:
        health_label = "uncertain"
    elif health < 0.60:
        health_label = "steady"
    elif health < 0.80:
        health_label = "strong"
    else:
        health_label = "thriving"

    # Trajectory — based on interaction cadence and recency
    days_known = max(0.0, (now - doc.created_at) / 86400.0) if doc.created_at > 0 else 0.0
    silence_hours = max(0.0, (now - doc.last_interaction) / 3600.0) if doc.last_interaction > 0 else 0.0

    if n < 3:
        trajectory, trajectory_emoji = "new", "🆕"
    elif silence_hours > 168:  # 7 days
        trajectory, trajectory_emoji = "dormant", "💤"
    elif days_known > 0:
        rate = n / max(1.0, days_known)  # interactions per day
        if rate > 5.0:
            trajectory, trajectory_emoji = "accelerating", "📈"
        elif rate > 1.0:
            trajectory, trajectory_emoji = "steady", "➡️"
        else:
            trajectory, trajectory_emoji = "fading", "📉"
    else:
        trajectory, trajectory_emoji = "new", "🆕"

    # Cadence label
    if silence_hours < 1:
        cadence_label = "active now"
    elif silence_hours < 24:
        cadence_label = "daily contact"
    elif silence_hours < 72:
        cadence_label = "regular"
    elif silence_hours < 168:
        cadence_label = "occasional"
    elif silence_hours < 720:
        cadence_label = "infrequent"
    else:
        cadence_label = "long gap"

    return {
        "stage": stage,
        "stage_emoji": stage_emoji,
        "health": health,
        "health_label": health_label,
        "trajectory": trajectory,
        "trajectory_emoji": trajectory_emoji,
        "cadence_label": cadence_label,
        "days_known": days_known,
        "silence_hours": silence_hours,
    }

def warmth_derived_to_relationship(w: float) -> float:
    """Map 0..1 derived warmth to Relationship's -1..1 scale."""
    w = max(0.0, min(1.0, float(w)))
    return max(-1.0, min(1.0, w * 2.0 - 1.0))


def trim_document_for_context(text: str, *, max_chars: int) -> str:
    """Prefer tail when trimming; keeps the most recent prose."""
    t = (text or "").strip()
    if len(t) <= max_chars:
        return t
    return "…\n\n" + t[-max_chars:]


def tail_sentences(text: str, n: int = 3) -> str:
    """Last ``n`` sentences for lightweight GEN / group summaries."""
    t = (text or "").strip()
    if not t:
        return ""
    parts = re.split(r"(?<=[.!?])\s+", t)
    parts = [p.strip() for p in parts if p.strip()]
    if not parts:
        return t[:400]
    return " ".join(parts[-n:])


def bootstrap_document_from_relationship(rel: Relationship) -> str:
    """One-time prose seed from legacy scalar row."""
    warm_word = "warm"
    if rel.warmth < -0.15:
        warm_word = "cool"
    elif rel.warmth > 0.35:
        warm_word = "warm"
    else:
        warm_word = "neutral"
    trust_word = "moderate"
    if rel.trust < 0.35:
        trust_word = "low"
    elif rel.trust > 0.65:
        trust_word = "high"
    topics = rel.topics_shared or []
    topic_bit = ""
    if topics:
        topic_bit = f" Shared threads I've noticed: {', '.join(str(x) for x in topics[:8])}."
    notes_bit = ""
    if rel.notes:
        notes_bit = f" Notes I've jotted: {'; '.join(str(x) for x in rel.notes[-5:])}."
    return (
        f"## {rel.name}\n\nI've had {rel.interaction_count} interactions with {rel.name}. "
        f"My general sense is {warm_word} with {trust_word} trust (familiarity around "
        f"{rel.familiarity:.2f}).{topic_bit}{notes_bit}\n\n"
        f"(Migrated from earlier scalar relationship data — I'll keep refining this in prose.)"
    )


class RelationalDocumentMemory:
    """CRUD for ``relational_documents`` + filesystem mirror + sync to ``relationships``."""

    def __init__(
        self,
        *,
        entity_config: EntityConfig,
        settings: RelationalDocumentSettings | None = None,
    ) -> None:
        self._cfg = entity_config
        self._settings = settings

    def _rs(self) -> RelationalDocumentSettings:
        from ngram.config import RelationalDocumentSettings

        s = self._settings
        if s is not None:
            return s
        m = getattr(self._cfg.harness.memory, "relational", None)
        if isinstance(m, RelationalDocumentSettings):
            return m
        return RelationalDocumentSettings()

    def _row_to_doc(self, row: tuple[Any, ...]) -> RelationalDocument:
        raw_scores = row[3] or "{}"
        try:
            derived = json.loads(raw_scores) if isinstance(raw_scores, str) else {}
        except json.JSONDecodeError:
            derived = {}
        if not isinstance(derived, dict):
            derived = {}
        merged = {**_default_derived(), **{k: float(v) for k, v in derived.items() if isinstance(v, (int, float))}}
        try:
            moments = json.loads(row[7] or "[]")
        except json.JSONDecodeError:
            moments = []
        if not isinstance(moments, list):
            moments = []
        try:
            meta = json.loads(row[8] or "{}")
        except json.JSONDecodeError:
            meta = {}
        if not isinstance(meta, dict):
            meta = {}
        return RelationalDocument(
            person_id=str(row[0]),
            person_name=str(row[1] or ""),
            document=str(row[2] or ""),
            derived_scores=merged,
            last_interaction=float(row[4] or 0),
            last_reflection=float(row[5] or 0),
            interaction_count=int(row[6] or 0),
            significant_moments=[str(x) for x in moments][-24:],
            meta=meta,
            created_at=float(row[9] or 0),
            updated_at=float(row[10] or 0),
        )

    async def get(self, conn: Any, person_id: str) -> Optional[RelationalDocument]:
        cur = await conn.execute(
            "SELECT * FROM relational_documents WHERE person_id = ?",
            (person_id,),
        )
        row = await cur.fetchone()
        if not row:
            return None
        return self._row_to_doc(row)

    async def upsert(
        self,
        conn: Any,
        doc: RelationalDocument,
    ) -> None:
        now = time.time()
        if doc.created_at <= 0:
            doc.created_at = now
        doc.updated_at = now
        await conn.execute(
            """INSERT OR REPLACE INTO relational_documents
            (person_id, person_name, document, derived_scores, last_interaction, last_reflection,
             interaction_count, significant_moments, meta, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                doc.person_id,
                doc.person_name,
                doc.document,
                json.dumps(doc.derived_scores),
                doc.last_interaction,
                doc.last_reflection,
                doc.interaction_count,
                json.dumps(doc.significant_moments),
                json.dumps(doc.meta),
                doc.created_at,
                doc.updated_at,
            ),
        )
        await conn.commit()
        if self._rs().flush_to_disk:
            try:
                self.flush_doc_to_disk(doc)
            except OSError as e:
                log.debug("relational_doc_flush_failed", error=str(e))

    async def ensure_from_legacy_row(
        self,
        conn: Any,
        rel: Relationship | None,
    ) -> Optional[RelationalDocument]:
        """If a legacy ``relationships`` row exists but no document yet, bootstrap one."""
        if rel is None:
            return None
        existing = await self.get(conn, rel.person_id)
        if existing and (existing.document or "").strip():
            return existing
        body = bootstrap_document_from_relationship(rel)
        doc = RelationalDocument(
            person_id=rel.person_id,
            person_name=rel.name,
            document=body,
            derived_scores={
                "familiarity": max(0.0, min(1.0, float(rel.familiarity))),
                "warmth": max(0.0, min(1.0, (float(rel.warmth) + 1.0) / 2.0)),
                "trust": max(0.0, min(1.0, float(rel.trust))),
                "tension": 0.15,
                "investment": min(1.0, 0.25 + float(rel.familiarity) * 0.5),
            },
            last_interaction=float(rel.last_interaction),
            last_reflection=0.0,
            interaction_count=int(rel.interaction_count),
            significant_moments=[],
            meta={"bootstrapped_from_legacy": True},
            created_at=time.time(),
            updated_at=time.time(),
        )
        await self.upsert(conn, doc)
        return doc

    def format_for_prompt(
        self,
        doc: RelationalDocument,
        *,
        max_chars: int,
        compact: bool = False,
    ) -> str:
        """Preamble text: full trimmed document plus a one-line score hint."""
        body = trim_document_for_context(doc.document, max_chars=max_chars)
        ds = doc.derived_scores
        if compact:
            return (
                f"{doc.person_name}: {tail_sentences(body, n=2)} "
                f"(warmth≈{ds.get('warmth', 0):.2f}, trust≈{ds.get('trust', 0):.2f})"
            )
        line = (
            f"[derived: familiarity {ds.get('familiarity', 0):.2f}, "
            f"warmth {ds.get('warmth', 0):.2f}, trust {ds.get('trust', 0):.2f}, "
            f"tension {ds.get('tension', 0):.2f}, investment {ds.get('investment', 0):.2f}]"
        )
        return f"{line}\n\n{body}"

    def flush_doc_to_disk(self, doc: RelationalDocument) -> None:
        base = Path(self._cfg.relationships_dir())
        base.mkdir(parents=True, exist_ok=True)
        safe_id = re.sub(r"[^a-zA-Z0-9._-]+", "_", doc.person_id)[:120]
        path = base / f"{safe_id}.md"
        path.write_text(doc.document, encoding="utf-8")

    async def add_pending_distillation(
        self,
        conn: Any,
        person_id: str,
        note: str,
    ) -> None:
        note = (note or "").strip()
        if not note:
            return
        doc = await self.get(conn, person_id)
        if doc is None:
            return
        pending = doc.meta.get("pending_distillation") or []
        if not isinstance(pending, list):
            pending = []
        pending.append(note)
        doc.meta["pending_distillation"] = pending[-12:]
        await self.upsert(conn, doc)

    async def clear_pending_distillation(self, conn: Any, person_id: str) -> list[str]:
        doc = await self.get(conn, person_id)
        if doc is None:
            return []
        pending = doc.meta.get("pending_distillation") or []
        if not isinstance(pending, list):
            pending = []
        doc.meta["pending_distillation"] = []
        await self.upsert(conn, doc)
        return [str(x) for x in pending]

    async def sync_derived_to_relationship_row(
        self,
        conn: Any,
        doc: RelationalDocument,
    ) -> None:
        """Push derived familiarity/warmth/trust into ``relationships`` for legacy readers."""
        ds = doc.derived_scores
        fam = max(0.0, min(1.0, float(ds.get("familiarity", 0))))
        warmth = warmth_derived_to_relationship(float(ds.get("warmth", 0.5)))
        trust = max(0.0, min(1.0, float(ds.get("trust", 0))))
        await conn.execute(
            """UPDATE relationships SET name = ?, last_interaction = ?, familiarity = ?, warmth = ?, trust = ?
            WHERE person_id = ?""",
            (
                doc.person_name,
                doc.last_interaction,
                fam,
                warmth,
                trust,
                doc.person_id,
            ),
        )
        await conn.commit()

    async def list_recent_for_gen(
        self,
        conn: Any,
        *,
        since_ts: float,
        limit: int = 6,
    ) -> list[RelationalDocument]:
        cur = await conn.execute(
            "SELECT * FROM relational_documents WHERE last_interaction >= ? "
            "ORDER BY last_interaction DESC LIMIT ?",
            (since_ts, limit),
        )
        rows = await cur.fetchall()
        return [self._row_to_doc(r) for r in rows]

    def gen_context_tail(self, docs: list[RelationalDocument]) -> str:
        rs = self._rs()
        n = max(1, int(rs.gen_tail_sentences))
        lines: list[str] = []
        for d in docs:
            tail = tail_sentences(d.document, n=n)
            if tail:
                lines.append(f"- {d.person_name}: {tail}")
        return "\n".join(lines)
