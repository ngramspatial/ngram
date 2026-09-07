"""Context compaction engine — Proactive context compression with structured summaries.

The legacy ``merge_rolling_summary`` (post-commit safety net) is preserved alongside the new
proactive compactor that fires *before* inference when context approaches the token limit.

Proactive compaction algorithm:
  1. Prune old tool results (cheap pre-pass, no LLM call)
  2. Protect head messages (first exchange) and tail (recent context by token budget)
  3. Summarize middle turns with a structured template (iteratively updated across compactions)
  4. Reassemble history as head + tail; summary stored in ``_history_rolling_summary``
"""

from __future__ import annotations

import json
import structlog
from typing import Any

from ngram.cognition import gemma
from ngram.inference import InferenceProvider

log = structlog.get_logger("ngram.cognition.history_compression")

_CHARS_PER_TOKEN = 4

_PRUNED_TOOL_PLACEHOLDER = "[Old tool output cleared to save context space]"

COMPACTION_SUMMARY_PREFIX = (
    "[CONTEXT COMPACTION] Earlier turns in this conversation were compacted. "
    "The summary below describes what happened. The current session state reflects "
    "that work (files may already be changed, decisions already made). Continue from "
    "where things left off — do not repeat work or reset with a generic greeting."
)

# --- legacy merge prompt (kept for the post-commit safety-net path) ---

_SYS = (
    "You maintain a compact rolling memory of a conversation. "
    "You receive (1) a prior summary, possibly empty, and (2) older transcript lines that no longer "
    "fit in the live chat window. Merge them: keep concrete facts, names, decisions, open questions, "
    "emotional beats, and what the user and assistant were working on. Drop filler and repetition. "
    "Write in first person as the assistant (I / we / they / the user). "
    "Output ONLY the updated summary — no title, no preamble, no quotes around it."
)

# --- structured compaction templates ---

_COMPACTION_SYS_FIRST = """\
Create a structured handoff summary so the conversation can continue seamlessly after \
earlier turns are compressed. Be specific — include file paths, command outputs, error \
messages, concrete values, names, dates, and emotional context rather than vague descriptions.

TURNS TO SUMMARIZE:
{content}

Use this structure:

## Goal
[What the user/entity is trying to accomplish]

## Constraints & Preferences
[User preferences, style, corrections, important instructions]

## Progress
### Done
[Completed work — specific file paths, commands run, results obtained]
### In Progress
[Work currently underway]
### Blocked
[Any blockers or issues encountered]

## Key Decisions
[Important decisions and why they were made]

## Emotional & Relational Context
[Tone of the conversation, rapport, mood, any emotional beats worth preserving]

## Critical Context
[Specific values, error messages, configuration details, or data that would be lost]

## Next Steps
[What needs to happen next]

Target ~{budget} tokens. Write only the summary body. No preamble or prefix."""

_COMPACTION_SYS_UPDATE = """\
You are updating a context compaction summary. A previous compaction produced the summary \
below. New conversation turns have occurred since then and need to be incorporated.

PREVIOUS SUMMARY:
{previous}

NEW TURNS TO INCORPORATE:
{content}

Update the summary using the same structure. PRESERVE all existing information that is \
still relevant. ADD new progress. Move items from "In Progress" to "Done" when completed. \
Remove information only if it is clearly obsolete.

## Goal
[Preserve from previous, update if goal evolved]

## Constraints & Preferences
[Accumulate across compactions]

## Progress
### Done
[Include specific file paths, commands run, results]
### In Progress
[Work currently underway]
### Blocked
[Blockers or issues]

## Key Decisions
[Accumulate across compactions]

## Emotional & Relational Context
[Current tone and rapport]

## Critical Context
[Values, errors, config that would be lost]

## Next Steps
[What needs to happen next]

Target ~{budget} tokens. Be specific. Write only the summary body. No preamble or prefix."""

