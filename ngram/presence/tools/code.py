"""Dangerous code execution tools routed through execution backend."""

from __future__ import annotations

import json
from typing import Any

from ngram.presence.tools.execution_rpc import get_execution_client
from ngram.presence.tools.registry import tool
from ngram.presence.tools.runtime import require_tool_runtime


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = {**base}
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _code_timeout(requested_timeout: int) -> int:
    if requested_timeout:
        return max(1, int(requested_timeout))
    ctx = require_tool_runtime()
    entity = ctx.entity
    base = entity.config.harness.tools if isinstance(entity.config.harness.tools, dict) else {}
    over = entity.config.raw.get("tools") if isinstance(entity.config.raw, dict) else {}
    merged = _deep_merge(base, over if isinstance(over, dict) else {})
    code_cfg = merged.get("code") if isinstance(merged.get("code"), dict) else {}
    try:
        return max(1, int(code_cfg.get("timeout") or 30))
    except Exception:
        return 30


@tool(
    name="execute_python",
    description="Write and run Python code. Prototype ideas, process data, test things, build tools for yourself.",
)
async def execute_python(code: str, timeout: int = 0) -> str:
    src = (code or "").strip()
    if not src:
        return json.dumps({"error": "empty code"})
    to = _code_timeout(timeout)
    client = get_execution_client()
    res = await client.call("execute_python", {"code": src, "timeout": to})
    return json.dumps(res, ensure_ascii=False)


@tool(
    name="execute_javascript",
    description="Run JavaScript code via Node.js",
)
async def execute_javascript(code: str, timeout: int = 0) -> str:
    src = (code or "").strip()
    if not src:
        return json.dumps({"error": "empty code"})
    to = _code_timeout(timeout)
    client = get_execution_client()
    res = await client.call("execute_javascript", {"code": src, "timeout": to})
    return json.dumps(res, ensure_ascii=False)
