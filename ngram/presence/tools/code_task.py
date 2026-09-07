"""Bounded multi-phase coding runs with a persisted markdown task record (workspace)."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from ngram.models import Input
from ngram.presence.tools.delegation import _DEFAULT_ALLOW
from ngram.presence.tools.execution_rpc import get_execution_client
from ngram.presence.tools.registry import tool
from ngram.presence.tools.runtime import require_tool_runtime

_TASK_DIR = "code_tasks"

# Delegation base + shell/code + execution checkpoints (coding workflows).
_EXTRA = frozenset(
    {
        "run_command",
        "run_background",
        "check_process",
        "kill_process",
        "execute_python",
        "execute_javascript",
        "get_execution_context",
        "list_checkpoints",
        "rollback_checkpoint",
    }
)


def _slug_objective(text: str, max_len: int = 40) -> str:
    t = (text or "").lower().strip()
    t = re.sub(r"[^a-z0-9]+", "-", t)
    t = t.strip("-")[:max_len].strip("-")
    return t or "task"


def _normalize_task_path(rel: str, *, slug: str) -> str:
    """Force paths under code_tasks/; no traversal."""
    raw = (rel or "").strip().replace("\\", "/")
    if not raw or ".." in raw:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        return f"{_TASK_DIR}/{ts}_{slug}.md"
    if raw.startswith("/"):
        raw = raw.lstrip("/")
    if not raw.startswith(f"{_TASK_DIR}/"):
        raw = f"{_TASK_DIR}/{raw.lstrip('./')}"
    return raw


def _initial_markdown(*, objective: str, rel_path: str, max_phases: int, steps_per_phase: int) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return (
        f"# Code task\n\n"
        f"- **Created:** {now}\n"
        f"- **Task record:** `{rel_path}`\n"
        f"- **Phases planned:** {max_phases} (≈{steps_per_phase} tool steps each)\n\n"
        f"## Objective\n\n{objective.strip()}\n\n"
        f"## Status\n\nIN_PROGRESS\n\n"
        f"## Phase log\n\n"
        f"(Append dated notes as you work — decisions, files touched, tests, blockers.)\n\n"
    )


async def _read_tail(client: object, path: str, max_chars: int = 6000) -> str:
    res = await client.call("read_file", {"path": path, "max_bytes": 1_048_576})
    if not res.get("ok"):
        return ""
    body = str(res.get("content") or "")
    if len(body) <= max_chars:
        return body
    return body[-max_chars:]


@tool(
    name="code_task_session",
    description=(
        "Run a **bounded multi-phase coding session** with a markdown **task record** "
        f"under `{_TASK_DIR}/` (status, decisions, files, blockers). "
        "Each phase is a separate tool-budgeted run — use for implement → test → refine. "
        "While working, use **say()** for short Telegram/Discord updates so the user sees progress "
        "(the phase closing text is not mirrored to chat). "
        "Does not nest inside another code_task_session."
    ),
)
async def code_task_session(
    objective: str,
    max_phases: int = 3,
    steps_per_phase: int = 14,
    task_record_path: str = "",
) -> str:
    ctx = require_tool_runtime()
    ent = ctx.entity
    inp = ctx.inp

    if getattr(ent, "_code_task_session_depth", 0) > 0:
        return json.dumps({"error": "nested code_task_session is not allowed"}, ensure_ascii=False)

    obj = (objective or "").strip()
    if not obj:
        return json.dumps({"error": "objective is empty"}, ensure_ascii=False)

    phases = max(1, min(8, int(max_phases or 3)))
    steps = max(5, min(25, int(steps_per_phase or 14)))

    slug = _slug_objective(obj)
    rel = _normalize_task_path(task_record_path, slug=slug)

    client = get_execution_client()
    init_body = _initial_markdown(
        objective=obj,
        rel_path=rel,
        max_phases=phases,
        steps_per_phase=steps,
    )
    w = await client.call("write_file", {"path": rel, "content": init_body})
    if not w.get("ok"):
        return json.dumps(
            {"error": w.get("error") or "could not create task record"},
            ensure_ascii=False,
        )

    names = set(_DEFAULT_ALLOW) | set(_EXTRA)
    names.update({"think", "end_turn", "say"})
    for x in (
        "code_task_session",
        "delegate_task",
        "ask_user",
        "send_dm",
        "send_message_to",
    ):
        names.discard(x)

    sub = ent.tools.subset(names)
    if len(sub.openai_tools()) < 2:
        return json.dumps({"error": "code_task tool subset could not be built."}, ensure_ascii=False)

    old_tools = ent.tools
    old_pf = ent.current_platform
    ent.tools = sub
    ent._code_task_session_depth = getattr(ent, "_code_task_session_depth", 0) + 1

    plat_name = (inp.platform or "").strip().lower() if inp else ""
    reply_pf = None
    if plat_name in ("telegram", "discord"):
        reply_pf = ent._platforms.get(plat_name)

    phase_summaries: list[dict[str, str]] = []
    try:
        for phase_idx in range(phases):
            pnum = phase_idx + 1
            tail = await _read_tail(client, rel)
            tail_note = ""
            if tail.strip():
                tail_note = (
                    "\n\n---\n**Latest task record (tail — full file at `" + rel + "`):**\n\n"
                    "```\n"
                    + tail[-8000:]
                    + "\n```\n"
                )

            phase_user = (
                f"## Code task — phase {pnum}/{phases}\n\n"
                f"**Task record (read and keep updated with `append_file` / `write_file`):** `{rel}`\n\n"
                f"**Objective:**\n{obj}\n"
                f"{tail_note}\n"
                f"**Instructions:**\n"
                f"- Work toward the objective using your tools. Prefer small verifiable steps.\n"
                f"- Append dated entries under `## Phase log` in `{rel}`: what you did, paths touched, "
                f"commands/tests, open questions.\n"
                f"- Under `## Status`, set `COMPLETE` only when the objective is fully satisfied; "
                f"otherwise leave `IN_PROGRESS` and list remaining work.\n"
            )
            if reply_pf is not None:
                phase_user += (
                    "- **say()** occasionally: one or two short lines to the user on what's happening "
                    "(this phase, blockers, wins) — not a full essay.\n"
                )
            phase_user += f"- This is phase {pnum} of {phases} (≈{steps} tool steps this phase). "
            if pnum < phases:
                phase_user += (
                    "If more work remains after this phase, leave a clear handoff in the task record; "
                    "the next phase will read it.\n"
                )
            else:
                phase_user += "This is the **last** phase — aim to finish or document what is left.\n"

            meta = {
                "code_task": True,
                "code_task_max_steps": steps,
            }
            d_inp = Input(
                text=phase_user,
                person_id=(inp.person_id if inp else "") or "code_task",
                person_name=(inp.person_name if inp else "") or "code_task",
                channel=(inp.channel if inp else "") or "code_task",
                platform="code_task",
                metadata=meta,
            )

            reply, _reflex = await ent.perceive(
                d_inp,
                reply_platform=reply_pf,
                routine_history=False,
                record_user_message=False,
                skip_episode=True,
                skip_relational_upsert=True,
                skip_drive_interaction=True,
                preserve_conversation_route=True,
                meaningful_override=False,
            )
            excerpt = (reply or "").strip()[:12000]
            phase_summaries.append({"phase": str(pnum), "summary": excerpt})

            append_md = (
                f"\n\n---\n### Phase {pnum} closed ({datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')})\n\n"
                f"{excerpt}\n"
            )
            ap = await client.call("append_file", {"path": rel, "content": append_md})
            if not ap.get("ok"):
                phase_summaries[-1]["append_error"] = str(ap.get("error") or "append failed")

            status_tail = await _read_tail(client, rel, max_chars=4000)
            # Stop early if the task record marks completion under ## Status
            if re.search(r"(?is)##\s*Status\s*\n+\s*COMPLETE\b", status_tail):
                break
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e), "task_record": rel}, ensure_ascii=False)
    finally:
        ent.tools = old_tools
        ent.current_platform = old_pf
        ent._code_task_session_depth = max(0, getattr(ent, "_code_task_session_depth", 1) - 1)

    return json.dumps(
        {
            "ok": True,
            "task_record": rel,
            "phases_run": len(phase_summaries),
            "phases": phase_summaries,
        },
        ensure_ascii=False,
    )
