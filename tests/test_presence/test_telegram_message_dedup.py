"""Telegram (chat_id, message_id) dedup + per-chat serialization for entity callback."""

from __future__ import annotations

import pytest

from ngram.models import Input
from ngram.presence.platforms.telegram_platform import TelegramPlatform


class _StubEntity:
    """TelegramPlatform only needs an object for typing; callback tests skip connect()."""


def test_parse_memories_count_or_error_accepts_valid_range() -> None:
    count, err = TelegramPlatform._parse_memories_count_or_error(["8"], default=5, min_count=1, max_count=12)
    assert count == 8
    assert err is None


def test_parse_memories_count_or_error_rejects_invalid_input() -> None:
    count, err = TelegramPlatform._parse_memories_count_or_error(["abc"], default=5, min_count=1, max_count=12)
    assert count == 5
    assert "Count must be a number" in str(err)


@pytest.mark.asyncio
async def test_telegram_skips_second_callback_same_message_id() -> None:
    calls: list[int] = []

    async def cb(_inp: Input) -> None:
        calls.append(1)

    p = TelegramPlatform("000000:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", entity=_StubEntity())
    p._cb = cb
    inp = Input(
        text="hi",
        person_id="1",
        person_name="u",
        channel="99",
        platform="telegram",
        metadata={"telegram_message_id": 42, "chat_type": "private"},
    )
    await p._run_callback(inp)
    task = p._chat_active_tasks[99]
    await p._run_callback(inp)
    await task
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_telegram_reruns_after_failed_callback_releases_claim() -> None:
    calls: list[int] = []
    fail_once = {"v": True}

    async def cb(_inp: Input) -> None:
        calls.append(1)
        if fail_once["v"]:
            fail_once["v"] = False
            raise RuntimeError("boom")

    p = TelegramPlatform("000000:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", entity=_StubEntity())
    p._cb = cb
    inp = Input(
        text="hi",
        person_id="1",
        person_name="u",
        channel="100",
        platform="telegram",
        metadata={"telegram_message_id": 7, "chat_type": "private"},
    )
    await p._run_callback(inp)
    await p._chat_active_tasks[100]
    await p._run_callback(inp)
    await p._chat_active_tasks[100]
    assert len(calls) == 2
