"""Spawn a bounded sub-perceive with a subset of tools (no user-visible chat)."""

from __future__ import annotations

import json

from ngram.models import Input
from ngram.presence.tools.registry import tool
from ngram.presence.tools.runtime import require_tool_runtime


_DEFAULT_ALLOW = frozenset(
    {
        "think",
        "end_turn",
        "wait",
        "observe",
        "read_file",
        "list_directory",
        "search_files",
        "write_file",
        "append_file",
        "apply_patch",
        "search_web",
        "fetch_url",
        "search_past_conversations",
        "todo_add",
        "todo_list",
        "todo_complete",
        "todo_remove",
        "search_tools",
        "describe_tool",
        "get_current_time",
        "update_knowledge",
    }
)


@tool(
    name="delegate_task",
    description=(
        "Run a focused sub-task in isolation: same brain, but only a subset of tools and a tight step budget. "
        "Use for exploration that should not spam the user chat. Returns a text summary from the sub-run. "
        "Do not nest delegate_task inside delegate_task."
    ),
)
async def delegate_task(
    objective: str,
    tool_allowlist: str = "",
    max_tool_steps: int = 12,
) -> str:
    ctx = require_tool_runtime()
    ent = ctx.entity
    inp = ctx.inp
    if getattr(ent, "_delegate_depth", 0) > 0:
        return json.dumps({"error": "nested delegate_task is not allowed"}, ensure_ascii=False)

    obj = (objective or "").strip()
    if not obj:
        return json.dumps({"error": "objective is empty"}, ensure_ascii=False)

    raw = (tool_allowlist or "").strip()
    if raw:
        names = {x.strip() for x in raw.replace(",", " ").split() if x.strip()}
    else:
        names = set(_DEFAULT_ALLOW)

    # Ensure minimal agency
    names.add("think")
    names.add("end_turn")

    # Never delegate the delegator
    names.discard("delegate_task")
    names.discard("code_task_session")
    names.discard("ask_user")
    names.discard("say")

    sub = ent.tools.subset(names)
    if len(sub.openai_tools()) < 2:
        return json.dumps(
            {"error": "no matching tools in allowlist — check tool names."},
            ensure_ascii=False,
        )

    steps = max(3, min(25, int(max_tool_steps or 12)))

    meta = {
        "delegation": True,
        "delegation_max_steps": steps,
    }
    d_inp = Input(
        text=obj,
        person_id=(inp.person_id if inp else "") or "delegation",
        person_name=(inp.person_name if inp else "") or "delegation",
        channel=(inp.channel if inp else "") or "delegation",
        platform="delegation",
        metadata=meta,
    )

    old = ent.tools
    old_pf = ent.current_platform
    ent.tools = sub
    ent._delegate_depth = getattr(ent, "_delegate_depth", 0) + 1
    try:
        reply, _reflex = await ent.perceive(
            d_inp,
            reply_platform=None,
            routine_history=False,
            record_user_message=False,
            skip_episode=True,
            skip_relational_upsert=True,
            skip_drive_interaction=True,
            preserve_conversation_route=True,
            meaningful_override=False,
        )
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)
    finally:
        ent.tools = old
        ent.current_platform = old_pf
        ent._delegate_depth = max(0, getattr(ent, "_delegate_depth", 1) - 1)

    return json.dumps(
        {"ok": True, "summary": (reply or "")[:12000]},
        ensure_ascii=False,
    )
