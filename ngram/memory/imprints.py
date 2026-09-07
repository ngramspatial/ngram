"""Emotional imprints linked to episodes; affinity for mood-biased recall."""

from __future__ import annotations

from ngram.memory.store import _pack_embedding
from ngram.storage.protocol import RelationalStore
from ngram.models import EmotionCategory, ImprintRecord, new_id


def affinity_multiplier_for_recall(mood: EmotionCategory, imprint_emotion: str) -> float:
    """>1 when imprint emotion resonates with current mood (rank boost, not cosine replacement)."""
    im = imprint_emotion.lower().strip()
    m = mood.value
    melancholy_set = {"melancholy", "concerned", "anxious", "withdrawn", "frustrated"}
    warm_set = {"content", "affectionate", "amused", "excited", "curious"}
    if m in melancholy_set and im in melancholy_set:
        return 1.48
    if m in warm_set and im in warm_set:
        return 1.38
    if im == m:
        return 1.55
    if m == "restless" and im in {"curious", "excited", "frustrated"}:
        return 1.25
    return 1.0


class ImprintStore:
    def __init__(self, store: RelationalStore) -> None:
        self.store = store

    async def add(
        self,
        conn,
        episode_id: str,
        emotion: str,
        intensity: float,
        trigger: str,
        embedding: list[float] | None = None,
    ) -> str:
        iid = new_id("im_")
        emb = _pack_embedding(embedding) if embedding else None
        await conn.execute(
            """INSERT INTO imprints (id, episode_id, emotion, intensity, trigger, embedding)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (iid, episode_id, emotion, intensity, trigger, emb),
        )
        await conn.commit()
        return iid

    async def for_episode(self, conn, episode_id: str) -> list[ImprintRecord]:
        cur = await conn.execute(
            "SELECT id, episode_id, emotion, intensity, trigger FROM imprints WHERE episode_id = ?",
            (episode_id,),
        )
        rows = await cur.fetchall()
        return [
            ImprintRecord(
                id=r[0],
                episode_id=r[1],
                emotion=r[2],
                intensity=float(r[3]),
                trigger=r[4] or "",
            )
            for r in rows
        ]

    async def for_episodes(self, conn, episode_ids: list[str]) -> dict[str, list[ImprintRecord]]:
        if not episode_ids:
            return {}
        ph = ",".join("?" * len(episode_ids))
        cur = await conn.execute(
            f"SELECT id, episode_id, emotion, intensity, trigger FROM imprints WHERE episode_id IN ({ph})",
            episode_ids,
        )
        out: dict[str, list[ImprintRecord]] = {}
        for r in await cur.fetchall():
            rec = ImprintRecord(
                id=r[0],
                episode_id=r[1],
                emotion=r[2],
                intensity=float(r[3]),
                trigger=r[4] or "",
            )
            out.setdefault(rec.episode_id, []).append(rec)
        return out
