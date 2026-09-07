"""Classify reflex vs deliberate; assemble context package."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

import structlog

from ngram.config import EntityConfig
from ngram.models import EmotionCategory, EmotionalState, Input
from ngram.inference.protocol import InferenceProvider

log = structlog.get_logger("ngram.cognition.router")


RouteKind = Literal["reflex", "deliberate"]
RouteAssessment = Literal["chat", "grounded", "exact", "deep"]


@dataclass
class ContextPackage:
    emotional_state: EmotionalState
    memory_snippets: list[str] = field(default_factory=list)
    relationship_blurb: str = ""
    drives_summary: str = ""
    inner_summary: str = ""
    reflex_hint: str = ""


def _identity_or_self_question(low: str) -> bool:
    """Short questions with ? were reflex-routed and hit reflex_max_tokens — bad for name/self."""
    phrases = (
        "who are you",
        "what are you",
        "what're you",
        "whatre you",
        "your name",
        "what's your name",
        "whats your name",
        "what is your name",
        "who am i talking",
        "what should i call you",
        "do you have a name",
        "tell me about yourself",
        "what do you call yourself",
        "are you an ai",
        "are you a bot",
        "are you human",
        "are you real",
        "are you a person",
    )
    return any(p in low for p in phrases)


def _file_or_workspace_question(low: str) -> bool:
    """Needs deliberate + tools (read_file, list_directory); reflex has no tool API."""
    if "readme" in low:
        return True
    if ".md" in low or ".txt" in low or ".yaml" in low or ".yml" in low:
        return True
    workspace_terms = (
        "workspace",
        "repo",
        "repository",
        "project root",
        "working directory",
        "current directory",
    )
    if any(term in low for term in workspace_terms):
        return True
    if re.search(r"\b(list|show|check|look\s+in|look\s+around)\b.*\b(files?|folders?|directories?)\b", low):
        return True
    if re.search(
        r"\bwhat(?:'s| is)?\s+in\b.*\b(workspace|repo|repository|project|folder|directory)\b",
        low,
    ):
        return True
    if re.search(r"\bwhat\s+files?\b.*\b(have|there|workspace|repo|repository|project)\b", low):
        return True
    if re.search(r"\bline\s+\d+", low):
        return True
    if "what's on line" in low or "whats on line" in low or "what is on line" in low:
        return True
    return False


def _capability_question(low: str) -> bool:
    """Needs deliberate + tools; short phrasing was reflex-routed without tool context."""
    phrases = (
        "what can you do",
        "what you can do",
        "what do you do",
        "what are you able",
        "what can u do",
        "what u can do",
        "your capabilities",
        "list your tools",
        "what tools",
        "do you have tools",
        "can you search the web",
        "can you browse",
    )
    return any(p in low for p in phrases)


def _short_casual_vibe(low: str, char_len: int) -> bool:
    """Keep small talk on reflex; classifier often wrongly picks DELIBERATE for slang."""
    if char_len > 120:
        return False
    markers = (
        "wagwan",
        "wag1",
        "waddup",
        "whaddup",
        "sup ",
        " sup",
        " nm",
        "nm ",
        "nuthin",
        "nothin much",
        "nothing much",
        "chillin",
        "chilling",
        "just vibing",
        "yo ",
        " aye",
        "safe ",
        "innit",
        "lol",
        "lmao",
        "nah ",
        " naw",
    )
    return any(m in low for m in markers)


def _tiny_non_identity_reaction(t: str, low: str) -> bool:
    """Short interjections like ``WHAT?`` — keep reflex so the classifier can't go cosmic."""
    if len(t) > 18 or "?" not in t:
        return False
    return not _identity_or_self_question(low)


