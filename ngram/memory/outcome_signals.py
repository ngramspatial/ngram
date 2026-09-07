"""Outcome signal detection — pure functions that classify turn quality.

These heuristics extract learning signals from conversation patterns without
requiring LLM calls.  They feed into the TurnOutcomeStore so the agent can
close the feedback loop: *did that turn go well?*
"""

from __future__ import annotations

import re


# ---------------------------------------------------------------------------
# Correction detection
# ---------------------------------------------------------------------------

_CORRECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bno[,.]?\s+(i\s+)?(meant|mean|said|was asking|wanted|need)",
        r"\bthat('?s|\s+is)\s+(wrong|incorrect|not right|not what i)",
        r"\bactually[,.]?\s+(i\s+)?(meant|mean|want|need)",
        r"\byou\s+(misunderstood|got\s+it\s+wrong|missed)",
        r"\bnot\s+what\s+i\s+(asked|meant|wanted|said)",
        r"\bplease\s+(re-?read|re-?check|look\s+again|try\s+again)",
        r"\bwrong\s+(answer|response|result|output|file|path)",
        r"\bthat\s+doesn'?t?\s+(work|help|answer|solve)",
        r"\bi\s+already\s+(said|told|mentioned|asked)",
        r"\bstill\s+(wrong|broken|not working|doesn'?t)",
        r"\bdon'?t\s+(just|only)?\s*(repeat|parrot|echo)\b",
    )
]


def detect_correction(user_text: str) -> bool:
    """Return True when the user is correcting a prior agent response."""
    t = (user_text or "").strip()
    if not t:
        return False
    return any(p.search(t) for p in _CORRECTION_PATTERNS)


# ---------------------------------------------------------------------------
# Repetition detection
# ---------------------------------------------------------------------------

def _normalize_for_comparison(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    t = (text or "").strip().lower()
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def detect_repetition(current_text: str, previous_texts: list[str], threshold: float = 0.7) -> bool:
    """Return True when the current message substantially repeats a recent one.

    Uses token-overlap ratio (Jaccard-like) — cheap and effective for detecting
    rephrased re-asks without needing embeddings.
    """
    cur = _normalize_for_comparison(current_text)
    if not cur or len(cur) < 10:
        return False
    cur_tokens = set(cur.split())
    if len(cur_tokens) < 3:
        return False
    for prev in previous_texts:
        prev_norm = _normalize_for_comparison(prev)
        if not prev_norm:
            continue
        prev_tokens = set(prev_norm.split())
        if not prev_tokens:
            continue
        intersection = cur_tokens & prev_tokens
        union = cur_tokens | prev_tokens
        if not union:
            continue
        similarity = len(intersection) / len(union)
        if similarity >= threshold:
            return True
    return False


# ---------------------------------------------------------------------------
# Positive reaction detection
# ---------------------------------------------------------------------------

_POSITIVE_STARTERS = frozenset({
    "thanks",
    "thank you",
    "thx",
    "ty",
    "perfect",
    "exactly",
    "great",
    "awesome",
    "nice",
    "love it",
    "love this",
    "that's it",
    "that's what i needed",
    "that's exactly",
    "spot on",
    "nailed it",
    "well done",
    "good job",
    "amazing",
    "brilliant",
    "excellent",
    "wonderful",
    "helpful",
    "this helps",
    "that helps",
    "you're the best",
    "you rock",
})

_POSITIVE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^(thanks|thx|ty|thank\s+you)\b",
        r"\b(perfect|exactly|spot\s+on|nailed\s+it)\b",
        r"^(great|awesome|nice|amazing|brilliant|excellent|wonderful)\b",
        r"\bthat('?s|\s+is)\s+(exactly\s+)?(what\s+i\s+)?(needed|wanted|was looking for)\b",
        r"\b(love\s+(it|this)|very\s+helpful|super\s+helpful)\b",
    )
]


def detect_positive_reaction(user_text: str) -> bool:
    """Return True when the user is expressing satisfaction with the prior response."""
    t = (user_text or "").strip()
    if not t:
        return False
    low = t.lower().strip("!.… ")
    if low in _POSITIVE_STARTERS:
        return True
    return any(p.search(t) for p in _POSITIVE_PATTERNS)


# ---------------------------------------------------------------------------
# Negative reaction detection
# ---------------------------------------------------------------------------

_NEGATIVE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(useless|worthless|terrible|horrible|awful)\b",
        r"\bthat('?s|\s+is)\s+(bad|garbage|trash|junk|rubbish|crap)\b",
        r"\b(you\s+suck|you'?re\s+useless|you'?re\s+broken)\b",
        r"\b(stop|quit|shut\s+up|give\s+up|forget\s+it|never\s+mind)\b",
    )
]


def detect_negative_reaction(user_text: str) -> bool:
    """Return True when the user expresses frustration or dissatisfaction."""
    t = (user_text or "").strip()
    if not t:
        return False
    return any(p.search(t) for p in _NEGATIVE_PATTERNS)


# ---------------------------------------------------------------------------
# Composite outcome score
# ---------------------------------------------------------------------------

def classify_user_signal(
    user_text: str,
    previous_texts: list[str] | None = None,
) -> str:
    """Classify the user's reaction to the previous turn.

    Returns one of: "positive", "corrective", "repetition", "negative", "neutral".
    """
    if detect_positive_reaction(user_text):
        return "positive"
    if detect_correction(user_text):
        return "corrective"
    if previous_texts and detect_repetition(user_text, previous_texts):
        return "repetition"
    if detect_negative_reaction(user_text):
        return "negative"
    return "neutral"


def compute_outcome_score(
    *,
    user_signal: str,
    tool_success_rate: float,
    reply_length: int,
    duration_seconds: float,
) -> float:
    """Compute a composite 0.0–1.0 outcome score for a completed turn.

    Higher = better outcome. The score blends:
    - User reaction signal (dominant factor)
    - Tool success rate
    - Reply substantiveness (did the agent produce content?)
    - Efficiency (reasonable response time)
    """
    # Base score from user signal
    signal_scores = {
        "positive": 0.90,
        "neutral": 0.55,
        "corrective": 0.20,
        "repetition": 0.15,
        "negative": 0.05,
    }
    base = signal_scores.get(user_signal, 0.55)

    # Tool effectiveness modifier (±0.15)
    if tool_success_rate >= 0.0:
        tool_mod = (tool_success_rate - 0.5) * 0.30  # range: -0.15 to +0.15
    else:
        tool_mod = 0.0

    # Reply substantiveness (small bonus for actually producing content)
    reply_mod = 0.0
    if reply_length < 10:
        reply_mod = -0.10  # empty or near-empty reply is bad
    elif reply_length > 50:
        reply_mod = 0.05   # produced real content

    # Combine and clamp
    score = base + tool_mod + reply_mod
    return max(0.0, min(1.0, score))


def extract_correction_content(user_text: str) -> str | None:
    """Extract the corrective instruction from a correction message.

    Returns the portion of text after the correction signal, or None if no
    correction was detected.
    """
    if not detect_correction(user_text):
        return None
    t = (user_text or "").strip()
    # Try to extract the "actual" part after the correction marker.
    for pat in _CORRECTION_PATTERNS:
        m = pat.search(t)
        if m:
            after = t[m.end():].strip(" ,.:;—-")
            if len(after) > 10:
                return after
    # Fallback: return the full message as the correction content.
    return t
