"""Expression metadata: typing delay, tone; light drift detection (no full rewrite)."""

from __future__ import annotations

import random
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ngram.cognition import gemma
from ngram.config import EntityConfig
from ngram.models import EmotionalState


_LEADING_PAREN_BLOCK = re.compile(
    r"^\s*\([^)]{0,500}\)\s*(\n\s*)*",
    re.MULTILINE,
)
_LINE_ONLY_PAREN = re.compile(r"^\s*\([^)]{0,500}\)\s*$")
_LINE_ONLY_ASTERISK_ACTION = re.compile(r"^\s*\*[^*]{1,200}\*\s*$")
_MEDIA_TAG_BLOCK = re.compile(
    r"<\s*(audio|video)\b[^>]*>.*?<\s*/\s*\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
_MEDIA_TAG_SINGLE = re.compile(
    r"<\s*(audio|video|source)\b[^>]*\/?\s*>",
    re.IGNORECASE,
)
# Layout / inline tags models sometimes emit (plain chat is not HTML).
_HTML_BR = re.compile(r"<\s*br\s*/?\s*>", re.IGNORECASE)
_HTML_LAYOUT = re.compile(
    r"<\s*/?\s*(?:p|div|span|section|article|body|html|head|blockquote|message)\b[^>]*>",
    re.IGNORECASE,
)
_HTML_INLINE = re.compile(
    r"<\s*/?\s*(?:strong|b|em|i|u)\b[^>]*>",
    re.IGNORECASE,
)
# Echo of deliberate tool-continuation nudge ("…finished…") on its own line.
_TRAILING_TOOL_META_DONE = re.compile(
    r"(?:\n\n|\n)\s*i['\u2019]?m\s+done\.?\s*$",
    re.IGNORECASE,
)
# History bookkeeping for proactive `say()` turns (routine_history=False); models sometimes echo this aloud.
_INTERNAL_PROACTIVE_MARKERS = (
    re.compile(r"\[\s*you\s+sent\s+this\s+unprompted\s*\]", re.IGNORECASE),
    re.compile(r"\[\s*ngram:proactive_outbound\s*\]", re.IGNORECASE),
)

# LLM sometimes dumps tool names into the say text when using tool forcing
_TOOL_LEAK = re.compile(r"<?end_turn\(\)>?|\[end_turn\]", re.IGNORECASE)


def strip_html_layout_leaks(text: str) -> str:
    """Remove common HTML tags from user-visible chat (plain-text platforms, no parse_mode)."""
    if not text:
        return text
    t = _HTML_BR.sub("\n", text)
    t = _HTML_LAYOUT.sub("", t)
    t = _HTML_INLINE.sub("", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def strip_internal_history_echo(text: str) -> str:
    """Remove echoes of internal proactive-outbound history tags (wake cycles, delegation)."""
    t = text or ""
    if not t.strip():
        return (text or "").strip()
    for rx in _INTERNAL_PROACTIVE_MARKERS:
        t = rx.sub("", t)
    out_lines: list[str] = []
    for line in t.splitlines():
        s = re.sub(r"[ \t]+", " ", line.strip())
        if s:
            out_lines.append(s)
    return "\n".join(out_lines).strip()


@dataclass
class ExpressionMeta:
    typing_delay_seconds: float
    tone_tag: str
    chunk_pause: float


def strip_stage_directions(text: str) -> str:
    """
    Remove roleplay narration the model should not send on text platforms:
    leading ``(Chuckles)``, whole lines that are only parentheses, lone ``*actions*``.
    """
    t = (text or "").strip()
    if not t:
        return t
    # Strip media HTML tags if they leak from tool-context synthesis.
    t = _MEDIA_TAG_BLOCK.sub(" ", t)
    t = _MEDIA_TAG_SINGLE.sub(" ", t)
    while True:
        m = _LEADING_PAREN_BLOCK.match(t)
        if not m:
            break
        t = t[m.end() :].lstrip()
    out_lines: list[str] = []
    for line in t.splitlines():
        s = line.strip()
        if not s:
            if out_lines and out_lines[-1] != "":
                out_lines.append("")
            continue
        if _LINE_ONLY_PAREN.match(line) or _LINE_ONLY_ASTERISK_ACTION.match(line):
            continue
        out_lines.append(line.rstrip())
    t = "\n".join(out_lines).strip()
    while True:
        m = _LEADING_PAREN_BLOCK.match(t)
        if not m:
            break
        t = t[m.end() :].lstrip()
    t = _TRAILING_TOOL_META_DONE.sub("", t).strip()
    t = _TOOL_LEAK.sub("", t).strip()
    return gemma.strip_leaked_control_tokens(t)


def apply_voice_outgoing_substitutions(text: str, voice: Mapping[str, Any] | None) -> str:
    """
    Optional ``personality.voice.outgoing_text_substitutions`` map in entity YAML:
    replace keys with values in outbound user-visible text. Keys that are plain
    alphanumeric tokens use case-insensitive whole-word replacement; other keys
    use literal substring replacement (first match wins per key, in YAML order).
    """
    if not voice:
        return text
    raw = voice.get("outgoing_text_substitutions")
    if not raw or not isinstance(raw, dict):
        return text
    t = text or ""
    for k, v in raw.items():
        if not isinstance(k, str) or not k.strip() or v is None:
            continue
        repl = str(v)
        key = k.strip()
        if key.isascii() and key.isalnum() and len(key) <= 48:
            t = re.sub(r"\b" + re.escape(key) + r"\b", repl, t, flags=re.IGNORECASE)
        else:
            t = t.replace(key, repl)
    return t


_INLINE_REPEAT = re.compile(r"(.{3,40}?)\1{3,}", re.DOTALL)


def _strip_degenerate_repetition(text: str, max_repeats: int = 3) -> str:
    """Detect and truncate degenerate repetition loops.

    Catches two patterns:
    1. Full-line repeats (same line appearing 3+ times)
    2. Inline substring repeats (same 3-40 char fragment repeating 4+ times within a line)
    """
    if not text or len(text) < 80:
        return text

    # --- inline repetition: truncate at the point where the repeat starts ---
    m = _INLINE_REPEAT.search(text)
    if m:
        text = text[:m.start()].rstrip()
        if not text:
            return ""

    # --- line-level repetition ---
    lines = text.splitlines()
    if len(lines) < max_repeats + 2:
        return text
    seen: dict[str, int] = {}
    first_degen_idx = -1
    for i, line in enumerate(lines):
        key = line.strip().lower().replace("_", " ").replace("-", " ")
        if not key or len(key) < 3:
            continue
        seen[key] = seen.get(key, 0) + 1
        if seen[key] >= max_repeats and first_degen_idx == -1:
            first_degen_idx = i - max_repeats + 1
    if first_degen_idx <= 0:
        return text
    truncated = "\n".join(lines[:max(1, first_degen_idx)]).strip()
    return truncated if truncated else ""


class VoiceController:
    def __init__(self, entity: EntityConfig) -> None:
        self.entity = entity

    def sanitize_reply(self, text: str) -> str:
        t = strip_stage_directions(text)
        t = strip_html_layout_leaks(t)
        t = strip_internal_history_echo(t)
        t = _strip_degenerate_repetition(t)
        vis, cot = gemma.separate_plaintext_chain_of_thought(t)
        if cot and vis:
            t = vis
        elif cot and not vis:
            t = ""
        while t.startswith("---"):
            t = t[3:].lstrip("\n").lstrip()
        while t.startswith(">"):
            t = t[1:].lstrip()
        return apply_voice_outgoing_substitutions(t, self.entity.personality.voice)

    def meta_for_response(
        self,
        emotional_state: EmotionalState,
        response_length: int,
        platform: str = "cli",
    ) -> ExpressionMeta:
        h = self.entity.harness.presence
        base_cps = h.typing_speed_base
        var = h.typing_speed_variance
        mood = emotional_state.primary.value
        if mood in ("excited", "amused"):
            cps = base_cps * (1.2 + random.uniform(-var, var))
        elif mood in ("melancholy", "withdrawn", "anxious"):
            cps = base_cps * (0.65 + random.uniform(-var, var))
        else:
            cps = base_cps * (1.0 + random.uniform(-var, var))
        cps = max(5.0, cps)
        delay = response_length / cps
        tone = mood if platform != "telegram" else f"measured_{mood}"
        return ExpressionMeta(
            typing_delay_seconds=min(12.0, max(0.3, delay)),
            tone_tag=tone,
            chunk_pause=h.chunk_delay,
        )
