"""Observation tools for autonomously watching files and logs."""

from __future__ import annotations

import json
import os
from pathlib import Path

from ngram.presence.tools.registry import tool
from ngram.presence.tools.runtime import require_tool_runtime
from ngram.presence.tools.execution_rpc import read_only_workspace_fs_allowed, local_tool_block_message

@tool(
    name="tail_file",
    description="Read the last N lines of a file. Very useful for checking logs or observing an actively writing file in an automation.",
)
async def tail_file(path: str, lines: int = 20) -> str:
    ctx = require_tool_runtime()
    if not read_only_workspace_fs_allowed(ctx.entity):
        return json.dumps({"error": local_tool_block_message(ctx.entity)})

    p = Path(path).expanduser()
    if not p.is_file():
        return json.dumps({"error": f"File not found: {path}"})

    num_lines = max(1, min(1000, int(lines)))

    try:
        # A simple tail implementation in Python without reading the whole file
        with open(p, "rb") as f:
            f.seek(0, os.SEEK_END)
            buffer = bytearray()
            pointer = f.tell()
            lines_found = 0

            # Read backwards chunk by chunk
            while pointer >= 0 and lines_found <= num_lines:
                f.seek(pointer)
                char = f.read(1)
                if char == b'\n':
                    lines_found += 1
                buffer.extend(char)
                pointer -= 1

            # Reverse the buffer and decode
            buffer.reverse()
            text = buffer.decode("utf-8", errors="replace")

            # If we hit the top of the file without finding enough newlines, just read everything
            if pointer < 0 and lines_found <= num_lines:
                f.seek(0)
                text = f.read().decode("utf-8", errors="replace")

            tail_lines = text.splitlines()[-num_lines:]
            return json.dumps({
                "path": str(p),
                "lines_returned": len(tail_lines),
                "content": "\n".join(tail_lines)
            }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"error": str(e)})

@tool(
    name="check_file_modified",
    description="Check the last modified time and size of a file. Use this to quickly see if a log or data file has changed since you last checked it.",
)
async def check_file_modified(path: str) -> str:
    ctx = require_tool_runtime()
    if not read_only_workspace_fs_allowed(ctx.entity):
        return json.dumps({"error": local_tool_block_message(ctx.entity)})

    p = Path(path).expanduser()
    if not p.exists():
        return json.dumps({"error": f"Path not found: {path}"})

    try:
        stat = p.stat()
        return json.dumps({
            "path": str(p),
            "is_file": p.is_file(),
            "size_bytes": stat.st_size,
            "last_modified_timestamp": stat.st_mtime
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)})
