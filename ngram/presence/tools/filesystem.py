"""Filesystem tools: read-only and write paths via the shared execution backend (local or RPC)."""

from __future__ import annotations

import json

from ngram.presence.tools.execution_rpc import (
    get_execution_client,
    local_tool_block_message,
    read_only_workspace_fs_allowed,
)
from ngram.presence.tools.registry import tool
from ngram.presence.tools.runtime import require_tool_runtime


@tool(
    name="read_file",
    description=(
        "Read a text or UTF-8 file under the entity workspace / execution host (read-only). "
        "Uses the same backend as write_file (RPC when configured). Only open paths you were given.\n\n"
        "When the user asks for a specific line number or a small range, set start_line (1-based, first line is 1) "
        "and optionally end_line (inclusive). The tool returns lines prefixed with the line number — quote that "
        "exactly; do not guess or recount from a truncated read. Blank lines count as lines. Omit start_line to "
        "read from the start of the file up to max_bytes."
    ),
)
async def read_file(path: str, max_bytes: int = 48000, start_line: int = 0, end_line: int = 0) -> str:
    ctx = require_tool_runtime()
    if not read_only_workspace_fs_allowed(ctx.entity):
        return json.dumps({"error": local_tool_block_message(ctx.entity)})
    client = get_execution_client()
    payload: dict[str, int | str] = {
        "path": path or ".",
        "max_bytes": max(1024, min(max_bytes, 1_048_576)),
    }
    if start_line > 0:
        payload["start_line"] = int(start_line)
    if end_line > 0:
        payload["end_line"] = int(end_line)
    res = await client.call("read_file", payload)
    if res.get("ok"):
        return str(res.get("content") or "")
    return json.dumps({"error": res.get("error") or "read failed"})


@tool(
    name="list_directory",
    description=(
        "List files and folders under the entity workspace on the execution host (same as write_file / "
        "run_command). When the worker runs on Railway, this is the container; with NGRAM_EXECUTION_RPC_URL "
        "set from a hybrid laptop, it is the RPC host — not your dev PC unless allow_local is on."
    ),
)
async def list_directory(path: str = ".") -> str:
    ctx = require_tool_runtime()
    if not read_only_workspace_fs_allowed(ctx.entity):
        return json.dumps({"error": local_tool_block_message(ctx.entity)})
    client = get_execution_client()
    res = await client.call("list_directory", {"path": path or "."})
    if res.get("ok"):
        return json.dumps(
            {"path": str(res.get("path") or ""), "entries": res.get("entries") or []},
            ensure_ascii=False,
        )
    return json.dumps({"error": res.get("error") or "list failed"})


@tool(
    name="search_files",
    description="Search for files matching a pattern under the entity workspace / execution host.",
)
async def search_files(directory: str, pattern: str) -> str:
    ctx = require_tool_runtime()
    if not read_only_workspace_fs_allowed(ctx.entity):
        return json.dumps({"error": local_tool_block_message(ctx.entity)})
    client = get_execution_client()
    res = await client.call(
        "search_files",
        {"directory": directory or ".", "pattern": (pattern or "").strip() or "*"},
    )
    if res.get("ok"):
        return json.dumps(
            {
                "directory": str(res.get("directory") or ""),
                "pattern": str(res.get("pattern") or ""),
                "matches": res.get("matches") or [],
            },
            ensure_ascii=False,
        )
    return json.dumps({"error": res.get("error") or "search failed"})


@tool(
    name="write_file",
    description="Create or overwrite a file.",
)
async def write_file(path: str, content: str) -> str:
    client = get_execution_client()
    res = await client.call("write_file", {"path": path, "content": content})
    return json.dumps(res, ensure_ascii=False)


@tool(
    name="append_file",
    description="Add content to the end of an existing file.",
)
async def append_file(path: str, content: str) -> str:
    client = get_execution_client()
    res = await client.call("append_file", {"path": path, "content": content})
    return json.dumps(res, ensure_ascii=False)


async def read_file_safe(path: str, max_bytes: int = 32000) -> str:
    """Backward-compatible name for tests and legacy callers."""
    return await read_file(path, max_bytes=max_bytes)
