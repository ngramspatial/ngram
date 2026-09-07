"""Persisted Telegram access control (entity_state) + helpers for /privacy commands."""

from __future__ import annotations

import json
from typing import Any

# Keys in entity_state (namespaced; ignored by personality hydrate).
KEY_ENFORCED = "telegram.privacy.enforced"
KEY_USER_IDS = "telegram.privacy.user_ids"
KEY_CHAT_IDS = "telegram.privacy.chat_ids"
# Chats where the pinned busy/working line is disabled (/busy off).
KEY_BUSY_DISABLED_CHAT_IDS = "telegram.busy.disabled_chat_ids"
# When set to a truthy token (/wakequiet on), autonomous wake mirrors no status/tool lines to Telegram
# (transcript only). Overrides YAML wake_user_visible_status and wake_chat_tool_activity until cleared.
KEY_WAKE_QUIET = "telegram.autonomy.wake_quiet"


async def entity_state_get(conn: Any, key: str) -> str | None:
    cur = await conn.execute("SELECT value FROM entity_state WHERE key = ?", (key,))
    row = await cur.fetchone()
    if not row or row[0] is None:
        return None
    return str(row[0])


async def entity_state_set(conn: Any, key: str, value: str) -> None:
    await conn.execute(
        "INSERT OR REPLACE INTO entity_state (key, value) VALUES (?, ?)",
        (key, value),
    )
    await conn.commit()


async def entity_state_delete(conn: Any, key: str) -> None:
    await conn.execute("DELETE FROM entity_state WHERE key = ?", (key,))
    await conn.commit()


def _parse_id_set(raw: str | None) -> set[int]:
    if not raw:
        return set()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return set()
    if not isinstance(data, list):
        return set()
    out: set[int] = set()
    for x in data:
        try:
            out.add(int(x))
        except (TypeError, ValueError):
            continue
    return out


async def load_telegram_privacy_from_db(conn: Any) -> tuple[bool, set[int], set[int]]:
    """Return (enforced, allowed_user_ids, allowed_chat_ids). Chat set empty means no chat filter."""
    enf = await entity_state_get(conn, KEY_ENFORCED)
    if (enf or "").strip() != "1":
        return False, set(), set()
    users = _parse_id_set(await entity_state_get(conn, KEY_USER_IDS))
    chats = _parse_id_set(await entity_state_get(conn, KEY_CHAT_IDS))
    return True, users, chats


async def save_telegram_privacy_open(conn: Any) -> None:
    await entity_state_set(conn, KEY_ENFORCED, "0")
    await entity_state_set(conn, KEY_USER_IDS, "[]")
    await entity_state_set(conn, KEY_CHAT_IDS, "[]")


async def load_telegram_busy_disabled_chat_ids(conn: Any) -> set[int]:
    """Chats that opted out of the pinned busy indicator (default: none → busy on everywhere)."""
    return _parse_id_set(await entity_state_get(conn, KEY_BUSY_DISABLED_CHAT_IDS))


async def save_telegram_busy_disabled_chat_ids(conn: Any, chat_ids: set[int]) -> None:
    await entity_state_set(conn, KEY_BUSY_DISABLED_CHAT_IDS, json.dumps(sorted(chat_ids)))


async def save_telegram_privacy_enforced(
    conn: Any,
    *,
    user_ids: set[int],
    chat_ids: set[int] | None = None,
) -> None:
    u = sorted(user_ids)
    c = sorted(chat_ids) if chat_ids is not None else []
    await entity_state_set(conn, KEY_ENFORCED, "1")
    await entity_state_set(conn, KEY_USER_IDS, json.dumps(u))
    await entity_state_set(conn, KEY_CHAT_IDS, json.dumps(c))


def _truthy_wake_quiet(raw: str | None) -> bool:
    return (raw or "").strip().lower() in ("1", "true", "yes", "on", "quiet")


async def is_wake_quiet(conn: Any) -> bool:
    """If True, do not mirror autonomous wake status/tool lines to Telegram (transcript only)."""
    return _truthy_wake_quiet(await entity_state_get(conn, KEY_WAKE_QUIET))


async def effective_wake_status_in_chat(entity: Any, autonomy_cfg: Any) -> bool:
    """Effective mirror for wake status lines: YAML unless /wakequiet forced transcript-only."""
    base = bool(getattr(autonomy_cfg, "wake_user_visible_status", True))
    try:
        async with entity.store.session() as conn:
            if await is_wake_quiet(conn):
                return False
    except Exception:
        return base
    return base


async def effective_wake_tools_in_chat(entity: Any, autonomy_cfg: Any) -> bool:
    """Effective mirror for per-tool lines during autonomous wake."""
    base = bool(getattr(autonomy_cfg, "wake_chat_tool_activity", True))
    try:
        async with entity.store.session() as conn:
            if await is_wake_quiet(conn):
                return False
    except Exception:
        return base
