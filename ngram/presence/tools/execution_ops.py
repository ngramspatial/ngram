"""Execution environment / rollback tools."""

from __future__ import annotations

import json

from ngram.presence.tools.execution_rpc import get_execution_client
from ngram.presence.tools.registry import tool


@tool(
    name="get_execution_context",
    description="Describe the current execution environment, workspace root, and backend.",
)
async def get_execution_context() -> str:
    client = get_execution_client()
    res = await client.call("get_execution_context", {})
    return json.dumps(res, ensure_ascii=False)


@tool(
    name="list_checkpoints",
    description="List recent workspace checkpoints created before file mutations.",
)
async def list_checkpoints(limit: int = 20) -> str:
    client = get_execution_client()
    res = await client.call("list_checkpoints", {"limit": max(1, min(int(limit or 20), 100))})
    return json.dumps(res, ensure_ascii=False)


@tool(
    name="rollback_checkpoint",
    description="Restore a file mutation checkpoint by id.",
)
async def rollback_checkpoint(checkpoint_id: str) -> str:
    client = get_execution_client()
    res = await client.call("rollback_checkpoint", {"checkpoint_id": (checkpoint_id or "").strip()})
    return json.dumps(res, ensure_ascii=False)
