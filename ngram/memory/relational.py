"""Per-person relationship models.

Scalar-first tracking (familiarity, warmth, trust) remains in the ``relationships`` table.
When :class:`ngram.memory.relational_document.RelationalDocumentMemory` is enabled,
warmth/trust/familiarity are primarily derived from LLM-maintained documents and synced here
for backward compatibility — see ``rel_sync_derived`` / upsert ``document_mode`` flag.
"""

from __future__ import annotations

import json
import math
import time
from typing import TYPE_CHECKING, Optional

from ngram.storage.protocol import RelationalStore
from ngram.models import Relationship

if TYPE_CHECKING:
    from ngram.config import MemoryHarnessSettings


def _decay_toward_floor(
    value: float,
    floor: float,
    dt_hours: float,
    half_life_hours: float,
) -> float:
    """Exponential approach: excess above floor halves every half_life_hours (soma-style homeostasis)."""
    if dt_hours <= 0 or half_life_hours <= 0:
        return max(floor, min(1.0, float(value)))
    floor = max(0.0, min(1.0, float(floor)))
    v = max(floor, min(1.0, float(value)))
    span = max(0.0, v - floor)
    if span <= 0:
        return floor
    decay = math.exp(-math.log(2.0) * float(dt_hours) / float(half_life_hours))
    return max(floor, min(1.0, floor + span * decay))


class RelationalMemory:
    def __init__(
        self,
        store: RelationalStore,
        memory_settings: Optional["MemoryHarnessSettings"] = None,
    ) -> None:
        self.store = store
        self._mem = memory_settings

    async def get(self, conn, person_id: str) -> Optional[Relationship]:
        cur = await conn.execute("SELECT * FROM relationships WHERE person_id = ?", (person_id,))
        row = await cur.fetchone()
        if not row:
            return None
        return self._row_to_rel(row)

    def _row_to_rel(self, row) -> Relationship:
        return Relationship(
            person_id=row[0],
            name=row[1],
            first_met=float(row[2]),
            last_interaction=float(row[3]),
            interaction_count=int(row[4]),
            familiarity=float(row[5]),
            warmth=float(row[6]),
            trust=float(row[7]),
            dynamic=row[8] or "neutral",
            notes=json.loads(row[9] or "[]"),
            topics_shared=json.loads(row[10] or "[]"),
            unresolved=json.loads(row[11] or "[]"),
        )

    def _familiarity_settings(self) -> tuple[float, float, float, float]:
        m = self._mem
        if m is None:
            return (336.0, 0.08, 0.017, 0.007)
        return (
            max(1.0, float(getattr(m, "familiarity_decay_half_life_hours", 336.0) or 336.0)),
            max(0.0, min(0.5, float(getattr(m, "familiarity_floor", 0.08) or 0.08))),
            max(0.0, min(0.08, float(getattr(m, "familiarity_bump_meaningful", 0.017) or 0.017))),
            max(0.0, min(0.05, float(getattr(m, "familiarity_bump_light", 0.007) or 0.007))),
        )

    async def upsert_interaction(
        self,
        conn,
        person_id: str,
        name: str,
        *,
        warmth_delta: float = 0.0,
        trust_delta: float = 0.0,
        note: Optional[str] = None,
        meaningful: bool = True,
        document_mode: bool = False,
    ) -> Relationship:
        """When ``document_mode`` is True, only bump interaction metadata; scalars stay for reflection sync."""
        now = time.time()
        half_life_h, fam_floor, bump_hi, bump_lo = self._familiarity_settings()
        bump = bump_hi if meaningful else bump_lo
        existing = await self.get(conn, person_id)
        if existing:
            n = existing.interaction_count + 1
            if document_mode:
                notes = list(existing.notes)
                if note:
                    notes.append(note)
                rel = Relationship(
                    person_id=person_id,
                    name=name or existing.name,
                    first_met=existing.first_met,
                    last_interaction=now,
                    interaction_count=n,
                    familiarity=existing.familiarity,
                    warmth=existing.warmth,
                    trust=existing.trust,
                    dynamic=existing.dynamic,
                    notes=notes[-50:],
                    topics_shared=existing.topics_shared,
                    unresolved=existing.unresolved,
                )
            else:
                dt_hours = max(0.0, (now - float(existing.last_interaction)) / 3600.0)
                fam = _decay_toward_floor(
                    existing.familiarity, fam_floor, dt_hours, half_life_h
                )
                fam = min(1.0, fam + bump)
                cap = min(1.0, math.log1p(n) / math.log1p(80))
                fam = min(fam, cap)
                warmth = max(-1.0, min(1.0, existing.warmth + warmth_delta))
                trust = max(0.0, min(1.0, existing.trust + trust_delta))
                notes = list(existing.notes)
                if note:
                    notes.append(note)
                rel = Relationship(
                    person_id=person_id,
                    name=name or existing.name,
                    first_met=existing.first_met,
                    last_interaction=now,
                    interaction_count=n,
                    familiarity=fam,
                    warmth=warmth,
                    trust=trust,
                    dynamic=existing.dynamic,
                    notes=notes[-50:],
                    topics_shared=existing.topics_shared,
                    unresolved=existing.unresolved,
                )
        else:
            rel = Relationship(
                person_id=person_id,
                name=name,
                first_met=now,
                last_interaction=now,
                interaction_count=1,
                familiarity=0.15,
                warmth=0.1 + warmth_delta,
                trust=0.2 + trust_delta,
                dynamic="forming",
                notes=[note] if note else [],
                topics_shared=[],
                unresolved=[],
            )
        await conn.execute(
            """INSERT OR REPLACE INTO relationships
            (person_id, name, first_met, last_interaction, interaction_count, familiarity,
             warmth, trust, dynamic, notes, topics_shared, unresolved)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                rel.person_id,
                rel.name,
                rel.first_met,
                rel.last_interaction,
                rel.interaction_count,
                rel.familiarity,
                rel.warmth,
                rel.trust,
                rel.dynamic,
                json.dumps(rel.notes),
                json.dumps(rel.topics_shared),
                json.dumps(rel.unresolved),
            ),
        )
        await conn.commit()
        return rel

    async def list_recent(self, conn, limit: int = 15) -> list[Relationship]:
        cur = await conn.execute(
            "SELECT * FROM relationships ORDER BY last_interaction DESC LIMIT ?",
            (limit,),
        )
        rows = await cur.fetchall()
        return [self._row_to_rel(r) for r in rows]

    def blurb(self, rel: Relationship) -> str:
        return (
            f"{rel.name} — familiarity {rel.familiarity:.2f}, warmth {rel.warmth:.2f}, "
            f"trust {rel.trust:.2f}. Dynamic: {rel.dynamic}. "
            f"Recent notes: {'; '.join(rel.notes[-3:])}"
        )
