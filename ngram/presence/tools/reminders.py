"""Persistent reminders with natural-language time parsing."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from ngram.presence.tools.registry import tool
from ngram.presence.tools.runtime import require_tool_runtime

log = structlog.get_logger("ngram.presence.tools.reminders")

_MANAGERS: dict[str, "ReminderManager"] = {}


@dataclass
class ReminderRow:
    reminder_id: str
    message: str
    due_at: float
    target_person: str
    platform: str
    channel: str
    person_id: str
    status: str
    created_at: float
    fired_at: float | None


def _row_from_tuple(row: tuple[Any, ...]) -> ReminderRow:
    return ReminderRow(
        reminder_id=str(row[0]),
        message=str(row[1] or ""),
        due_at=float(row[2] or 0.0),
        target_person=str(row[3] or ""),
        platform=str(row[4] or ""),
        channel=str(row[5] or ""),
        person_id=str(row[6] or ""),
        status=str(row[7] or "active"),
        created_at=float(row[8] or 0.0),
        fired_at=(float(row[9]) if row[9] is not None else None),
    )


def _parse_when(raw: str) -> float | None:
    try:
        import dateparser  # type: ignore[import-not-found]
    except ImportError:
        raise RuntimeError("dateparser not installed. Install with: pip install dateparser")
    dt = dateparser.parse(
        (raw or "").strip(),
        settings={
            "PREFER_DATES_FROM": "future",
            "RETURN_AS_TIMEZONE_AWARE": False,
        },
    )
    if dt is None:
        return None
    return float(dt.timestamp())


class ReminderManager:
    def __init__(self, entity: Any) -> None:
        self.entity = entity
        self.scheduler = AsyncIOScheduler()
        self.started = False
        self.loaded = False
        self.job_ids: set[str] = set()

    def _job_id(self, reminder_id: str) -> str:
        return f"reminder:{self.entity.config.name}:{reminder_id}"

    async def ensure_started(self) -> None:
        if not self.started:
            self.scheduler.start()
            self.started = True
        if not self.loaded:
            await self.reload_from_db()
            self.loaded = True

    async def reload_from_db(self) -> None:
        now = time.time()
        async with self.entity.store.session() as conn:
            cur = await conn.execute(
                "SELECT id, message, due_at, target_person, platform, channel, person_id, status, created_at, fired_at "
                "FROM reminders WHERE status = ? AND due_at >= ?",
                ("active", now - 3),
            )
            rows = await cur.fetchall()
        for row in rows:
            self._schedule(_row_from_tuple(tuple(row)))

    def _schedule(self, rem: ReminderRow) -> None:
        if rem.status != "active":
            return
        jid = self._job_id(rem.reminder_id)
        if jid in self.job_ids:
            return
        run_at = datetime.fromtimestamp(rem.due_at)
        self.scheduler.add_job(
            self._fire,
            "date",
            run_date=run_at,
            args=[rem.reminder_id],
            id=jid,
            replace_existing=True,
        )
        self.job_ids.add(jid)

    def unschedule(self, reminder_id: str) -> None:
        jid = self._job_id(reminder_id)
        try:
            self.scheduler.remove_job(jid)
        except Exception:
            pass
        self.job_ids.discard(jid)

    async def _fire(self, reminder_id: str) -> None:
        now = time.time()
        self.unschedule(reminder_id)
        async with self.entity.store.session() as conn:
            cur = await conn.execute(
                "SELECT id, message, due_at, target_person, platform, channel, person_id, status, created_at, fired_at "
                "FROM reminders WHERE id = ?",
                (reminder_id,),
            )
            row = await cur.fetchone()
            if not row:
                return
            rem = _row_from_tuple(tuple(row))
            if rem.status != "active":
                return
            route = {"platform": rem.platform, "channel": rem.channel}
            target_person = rem.target_person.strip()
            if target_person:
                resolver = getattr(self.entity, "resolve_person_route", None)
                if callable(resolver):
                    try:
                        resolved = resolver(target_person)
                        if isinstance(resolved, dict) and resolved.get("platform") and resolved.get("channel"):
                            route = {"platform": str(resolved["platform"]), "channel": str(resolved["channel"])}
                    except Exception:
                        pass
            if not route.get("platform") or not route.get("channel"):
                last = getattr(self.entity, "_last_conversation", {}) or {}
                route = {
                    "platform": str(last.get("platform") or "cli"),
                    "channel": str(last.get("channel") or "cli"),
                }
            text = f"Reminder: {rem.message}"
            sender = getattr(self.entity, "send_message_to_platform", None)
            if callable(sender):
                try:
                    await sender(route["platform"], route["channel"], text)
                except Exception as e:
                    log.warning("reminder_send_failed", reminder_id=reminder_id, error=str(e))
            await conn.execute(
                "UPDATE reminders SET status = ?, fired_at = ? WHERE id = ?",
                ("fired", now, reminder_id),
            )
            await conn.commit()


def _manager_for_entity(entity: Any) -> ReminderManager:
    key = f"{entity.config.name}:{id(entity)}"
    mgr = _MANAGERS.get(key)
    if mgr is None:
        mgr = ReminderManager(entity)
        _MANAGERS[key] = mgr
    return mgr


async def _next_reminder_id(conn) -> str:
    cur = await conn.execute(
        "SELECT id FROM reminders WHERE id LIKE ? ORDER BY created_at DESC LIMIT 500",
        ("rem_%",),
    )
    rows = await cur.fetchall()
    max_n = 0
    for (rid,) in rows:
        s = str(rid or "")
        if not s.startswith("rem_"):
            continue
        try:
            max_n = max(max_n, int(s[4:]))
        except ValueError:
            continue
    return f"rem_{max_n + 1:02d}"


@tool(
    name="set_reminder",
    description="Set a reminder for yourself or someone else. Follow through on promises.",
)
async def set_reminder(message: str, when: str, target_person: str = "") -> str:
    msg = (message or "").strip()
    when_raw = (when or "").strip()
    if not msg:
        return json.dumps({"error": "message is required"})
    if not when_raw:
        return json.dumps({"error": "when is required"})
    try:
        due_ts = _parse_when(when_raw)
    except Exception as e:
        return json.dumps({"error": str(e)})
    if due_ts is None:
        return json.dumps({"error": f"could not parse time: {when_raw}"})
    if due_ts < time.time() - 10:
        return json.dumps({"error": f"time is in the past: {when_raw}"})

    ctx = require_tool_runtime()
    entity = ctx.entity
    inp = ctx.inp
    if inp is None:
        return json.dumps({"error": "no active conversation context"})
    now = time.time()
    async with entity.store.session() as conn:
        reminder_id = await _next_reminder_id(conn)
        await conn.execute(
            "INSERT INTO reminders (id, message, due_at, target_person, platform, channel, person_id, status, created_at, fired_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (id) DO UPDATE SET message = EXCLUDED.message, due_at = EXCLUDED.due_at, "
            "target_person = EXCLUDED.target_person, platform = EXCLUDED.platform, channel = EXCLUDED.channel, "
            "person_id = EXCLUDED.person_id, status = EXCLUDED.status, created_at = EXCLUDED.created_at, fired_at = EXCLUDED.fired_at",
            (
                reminder_id,
                msg,
                due_ts,
                (target_person or "").strip(),
                inp.platform,
                inp.channel,
                inp.person_id,
                "active",
                now,
                None,
            ),
        )
        await conn.commit()

    mgr = _manager_for_entity(entity)
    await mgr.ensure_started()
    mgr._schedule(
        ReminderRow(
            reminder_id=reminder_id,
            message=msg,
            due_at=due_ts,
            target_person=(target_person or "").strip(),
            platform=inp.platform,
            channel=inp.channel,
            person_id=inp.person_id,
            status="active",
            created_at=now,
            fired_at=None,
        )
    )
    return json.dumps(
        {
            "ok": True,
            "reminder_id": reminder_id,
            "when": datetime.fromtimestamp(due_ts).isoformat(sep=" ", timespec="minutes"),
            "target_person": (target_person or "").strip(),
        },
        ensure_ascii=False,
    )


@tool(
    name="list_reminders",
    description="See all your active reminders",
)
async def list_reminders() -> str:
    ctx = require_tool_runtime()
    entity = ctx.entity
    async with entity.store.session() as conn:
        cur = await conn.execute(
            "SELECT id, message, due_at, target_person, platform, channel, person_id, status, created_at, fired_at "
            "FROM reminders WHERE status = ? ORDER BY due_at ASC LIMIT 200",
            ("active",),
        )
        rows = await cur.fetchall()
    out = []
    for row in rows:
        r = _row_from_tuple(tuple(row))
        out.append(
            {
                "id": r.reminder_id,
                "when": datetime.fromtimestamp(r.due_at).isoformat(sep=" ", timespec="minutes"),
                "message": r.message,
                "target_person": r.target_person,
                "platform": r.platform,
                "channel": r.channel,
            }
        )
    return json.dumps({"active": out}, ensure_ascii=False)


@tool(
    name="cancel_reminder",
    description="Cancel a reminder you set",
)
async def cancel_reminder(reminder_id: str) -> str:
    rid = (reminder_id or "").strip()
    if not rid:
        return json.dumps({"error": "reminder_id required"})
    ctx = require_tool_runtime()
    entity = ctx.entity
    async with entity.store.session() as conn:
        await conn.execute(
            "UPDATE reminders SET status = ? WHERE id = ? AND status = ?",
            ("cancelled", rid, "active"),
        )
        await conn.commit()
    mgr = _manager_for_entity(entity)
    await mgr.ensure_started()
    mgr.unschedule(rid)
    return json.dumps({"ok": True, "reminder_id": rid}, ensure_ascii=False)
