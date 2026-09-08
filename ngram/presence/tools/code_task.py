"""Tool interface for durable background coding goals."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone

from ngram.presence.tools.registry import tool
from ngram.presence.tools.runtime import require_tool_runtime

_TASK_DIR = "code_tasks"


def _slug_objective(text: str, max_len: int = 40) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:max_len].rstrip("-") or "task"


def _normalize_task_path(rel: str, *, slug: str) -> str:
    raw = (rel or "").strip().replace("\\", "/").lstrip("/")
    if not raw or ".." in raw or ":" in raw:
        return f"{_TASK_DIR}/{datetime.now(timezone.utc):%Y%m%d-%H%M%S}_{slug}_{uuid.uuid4().hex[:8]}.md"
    if not raw.startswith(f"{_TASK_DIR}/"):
        raw = f"{_TASK_DIR}/{raw.lstrip('./')}"
    return raw


def manager_for(entity):
    from ngram.presence.code_goals import CodeTaskManager

    manager = getattr(entity, "_code_task_manager", None)
    if manager is None:
        manager = CodeTaskManager(entity)
        entity._code_task_manager = manager
    return manager


@tool("code_task_session", "Launch a durable background coding goal and return its task_id immediately. Supply a self-contained objective, repository context, success criteria, and authorization constraints. Loops through implementation and fresh verification until complete, blocked, cancelled, or paused for budget. Survives browser disconnects; saved goals recover on worker startup. max_phases=0 means no phase-count limit; runtime defaults to six hours. Use code_task_status/resume/cancel to manage it. Never poll in a loop or wait inside the launching chat turn.")
async def code_task_session(
    objective: str, max_phases: int = 0, steps_per_phase: int = 24,
    task_record_path: str = "", success_criteria: str = "", max_runtime_seconds: int = 21600,
) -> str:
    ctx = require_tool_runtime()
    if ctx.inp and ctx.inp.platform in {"code_task", "delegation"}:
        return json.dumps({"ok": False, "error": "nested coding goals are not allowed"})
    return json.dumps(await manager_for(ctx.entity).submit(
        objective, ctx.inp, max_phases=max_phases, steps_per_phase=steps_per_phase,
        task_record_path=task_record_path, success_criteria=success_criteria,
        max_runtime_seconds=max_runtime_seconds,
    ), ensure_ascii=False)


@tool("code_task_status", "Read coding goal status, progress, remaining work and observed evidence. Omit task_id to list goals. A paused/blocked/failed goal is incomplete.")
async def code_task_status(task_id: str = "") -> str:
    return json.dumps(manager_for(require_tool_runtime().entity).status(task_id), ensure_ascii=False)


@tool("code_task_resume", "Resume an incomplete coding goal from saved state. Supply new user guidance or a resolved blocker in instructions. Adds runtime allowance; preserves the original goal and previous work.")
async def code_task_resume(task_id: str, instructions: str = "", additional_seconds: int = 21600) -> str:
    return json.dumps(await manager_for(require_tool_runtime().entity).resume(
        task_id, instructions, additional_seconds,
    ), ensure_ascii=False)


@tool("code_task_cancel", "Cancel a coding goal. Stops scheduling new work; an already executing tool is allowed to finish. Saved work and evidence remain available.")
async def code_task_cancel(task_id: str) -> str:
    return json.dumps(await manager_for(require_tool_runtime().entity).cancel(task_id), ensure_ascii=False)
