"""Apply unified diffs to workspace files (read → patch → write via execution backend)."""

from __future__ import annotations

import json

from ngram.presence.tools.execution_rpc import get_execution_client
from ngram.presence.tools.registry import tool
from ngram.presence.tools.runtime import require_tool_runtime
from ngram.utils.unified_diff_apply import apply_unified_diff, strip_diff_headers


@tool(
    name="apply_patch",
    description=(
        "Apply a unified diff patch to a text file under your workspace (same paths as read_file/write_file). "
        "The patch must use @@ hunk headers (standard git unified diff). "
        "Use for precise edits; prefer this over rewriting whole files when changing a few lines."
    ),
)
async def apply_patch(path: str, unified_diff: str) -> str:
    require_tool_runtime()
    p = (path or "").strip()
    diff = strip_diff_headers(unified_diff or "")
    if not p:
        return json.dumps({"error": "path is required"}, ensure_ascii=False)
    if not diff.strip():
        return json.dumps({"error": "unified_diff is empty"}, ensure_ascii=False)

    client = get_execution_client()
    read_res = await client.call("read_file", {"path": p, "max_bytes": 512_000})
    if not read_res.get("ok"):
        return json.dumps({"error": read_res.get("error") or "read failed"}, ensure_ascii=False)
    content = str(read_res.get("content") or "")

    try:
        new_content = apply_unified_diff(content, diff)
    except ValueError as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)

    wr = await client.call("write_file", {"path": p, "content": new_content})
    return json.dumps({"ok": bool(wr.get("ok")), **{k: v for k, v in wr.items() if k != "ok"}}, ensure_ascii=False)
