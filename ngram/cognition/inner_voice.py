"""Thinking capture → summaries, signals, rolling buffer."""

from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass

import structlog

from ngram.storage.protocol import RelationalStore
from ngram.models import new_id

log = structlog.get_logger("ngram.cognition.inner_voice")


@dataclass
class InnerVoiceSlice:
    summary: str
    emotional_cues: list[str]
    belief_hints: list[str]
    relationship_hints: list[str]


class InnerVoiceProcessor:
    def __init__(self, store: RelationalStore, max_buffer: int = 12) -> None:
        self.store = store
        self._buffer: deque[str] = deque(maxlen=max_buffer)

    def recent_summary(self) -> str:
        if not self._buffer:
            return ""
        return self._buffer[-1]

    def full_context(self) -> str:
        return " | ".join(list(self._buffer)[-5:])

    def clear_buffer(self) -> None:
        self._buffer.clear()

    def process(self, thinking: str | None, visible_reply: str) -> InnerVoiceSlice:
        t = thinking or ""
        cues = re.findall(r"\b(fear|hope|doubt|trust|curious|angry|sad|joy)\w*\b", t, re.I)
        beliefs = re.findall(r"I (?:think|believe|wonder) ([^.]{5,120})", t, re.I)
        rel = re.findall(r"(they|you) (?:seem|feel|sound|are) ([^.]{5,80})", t, re.I)
        summary = (t[:500] + "…") if len(t) > 500 else (t or visible_reply[:200])
        slice_ = InnerVoiceSlice(
            summary=summary.strip(),
            emotional_cues=list({c.lower() for c in cues})[:8],
            belief_hints=beliefs[:5],
            relationship_hints=[f"{a} {b}" for a, b in rel][:5],
        )
        if slice_.summary:
            self._buffer.append(slice_.summary)
        log.info(
            "inner_voice_processed",
            module="cognition",
            cues=slice_.emotional_cues,
        )
        return slice_

    async def persist_summary(self, conn, summary: str, emotional_context: str) -> None:
        await conn.execute(
            "INSERT INTO inner_voice (id, timestamp, summary, emotional_context, embedding) VALUES (?, ?, ?, ?, ?)",
            (new_id("iv_"), time.time(), summary, emotional_context, None),
        )
        await conn.commit()
