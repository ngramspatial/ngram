"""Introspect the entity's currently registered tools (names, descriptions, schemas)."""

from __future__ import annotations

import json
from typing import Any

from ngram.presence.tools.registry import tool
from ngram.presence.tools.runtime import require_tool_runtime


def _discovery_score(query: str, row: dict[str, Any]) -> float:
    q = (query or "").strip().lower()
    if not q:
        return 0.0
    name = str(row.get("name") or "").lower()
    desc = str(row.get("description") or "").lower()
    params = " ".join(str(p) for p in (row.get("parameters") or [])).lower()
    hay = f"{name} {desc} {params}"

    if name == q:
        return 1000.0
    if name.startswith(q):
        return 900.0
    if q in name:
        return 800.0
    if q in hay:
        return 500.0 + min(len(q), 40)
    tokens = [w for w in q.split() if len(w) >= 2]
    if not tokens:
        return 0.0
    hits = sum(1 for tok in tokens if tok in hay)
    return float(hits * 50)


@tool(
    name="search_tools",
    description=(
        "Search this entity's currently registered tools by keyword. "
        "Use when you are unsure what capabilities exist, after config changes, or when the user asks what you can do. "
        "Empty query lists tools alphabetically (truncated)."
    ),
)
async def search_tools(query: str = "", limit: int = 25) -> str:
    ctx = require_tool_runtime()
    registry = getattr(ctx.entity, "tools", None)
    if registry is None or not hasattr(registry, "tool_discovery_summaries"):
        return json.dumps({"ok": False, "error": "tool registry unavailable"})

    lim = max(1, min(int(limit or 25), 60))
    rows = registry.tool_discovery_summaries()
    q = (query or "").strip()

    if not q:
        slim = [
            {
                "name": r["name"],
                "description": (str(r.get("description") or ""))[:280],
                "parameters": r.get("parameters") or [],
            }
            for r in rows[:lim]
        ]
        return json.dumps(
            {
                "ok": True,
                "query": "",
                "total_registered": len(rows),
                "returned": len(slim),
                "tools": slim,
                "hint": "Pass a query to filter, or call describe_tool for full JSON Schema parameters.",
            },
            ensure_ascii=False,
        )

    scored: list[tuple[float, dict[str, Any]]] = []
    for r in rows:
        s = _discovery_score(q, r)
        if s > 0:
            scored.append((s, r))

    scored.sort(key=lambda x: (-x[0], x[1]["name"]))
    top = scored[:lim]
    tools_out: list[dict[str, Any]] = []
    for _s, r in top:
        tools_out.append(
            {
                "name": r["name"],
                "description": (str(r.get("description") or ""))[:400],
                "parameters": r.get("parameters") or [],
                "required": r.get("required") or [],
            }
        )

    return json.dumps(
        {
            "ok": True,
            "query": q,
            "total_registered": len(rows),
            "returned": len(tools_out),
            "tools": tools_out,
            "hint": "Call describe_tool with an exact tool name for the full parameter schema.",
        },
        ensure_ascii=False,
    )


@tool(
    name="describe_tool",
    description=(
        "Return the full definition for one registered tool: name, description, and JSON Schema parameters. "
        "Use after search_tools to learn exact argument names and types."
    ),
)
async def describe_tool(tool_name: str) -> str:
    raw = (tool_name or "").strip()
    if not raw:
        return json.dumps({"ok": False, "error": "tool_name is required"})

    ctx = require_tool_runtime()
    registry = getattr(ctx.entity, "tools", None)
    if registry is None or not hasattr(registry, "tool_discovery_detail"):
        return json.dumps({"ok": False, "error": "tool registry unavailable"})

    resolver = getattr(registry, "resolve_tool_name", None)
    suggester = getattr(registry, "suggest_tool_names", None)
    target = raw
    if callable(resolver):
        resolved = resolver(raw)
        if resolved:
            target = resolved

    detail = registry.tool_discovery_detail(target)
    if detail is None:
        sug: list[str] = []
        if callable(suggester):
            sug = suggester(raw, 8)
        return json.dumps(
            {
                "ok": False,
                "error": f"unknown tool: {raw}",
                "suggestions": sug,
            },
            ensure_ascii=False,
        )

    return json.dumps({"ok": True, "tool": detail}, ensure_ascii=False)
