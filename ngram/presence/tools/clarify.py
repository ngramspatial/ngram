"""Ask the human operator a clarifying question; next user message carries the answer."""

from __future__ import annotations

import json
import time
import uuid

from ngram.presence.tools.registry import tool
from ngram.presence.tools.runtime import require_tool_runtime


@tool(
    name="ask_user",
    description=(
        "Ask the user a clarifying question. Your next turn should wait for their reply in chat — "
        "their following message will be labeled as answering this question. "
        "Optionally pass short choices (numbered automatically). Do not use for autonomous or delegation runs."
    ),
)
async def ask_user(
    question: str,
    choices: str = "",
    timeout_minutes: int = 60,
) -> str:
    ctx = require_tool_runtime()
    ent = ctx.entity
    inp = ctx.inp
    platform = ctx.platform
    if inp is None:
        return json.dumps({"error": "no active input context"}, ensure_ascii=False)
    if (inp.platform or "") in ("autonomous", "delegation", "automation", "code_task"):
        return json.dumps(
            {"error": "ask_user is not available during autonomous/delegation runs."},
            ensure_ascii=False,
        )
    q = (question or "").strip()
    if not q:
        return json.dumps({"error": "question is empty"}, ensure_ascii=False)

    opts: list[str] = []
    for part in (choices or "").split("|"):
        s = part.strip()
        if s:
            opts.append(s[:500])
    cid = uuid.uuid4().hex[:12]
    ent._pending_clarification = {
        "id": cid,
        "channel": str(inp.channel or ""),
        "platform": str(inp.platform or ""),
        "question": q[:4000],
        "choices": opts[:12],
        "created_at": time.time(),
        "timeout_minutes": max(1, min(24 * 60, int(timeout_minutes or 60))),
    }

    lines = [q]
    if opts:
        lines.append("")
        for i, o in enumerate(opts, start=1):
            lines.append(f"{i}. {o}")
    body = "\n".join(lines)

    if platform is not None:
        try:
            await platform.send_message(inp.channel, body)
        except Exception as e:
            ent._pending_clarification = None
            return json.dumps({"error": f"could not send question: {e}"}, ensure_ascii=False)

    return json.dumps(
        {
            "ok": True,
            "clarification_id": cid,
            "status": "pending",
            "echo_for_user": body if platform is None else "",
            "hint": (
                "When the user replies, their next message will be marked as the answer to this question."
                if platform is not None
                else "Echo echo_for_user to the user if no message was sent automatically."
            ),
        },
        ensure_ascii=False,
    )