_KNOWLEDGE_FLUSH_PROMPT = """\
You are reviewing conversation turns that are about to be compressed. Extract any \
durable facts worth remembering long-term — user preferences, corrections, stated facts, \
learned information, or recurring patterns. Do NOT include task-specific ephemera that \
will already be in the compaction summary.

TURNS TO REVIEW:
{content}

If there are facts worth persisting, return a JSON array of objects with "title" and "body" \
fields. Each becomes a ## section in knowledge.md. Use short, descriptive titles.

Example: [
  {{"title": "user preferences", "body": "Prefers dark mode. Uses vim keybindings."}},
  {{"title": "project context", "body": "Main repo is a Python FastAPI app at ~/projects/api."}}
]

If nothing is worth persisting, return an empty array: []

Return ONLY valid JSON — no markdown fences, no explanation."""


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------

def estimate_message_tokens(msg: dict[str, Any]) -> int:
    """Rough token estimate for a single message dict."""
    content = msg.get("content", "")
    if isinstance(content, list):
        content = gemma.stringify_content_blocks(content)
    tokens = max(1, len(str(content)) // _CHARS_PER_TOKEN) + 10
    for tc in msg.get("tool_calls") or []:
        if isinstance(tc, dict):
            args = tc.get("function", {}).get("arguments", "")
            tokens += max(1, len(str(args)) // _CHARS_PER_TOKEN)
    return tokens


def estimate_context_tokens(
    system_prompt: str,
    messages: list[dict[str, Any]],
    tools_block: str = "",
) -> int:
    """Rough total token estimate for a full inference call."""
    total = max(1, len(system_prompt) // _CHARS_PER_TOKEN)
    total += max(1, len(tools_block) // _CHARS_PER_TOKEN) if tools_block else 0
    for msg in messages:
        total += estimate_message_tokens(msg)
    return total


# ---------------------------------------------------------------------------
# Tool result pruning (cheap pre-pass, no LLM)
# ---------------------------------------------------------------------------

def prune_old_tool_results(
    messages: list[dict[str, Any]],
    protect_tail_count: int,
) -> tuple[list[dict[str, Any]], int]:
    """Replace verbose tool results outside the protected tail with a placeholder."""
    if not messages:
        return messages, 0
    result = [m.copy() for m in messages]
    pruned = 0
    boundary = max(0, len(result) - protect_tail_count)
    for i in range(boundary):
        msg = result[i]
        if msg.get("role") != "tool":
            continue
        content = msg.get("content", "")
        if not content or content == _PRUNED_TOOL_PLACEHOLDER:
            continue
        if len(content) > 200:
            result[i] = {**msg, "content": _PRUNED_TOOL_PLACEHOLDER}
            pruned += 1
    return result, pruned


# ---------------------------------------------------------------------------
# Boundary detection with tool-group alignment
# ---------------------------------------------------------------------------

def _align_boundary_forward(messages: list[dict[str, Any]], idx: int) -> int:
    """Push a compress-start boundary forward past orphan tool results."""
    while idx < len(messages) and messages[idx].get("role") == "tool":
        idx += 1
    return idx


def _align_boundary_backward(messages: list[dict[str, Any]], idx: int) -> int:
    """Pull a compress-end boundary backward to avoid splitting a tool_call/result group."""
    if idx <= 0 or idx >= len(messages):
        return idx
    check = idx - 1
    while check >= 0 and messages[check].get("role") == "tool":
        check -= 1
    if check >= 0 and messages[check].get("role") == "assistant" and messages[check].get("tool_calls"):
        idx = check
    return idx


def find_compaction_boundaries(
    messages: list[dict[str, Any]],
    head_n: int,
    tail_token_budget: int,
    min_tail_n: int,
) -> tuple[int, int]:
    """Return ``(compress_start, compress_end)`` indices into *messages*.

    Head messages ``[0..head_n)`` and tail messages ``[compress_end..len)`` are protected.
    The middle ``[compress_start..compress_end)`` is the region to summarize.
    """
    n = len(messages)
    compress_start = min(head_n, n)
    compress_start = _align_boundary_forward(messages, compress_start)

    accumulated = 0
    compress_end = n
    for i in range(n - 1, compress_start - 1, -1):
        msg_tok = estimate_message_tokens(messages[i])
        if accumulated + msg_tok > tail_token_budget and (n - i) >= min_tail_n:
            break
        accumulated += msg_tok
        compress_end = i

    fallback_cut = n - min_tail_n
    if compress_end > fallback_cut:
        compress_end = fallback_cut

    if compress_end <= compress_start:
        compress_end = fallback_cut

    compress_end = _align_boundary_backward(messages, compress_end)
    compress_end = max(compress_end, compress_start + 1)

    if compress_end > n:
        compress_end = n

    return compress_start, compress_end


# ---------------------------------------------------------------------------
# Serialization for the summarizer
# ---------------------------------------------------------------------------

def serialize_for_summary(turns: list[dict[str, Any]], per_msg_cap: int = 3000) -> str:
    """Serialize conversation turns into labeled text for the summarizer."""
    parts: list[str] = []
    for msg in turns:
        role = msg.get("role", "unknown")
        content = msg.get("content") or ""
        if isinstance(content, list):
            content = gemma.stringify_content_blocks(content)
        content = str(content).strip()

        if role == "tool":
            tool_id = msg.get("tool_call_id", "")
            name = msg.get("name", "")
            label = f"tool_result({name})" if name else f"tool_result({tool_id})"
            if len(content) > per_msg_cap:
                content = content[:2000] + "\n...[truncated]...\n" + content[-800:]
            parts.append(f"[{label}]: {content}")
            continue

        if role == "assistant":
            if len(content) > per_msg_cap:
                content = content[:2000] + "\n...[truncated]...\n" + content[-800:]
            tool_calls = msg.get("tool_calls") or []
            if tool_calls:
                tc_parts: list[str] = []
                for tc in tool_calls:
                    if isinstance(tc, dict):
                        fn = tc.get("function", {})
                        name = fn.get("name", "?")
                        args = fn.get("arguments", "")
                        if len(args) > 500:
                            args = args[:400] + "..."
                        tc_parts.append(f"  {name}({args})")
                    else:
                        fn = getattr(tc, "function", None)
                        name = getattr(fn, "name", "?") if fn else "?"
                        tc_parts.append(f"  {name}(...)")
                content += "\n[Tool calls:\n" + "\n".join(tc_parts) + "\n]"
            parts.append(f"[ASSISTANT]: {content}")
            continue

        if len(content) > per_msg_cap:
            content = content[:2000] + "\n...[truncated]...\n" + content[-800:]
        parts.append(f"[{role.upper()}]: {content}")

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Structured summary generation
# ---------------------------------------------------------------------------

_MIN_SUMMARY_TOKENS = 2000
_SUMMARY_RATIO = 0.20
_SUMMARY_TOKENS_CEILING = 8000


def _compute_summary_budget(
    turns: list[dict[str, Any]],
    context_length: int,
) -> int:
    """Scale summary token budget with the amount of content being compressed."""
    content_tokens = sum(estimate_message_tokens(m) for m in turns)
    budget = int(content_tokens * _SUMMARY_RATIO)
    ceiling = min(int(context_length * 0.05), _SUMMARY_TOKENS_CEILING)
    return max(_MIN_SUMMARY_TOKENS, min(budget, ceiling))


async def generate_structured_summary(
    client: InferenceProvider,
    model: str,
    *,
    turns: list[dict[str, Any]],
    previous_summary: str,
    entity_name: str,
    context_length: int,
    num_ctx: int | None = None,
) -> str | None:
    """Generate a structured compaction summary of the middle turns.

    Returns ``None`` on failure; callers must retain the original conversation.
    """
    budget = _compute_summary_budget(turns, context_length)
    content = serialize_for_summary(turns)

    if previous_summary:
        prompt = _COMPACTION_SYS_UPDATE.format(
            previous=previous_summary,
            content=content,
            budget=budget,
        )
    else:
        prompt = _COMPACTION_SYS_FIRST.format(
            content=content,
            budget=budget,
        )

    try:
        res = await client.chat_completion(
            model,
            [{"role": "user", "content": prompt}],
            temperature=0.25,
            max_tokens=min(budget * 2, 8000),
            think=False,
            num_ctx=num_ctx,
        )
        out = (getattr(res, "content", None) or "").strip()
    except Exception as e:
        log.warning("compaction_summary_failed", error=str(e))
        return None

    if len(out) < 30:
        log.info("compaction_summary_too_short", out_len=len(out))
        return None

    log.info(
        "compaction_summary_generated",
        turns_summarized=len(turns),
        summary_chars=len(out),
        budget_tokens=budget,
    )
    return f"{COMPACTION_SUMMARY_PREFIX}\n\n{out}"


# ---------------------------------------------------------------------------
# Tool-pair sanitization
# ---------------------------------------------------------------------------

def sanitize_tool_pairs(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fix orphaned tool_call / tool_result pairs after compression."""
    surviving_call_ids: set[str] = set()
    for msg in messages:
        if msg.get("role") == "assistant":
            for tc in msg.get("tool_calls") or []:
                cid = tc.get("id", "") if isinstance(tc, dict) else getattr(tc, "id", "")
                if cid:
                    surviving_call_ids.add(cid)

    result_call_ids: set[str] = set()
    for msg in messages:
        if msg.get("role") == "tool":
            cid = msg.get("tool_call_id")
            if cid:
                result_call_ids.add(cid)

    orphaned_results = result_call_ids - surviving_call_ids
    if orphaned_results:
        messages = [
            m for m in messages
            if not (m.get("role") == "tool" and m.get("tool_call_id") in orphaned_results)
        ]
        log.info("compaction_sanitized_orphan_results", count=len(orphaned_results))

    missing_results = surviving_call_ids - result_call_ids
    if missing_results:
        patched: list[dict[str, Any]] = []
        for msg in messages:
            patched.append(msg)
            if msg.get("role") == "assistant":
                for tc in msg.get("tool_calls") or []:
                    cid = tc.get("id", "") if isinstance(tc, dict) else getattr(tc, "id", "")
                    if cid in missing_results:
                        patched.append({
                            "role": "tool",
                            "content": "[Result from earlier conversation — see compaction summary above]",
                            "tool_call_id": cid,
                        })
        messages = patched
        log.info("compaction_sanitized_stub_results", count=len(missing_results))

    return messages


# ---------------------------------------------------------------------------
# Knowledge flush (pre-compaction memory extraction)
# ---------------------------------------------------------------------------

async def extract_knowledge_for_flush(
    client: InferenceProvider,
    model: str,
    *,
    turns: list[dict[str, Any]],
    entity_name: str,
    num_ctx: int | None = None,
) -> list[tuple[str, str]]:
    """Extract durable facts from turns before they are compressed.

    Returns a list of ``(section_title, body)`` for knowledge.md, or empty on failure.
    """
    content = serialize_for_summary(turns, per_msg_cap=2000)
    if len(content) < 100:
        return []

    prompt = _KNOWLEDGE_FLUSH_PROMPT.format(content=content)

    try:
        res = await client.chat_completion(
            model,
            [{"role": "user", "content": prompt}],
            temperature=0.15,
            max_tokens=2000,
            think=False,
            num_ctx=num_ctx,
        )
        raw = (getattr(res, "content", None) or "").strip()
    except Exception as e:
        log.warning("knowledge_flush_failed", error=str(e))
        return []

    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()

    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        log.info("knowledge_flush_parse_failed", raw_len=len(raw))
        return []

    if not isinstance(parsed, list):
        return []

    results: list[tuple[str, str]] = []
    for item in parsed:
        if isinstance(item, dict):
            title = str(item.get("title", "")).strip()
            body = str(item.get("body", "")).strip()
            if title and body:
                results.append((title, body))

    log.info("knowledge_flush_extracted", items=len(results), entity=entity_name)
    return results


# ---------------------------------------------------------------------------
# Legacy merge (post-commit safety net — unchanged)
# ---------------------------------------------------------------------------

def format_history_messages_for_summary(messages: list[dict[str, Any]], per_msg_cap: int = 2200) -> str:
    """Flatten history dicts into lines for the summarizer."""
    lines: list[str] = []
    for m in messages:
        role = str(m.get("role") or "")
        content = m.get("content", "")
        if isinstance(content, list):
            content = gemma.stringify_content_blocks(content)
        text = str(content).strip()
        if len(text) > per_msg_cap:
            text = text[: per_msg_cap - 1] + "…"
        name = str(m.get("name") or "").strip()
        tool_calls = m.get("tool_calls")
        if role == "tool" and name:
            lines.append(f"tool_result({name}): {text}")
        elif tool_calls:
            lines.append(f"{role} [tool_calls]: {text[:800]}")
        else:
            lines.append(f"{role}: {text}")
    return "\n".join(lines).strip()


def _clip_block(s: str, max_chars: int) -> str:
    s = (s or "").strip()
    if len(s) <= max_chars:
        return s
    head = max_chars // 2
    tail = max_chars - head - 40
    return s[:head] + "\n… [middle omitted] …\n" + s[-tail:]


async def merge_rolling_summary(
    client: InferenceProvider,
    model: str,
    *,
    entity_name: str,
    prior_summary: str,
    dropped_messages: list[dict[str, Any]],
    per_msg_cap: int,
    max_merge_input_chars: int,
    merge_max_tokens: int,
    summary_max_chars: int,
    num_ctx: int | None = None,
    raise_on_failure: bool = False,
) -> str:
    """Call the model once to fold ``dropped_messages`` into ``prior_summary``."""
    dropped_fmt = format_history_messages_for_summary(dropped_messages, per_msg_cap=per_msg_cap)
    dropped_fmt = _clip_block(dropped_fmt, max_merge_input_chars)
    prior = (prior_summary or "").strip()
    prior_clipped = prior[:8000] if len(prior) > 8000 else prior
    user_block = (
        f"Entity name (for tone only): {entity_name}\n\n"
        f"Prior summary:\n{(prior_clipped or '[none]')}\n\n"
        f"Older transcript to fold in:\n{dropped_fmt or '[empty]'}"
    )
    try:
        res = await client.chat_completion(
            model,
            [
                {"role": "system", "content": _SYS},
                {"role": "user", "content": user_block},
            ],
            temperature=0.25,
            max_tokens=max(200, min(int(merge_max_tokens), 2048)),
            think=False,
            num_ctx=num_ctx,
        )
        out = (getattr(res, "content", None) or "").strip()
    except Exception as e:
        log.warning("history_merge_failed", error=str(e))
        if raise_on_failure:
            raise
        return (prior + "\n\n[Earlier thread dropped; summary merge failed.]").strip()[:summary_max_chars]

    if len(out) < 20:
        log.info("history_merge_too_short", out_len=len(out))
        if raise_on_failure:
            raise ValueError("History summary was empty or too short")
        return (prior + "\n\n[Earlier thread dropped; summary merge returned empty.]").strip()[
            :summary_max_chars
        ]

    if len(out) > summary_max_chars:
        out = out[: summary_max_chars - 20].rstrip() + "\n… [trimmed]"
    log.info(
        "history_merged",
        dropped_messages=len(dropped_messages),
        summary_chars=len(out),
    )
    return out
