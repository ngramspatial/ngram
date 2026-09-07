"""Self-story snapshots: deliberate-model synthesis + identity diff."""

from __future__ import annotations

import difflib
import json
import time
from typing import TYPE_CHECKING, Any, Optional

from ngram.storage.protocol import RelationalStore
from ngram.models import new_id

if TYPE_CHECKING:
    from ngram.config import EntityConfig
    from ngram.inference.protocol import InferenceProvider


class NarrativeMemory:
    def __init__(self, store: RelationalStore) -> None:
        self.store = store

    async def latest(self, conn) -> Optional[str]:
        row = await self._latest_row(conn)
        return row[2] if row else None

    async def latest_row_full(self, conn) -> Optional[tuple[str, float, str, str]]:
        """(id, timestamp, self_story, trait_snapshot_json)"""
        return await self._latest_row(conn)

    async def _latest_row(self, conn) -> Optional[tuple[Any, ...]]:
        cur = await conn.execute(
            "SELECT id, timestamp, self_story, trait_snapshot FROM narrative ORDER BY timestamp DESC LIMIT 1"
        )
        return await cur.fetchone()

    async def save(
        self,
        conn,
        self_story: str,
        trait_snapshot: dict[str, Any],
    ) -> None:
        nid = new_id("nar_")
        await conn.execute(
            "INSERT INTO narrative (id, timestamp, self_story, trait_snapshot) VALUES (?, ?, ?, ?)",
            (nid, time.time(), self_story, json.dumps(trait_snapshot)),
        )
        await conn.commit()


class NarrativeSynthesizer:
    """Calls the harness deliberate model to author first-person continuity of self."""

    def __init__(self, entity: EntityConfig, store: RelationalStore) -> None:
        self.entity = entity
        self.store = store
        self._mem = NarrativeMemory(store)

    async def synthesize(
        self,
        conn,
        client: InferenceProvider,
        *,
        episodes_payload: list[dict[str, Any]],
        relationships_payload: list[dict[str, Any]],
        beliefs_payload: list[dict[str, Any]],
        traits: dict[str, float],
    ) -> Optional[str]:
        model = self.entity.effective_deliberate_model()
        prev = await self._mem.latest_row_full(conn)
        prev_story = prev[2] if prev else ""

        blob = {
            "significant_episodes": episodes_payload,
            "relationships": relationships_payload,
            "beliefs": beliefs_payload,
            "core_traits": traits,
        }
        user_prompt = (
            "Here is structured memory about my recent life (JSON). "
            "Based on these experiences, write a short first-person reflection on who I am becoming. "
            "Reference specific events and people by name or description. "
            "Be honest about what I am uncertain about. "
            "Do not use bullet lists; write as continuous interior monologue (under 900 words).\n\n"
            f"{json.dumps(blob, indent=2)[:12000]}"
        )
        if prev_story:
            user_prompt += (
                "\n\nMy previous self-story was:\n---\n"
                f"{prev_story[:4000]}\n---\n"
                "You may echo continuity or note deliberate change."
            )

        messages = [
            {
                "role": "system",
                "content": (
                    f"You are {self.entity.name}'s continuity of consciousness. "
                    "Output ONLY the first-person narrative, no preamble."
                ),
            },
            {"role": "user", "content": user_prompt},
        ]
        try:
            res = await client.chat_completion(
                model,
                messages,
                temperature=0.72,
                max_tokens=1200,
                think=self.entity.harness.cognition.thinking_mode,
                num_ctx=self.entity.effective_ollama_num_ctx(),
            )
        except Exception:
            return None
        if not hasattr(res, "content"):
            return None
        story = (res.content or "").strip()
        if len(story) < 40:
            return None

        diff_text = ""
        if prev_story:
            diff_lines = list(
                difflib.unified_diff(
                    prev_story.splitlines(),
                    story.splitlines(),
                    lineterm="",
                    n=2,
                )
            )
            diff_text = "\n".join(diff_lines[:80])

        snapshot: dict[str, Any] = {
            "traits": traits,
            "episode_count": len(episodes_payload),
            "diff_from_previous": diff_text[:6000],
            "previous_narrative_id": prev[0] if prev else None,
        }
        await self._mem.save(conn, story, snapshot)
        return story
