"""Experience distillation — automatic memory formation from conversations.

A background process that periodically reviews recent conversation history
and extracts durable experiences into existing stores (knowledge.md,
relational notes, beliefs, journal).  Runs on a hybrid time + turn trigger
like soma ticks, using the reflex model for cheap structured extraction.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

import structlog

from ngram.cognition.history_compression import serialize_for_summary
from ngram.memory.knowledge import append_knowledge_sections

if TYPE_CHECKING:
    from ngram.config import DistillationSettings, EntityConfig
    from ngram.memory.beliefs import BeliefStore
    from ngram.memory.journal import Journal
    from ngram.memory.knowledge import KnowledgeStore
    from ngram.memory.relational import RelationalMemory

log = structlog.get_logger("ngram.memory.distillation")


# ---------------------------------------------------------------------------
# Result schema
# ---------------------------------------------------------------------------

@dataclass
class KnowledgeFact:
    title: str
    body: str


@dataclass
class RelationalInsight:
    person_id: str
    person_name: str
    note: str
    warmth_delta: float = 0.0
    trust_delta: float = 0.0


@dataclass
class BeliefExtract:
    category: str
    content: str
    confidence: float = 0.8


@dataclass
class DistillationResult:
    knowledge: list[KnowledgeFact] = field(default_factory=list)
    relational: list[RelationalInsight] = field(default_factory=list)
    beliefs: list[BeliefExtract] = field(default_factory=list)
    journal_entry: str | None = None


# ---------------------------------------------------------------------------
# LLM prompt
# ---------------------------------------------------------------------------

_DISTILL_PROMPT = """\
You are reviewing a conversation window for an AI entity named {entity_name}. \
Extract ONLY information worth remembering permanently. Be highly selective — \
most conversation is ephemeral and should NOT be extracted.

PARTICIPANTS: {participants}
ENTITY EMOTIONAL STATE: {soma_snapshot}

CONVERSATION WINDOW:
{conversation}

Return a JSON object with exactly these keys. \
Use empty arrays and null when a category has nothing worth extracting.

{{"knowledge": [{{"title": "short section title", "body": "the durable fact or preference"}}], \
"relational": [{{"person_id": "id", "person_name": "name", "note": "insight about this person", \
"warmth_delta": 0.0, "trust_delta": 0.0}}], \
"beliefs": [{{"category": "preference|correction|world_fact|self_insight", \
"content": "the belief", "confidence": 0.8}}], \
"journal_entry": null}}

Rules:
- knowledge: durable facts about people, places, projects, preferences, corrections. \
NOT task ephemera or things already in knowledge.
- relational: insights about a person's character, style, what matters to them. \
Deltas are small (-0.05 to +0.05).
- beliefs: corrections to prior assumptions, newly formed opinions, self-discoveries.
- journal_entry: only if the entity had a genuine self-insight. Null is the expected default.
- When emotional intensity is high, lower your threshold slightly for what counts as durable.
- Return ONLY valid JSON. No markdown fences, no explanation.\
"""

_DISTILL_PROMPT_NO_RELATIONAL = """\
You are reviewing a conversation window for an AI entity named {entity_name}. \
Extract ONLY information worth remembering permanently. Be highly selective — \
most conversation is ephemeral and should NOT be extracted.

PARTICIPANTS: {participants}
ENTITY EMOTIONAL STATE: {soma_snapshot}

CONVERSATION WINDOW:
{conversation}

Return a JSON object with exactly these keys (omit relational insights — they are handled elsewhere). \
Use empty arrays and null when a category has nothing worth extracting.

{{"knowledge": [{{"title": "short section title", "body": "the durable fact or preference"}}], \
"beliefs": [{{"category": "preference|correction|world_fact|self_insight", \
"content": "the belief", "confidence": 0.8}}], \
"journal_entry": null}}

