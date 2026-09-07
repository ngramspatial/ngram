"""Long-horizon project tools."""

from __future__ import annotations

import json

from ngram.presence.tools.registry import tool
from ngram.presence.tools.runtime import require_tool_runtime


def _split_csv(raw: str) -> list[str]:
    return [part.strip() for part in str(raw or "").split(",") if part.strip()]


@tool(
    name="create_project",
    description="Create a long-horizon project you want to keep alive across sessions.",
)
async def create_project(
    title: str,
    summary: str,
    why_it_matters: str = "",
    next_steps: str = "",
    related_people: str = "",
    tags: str = "",
) -> str:
    ctx = require_tool_runtime()
    ent = ctx.entity
    try:
        row = await ent.projects.create_project(
            title,
            summary,
            why_it_matters=why_it_matters,
            next_steps=_split_csv(next_steps),
            related_people=_split_csv(related_people),
            tags=_split_csv(tags),
        )
        await ent._refresh_self_model_snapshot(note=f"created project: {row.title}")
        return json.dumps({"ok": True, "project": row.__dict__}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})


@tool(
    name="list_projects",
    description="List your ongoing projects and long-running threads.",
)
async def list_projects(status: str = "", limit: int = 10) -> str:
    ctx = require_tool_runtime()
    ent = ctx.entity
    k = max(1, min(20, int(limit or 10)))
    want = (status or "").strip().lower()
    rows = await ent.projects.list_projects()
    if want:
        rows = [row for row in rows if row.status.lower() == want]
    return json.dumps({"projects": [row.__dict__ for row in rows[:k]]}, ensure_ascii=False)


@tool(
    name="update_project",
    description="Update the state of one long-horizon project — summary, status, next steps, or why it matters.",
)
async def update_project(
    project_id: str,
    summary: str = "",
    status: str = "",
    why_it_matters: str = "",
    next_steps: str = "",
    related_people: str = "",
    tags: str = "",
    last_activity: str = "",
) -> str:
    ctx = require_tool_runtime()
    ent = ctx.entity
    try:
        row = await ent.projects.update_project(
            project_id,
            summary=summary if summary.strip() else None,
            status=status if status.strip() else None,
            why_it_matters=why_it_matters if why_it_matters.strip() else None,
            next_steps=_split_csv(next_steps) if next_steps.strip() else None,
            related_people=_split_csv(related_people) if related_people.strip() else None,
            tags=_split_csv(tags) if tags.strip() else None,
            last_activity=last_activity if last_activity.strip() else None,
        )
        await ent._refresh_self_model_snapshot(note=f"updated project: {row.title}")
        return json.dumps({"ok": True, "project": row.__dict__}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})
