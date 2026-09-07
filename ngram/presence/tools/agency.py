"""Agency primitives — tools that give the model control over its own cognitive process.

These tools shape the model's internal flow: private reasoning, explicit turn
termination, temporal patience, and mid-turn communication. The model decides
when to think, when to speak, when to wait, and when it's done.
"""

from __future__ import annotations

import asyncio
import json

from ngram.cognition import gemma
from ngram.identity.voice import strip_html_layout_leaks
from ngram.presence.tools.registry import tool
from ngram.presence.tools.runtime import get_tool_runtime

# Mid-turn outbound cap: say() plus embodied stream segments share _messages_sent.
DEFAULT_SAY_BUDGET_PER_TURN = 12


@tool(
    "think",
    "Record a private thought. Nobody sees this. Use it to reason about what "
    "you observe, plan your next move, or process before acting. Think generously.",
)
async def think(thought: str) -> str:
    ctx = get_tool_runtime()
    if ctx.state is not None:
        ctx.state.setdefault("private_thoughts", []).append(thought)
    return "[thought recorded]"


@tool(
    "end_wake_session",
    "End the sustained autonomous wake — you are done for this whole spell, not just the current round. "
    "Multi-round wakes use a max-round ceiling, not a quota: one round is fine; call this when finished "
    "so no optional continuations run. Only meaningful during multi-round autonomous sessions. "
    "After this round completes, no further wake continuations will run.",
)
async def end_wake_session(reason: str = "") -> str:
    ctx = get_tool_runtime()
    if ctx.state is not None:
        ctx.state["_wake_session_done"] = True
    r = (reason or "").strip()
    return "[wake session end requested]" + (f" ({r})" if r else "")


@tool(
    "end_turn",
    "You're done with this turn. Optionally record your mood and a parting "
    "thought. Call this after any say() lines you need (you can send several "
    "short updates in one turn). If you truly have nothing to add, silence is valid.",
)
async def end_turn(mood: str = "", thought: str = "") -> str:
    ctx = get_tool_runtime()
    if ctx.state is not None:
        ctx.state["_end_turn"] = True
        if mood:
            ctx.state["_end_turn_mood"] = mood
        if thought:
            ctx.state["_end_turn_thought"] = thought
    return "[turn ended]"


@tool(
    "say",
    "Send a message to the user right now, mid-turn. The turn continues after. "
    "Use when you have something to share before you're done working — don't "
    "bundle everything into one response. Talk while you work, like a person texting. "
    "CRITICAL: Do NOT write 'end_turn()' inside the message text itself. To end the turn, you must make a separate call to the end_turn tool.",
)
async def say(message: str) -> str:
    ctx = get_tool_runtime()
    platform = ctx.platform
    inp = ctx.inp
    if platform is None or inp is None:
        return "[no active chat to send to]"
    import re
    has_end_turn_leak = bool(re.search(r"<?end_turn\(\)>?|\[end_turn\]", message, flags=re.IGNORECASE))

    text = gemma.strip_leaked_control_tokens(
        strip_html_layout_leaks((message or "").strip()),
    )
    if not text:
        return "[empty message, not sent]"
    count = 0
    if ctx.state is not None:
        sent_messages = ctx.state.get("_sent_messages")
        if isinstance(sent_messages, list) and any(
            str(previous).strip() == text for previous in sent_messages
        ):
            return "[duplicate message suppressed]"
        count = ctx.state.get("_messages_sent", 0)
        budget = int(ctx.state.get("_message_budget", DEFAULT_SAY_BUDGET_PER_TURN))
        if count >= budget:
            return (
                f"[you've already sent {count} messages this cycle — save it "
                "for next time. you can still think, observe, or end_turn.]"
            )
    try:
        await platform.send_message(inp.channel, text)
    except Exception as e:
        return f"[send failed: {e}]"
    if ctx.state is not None:
        ctx.state["_messages_sent"] = count + 1
        ctx.state.setdefault("_sent_messages", []).append(text)

    ret_msg = "[sent]"
    if has_end_turn_leak:
        if ctx.state is not None:
            ctx.state["_end_turn"] = True
        ret_msg += " [turn ended]"
    return ret_msg


@tool(
    "wait",
    "Pause before your next action. Use after sending a message or running a "
    "command when you want to let things settle before deciding your next move.",
)
async def wait(seconds: int = 3) -> str:
    secs = max(1, min(15, int(seconds)))
    await asyncio.sleep(secs)
    return f"[waited {secs}s]"


@tool(
    "observe",
    "Look at recent messages in a channel you're present in. Use to see "
    "what people have been talking about. Defaults to the last active channel.",
)
async def observe(channel: str = "", limit: int = 15) -> str:
    ctx = get_tool_runtime()
    platform = ctx.platform
    if platform is None:
        return "[no active platform to observe]"
    ch = (channel or "").strip()
    if not ch and ctx.inp:
        ch = ctx.inp.channel
    if not ch:
        return "[no channel specified]"
    lim = max(1, min(50, int(limit)))
    try:
        msgs = await platform.fetch_recent_messages(ch, lim)
    except Exception as e:
        return f"[observe failed: {e}]"
    if not msgs:
        return f"[{ch} — no recent messages]"
    lines: list[str] = []
    for m in msgs:
        ts = m.get("timestamp", "")
        who = m.get("sender", "?")
        content = m.get("content", "")
        prefix = f"[{ts}] " if ts else ""
        lines.append(f"{prefix}{who}: {content}")
    return f"[{ch} — {len(msgs)} messages]\n" + "\n".join(lines)


@tool(
    "compact_context",
    "Force a context compaction/summarization pass now so conversation continuity is preserved "
    "while reducing prompt size when context is near full.",
)
async def compact_context(aggressive: bool = False, passes: int = 1) -> str:
    ctx = get_tool_runtime()
    if ctx is None or getattr(ctx, "entity", None) is None:
        return json.dumps({"ok": False, "error": "no active entity runtime"})
    entity = ctx.entity
    compact = getattr(entity, "compact_context_now", None)
    if not callable(compact):
        return json.dumps({"ok": False, "error": "entity does not support manual compaction"})
    result = await compact(aggressive=bool(aggressive), passes=max(1, min(int(passes), 6)))
    return json.dumps(result)
