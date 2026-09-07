"""Procedural memory tools ("skills")."""

from __future__ import annotations

import json

from ngram.presence.tools.registry import tool
from ngram.presence.tools.runtime import require_tool_runtime


@tool(
    name="list_skills",
    description="List or search your procedural memory — reusable know-how you have learned before.",
)
async def list_skills(query: str = "", limit: int = 10) -> str:
    ctx = require_tool_runtime()
    ent = ctx.entity
    k = max(1, min(20, int(limit or 10)))
    if (query or "").strip():
        rows = await ent.procedural.list_skills()
        q = query.strip().lower()
        filt = [
            {
                "name": row.name,
                "slug": row.slug,
                "snippet": row.content[:280],
            }
            for row in rows
            if q in row.slug.lower() or q in row.content.lower()
        ][:k]
        return json.dumps({"skills": filt}, ensure_ascii=False)
    rows = await ent.procedural.list_skills()
    payload = [{"name": row.name, "slug": row.slug, "snippet": row.content[:180]} for row in rows[:k]]
    return json.dumps({"skills": payload}, ensure_ascii=False)


@tool(
    name="read_skill",
    description="Read one skill from your procedural memory by name.",
)
async def read_skill(name: str) -> str:
    ctx = require_tool_runtime()
    ent = ctx.entity
    row = await ent.procedural.read_skill(name)
    if row is None:
        return json.dumps({"ok": False, "error": f"unknown skill: {name}"})
    return json.dumps(
        {"ok": True, "name": row.name, "slug": row.slug, "content": row.content},
        ensure_ascii=False,
    )


@tool(
    name="update_skill",
    description="Create or update a skill in your procedural memory when you discover a repeatable way of doing something.",
)
async def update_skill(name: str, content: str) -> str:
    ctx = require_tool_runtime()
    ent = ctx.entity
    try:
        row = await ent.procedural.upsert_skill(name, content)
        await ent._refresh_self_model_snapshot(note=f"updated procedural memory: {row.name}")
        return json.dumps({"ok": True, "name": row.name, "slug": row.slug}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})
