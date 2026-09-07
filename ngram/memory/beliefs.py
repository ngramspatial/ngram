"""World model: facts, opinions, uncertainties."""

from __future__ import annotations

import time
from typing import Any, Optional

from ngram.memory.store import _pack_embedding
from ngram.storage.protocol import RelationalStore
from ngram.models import new_id


class BeliefStore:
    def __init__(self, store: RelationalStore) -> None:
        self.store = store

    async def add_belief(
        self,
        conn,
        category: str,
        content: str,
        confidence: float,
        source: str,
        embedding: Optional[list[float]] = None,
    ) -> str:
        bid = new_id("bf_")
        now = time.time()
        emb_blob = _pack_embedding(embedding) if embedding else None
        await conn.execute(
            """INSERT INTO beliefs
            (id, category, content, confidence, source, formed_at, last_reinforced, times_reinforced, embedding)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (bid, category, content, confidence, source, now, now, 1, emb_blob),
        )
        await conn.commit()
        return bid

    async def list_recent(self, conn, limit: int = 20) -> list[dict[str, Any]]:
        cur = await conn.execute(
            "SELECT id, category, content, confidence, source, formed_at FROM beliefs ORDER BY formed_at DESC LIMIT ?",
            (limit,),
        )
        rows = await cur.fetchall()
        return [
            {
                "id": r[0],
                "category": r[1],
                "content": r[2],
                "confidence": float(r[3]),
                "source": r[4],
                "formed_at": float(r[5]),
            }
            for r in rows
        ]