class CognitionRouter:
    def __init__(self, entity: EntityConfig, client: InferenceProvider) -> None:
        self.entity = entity
        self.client = client

    def heuristic_route(self, inp: Input, emotional: EmotionalState) -> RouteKind:
        if inp.platform == "automation":
            return "deliberate"
        t = inp.text.strip()
        low = t.lower()
        if len(t) < 4:
            return "reflex"
        if low in ("hi", "hello", "hey", "thanks", "thank you", "ok", "okay", "bye"):
            return "reflex"
        if _identity_or_self_question(low) or _capability_question(low):
            return "deliberate"
        if _file_or_workspace_question(low):
            return "deliberate"
        if "?" in t and len(t) < 80 and emotional.intensity < 0.6:
            return "reflex"
        if any(
            w in low
            for w in (
                "why",
                "meaning",
                "feel",
                "afraid",
                "love",
                "death",
                "exist",
                "philosophy",
            )
        ):
            return "deliberate"
        if emotional.primary in (
            EmotionCategory.MELANCHOLY,
            EmotionCategory.ANXIOUS,
            EmotionCategory.AFFECTIONATE,
        ):
            return "deliberate"
        return "deliberate" if len(t) > 200 else "reflex"

    async def classify_with_reflex(self, inp: Input) -> tuple[RouteKind, RouteAssessment, float]:
        """Use reflex model for a lightweight grounding/exactness judgment."""
        model = self.entity.cognition.reflex_model
        messages = [
            {
                "role": "system",
                "content": (
                    "Reply with exactly one token: CHAT, GROUNDED, EXACT, or DEEP.\n"
                    "CHAT = casual chat, greeting, joke, reaction, or simple reply from memory is fine.\n"
                    "GROUNDED = likely needs tools or verification against files, workspace, web, system state, or another source.\n"
                    "EXACT = user wants exact text, line, path, stdout, filename, quote, error, or other precise value; prefer verification.\n"
                    "DEEP = emotional depth, creative reasoning, philosophy, or multi-step thinking.\n"
                    "When unsure between CHAT and GROUNDED, choose GROUNDED."
                ),
            },
            {"role": "user", "content": inp.text[:1500]},
        ]
        try:
            res = await self.client.chat_completion(
                model,
                messages,
                temperature=0.2,
                max_tokens=8,
                think=False,
                num_ctx=self.entity.effective_ollama_num_ctx(),
            )
            word = re.sub(r"[^A-Z]", "", (res.content or "").strip().upper())
            if "EXACT" in word:
                return "deliberate", "exact", 0.15
            if "GROUNDED" in word:
                return "deliberate", "grounded", 0.15
            if "DEEP" in word:
                return "deliberate", "deep", 0.2
            if "CHAT" in word:
                return "reflex", "chat", 0.2
        except Exception as e:
            log.warning("classify_with_reflex_failed", error=str(e))
        return "deliberate", "grounded", 0.6

    async def route(
        self,
        inp: Input,
        emotional: EmotionalState,
        context: ContextPackage,
    ) -> tuple[RouteKind, ContextPackage]:
        if inp.platform in ("automation", "autonomous"):
            context.reflex_hint = "deliberate"
            return "deliberate", context
        if self.entity.cognition.always_deliberate:
            context.reflex_hint = "deliberate"
            return "deliberate", context
        heuristic_kind = self.heuristic_route(inp, emotional)
        kind = heuristic_kind
        assessment: RouteAssessment | None = None
        t = inp.text.strip()
        low = t.lower()
        if kind == "reflex" and (
            _short_casual_vibe(low, len(t)) or _tiny_non_identity_reaction(t, low)
        ):
            context.reflex_hint = kind
            log.info("route_decision", route=kind, heuristic=heuristic_kind, platform=inp.platform, text_len=len(t))
            return kind, context
        if kind == "reflex":
            rk, assessment, unc = await self.classify_with_reflex(inp)
            thresh = self.entity.harness.cognition.escalation_threshold
            if rk == "deliberate" or assessment in ("grounded", "exact", "deep") or unc > 1.0 - thresh:
                kind = "deliberate"
        context.reflex_hint = kind
        log.info(
            "route_decision",
            route=kind,
            heuristic=heuristic_kind,
            assessment=assessment,
            platform=inp.platform,
            text_len=len(t),
        )
        return kind, context
