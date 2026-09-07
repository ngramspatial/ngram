"""Proactive messages when drives cross thresholds."""

from __future__ import annotations

import structlog

from ngram.config import EntityConfig
from ngram.identity.drives import Drive
from ngram.inference.protocol import InferenceProvider

log = structlog.get_logger("ngram.presence.initiative")


class InitiativeEngine:
    def __init__(self, entity: EntityConfig, client: InferenceProvider) -> None:
        self.entity = entity
        self.client = client

    async def compose_proactive(self, drive: Drive, context: str) -> str:
        model = self.entity.cognition.deliberate_model
        messages = [
            {
                "role": "system",
                "content": (
                    f"You are {self.entity.name}. A inner drive ({drive.name}) has grown strong. "
                    "Write one short unsolicited message (2–4 sentences) you might send someone "
                    "you know — not generic, not a question list. Let active projects, relationship context, "
                    "and your own habits shape what feels natural. Stay in character."
                ),
            },
            {"role": "user", "content": f"Context:\n{context[:3000]}"},
        ]
        try:
            res = await self.client.chat_completion(
                model,
                messages,
                temperature=self.entity.harness.cognition.temperature,
                max_tokens=400,
                think=False,
                num_ctx=self.entity.effective_ollama_num_ctx(),
            )
            # Empty completion: do not broadcast a placeholder (was "…" and looked like a broken routine).
            return (res.content or "").strip()
        except Exception as e:
            log.warning("initiative_failed", module="presence", error=str(e))
            return ""
