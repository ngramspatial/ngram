"""entity_state persistence for Telegram /privacy allowlists."""

from __future__ import annotations

import pytest

from ngram.memory.store import MemoryStore
from ngram.presence.platforms.telegram_privacy import (
    load_telegram_busy_disabled_chat_ids,
    load_telegram_privacy_from_db,
    save_telegram_busy_disabled_chat_ids,
    save_telegram_privacy_enforced,
    save_telegram_privacy_open,
)


@pytest.mark.asyncio
async def test_telegram_privacy_open_by_default(tmp_path) -> None:
    store = MemoryStore(str(tmp_path / "ent.db"))
    async with store.session() as conn:
        enf, users, chats = await load_telegram_privacy_from_db(conn)
    assert enf is False
    assert users == set()
    assert chats == set()


@pytest.mark.asyncio
async def test_telegram_privacy_lock_open_roundtrip(tmp_path) -> None:
    store = MemoryStore(str(tmp_path / "ent.db"))
    async with store.session() as conn:
        await save_telegram_privacy_enforced(conn, user_ids={9, 1}, chat_ids=set())
    async with store.session() as conn:
        enf, users, chats = await load_telegram_privacy_from_db(conn)
    assert enf is True
    assert users == {1, 9}
    assert chats == set()

    async with store.session() as conn:
        await save_telegram_privacy_open(conn)
    async with store.session() as conn:
        enf2, users2, chats2 = await load_telegram_privacy_from_db(conn)
    assert enf2 is False
    assert users2 == set()
    assert chats2 == set()


@pytest.mark.asyncio
async def test_telegram_busy_disabled_chats_roundtrip(tmp_path) -> None:
    store = MemoryStore(str(tmp_path / "ent.db"))
    async with store.session() as conn:
        assert await load_telegram_busy_disabled_chat_ids(conn) == set()
    async with store.session() as conn:
        await save_telegram_busy_disabled_chat_ids(conn, {42, 99})
    async with store.session() as conn:
        assert await load_telegram_busy_disabled_chat_ids(conn) == {42, 99}
