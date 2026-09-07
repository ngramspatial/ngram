"""Entity-managed scheduled routines."""

from __future__ import annotations

import json
import re

from ngram.presence.automations.engine import build_automation_from_tool_args
from ngram.presence.automations.models import AutomationOrigin
from ngram.presence.automations.schedule import ScheduleParseError, parse_schedule
from ngram.presence.tools.registry import tool
from ngram.presence.tools.runtime import ToolRuntimeContext, require_tool_runtime


def _engine(ent: object):
    return getattr(ent, "automation_engine", None)


def _default_deliver_from_ctx(ctx: ToolRuntimeContext) -> str:
    """If the user is on Telegram or Discord, default delivery to this chat."""
    inp = ctx.inp
    if inp is None:
        return ""
    p = (inp.platform or "").strip().lower()
    ch = (inp.channel or "").strip()
    if p in ("telegram", "discord") and ch:
        return f"{p}:{ch}"
    return ""


_BARE_ID = re.compile(r"^-?\d+$")


def _qualify_deliver_to_if_bare_id(raw: str, ctx: ToolRuntimeContext) -> str:
    """
    If deliver_to is a bare numeric id (e.g. Telegram chat id, including -100… groups),
    prefix with the current platform when the turn is Telegram or Discord.
    """
    s = (raw or "").strip()
    if not s or ":" in s:
        return s
    if not _BARE_ID.fullmatch(s):
        return s
    inp = ctx.inp
    if inp is None:
        return s
    p = (inp.platform or "").strip().lower()
    if p in ("telegram", "discord"):
        return f"{p}:{s}"
    return s


@tool(
    name="create_automation",
    description=(
        "Create a new scheduled routine for yourself. Use when something should happen regularly — "
        "checking news, reaching out, monitoring a topic, reflecting. You may create routines on your "
        "own when your drives motivate you. For routines that should message the user on Telegram or "
        "Discord, set deliver_to to telegram:<chat_id> or discord:<channel_id>, pass only the numeric "
        "id while in that platform's chat to target that id, or leave deliver_to empty to use the "
        "current chat."
    ),
)
async def create_automation(
    name: str,
    description: str,
    schedule: str,
    deliver_to: str = "",
    priority: str = "normal",
    context: str = "",
    self_destruct_condition: str = "",
) -> str:
    ctx = require_tool_runtime()
    ent = ctx.entity
    cfg = ent.config.automations
    if not cfg.enabled or not ent._tool_enabled("automations", True):
        return json.dumps({"ok": False, "error": "automations disabled"})
    eng = _engine(ent)
    eff_deliver = (deliver_to or "").strip()
    if not eff_deliver:
        eff_deliver = _default_deliver_from_ctx(ctx)
    else:
        eff_deliver = _qualify_deliver_to_if_bare_id(eff_deliver, ctx)
    try:
        auto = build_automation_from_tool_args(
            name=name,
            description=description,
            schedule=schedule,
            deliver_to=eff_deliver,
            priority=priority,
            context=context,
            self_destruct_condition=self_destruct_condition,
            origin=AutomationOrigin.SELF,
            created_by="self",
        )
    except ScheduleParseError as e:
        return json.dumps({"ok": False, "error": str(e)})
    try:
        if eng is None:
            await ent.store.save_automation(auto)
            return json.dumps(
                {
                    "ok": True,
                    "id": auto.id,
                    "cron": auto.cron_expression,
                    "deliver_to": auto.deliver_to,
                    "note": "saved; scheduler will pick it up when the daemon runs",
                },
                ensure_ascii=False,
            )
        await eng.create(auto)
        return json.dumps(
            {
                "ok": True,
                "id": auto.id,
                "cron": auto.cron_expression,
                "deliver_to": auto.deliver_to,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})


@tool(
    name="list_automations",
    description="See all your active routines and habits.",
)
async def list_automations() -> str:
    ctx = require_tool_runtime()
    ent = ctx.entity
    rows = await ent.store.list_automations(enabled_only=False)
    payload = [
        {
            "id": a.id,
            "name": a.name,
            "enabled": a.enabled,
            "schedule": a.schedule_natural,
            "cron": a.cron_expression,
            "origin": a.origin.value,
            "runs": a.run_count,
            "last_summary": a.last_result_summary,
            "deliver_to": a.deliver_to,
        }
        for a in rows
    ]
    return json.dumps({"routines": payload}, ensure_ascii=False)


@tool(
    name="edit_automation",
    description="Modify a routine — field one of: name, description, schedule_natural, cron_expression, "
    "deliver_to, priority, context, self_destruct_condition, tools_hint (JSON array string). "
    "For deliver_to, use telegram:<id>, discord:<id>, or a bare numeric id while in that platform's chat.",
)
async def edit_automation(automation_id: str, field: str, value: str) -> str:
    ctx = require_tool_runtime()
    ent = ctx.entity
    eng = _engine(ent)
    f = (field or "").strip().lower()
    allowed = {
        "name",
        "description",
        "schedule_natural",
        "cron_expression",
        "deliver_to",
        "priority",
        "context",
        "self_destruct_condition",
        "tools_hint",
    }
    if f not in allowed:
        return json.dumps({"ok": False, "error": f"unknown field: {field}"})
    kwargs: dict = {f: value}
    if f == "schedule_natural":
        try:
            kwargs["cron_expression"] = parse_schedule(value)
        except ScheduleParseError as e:
            return json.dumps({"ok": False, "error": str(e)})
    if f == "deliver_to":
        from ngram.presence.automations.engine import _parse_deliver_to

        resolved = (value or "").strip()
        if resolved:
            resolved = _qualify_deliver_to_if_bare_id(resolved, ctx)
        kwargs["deliver_to"] = resolved
        plat, tgt = _parse_deliver_to(resolved or None)
        kwargs["deliver_platform"] = plat
    if f == "tools_hint":
        try:
            kwargs["tools_hint"] = json.loads(value or "[]")
        except json.JSONDecodeError:
            return json.dumps({"ok": False, "error": "tools_hint must be JSON array"})
    try:
        await ent.store.update_automation(automation_id.strip(), **kwargs)
        if eng:
            await eng.reschedule(automation_id.strip())
        return json.dumps({"ok": True}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})


@tool(
    name="toggle_automation",
    description="Enable or disable a routine.",
)
async def toggle_automation(automation_id: str, enabled: bool) -> str:
    ctx = require_tool_runtime()
    ent = ctx.entity
    eng = _engine(ent)
    aid = (automation_id or "").strip()
    await ent.store.update_automation(aid, enabled=bool(enabled))
    if eng:
        await eng.reschedule(aid)
    return json.dumps({"ok": True, "id": aid, "enabled": bool(enabled)}, ensure_ascii=False)


@tool(
    name="delete_automation",
    description="Permanently remove a routine.",
)
async def delete_automation(automation_id: str) -> str:
    ctx = require_tool_runtime()
    ent = ctx.entity
    eng = _engine(ent)
    aid = (automation_id or "").strip()
    if eng:
        await eng.unregister(aid)
    await ent.store.delete_automation(aid)
    return json.dumps({"ok": True, "id": aid}, ensure_ascii=False)


@tool(
    name="run_automation_now",
    description="Run one of your routines immediately.",
)
async def run_automation_now(automation_id: str) -> str:
    ctx = require_tool_runtime()
    ent = ctx.entity
    eng = _engine(ent)
    if eng is None:
        return json.dumps({"ok": False, "error": "automation engine not running (start via ngram run)"})
    aid = (automation_id or "").strip()
    try:
        out = await eng.execute(aid)
        return json.dumps({"ok": True, **out}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})
