"""
Gemma 4 control-token parsing (literal token boundaries, not NL regex).

Spec: https://ai.google.dev/gemma/docs/core/prompt-formatting-gemma4

We scan UTF-8 text left-to-right for exact reserved token strings. Ollama returns
decoded strings; those correspond 1:1 to tokenizer control token spellings.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

# --- Documented Gemma 4 literal spellings (verify against official spec) ---
TURN_START = "<|turn|>"
TURN_END = "<turn|>"
THINK = "<|think|>"
CHANNEL_THOUGHT = "<|channel>thought"
CHANNEL_END = "<channel|>"
TOOL_CALL_START = "<|tool_call>"
TOOL_CALL_END = "<tool_call|>"
TOOL_RESPONSE_START = "<|tool_response>"
TOOL_RESPONSE_END = "<tool_response|>"
TOOL_DECL_START = "<|tool>"
TOOL_DECL_END = "<tool|>"
STRING_DELIM = '<|"|>'
IMAGE_TOKEN = "<|image|>"
AUDIO_TOKEN = "<|audio|>"

REDACTED_THINK_OPEN = "<redacted_thinking>"
REDACTED_THINK_CLOSE = "</redacted_thinking>"

# Spellings to strip from user-visible chat when the model echoes or mangles tool format.
_CONTROL_TOKEN_LEAK_MARKERS: tuple[str, ...] = (
    TOOL_RESPONSE_START,
    TOOL_RESPONSE_END,
    TOOL_CALL_START,
    TOOL_CALL_END,
    TOOL_DECL_START,
    TOOL_DECL_END,
    TURN_START,
    TURN_END,
    THINK,
    CHANNEL_THOUGHT,
    CHANNEL_END,
    STRING_DELIM,
    IMAGE_TOKEN,
    AUDIO_TOKEN,
    REDACTED_THINK_OPEN,
    REDACTED_THINK_CLOSE,
)


# After exact-marker removal, catch concatenated / partial fragments (no stable close delimiter).
_GEMMA_LEAK_TAIL = re.compile(
    r"<\|[^>\n]{1,120}>"
    r"|<tool_(?:call|response)\|>",
    re.IGNORECASE,
)

# Informal tool-like tags Gemma sometimes emits as plain text instead of proper tool calls.
# Catches: <thought>, <t>, </t>, <mood>, <endofturn>, <say>, etc. Closing > is optional.
_INFORMAL_TOOL_TAGS = re.compile(
    r"</?(?:thought|think|end_turn|endofturn|say|wait|tool_call|tool_response|t|mood)[^>\n]*>?",
    re.IGNORECASE,
)

# Raw function-call-style leaks: say{...}, end_turn: { ... } (models often add a colon),
# think{thought:...}, etc. [^}] spans newlines; cap length so a stray "{" in chat does not
# eat the rest of the message.
_INFORMAL_TOOL_CALLS = re.compile(
    r"(?:say|think|end_turn|wait)\s*(?::\s*)?\{[^}]{0,8000}\}",
    re.IGNORECASE,
)

# Metadata tag lines to discard entirely: <mood>restless</mood>, <endofturn>, etc.
_META_TAG_LINE = re.compile(
    r"^\s*</?(?:mood|endofturn|end_of_turn|internal|meta|analysis)[^>]*>(?:[^<\n]{0,200}</[^>]{1,60}>)?\s*$",
    re.MULTILINE | re.IGNORECASE,
)

# Malformed thought-channel lines models sometimes spit when CoT runs wrong (not real <|channel>thought).
_THOUGHT_LINE_JUNK = re.compile(
    r"(?mi)^\s*</?thought\b[^>]*\s*/?>\s*$|^\s*<\s*channel\s*\|\s*>\s*$",
)

# Injected by deliberate loop for the model; must never appear in user chat.
_LENGTH_STALL_OUTBOUND_LEAK = re.compile(
    r"\n*\(?\s*your response was too long[^.]*(?:write_file|large content)[^.]*\.?\s*\)?",
    re.IGNORECASE,
)


def strip_leaked_control_tokens(text: str) -> str:
    """Remove Gemma control token literals from strings shown to humans (mangled duplicates, orphans)."""
    t = text or ""
    if not t:
        return t
    changed = True
    while changed:
        changed = False
        for m in _CONTROL_TOKEN_LEAK_MARKERS:
            if m in t:
                t = t.replace(m, "")
                changed = True
    t = t.strip()
    while True:
        nxt = _GEMMA_LEAK_TAIL.sub("", t).strip()
        if nxt == t:
            break
        t = nxt
    t = _META_TAG_LINE.sub("", t).strip()
    t = _INFORMAL_TOOL_TAGS.sub("", t).strip()
    t = _INFORMAL_TOOL_CALLS.sub("", t).strip()
    t = "\n".join(ln for ln in t.splitlines() if not _THOUGHT_LINE_JUNK.match(ln)).strip()
    t = _LENGTH_STALL_OUTBOUND_LEAK.sub("", t).strip()
    return t


# Empty thought channel (suppress spurious CoT when thinking mode is off; large models)
EMPTY_THOUGHT_TURN = f"{TURN_START}model\n{CHANNEL_THOUGHT}\n{CHANNEL_END}\n"

# Unstructured chain-of-thought some models write in plain text (no <|channel>thought tokens).
_PLAINTEXT_COT_HEADER = re.compile(
    r"(?is)^\s*(?:"
    r"(?:\*\*\s*)?thinking\s+process(?:\s*\*\*)?"
    r"|(?:\*\*\s*)?chain\s+of\s+thought(?:\s*\*\*)?"
    r"|(?:\*\*\s*)?internal\s+analysis(?:\s*\*\*)?"
    r"|(?:\*\*\s*)?scratch\s*pad(?:\s*\*\*)?"
    r")\s*:",
)


_IM_PLANNING_VERB = re.compile(
    r"(?is)^i['\u2019]?m\s+"
    r"(?:refining|considering|trying|thinking|working|looking|going|noting|weighing|"
    r"deciding|planning|attempting|figuring|drafting|preparing|answering|processing|"
    r"reflecting|brainstorming|structuring|organizing|organising)\b"
)


def _line_looks_like_cot_continuation(line: str) -> bool:
    s = line.strip()
    if not s:
        return True
    if re.match(r"^\d+\.\s", s):
        return True
    if re.match(r"^[\-*•]\s", s):
        return True
    low = s.lower()
    if _IM_PLANNING_VERB.match(s):
        return True
    if low.startswith(
        (
            "i need to ",
            "i should ",
            "i'll try",
            "i will try",
            "i want to ",
            "first, ",
            "first i ",
            "the user ",
            "this question ",
            "drafting",
            "establish",
            "determine",
            "review",
            "analyze",
            "analyse",
            "consider",
            "the goal",
            "step ",
            "final ",
            "potential",
            "identify",
            "summarize",
            "summarise",
            "breakdown",
            "break down",
            "output",
            "constraint",
        )
    ):
        return True
    if s.endswith(":") and len(s) < 80 and " " in s:
        return True
    return False


def visible_reply_looks_truncated_stub(s: str) -> bool:
    """True when user-visible text is almost certainly incomplete (parser or stop mid-clause)."""
    t = (s or "").strip()
    if not t:
        return True
    if re.match(r"(?is)^i['\u2019]?m\s*$", t):
        return True
    if re.match(r"(?is)^i\s+am\s*$", t):
        return True
    return False


def visible_reply_looks_abruptly_cut(s: str) -> bool:
    """
    Heuristic: reply probably hit max_tokens mid-sentence (e.g. ends with ``? I`` before ``'m``).
    """
    t = (s or "").rstrip()
    if len(t) < 30:
        return False
    if t[-1] in (".", "!", "?", "…", '"', "'", ")", "]"):
        return False
    # Common cut: ``… on me? I`` (next tokens would be ``'m …``)
    if re.search(r"[.!?:]\s+I\s*$", t):
        return True
    return False


def join_continuation_fragment(partial: str, extra: str) -> str:
    """Join first completion + continuation without spurious spaces (e.g. ``I`` + ``'m …``)."""
    p = (partial or "").rstrip()
    e = (extra or "").lstrip()
    if not e:
        return p
    if not p:
        return e
    if p[-1].isspace():
        return p + e
    if e[0].isspace():
        return p + e
    if p[-1].isalnum() and e[0].isalnum():
        return p + " " + e
    return p + e


def separate_plaintext_chain_of_thought(raw: str) -> tuple[str, str]:
    """
    When the model emits a leading 'Thinking Process:' / numbered analysis block in plain text,
    split user-visible reply from content that should stay in inner monologue only.

    Returns (visible_fragment, thinking_blob). If there is no separate reply, visible is '' and
    thinking_blob is the full ``raw`` (caller should not show it to the user).
    """
    t = raw.strip()
    if not t:
        return "", ""
    m = _PLAINTEXT_COT_HEADER.match(t)
    if not m:
        return t, ""
    after = t[m.end() :].lstrip("\n")
    if not after:
        return "", t
    pos = 0
    n = len(after)
    while pos < n:
        dbl = after.find("\n\n", pos)
        if dbl == -1:
            return "", t
        seg_start = dbl + 2
        while seg_start < n and after[seg_start] in " \t\r":
            seg_start += 1
        if seg_start >= n:
            return "", t
        line_end = after.find("\n", seg_start)
        first_line = after[seg_start:line_end] if line_end != -1 else after[seg_start:]
        if not _line_looks_like_cot_continuation(first_line):
            visible = after[seg_start:].strip()
            if visible:
                thinking = (t[: m.end()] + after[:seg_start]).strip()
                return visible, thinking
        pos = dbl + 2
    return "", t


@dataclass
class ParsedAssistantOutput:
    """Result of parsing a single assistant message string from the API."""

    thinking_parts: list[str] = field(default_factory=list)
    tool_call_raws: list[str] = field(default_factory=list)
    tool_response_raws: list[str] = field(default_factory=list)
    text_segments: list[str] = field(default_factory=list)

    @property
    def thinking(self) -> Optional[str]:
        if not self.thinking_parts:
            return None
        return "\n\n".join(self.thinking_parts).strip() or None

    @property
    def visible_user_text(self) -> str:
        """Final user-visible reply: plain text segments only (no thought/tool tokens)."""
        return "".join(self.text_segments).strip()

    def text_for_history_between_turns(self) -> str:
        """
        Strip thought channels for the next user turn; preserve tool_call / tool_response
        literals so a single agentic turn stays reconstructible when needed.
        """
        parts: list[str] = []
        for seg in self._ordered_chunks():
            kind, payload = seg
            if kind == "thought":
                continue
            parts.append(payload)
        return "".join(parts).strip()

    def rebuild_preserving_thought_and_tools(self) -> str:
        """Full string for the same agentic turn (e.g. before follow-up completion)."""
        parts: list[str] = []
        for kind, payload in self._ordered_chunks():
            if kind == "text":
                parts.append(payload)
            elif kind == "thought":
                parts.append(f"{CHANNEL_THOUGHT}\n{payload}\n{CHANNEL_END}")
            elif kind == "tool_call":
                parts.append(f"{TOOL_CALL_START}{payload}{TOOL_CALL_END}")
            elif kind == "tool_response":
                parts.append(f"{TOOL_RESPONSE_START}{payload}{TOOL_RESPONSE_END}")
        return "".join(parts)

    def _ordered_chunks(self) -> list[tuple[str, str]]:
        return getattr(self, "_chunks", [])


def _set_chunks(p: ParsedAssistantOutput, chunks: list[tuple[str, str]]) -> ParsedAssistantOutput:
    p._chunks = chunks  # type: ignore[attr-defined]
    return p


def parse_assistant_output(text: str) -> ParsedAssistantOutput:
    """
    Scan `text` for Gemma 4 control tokens at string boundaries.
    Order: thought blocks, redacted thinking, tool_call, tool_response, skip turn/think markers, text runs.
    """
    if not text:
        return _set_chunks(ParsedAssistantOutput(), [])

    i = 0
    n = len(text)
    out = ParsedAssistantOutput()
    chunks: list[tuple[str, str]] = []

    def emit_text(a: int, b: int) -> None:
        if a < b:
            piece = text[a:b]
            out.text_segments.append(piece)
            chunks.append(("text", piece))

    while i < n:
        if text.startswith(CHANNEL_THOUGHT, i):
            start_body = i + len(CHANNEL_THOUGHT)
            end = text.find(CHANNEL_END, start_body)
            if end == -1:
                emit_text(i, n)
                break
            body = text[start_body:end].lstrip("\n\r \t")
            out.thinking_parts.append(body)
            chunks.append(("thought", body))
            i = end + len(CHANNEL_END)
            continue

        if text.startswith(REDACTED_THINK_OPEN, i):
            start_body = i + len(REDACTED_THINK_OPEN)
            end = text.find(REDACTED_THINK_CLOSE, start_body)
            if end == -1:
                emit_text(i, n)
                break
            body = text[start_body:end].strip()
            out.thinking_parts.append(body)
            chunks.append(("thought", body))
            i = end + len(REDACTED_THINK_CLOSE)
            continue

        if text.startswith(TOOL_CALL_START, i):
            start_body = i + len(TOOL_CALL_START)
            end = text.find(TOOL_CALL_END, start_body)
            if end == -1:
                i += len(TOOL_CALL_START)
                continue
            inner = text[start_body:end]
            out.tool_call_raws.append(inner)
            chunks.append(("tool_call", inner))
            i = end + len(TOOL_CALL_END)
            continue

        if text.startswith(TOOL_RESPONSE_START, i):
            start_body = i + len(TOOL_RESPONSE_START)
            end = text.find(TOOL_RESPONSE_END, start_body)
            if end == -1:
                i += len(TOOL_RESPONSE_START)
                continue
            inner = text[start_body:end]
            out.tool_response_raws.append(inner)
            chunks.append(("tool_response", inner))
            i = end + len(TOOL_RESPONSE_END)
            continue

        # Orphan closers (model echoed only end delimiters) — never user-visible.
        if text.startswith(TOOL_CALL_END, i):
            i += len(TOOL_CALL_END)
            continue
        if text.startswith(TOOL_RESPONSE_END, i):
            i += len(TOOL_RESPONSE_END)
            continue
        if text.startswith(TOOL_DECL_END, i):
            i += len(TOOL_DECL_END)
            continue

        if text.startswith(TURN_START, i):
            i += len(TURN_START)
            continue
        if text.startswith(TURN_END, i):
            i += len(TURN_END)
            continue
        if text.startswith(THINK, i):
            i += len(THINK)
            continue

        nxt = n
        for marker in (
            CHANNEL_THOUGHT,
            REDACTED_THINK_OPEN,
            TOOL_CALL_START,
            TOOL_RESPONSE_START,
            TURN_START,
            TURN_END,
            THINK,
        ):
            p = text.find(marker, i + 1)
            if p != -1 and p < nxt:
                nxt = p
        emit_text(i, nxt)
        i = nxt if nxt > i else i + 1

    return _set_chunks(out, chunks)


def parse_tool_call_inner(inner: str) -> tuple[str, dict[str, Any]]:
    """
    Parse body inside <|tool_call> ... <|tool_call|>.
    Expected: call:<name>{key:<|"|>value<|"|>,...}
    """
    inner = inner.strip()
    if not inner.startswith("call:"):
        return "", {}
    rest = inner[5:].lstrip()
    brace = rest.find("{")
    if brace == -1:
        return rest, {}
    name = rest[:brace].strip()
    body = rest[brace + 1 :]
    depth = 1
    end_idx = -1
    for j, c in enumerate(body):
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                end_idx = j
                break
    if end_idx == -1:
        return name, {"raw": body}
    arg_str = body[:end_idx]
    return name, _parse_gemma_brace_args(arg_str)


def _parse_gemma_brace_args(arg_str: str) -> dict[str, Any]:
    args: dict[str, Any] = {}
    i = 0
    n = len(arg_str)
    while i < n:
        while i < n and arg_str[i] in " \n\t,":
            i += 1
        if i >= n:
            break
        ke = i
        while ke < n and arg_str[ke] not in ":":
            ke += 1
        if ke >= n:
            break
        key = arg_str[i:ke].strip()
        if not key:
            i = ke + 1
            continue
        i = ke + 1
        while i < n and arg_str[i] in " \n\t":
            i += 1
        if i < n and arg_str.startswith(STRING_DELIM, i):
            i += len(STRING_DELIM)
            end = arg_str.find(STRING_DELIM, i)
            if end == -1:
                args[key] = arg_str[i:]
                break
            args[key] = arg_str[i:end]
            i = end + len(STRING_DELIM)
            continue
        # unquoted token until comma or end
        start_val = i
        while i < n and arg_str[i] not in ",":
            i += 1
        raw = arg_str[start_val:i].strip()
        if raw:
            try:
                args[key] = json.loads(raw)
            except json.JSONDecodeError:
                args[key] = raw
    return args


def tool_calls_from_parsed(p: ParsedAssistantOutput) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in p.tool_call_raws:
        name, args = parse_tool_call_inner(raw)
        if name:
            out.append({"name": name, "arguments": args})
    return out


def split_thinking_and_visible(text: str) -> tuple[Optional[str], str]:
    p = parse_assistant_output(text)
    return p.thinking, p.visible_user_text


def strip_thinking_for_history(text: str) -> str:
    """Between separate user turns: drop thought channels; keep tool literals."""
    return parse_assistant_output(text).text_for_history_between_turns()


def inject_thinking_instruction(system_text: str, think: bool) -> str:
    if not think:
        return system_text
    if THINK in system_text:
        return system_text
    return f"{THINK}\n{system_text}"


def empty_thought_channel_fragment() -> str:
    """Append to system (or model prefix) when thinking mode is off — stabilizes 26B-class models."""
    return EMPTY_THOUGHT_TURN


def format_turns_openai(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if isinstance(content, list):
            content = stringify_content_blocks(content)
        parts.append(f"{TURN_START}{role}\n{content}{TURN_END}")
    return "\n".join(parts)


def stringify_content_blocks(blocks: list[dict[str, Any]]) -> str:
    out: list[str] = []
    for b in blocks:
        t = b.get("type")
        if t == "text":
            out.append(str(b.get("text", "")))
        elif t == "image_url":
            out.append(IMAGE_TOKEN)
        elif t == "input_audio":
            out.append(AUDIO_TOKEN)
        else:
            out.append(str(b))
    return "\n".join(out)


def estimate_tokens_approx(text: str) -> int:
    return max(1, len(text) // 4)


def new_tool_call_id() -> str:
    return f"call_{uuid.uuid4().hex[:12]}"


def format_tool_declarations_block(openai_style_tools: list[dict[str, Any]]) -> str:
    """
    Gemma 4 native tool declarations embedded in the system prompt.
    Each tool is one JSON blob wrapped in <|tool> ... <tool|>.
    """
    if not openai_style_tools:
        return ""
    parts: list[str] = []
    for t in openai_style_tools:
        fn = t.get("function") or {}
        decl = {
            "name": fn.get("name", ""),
            "description": fn.get("description", ""),
            "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
        }
        parts.append(f"{TOOL_DECL_START}{json.dumps(decl, ensure_ascii=False)}{TOOL_DECL_END}")
    return "\n".join(parts)


def format_tool_response_token(name: str, content: str) -> str:
    """Literal tool result channel for logging or manual transcript assembly."""
    body = json.dumps({"name": name, "content": content}, ensure_ascii=False)
    return f"{TOOL_RESPONSE_START}{body}{TOOL_RESPONSE_END}"
