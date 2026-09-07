"""Typing delay, chunking, platform tone hints."""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from ngram.config import EntityConfig
from ngram.identity.voice import ExpressionMeta, VoiceController


class Embodiment:
    def __init__(self, entity: EntityConfig) -> None:
        self.entity = entity
        self.voice = VoiceController(entity)

    def chunk_message(self, text: str) -> list[str]:
        m = self.entity.harness.presence.message_chunk_max
        if len(text) <= m:
            return [text]
        parts: list[str] = []
        i = 0
        while i < len(text):
            parts.append(text[i : i + m])
            i += m
        return parts

    async def deliver_chunks(
        self,
        send: Callable[[str, str], Awaitable[None]],
        channel: str,
        text: str,
        meta: ExpressionMeta,
    ) -> None:
        chunks = self.chunk_message(text)
        await asyncio.sleep(min(3.0, meta.typing_delay_seconds))
        for i, c in enumerate(chunks):
            await send(channel, c)
            if i < len(chunks) - 1:
                await asyncio.sleep(meta.chunk_pause)
