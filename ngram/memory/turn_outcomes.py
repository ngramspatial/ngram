"""Turn outcome store — records and queries structured turn-level feedback.

Each completed turn produces a ``TurnOutcome`` record that captures the
strategy used, tool results, user reaction signal, and a composite score.
This persistent record closes the feedback loop so the agent can learn
which strategies work for which kinds of inputs.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import structlog

from ngram.memory.store import _pack_embedding, _unpack_embedding, cosine_sim
from ngram.models import new_id

log = structlog.get_logger("ngram.memory.turn_outcomes")


@dataclass
class TurnOutcome:
    """Structured record of a single turn's quality signals."""

    id: str
    timestamp: float
    route: str                     # "reflex" or "deliberate"
    faculty: str                   # "social", "research", "planning", "execution", "review"
    tools_used: list[str]          # tool names called this turn
    tool_success_rate: float       # 0.0–1.0
    user_signal: str               # "positive", "corrective", "repetition", "negative", "neutral"
    outcome_score: float           # 0.0–1.0 composite
    input_hash: str                # short text fingerprint for dedup
    embedding: list[float] | None = None  # input embedding for similarity search


class TurnOutcomeStore:
    """Persist and query turn outcomes for learning."""

    def __init__(self, store: Any) -> None:
        self.store = store

    async def record(
        self,
        conn: Any,
        *,
        route: str,
        faculty: str,
        tools_used: list[str],
        tool_success_rate: float,
        user_signal: str,
        outcome_score: float,
        input_hash: str,
        embedding: list[float] | None = None,
    ) -> TurnOutcome:
        """Write a turn outcome record."""
        outcome = TurnOutcome(
            id=new_id("to_"),
            timestamp=time.time(),
            route=route,
            faculty=faculty,
            tools_used=tools_used,
            tool_success_rate=max(0.0, min(1.0, tool_success_rate)),
            user_signal=user_signal,
            outcome_score=max(0.0, min(1.0, outcome_score)),
            input_hash=input_hash,
            embedding=embedding,
        )
        emb_blob = _pack_embedding(embedding) if embedding else None
        await conn.execute(
            """INSERT INTO turn_outcomes
            (id, timestamp, route, faculty, tools_used, tool_success_rate,
             user_signal, outcome_score, input_hash, embedding)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                outcome.id,
                outcome.timestamp,
                outcome.route,
                outcome.faculty,
                json.dumps(outcome.tools_used),
                outcome.tool_success_rate,
                outcome.user_signal,
                outcome.outcome_score,
                outcome.input_hash,
                emb_blob,
            ),
        )
        await conn.commit()
        log.debug(
            "turn_outcome_recorded",
            outcome_id=outcome.id,
            route=route,
            score=round(outcome_score, 3),
            signal=user_signal,
            tools=tools_used,
        )
        return outcome

    async def query_similar(
        self,
        conn: Any,
        query_embedding: list[float],
        *,
        limit: int = 5,
        min_similarity: float = 0.4,
    ) -> list[TurnOutcome]:
        """Find past turns with similar inputs (by embedding similarity)."""
        cur = await conn.execute(
            "SELECT id, timestamp, route, faculty, tools_used, tool_success_rate, "
            "user_signal, outcome_score, input_hash, embedding "
            "FROM turn_outcomes WHERE embedding IS NOT NULL "
            "ORDER BY timestamp DESC LIMIT 200"
        )
        rows = await cur.fetchall()
        scored: list[tuple[float, TurnOutcome]] = []
        for row in rows:
            emb = _unpack_embedding(row[9])
            if not emb:
                continue
            sim = cosine_sim(query_embedding, emb)
            if sim >= min_similarity:
                scored.append((sim, self._row_to_outcome(row)))
        scored.sort(key=lambda x: -x[0])
        return [o for _, o in scored[:limit]]

    async def recent_outcomes(
        self,
        conn: Any,
        limit: int = 20,
    ) -> list[TurnOutcome]:
        """Fetch the most recent turn outcomes."""
        cur = await conn.execute(
            "SELECT id, timestamp, route, faculty, tools_used, tool_success_rate, "
            "user_signal, outcome_score, input_hash, embedding "
            "FROM turn_outcomes ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        )
        rows = await cur.fetchall()
        return [self._row_to_outcome(r) for r in rows]

    async def tool_effectiveness(
        self,
        conn: Any,
        *,
        window: int = 100,
    ) -> dict[str, dict[str, float]]:
        """Compute per-tool effectiveness stats over recent turns.

        Returns {tool_name: {"uses": N, "avg_score": float, "avg_success_rate": float}}.
        """
        cur = await conn.execute(
            "SELECT tools_used, tool_success_rate, outcome_score "
            "FROM turn_outcomes ORDER BY timestamp DESC LIMIT ?",
            (window,),
        )
        rows = await cur.fetchall()
        tool_stats: dict[str, dict[str, float]] = {}
        for tools_json, success_rate, score in rows:
            try:
                tools = json.loads(tools_json)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(tools, list):
                continue
            for tool_name in tools:
                if not isinstance(tool_name, str):
                    continue
                stats = tool_stats.setdefault(
                    tool_name, {"uses": 0, "total_score": 0.0, "total_success": 0.0}
                )
                stats["uses"] += 1
                stats["total_score"] += float(score or 0)
                stats["total_success"] += float(success_rate or 0)

        result: dict[str, dict[str, float]] = {}
        for name, stats in tool_stats.items():
            n = stats["uses"]
            if n > 0:
                result[name] = {
                    "uses": n,
                    "avg_score": round(stats["total_score"] / n, 3),
                    "avg_success_rate": round(stats["total_success"] / n, 3),
                }
        return result

    async def average_outcome_score(
        self,
        conn: Any,
        window: int = 50,
    ) -> float:
        """Average outcome score over the last N turns."""
        cur = await conn.execute(
            "SELECT AVG(outcome_score) FROM "
            "(SELECT outcome_score FROM turn_outcomes ORDER BY timestamp DESC LIMIT ?)",
            (window,),
        )
        row = await cur.fetchone()
        return float(row[0]) if row and row[0] is not None else 0.5

    @staticmethod
    def _row_to_outcome(row: tuple[Any, ...]) -> TurnOutcome:
        tools_raw = row[4]
        try:
            tools = json.loads(tools_raw) if isinstance(tools_raw, str) else []
        except (json.JSONDecodeError, TypeError):
            tools = []
        return TurnOutcome(
            id=str(row[0]),
            timestamp=float(row[1] or 0),
            route=str(row[2] or ""),
            faculty=str(row[3] or ""),
            tools_used=tools if isinstance(tools, list) else [],
            tool_success_rate=float(row[5] or 0),
            user_signal=str(row[6] or "neutral"),
            outcome_score=float(row[7] or 0),
            input_hash=str(row[8] or ""),
            embedding=_unpack_embedding(row[9]) if len(row) > 9 else None,
        )
