"""Episodic memory: store, recall, significance, imprint-aware recall."""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Optional

from ngram.memory.store import _pack_embedding, _unpack_embedding, cosine_sim
from ngram.storage.protocol import RelationalStore
from ngram.models import EmotionCategory, Episode, ImprintRecord, new_id

if TYPE_CHECKING:
    from ngram.config import EntityConfig
    from ngram.inference.protocol import InferenceProvider


MIN_CONFIDENT_RECALL_SIMILARITY = 0.50


class EpisodicMemory:
    def __init__(self, entity: EntityConfig, store: RelationalStore) -> None:
        self.entity = entity
        self.store = store

    async def recall(
        self,
        conn,
        query: str,
        query_embedding: list[float],
        limit: int = 5,
        min_significance: float = 0.0,
        current_mood: EmotionCategory | None = None,
        min_similarity: float | None = None,
    ) -> list[tuple[Episode, list[ImprintRecord]]]:
        from ngram.memory.imprints import ImprintStore

        now = time.time()
        mem = self.entity.harness.memory
        if current_mood is not None and query_embedding:
            ids = await self.store.search_episodes_by_embedding_biased(
                conn,
                query_embedding,
                current_mood,
                now,
                imprint_weight=mem.imprint_recall_weight,
                imprint_half_life=mem.imprint_decay_half_life_seconds,
                limit=max(limit * 3, 15),
                min_significance=min_significance,
            )
        else:
            ids = await self.store.search_episodes_by_embedding(
                conn,
                query_embedding,
                limit=limit * 3,
                min_significance=min_significance,
            )
        eids = [eid for eid, _ in ids[: max(limit * 2, 10)]]
        istore = ImprintStore(self.store)
        imap = await istore.for_episodes(conn, eids)
        out: list[tuple[Episode, list[ImprintRecord]]] = []
        seen: set[str] = set()
        for eid, _ in ids:
            if eid in seen or len(out) >= limit:
                continue
            ep = await self.get_by_id(conn, eid)
            if ep:
                if min_similarity is not None:
                    similarity = cosine_sim(query_embedding, ep.embedding or [])
                    if similarity < float(min_similarity):
                        continue
                seen.add(eid)
                out.append((ep, imap.get(eid, [])))
        return out

    async def fetch_top_for_narrative(self, conn, limit: int = 22) -> list[Episode]:
        cur = await conn.execute(
            """
            SELECT * FROM episodes
            ORDER BY significance DESC, timestamp DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cur.fetchall()
        return [self._row_to_episode(r) for r in rows]

    async def recent_for_evolution(self, conn, limit: int = 100) -> list[Episode]:
        cur = await conn.execute(
            "SELECT * FROM episodes ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        )
        rows = await cur.fetchall()
        return [self._row_to_episode(r) for r in rows]

    async def recent_summaries(self, conn, limit: int = 5) -> list[str]:
        cur = await conn.execute(
            "SELECT summary FROM episodes ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        )
        rows = await cur.fetchall()
        return [str(r[0]) for r in rows if r and r[0]]

    async def get_by_id(self, conn, eid: str) -> Optional[Episode]:
        cur = await conn.execute("SELECT * FROM episodes WHERE id = ?", (eid,))
        row = await cur.fetchone()
        if not row:
            return None
        return self._row_to_episode(row)

    def _row_to_episode(self, row) -> Episode:
        (
            eid,
            ts,
            summary,
            participants,
            emo,
            emo_i,
            sig,
            tags,
            raw,
            self_ref,
            emb_blob,
        ) = row
        return Episode(
            id=eid,
            timestamp=float(ts),
            summary=summary or "",
            participants=json.loads(participants or "[]"),
            emotional_imprint=EmotionCategory(emo) if emo else EmotionCategory.NEUTRAL,
            emotional_intensity=float(emo_i or 0),
            significance=float(sig or 0),
            tags=json.loads(tags or "[]"),
            raw_context=raw,
            self_reflection=self_ref,
            embedding=_unpack_embedding(emb_blob),
        )

    async def recall_about_person(self, conn, person_id: str, limit: int = 5) -> list[Episode]:
        cur = await conn.execute("SELECT * FROM episodes ORDER BY timestamp DESC")
        rows = await cur.fetchall()
        out: list[Episode] = []
        for row in rows:
            ep = self._row_to_episode(row)
            if person_id in ep.participants:
                out.append(ep)
            if len(out) >= limit:
                break
        return out

    async def recall_emotional(
        self, conn, emotion: EmotionCategory, limit: int = 5
    ) -> list[Episode]:
        cur = await conn.execute(
            "SELECT * FROM episodes WHERE emotional_imprint = ? ORDER BY emotional_intensity DESC LIMIT ?",
            (emotion.value, limit),
        )
        rows = await cur.fetchall()
        return [self._row_to_episode(r) for r in rows]

    async def save_episode(self, conn, episode: Episode) -> None:
        emb = _pack_embedding(episode.embedding) if episode.embedding else None
        await conn.execute(
            """INSERT OR REPLACE INTO episodes
            (id, timestamp, summary, participants, emotional_imprint, emotional_intensity,
             significance, tags, raw_context, self_reflection, embedding)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                episode.id,
                episode.timestamp,
                episode.summary,
                json.dumps(episode.participants),
                episode.emotional_imprint.value,
                episode.emotional_intensity,
                episode.significance,
                json.dumps(episode.tags),
                episode.raw_context,
                episode.self_reflection,
                emb,
            ),
        )
        await conn.commit()

    async def create_from_interaction(
        self,
        conn,
        client: InferenceProvider,
        *,
        summary: str,
        participants: list[str],
        imprint: EmotionCategory,
        imprint_i: float,
        significance: float,
        raw_context: str,
        self_reflection: Optional[str],
        tags: list[str],
    ) -> Episode:
        emb_model = self.entity.harness.models.embedding
        emb: list[float] | None = None
        try:
            vec = await client.embed(emb_model, summary + "\n" + raw_context[:2000])
            if vec:
                emb = vec
        except Exception:
            pass
        ep = Episode(
            id=new_id("ep_"),
            timestamp=time.time(),
            summary=summary,
            participants=participants,
            emotional_imprint=imprint,
            emotional_intensity=imprint_i,
            significance=significance,
            tags=tags,
            raw_context=raw_context[:8000],
            self_reflection=self_reflection,
            embedding=emb,
        )
        await self.save_episode(conn, ep)
        return ep

    async def sample_distant_pairs(
        self,
        conn,
        *,
        min_time_gap_hours: float = 24.0,
        max_similarity: float = 0.35,
        candidate_limit: int = 10,
        pair_count: int = 3,
    ) -> list[tuple[Episode, Episode]]:
        """Select pairs of temporally distant, topically dissimilar episodes.

        Used by the dream engine for creative recombination of memories
        that the waking mind would never juxtapose.

        1. Pull top candidates by significance from different time buckets.
        2. Compute pairwise cosine similarity on embeddings.
        3. Return pairs with similarity < ``max_similarity``.
        4. Falls back to random temporal sampling when embeddings are unavailable.
        """
        import random

        from ngram.memory.store import cosine_sim

        gap_seconds = min_time_gap_hours * 3600.0

        # Fetch significant episodes with embeddings
        cur = await conn.execute(
            "SELECT * FROM episodes WHERE significance > 0.1 "
            "ORDER BY significance DESC LIMIT ?",
            (max(candidate_limit * 3, 30),),
        )
        rows = await cur.fetchall()
        if len(rows) < 2:
            return []

        candidates = [self._row_to_episode(r) for r in rows]

        # Build pairs with temporal distance
        valid_pairs: list[tuple[Episode, Episode, float]] = []
        for i, ep_a in enumerate(candidates):
            for ep_b in candidates[i + 1 :]:
                time_gap = abs(ep_a.timestamp - ep_b.timestamp)
                if time_gap < gap_seconds:
                    continue

                # Compute similarity
                if ep_a.embedding and ep_b.embedding:
                    sim = cosine_sim(ep_a.embedding, ep_b.embedding)
                    if sim > max_similarity:
                        continue
                    valid_pairs.append((ep_a, ep_b, sim))
                else:
                    # No embeddings — accept based on temporal distance alone
                    valid_pairs.append((ep_a, ep_b, 0.0))

        if not valid_pairs:
            # Fallback: random temporal pairs regardless of similarity
            random.shuffle(candidates)
            for i in range(0, len(candidates) - 1, 2):
                if abs(candidates[i].timestamp - candidates[i + 1].timestamp) > gap_seconds:
                    valid_pairs.append((candidates[i], candidates[i + 1], 0.5))
                if len(valid_pairs) >= pair_count:
                    break

        # Sort by lowest similarity (most dissimilar = most dream-worthy)
        valid_pairs.sort(key=lambda x: x[2])

        return [(a, b) for a, b, _sim in valid_pairs[:pair_count]]
