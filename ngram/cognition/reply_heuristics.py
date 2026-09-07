"""Pure reply-quality heuristics — no entity or config dependency."""

from __future__ import annotations

import re
from typing import Any

from ngram.cognition import gemma
from ngram.utils.ollama_client import ChatCompletionResult

_FALLBACK_PLAIN_REPLY_FAILURE = (
    "something went wrong on my end and i couldn't finish that reply. "
    "try me again in a sec."
)

# After tool calls, models often emit a one-token ack; we already shipped the visible line pre-tool.
_PRO_FORMA_TOOL_FOLLOWUPS = frozenset(
    {
        "done",
        "done.",
        "ok",
        "ok.",
        "k",
        "k.",
        "yep",
        "yep.",
        "yeah",
        "yeah.",
        "there",
        "there.",
        "got it",
        "got it.",
        "sorted",
        "sorted.",
        "cool",
        "cool.",
        "all set",
        "all set.",
        "finished",
        "finished.",
        "i'm done",
        "i'm done.",
        "im done",
        "im done.",
        "i am done",
        "i am done.",
        "great",
        "great.",
        "nice",
        "nice.",
        "bet",
        "bet.",
        "alright",
        "aight",
    }
)


def short_error_text(raw: str | None, *, limit: int = 180) -> str:
    txt = re.sub(r"\s+", " ", str(raw or "")).strip().strip(".")
    if not txt:
        return ""
    if len(txt) > limit:
        txt = txt[: limit - 1].rstrip() + "…"
    return txt


def format_user_visible_failure(
    error: Exception | str | None = None,
    *,
    tool_state: dict[str, Any] | None = None,
) -> str:
    """Short user-facing failure line based on the real cause, not a generic model-server hint."""
    tool_err = None
    if isinstance(tool_state, dict):
        raw = tool_state.get("last_tool_error")
        if isinstance(raw, dict):
            tool_err = raw
    if tool_err:
        tool_name = str(tool_err.get("tool") or "tool").replace("_", " ").strip()
        detail = short_error_text(str(tool_err.get("error") or ""))
        low = detail.lower()
        if "locked to railway" in low or "railway container" in low:
            return f"i tried to use {tool_name}, but execution is locked to railway right now. {detail}"
        if "timeout" in low or "timed out" in low:
            return f"i tried to use {tool_name}, but it timed out. {detail}"
        if "no search results" in low or "empty" in low or "not found" in low:
            return f"i tried to use {tool_name}, but it came back empty. {detail}"
        if detail:
            return f"something went wrong while using {tool_name}. {detail}"
        return f"something went wrong while using {tool_name}."

    detail = short_error_text(str(error or ""))
    low = detail.lower()
    if any(
        marker in low
        for marker in (
            "connection refused",
            "connect",
            "unreachable",
            "timed out",
            "timeout",
            "ollama",
            "gateway",
            "api connection",
            "clientconnectorerror",
            "getaddrinfo failed",
        )
    ):
        return "i lost the inference backend mid-reply. try me again in a sec."
    if detail:
        return f"something went wrong on my end and i couldn't finish that reply. {detail}"
    return _FALLBACK_PLAIN_REPLY_FAILURE


def reply_too_thin(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < 2:
        return True
    if t in ("…", "...", "—", "-"):
        return True
    return gemma.visible_reply_looks_truncated_stub(t)


def intermediate_text_looks_like_tool_channel(text: str) -> bool:
    """True when the model leaked Gemma tool markup — must not be shown as chat."""
    t = text or ""
    return any(
        m in t
        for m in (
            gemma.TOOL_CALL_START,
            gemma.TOOL_RESPONSE_START,
            gemma.TOOL_DECL_START,
            gemma.TURN_START,
        )
    )


def pro_forma_tool_followup(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return True
    if re.fullmatch(r"i['\u2019]?m\s+done\.?", t):
        return True
    if t in _PRO_FORMA_TOOL_FOLLOWUPS:
        return True
    base = t.rstrip(".!?")
    if len(t) <= 20 and " " not in t:
        return base in {
            "done",
            "ok",
            "k",
            "yep",
            "yeah",
            "there",
            "cool",
            "nice",
            "great",
            "bet",
            "alright",
            "aight",
        }
    return False


def reply_looks_like_progress_only(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False

    # Catch explicit promises to do something instead of doing it
    promise_match = re.search(
        r"(i['\u2019]?ll|i will|i['\u2019]?m going to|let me|lemme|i can)\s+(find|look|search|check|read|open|get|send|pull|try|run|execute|figure out)",
        t[:150],
    )
    if promise_match:
        return True

    starters = (
        "checking ",
        "checking the ",
        "looking ",
        "looking in ",
        "looking around",
        "searching ",
        "reading ",
        "opening ",
        "hang on",
        "hold on",
        "one sec",
        "give me a sec",
        "lemme ",
        "let me ",
        "i'm on it",
        "on it!",
    )
    if any(t.startswith(s) for s in starters):
        return True
    return bool(
        re.fullmatch(
            r"(i['\u2019]?m|im|i am)\s+(checking|looking|searching|reading|opening).{0,80}",
            t,
        )
    )


def user_explicitly_requests_tool_grounding(text: str) -> bool:
    low = (text or "").lower()
    return any(
        phrase in low
        for phrase in (
            "use tools",
            "not memory",
            "don't guess",
            "do not guess",
            "exact path",
            "exact stdout",
            "exact error",
            "exact contents",
        )
    )


def finish_reason_hint(res: ChatCompletionResult) -> bool:
    fr = str(res.finish_reason or "").lower().replace("_", "").replace("-", "")
    return fr in ("length", "maxtokens", "maxlength")


def answer_ignores_tool_results(text: str, tool_state: dict[str, Any]) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return True
    previews = tool_state.get("tool_output_previews") if isinstance(tool_state, dict) else None
    if not isinstance(previews, list) or not previews:
        return False
    if "/" in t or "\\" in t:
        return False
    if any(ch.isdigit() for ch in t):
        return False
    interesting = 0
    for item in previews[-3:]:
        if not isinstance(item, dict):
            continue
        preview = str(item.get("preview") or "")
        tokens = [tok.strip(" .,:;()[]{}\"'") for tok in preview.split()]
        tokens = [tok.lower() for tok in tokens if len(tok) >= 4]
        hits = [tok for tok in tokens[:12] if tok in t]
        if hits:
            interesting += 1
    return interesting == 0


def tool_state_summary(tool_state: dict[str, Any], *, limit: int = 3) -> str:
    previews = tool_state.get("tool_output_previews") if isinstance(tool_state, dict) else None
    if not isinstance(previews, list) or not previews:
        return ""
    lines: list[str] = []
    for item in previews[-limit:]:
        if not isinstance(item, dict):
            continue
        tool = str(item.get("tool") or "tool")
        ok = bool(item.get("ok"))
        preview = short_error_text(str(item.get("preview") or ""), limit=220)
        lines.append(f"- {tool} ({'ok' if ok else 'error'}): {preview}")
    return "\n".join(lines)
