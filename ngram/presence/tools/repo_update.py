"""Agent tool: sync the ngram install with the public GitHub repository."""

from __future__ import annotations

import asyncio
import json

from ngram.presence.tools.execution_rpc import (
    self_update_host_permitted,
    self_update_tool_block_message,
)
from ngram.presence.tools.registry import tool
from ngram.presence.tools.runtime import require_tool_runtime
from ngram.utils.self_update import perform_self_update


@tool(
    name="update_ngram_from_upstream",
    description=(
        "Update this ngram installation from the public GitHub repository "
        "(git fast-forward when running from a clone, otherwise pip install --upgrade from git). "
        "Works on local installs, Railway workers, and hybrid home runners (remote inference). "
        "Restart long-running workers after updating."
    ),
)
async def update_ngram_from_upstream(reinstall_with_pip: bool = True) -> str:
    ctx = require_tool_runtime()
    if not self_update_host_permitted(ctx.entity):
        return json.dumps({"error": self_update_tool_block_message(ctx.entity)})

    lines: list[str] = []

    def _log(msg: str) -> None:
        lines.append(msg)

    result = await asyncio.to_thread(
        perform_self_update,
        pip_reinstall=reinstall_with_pip,
        dry_run=False,
        log=_log,
    )
    if lines:
        result = {**result, "log": lines}
    return json.dumps(result, ensure_ascii=False)
