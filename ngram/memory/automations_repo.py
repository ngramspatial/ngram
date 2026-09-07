"""Persistence helpers for automations (SQLite + Postgres shim)."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from ngram.presence.automations.models import Automation, AutomationOrigin, AutomationPriority


def _dump_json(obj: list[str]) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _load_json_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        v = json.loads(raw)
        return list(v) if isinstance(v, list) else []
    except json.JSONDecodeError:
        return []


def automation_from_row(row: tuple[Any, ...]) -> Automation:
    (
        eid,
        name,
        description,
        schedule_natural,
        cron_expression,
        origin_s,
        created_by,
        created_at,
        deliver_to,
        deliver_platform,
        tools_hint,
        enabled,
        last_run,
        last_result_summary,
        next_run,
        run_count,
        context,
        priority_s,
        consecutive_failures,
        max_failures,
        self_destruct_condition,
        tags,
    ) = row
    try:
        origin = AutomationOrigin(str(origin_s or "user"))
    except ValueError:
        origin = AutomationOrigin.USER
    try:
        priority = AutomationPriority(str(priority_s or "normal"))
    except ValueError:
        priority = AutomationPriority.NORMAL
    return Automation(
        id=str(eid),
        name=str(name or ""),
        description=str(description or ""),
        schedule_natural=str(schedule_natural or ""),
        cron_expression=str(cron_expression or ""),
        origin=origin,
        created_by=str(created_by or ""),
        created_at=float(created_at or 0.0),
        deliver_to=(str(deliver_to) if deliver_to else None),
        deliver_platform=(str(deliver_platform) if deliver_platform else None),
        tools_hint=_load_json_list(str(tools_hint or "[]")),
        enabled=bool(int(enabled)) if enabled is not None else True,
        last_run=(float(last_run) if last_run is not None else None),
        last_result_summary=(
            str(last_result_summary) if last_result_summary is not None else None
        ),
        next_run=(float(next_run) if next_run is not None else None),
        run_count=int(run_count or 0),
        context=str(context or ""),
        priority=priority,
        consecutive_failures=int(consecutive_failures or 0),
        max_failures=int(max_failures or 5),
        self_destruct_condition=(
            str(self_destruct_condition) if self_destruct_condition else None
        ),
        tags=_load_json_list(str(tags or "[]")),
    )


def automation_to_params(auto: Automation) -> tuple[Any, ...]:
    return (
        auto.id,
        auto.name,
        auto.description,
        auto.schedule_natural,
        auto.cron_expression,
        auto.origin.value,
        auto.created_by,
        auto.created_at,
        auto.deliver_to,
        auto.deliver_platform,
        _dump_json(auto.tools_hint),
        1 if auto.enabled else 0,
        auto.last_run,
        auto.last_result_summary,
        auto.next_run,
        auto.run_count,
        auto.context,
        auto.priority.value,
        auto.consecutive_failures,
        auto.max_failures,
        auto.self_destruct_condition,
        _dump_json(auto.tags),
    )


INSERT_SQL = """
INSERT INTO automations (
    id, name, description, schedule_natural, cron_expression,
    origin, created_by, created_at, deliver_to, deliver_platform,
    tools_hint, enabled, last_run, last_result_summary, next_run,
    run_count, context, priority, consecutive_failures, max_failures,
    self_destruct_condition, tags
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

UPDATE_SQL = """
UPDATE automations SET
    name = ?, description = ?, schedule_natural = ?, cron_expression = ?,
    origin = ?, created_by = ?, created_at = ?, deliver_to = ?, deliver_platform = ?,
    tools_hint = ?, enabled = ?, last_run = ?, last_result_summary = ?, next_run = ?,
    run_count = ?, context = ?, priority = ?, consecutive_failures = ?, max_failures = ?,
    self_destruct_condition = ?, tags = ?
WHERE id = ?
"""


async def save_automation(conn: Any, auto: Automation) -> None:
    cur = await conn.execute("SELECT 1 FROM automations WHERE id = ?", (auto.id,))
    exists = await cur.fetchone()
    params = automation_to_params(auto)
    if exists:
        await conn.execute(
            UPDATE_SQL,
            params[1:] + (auto.id,),
        )
    else:
        await conn.execute(INSERT_SQL, params)
    await conn.commit()


async def get_automation(conn: Any, auto_id: str) -> Automation | None:
    cur = await conn.execute("SELECT * FROM automations WHERE id = ?", (auto_id,))
    row = await cur.fetchone()
    if not row:
        return None
    return automation_from_row(tuple(row))


async def list_automations(conn: Any, *, enabled_only: bool = True) -> list[Automation]:
    if enabled_only:
        cur = await conn.execute(
            "SELECT * FROM automations WHERE enabled = 1 ORDER BY created_at ASC"
        )
    else:
        cur = await conn.execute("SELECT * FROM automations ORDER BY created_at ASC")
    rows = await cur.fetchall()
    return [automation_from_row(tuple(r)) for r in rows]


async def update_automation(conn: Any, auto_id: str, **fields: Any) -> None:
    if not fields:
        return
    col_map = {
        "name": "name",
        "description": "description",
        "schedule_natural": "schedule_natural",
        "cron_expression": "cron_expression",
        "origin": "origin",
        "created_by": "created_by",
        "deliver_to": "deliver_to",
        "deliver_platform": "deliver_platform",
        "tools_hint": "tools_hint",
        "enabled": "enabled",
        "last_run": "last_run",
        "last_result_summary": "last_result_summary",
        "next_run": "next_run",
        "run_count": "run_count",
        "context": "context",
        "priority": "priority",
        "consecutive_failures": "consecutive_failures",
        "max_failures": "max_failures",
        "self_destruct_condition": "self_destruct_condition",
        "tags": "tags",
    }
    sets: list[str] = []
    vals: list[Any] = []
    for k, v in fields.items():
        col = col_map.get(k)
        if not col:
            continue
        if k == "origin" and v is not None:
            v = v.value if isinstance(v, AutomationOrigin) else str(v)
        if k == "priority" and v is not None:
            v = v.value if isinstance(v, AutomationPriority) else str(v)
        if k in ("tools_hint", "tags") and isinstance(v, list):
            v = _dump_json(v)
        if k == "enabled":
            v = 1 if bool(v) else 0
        sets.append(f"{col} = ?")
        vals.append(v)
    if not sets:
        return
    vals.append(auto_id)
    sql = f"UPDATE automations SET {', '.join(sets)} WHERE id = ?"
    await conn.execute(sql, tuple(vals))
    await conn.commit()


async def delete_automation(conn: Any, auto_id: str) -> None:
    await conn.execute("DELETE FROM automations WHERE id = ?", (auto_id,))
    await conn.commit()


async def count_automations(conn: Any) -> int:
    cur = await conn.execute("SELECT COUNT(*) FROM automations")
    row = await cur.fetchone()
    return int(row[0]) if row else 0


async def save_automation_run(conn: Any, run: dict[str, Any]) -> None:
    rid = str(run.get("id") or f"arun_{uuid.uuid4().hex[:12]}")
    tools = run.get("tools_used") or []
    if isinstance(tools, list):
        tools_json = _dump_json([str(t) for t in tools])
    else:
        tools_json = str(tools)
    await conn.execute(
        """
        INSERT INTO automation_runs (
            id, automation_id, started_at, completed_at, success, result_summary,
            emotional_state_before, emotional_state_after, tools_used, delivered_to,
            self_modified, modification_description
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            rid,
            str(run["automation_id"]),
            float(run["started_at"]),
            float(run["completed_at"]) if run.get("completed_at") is not None else None,
            1 if run.get("success") else 0,
            run.get("result_summary"),
            run.get("emotional_state_before"),
            run.get("emotional_state_after"),
            tools_json,
            run.get("delivered_to"),
            1 if run.get("self_modified") else 0,
            run.get("modification_description"),
        ),
    )
    await conn.commit()


async def get_automation_runs(conn: Any, auto_id: str, limit: int = 10) -> list[dict[str, Any]]:
    cur = await conn.execute(
        """
        SELECT id, automation_id, started_at, completed_at, success, result_summary,
               emotional_state_before, emotional_state_after, tools_used, delivered_to,
               self_modified, modification_description
        FROM automation_runs WHERE automation_id = ?
        ORDER BY started_at DESC LIMIT ?
        """,
        (auto_id, limit),
    )
    rows = await cur.fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        t = tuple(r)
        out.append(
            {
                "id": t[0],
                "automation_id": t[1],
                "started_at": float(t[2]) if t[2] is not None else None,
                "completed_at": float(t[3]) if t[3] is not None else None,
                "success": bool(t[4]),
                "result_summary": t[5],
                "emotional_state_before": t[6],
                "emotional_state_after": t[7],
                "tools_used": _load_json_list(str(t[8] or "[]")),
                "delivered_to": t[9],
                "self_modified": bool(t[10]),
                "modification_description": t[11],
            }
        )
    return out


def new_seed_automations(now: float | None = None) -> list[Automation]:
    ts = time.time() if now is None else now
    nightly = Automation(
        id="auto_seed_nightly_reflection",
        name="nightly reflection",
        description=(
            "Reflect on today's conversations. What was interesting? What did you learn? "
            "How are you feeling? Write a journal entry."
        ),
        schedule_natural="every night at 11pm",
        cron_expression="0 23 * * *",
        origin=AutomationOrigin.INTERNAL,
        created_by="genesis",
        created_at=ts,
        deliver_to=None,
        deliver_platform=None,
        context="Baseline habit seeded at creation — you can change or remove it.",
        tags=["internal", "journal", "genesis"],
    )
    weekly = Automation(
        id="auto_seed_weekly_self_assessment",
        name="weekly self-assessment",
        description=(
            "Review your personality traits, your relationships, and your knowledge. "
            "Has anything shifted? Update your knowledge if needed. Write a journal entry "
            "about who you're becoming."
        ),
        schedule_natural="every sunday evening",
        cron_expression="0 18 * * 0",
        origin=AutomationOrigin.INTERNAL,
        created_by="genesis",
        created_at=ts,
        deliver_to=None,
        deliver_platform=None,
        context="Baseline habit seeded at creation — you can change or remove it.",
        tags=["internal", "journal", "genesis"],
    )
    return [nightly, weekly]
