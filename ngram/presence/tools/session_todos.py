"""Short-horizon session todo list (JSON on disk), distinct from project ledger."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any

from ngram.presence.tools.registry import tool
from ngram.presence.tools.runtime import require_tool_runtime

_LOCKS: dict[str, asyncio.Lock] = {}


def _lock_for(path: str) -> asyncio.Lock:
    key = str(Path(path).resolve())
    if key not in _LOCKS:
        _LOCKS[key] = asyncio.Lock()
    return _LOCKS[key]


async def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"items": []}
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
        data = json.loads(raw)
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            return data
    except Exception:
        pass
    return {"items": []}


async def _save(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2)

    def _w() -> None:
        path.write_text(text + "\n", encoding="utf-8")

    await asyncio.get_event_loop().run_in_executor(None, _w)


@tool(
    name="todo_add",
    description=(
        "Add a short-term task for the current work session (not a long-horizon project). "
        "Use create_project for multi-day threads; use this for a checklist you will clear soon."
    ),
)
async def todo_add(text: str) -> str:
    ctx = require_tool_runtime()
    ent = ctx.entity
    t = (text or "").strip()
    if not t:
        return json.dumps({"error": "text is empty"}, ensure_ascii=False)
    path = Path(ent.config.session_todos_path())
    async with _lock_for(str(path)):
        data = await _load(path)
        items = data.setdefault("items", [])
        tid = uuid.uuid4().hex[:12]
        items.append(
            {
                "id": tid,
                "text": t[:2000],
                "done": False,
                "created_at": time.time(),
            }
        )
        await _save(path, data)
    return json.dumps({"ok": True, "id": tid, "text": t}, ensure_ascii=False)


@tool(
    name="todo_list",
    description="List session todo items (short-horizon checklist).",
)
async def todo_list(include_done: bool = False) -> str:
    ctx = require_tool_runtime()
    path = Path(ctx.entity.config.session_todos_path())
    async with _lock_for(str(path)):
        data = await _load(path)
    items = [x for x in data.get("items", []) if isinstance(x, dict)]
    if not include_done:
        items = [x for x in items if not x.get("done")]
    return json.dumps({"items": items}, ensure_ascii=False)


@tool(
    name="todo_complete",
    description="Mark a session todo item done by id (from todo_list).",
)
async def todo_complete(item_id: str) -> str:
    ctx = require_tool_runtime()
    path = Path(ctx.entity.config.session_todos_path())
    iid = (item_id or "").strip()
    if not iid:
        return json.dumps({"error": "item_id required"}, ensure_ascii=False)
    async with _lock_for(str(path)):
        data = await _load(path)
        items = data.setdefault("items", [])
        for it in items:
            if isinstance(it, dict) and str(it.get("id")) == iid:
                it["done"] = True
                await _save(path, data)
                return json.dumps({"ok": True, "id": iid}, ensure_ascii=False)
    return json.dumps({"ok": False, "error": "id not found"}, ensure_ascii=False)


@tool(
    name="todo_remove",
    description="Remove a session todo item by id.",
)
async def todo_remove(item_id: str) -> str:
    ctx = require_tool_runtime()
    path = Path(ctx.entity.config.session_todos_path())
    iid = (item_id or "").strip()
    if not iid:
        return json.dumps({"error": "item_id required"}, ensure_ascii=False)
    async with _lock_for(str(path)):
        data = await _load(path)
        items = data.setdefault("items", [])
        new_items = [it for it in items if not (isinstance(it, dict) and str(it.get("id")) == iid)]
        if len(new_items) == len(items):
            return json.dumps({"ok": False, "error": "id not found"}, ensure_ascii=False)
        data["items"] = new_items
        await _save(path, data)
    return json.dumps({"ok": True, "id": iid}, ensure_ascii=False)