Rules:
- knowledge: durable facts about people, places, projects, preferences, corrections. \
NOT task ephemera or things already in knowledge.
- beliefs: corrections to prior assumptions, newly formed opinions, self-discoveries.
- journal_entry: only if the entity had a genuine self-insight. Null is the expected default.
- Return ONLY valid JSON. No markdown fences, no explanation.\
"""


# ---------------------------------------------------------------------------
# Core engine
# ---------------------------------------------------------------------------

class ExperienceDistiller:
    """Periodically extracts durable experiences from conversation history."""

    def __init__(self, settings: DistillationSettings) -> None:
        self._settings = settings
        self._last_tick: float = 0.0
        self._last_turn_index: int = 0
        self._last_hash: str = ""

    def should_distill(self, history_len: int, emotional_intensity: float) -> bool:
        if not self._settings.enabled:
            return False
        turns_since = history_len - self._last_turn_index
        if turns_since < self._settings.min_turns_absolute:
            return False
        elapsed = time.monotonic() - self._last_tick
        time_ready = elapsed >= self._settings.cycle_seconds
        effective_min = self._settings.min_turns
        if emotional_intensity > 0.6:
            scale = 1.0 + (emotional_intensity - 0.6) * (
                self._settings.soma_urgency_divisor - 1.0
            ) / 0.4
            effective_min = max(
                self._settings.min_turns_absolute, int(effective_min / scale)
            )
        turn_ready = turns_since >= effective_min
        return time_ready or turn_ready

    async def distill(
        self,
        client: Any,
        model: str,
        *,
        history_window: list[dict[str, Any]],
        entity_name: str,
        participants: list[str],
        soma_snapshot: str,
        num_ctx: int | None = None,
        skip_relational_extract: bool = False,
    ) -> DistillationResult | None:
        window_hash = self._window_hash(history_window)
        if window_hash == self._last_hash:
            log.debug("distillation_skipped_idempotent")
            return None

        conversation = serialize_for_summary(history_window, per_msg_cap=1500)
        budget = self._settings.context_char_budget
        if len(conversation) > budget:
            conversation = conversation[-budget:]

        tmpl = _DISTILL_PROMPT_NO_RELATIONAL if skip_relational_extract else _DISTILL_PROMPT
        prompt = tmpl.format(
            entity_name=entity_name,
            participants=", ".join(participants) if participants else "(unknown)",
            soma_snapshot=soma_snapshot or "(neutral)",
            conversation=conversation,
        )
        try:
            res = await client.chat_completion(
                model,
                [
                    {
                        "role": "system",
                        "content": (
                            "You extract durable memories from conversations. "
                            "Output exactly one JSON object."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=self._settings.temperature,
                max_tokens=self._settings.max_extract_tokens,
                think=False,
                num_ctx=num_ctx,
            )
        except Exception as e:
            log.warning("distillation_llm_failed", error=str(e))
            return None

        result = _parse_result((res.content or "").strip())
        if result is None:
            return None

        self._last_tick = time.monotonic()
        self._last_turn_index += len(history_window)
        self._last_hash = window_hash
        log.info(
            "distillation_extracted",
            knowledge=len(result.knowledge),
            relational=len(result.relational),
            beliefs=len(result.beliefs),
            journal=bool(result.journal_entry),
        )
        return result

    async def route_results(
        self,
        result: DistillationResult,
        *,
        entity_config: EntityConfig,
        knowledge_store: KnowledgeStore,
        relational: RelationalMemory,
        beliefs: BeliefStore,
        journal: Journal,
        conn: Any,
        client: Any,
        relational_doc_mode: bool = False,
        relational_docs: Any = None,
    ) -> dict[str, int]:
        counts: dict[str, int] = {"knowledge": 0, "relational": 0, "beliefs": 0, "journal": 0}

        if result.knowledge:
            sections = [(k.title, k.body) for k in result.knowledge]
            written = append_knowledge_sections(entity_config, sections)
            if written:
                await knowledge_store.refresh_after_edit()
            counts["knowledge"] = written

        for ri in result.relational:
            try:
                if relational_doc_mode and relational_docs is not None:
                    note = f"{ri.note} (warmth {ri.warmth_delta:+.2f}, trust {ri.trust_delta:+.2f})"
                    await relational_docs.add_pending_distillation(conn, ri.person_id, note)
                else:
                    await relational.upsert_interaction(
                        conn,
                        ri.person_id,
                        ri.person_name,
                        warmth_delta=max(-0.1, min(0.1, ri.warmth_delta)),
                        trust_delta=max(-0.1, min(0.1, ri.trust_delta)),
                        note=ri.note,
                    )
                counts["relational"] += 1
            except Exception as e:
                log.debug("distillation_relational_failed", person=ri.person_name, error=str(e))

        for be in result.beliefs:
            try:
                emb = None
                emb_model = getattr(
                    getattr(entity_config, "harness", None),
                    "models", None,
                )
                if emb_model:
                    emb_model = getattr(emb_model, "embedding", None)
                if emb_model:
                    try:
                        emb = await client.embed(emb_model, be.content[:2000])
                    except Exception:
                        pass
                await beliefs.add_belief(
                    conn,
                    category=be.category,
                    content=be.content,
                    confidence=be.confidence,
                    source="distillation",
                    embedding=emb,
                )
                counts["beliefs"] += 1
            except Exception as e:
                log.debug("distillation_belief_failed", error=str(e))

        if result.journal_entry and result.journal_entry.strip():
            try:
                await journal.write_entry(
                    result.journal_entry.strip(), tags=["distillation", "self-insight"]
                )
                counts["journal"] = 1
            except Exception as e:
                log.debug("distillation_journal_failed", error=str(e))

        return counts

    @staticmethod
    def _window_hash(window: list[dict[str, Any]]) -> str:
        h = hashlib.md5(usedforsecurity=False)
        for msg in window:
            h.update(str(msg.get("content", ""))[:500].encode("utf-8", errors="replace"))
        return h.hexdigest()


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------

def _parse_result(raw: str) -> DistillationResult | None:
    if not raw:
        return None
    text = raw
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        log.info("distillation_parse_failed", raw_len=len(raw))
        return None
    if not isinstance(parsed, dict):
        return None

    knowledge = []
    for item in parsed.get("knowledge") or []:
        if isinstance(item, dict) and item.get("title") and item.get("body"):
            knowledge.append(KnowledgeFact(
                title=str(item["title"]).strip(),
                body=str(item["body"]).strip(),
            ))

    relational = []
    for item in parsed.get("relational") or []:
        if isinstance(item, dict) and item.get("person_name") and item.get("note"):
            relational.append(RelationalInsight(
                person_id=str(item.get("person_id") or item["person_name"]).strip(),
                person_name=str(item["person_name"]).strip(),
                note=str(item["note"]).strip(),
                warmth_delta=float(item.get("warmth_delta") or 0),
                trust_delta=float(item.get("trust_delta") or 0),
            ))

    beliefs_list = []
    for item in parsed.get("beliefs") or []:
        if isinstance(item, dict) and item.get("content"):
            beliefs_list.append(BeliefExtract(
                category=str(item.get("category") or "world_fact").strip(),
                content=str(item["content"]).strip(),
                confidence=max(0.0, min(1.0, float(item.get("confidence") or 0.8))),
            ))

    journal_entry = parsed.get("journal_entry")
    if isinstance(journal_entry, str):
        journal_entry = journal_entry.strip() or None
    else:
        journal_entry = None

    result = DistillationResult(
        knowledge=knowledge,
        relational=relational,
        beliefs=beliefs_list,
        journal_entry=journal_entry,
    )
    if not knowledge and not relational and not beliefs_list and not journal_entry:
        return None
    return result


# ---------------------------------------------------------------------------
# Correction-driven learning
# ---------------------------------------------------------------------------

async def detect_and_persist_correction(
    *,
    user_text: str,
    agent_reply: str,
    conn: Any,
    beliefs: Any,
    procedural: Any,
    client: Any,
    entity_config: Any,
) -> bool:
    """Detect a user correction and persist it as a belief + procedural skill.

    Called from ``_commit_turn()`` when the user signal is ``"corrective"``.
    Creates:
    - A high-confidence (0.95) belief with source ``"correction"``
    - A procedural skill titled ``correction-<slug>`` with the rule

    Returns True if a correction was persisted.
    """
    from ngram.memory.outcome_signals import extract_correction_content

    correction = extract_correction_content(user_text)
    if not correction or len(correction) < 10:
        return False

    # Build a concise rule from the correction context
    rule_body = (
        f"When I said: {(agent_reply or '').strip()[:300]}\n\n"
        f"The user corrected me: {correction[:500]}\n\n"
        f"Rule: {correction[:300]}"
    )

    # Persist as a high-confidence belief
    try:
        emb = None
        emb_model = getattr(
            getattr(entity_config, "harness", None),
            "models", None,
        )
        if emb_model:
            emb_model = getattr(emb_model, "embedding", None)
        if emb_model and client:
            try:
                emb = await client.embed(emb_model, correction[:2000])
            except Exception:
                pass
        await beliefs.add_belief(
            conn,
            category="correction",
            content=correction[:1000],
            confidence=0.95,
            source="user_correction",
            embedding=emb,
        )
    except Exception as e:
        log.debug("correction_belief_failed", error=str(e))

    # Persist as a procedural skill
    try:
        # Create a short slug from the first few words
        words = correction.split()[:5]
        slug = "-".join(w.lower().strip(".,!?;:") for w in words if w.strip())
        skill_name = f"correction-{slug}"[:60]
        await procedural.upsert_skill(skill_name, rule_body)
    except Exception as e:
        log.debug("correction_skill_failed", error=str(e))

    log.info(
        "correction_learning_applied",
        correction_len=len(correction),
    )
    return True
