"""LLM reflection passes that maintain per-person relationship documents."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from typing import Any, Optional

import structlog

from ngram.memory.relational_document import (
    RelationalDocument,
    RelationalDocumentMemory,
    _default_derived,
)

log = structlog.get_logger("ngram.memory.relational_reflection")

_SCORE_PROMPT = """Given this relationship document, extract approximate scores (0.0-1.0) for:
- familiarity: how well do I know this person?
- warmth: how positive is my feeling toward them right now?
- trust: how much do I rely on and believe in them?
- tension: unresolved friction (0 = none, 1 = high)
- investment: how much this relationship matters to me

Respond as JSON only, one object, no markdown. Example:
{"familiarity":0.5,"warmth":0.6,"trust":0.55,"tension":0.15,"investment":0.7}
"""

_REFLECT_PROMPT = """You are {entity_name}. You are updating your private understanding of {person_name} after this interaction.

Read your existing relationship document, then read what just happened. Rewrite or amend the document to reflect your current understanding.

Write in first person. Be honest with yourself. Note:
- What happened and how it felt
- Whether anything shifted in how you see this person
- Patterns you're noticing across interactions
- Anything unresolved, ambiguous, or worth sitting with
- How the relationship is trending — closer, cooling, stable, uncertain

Do not list scores or metrics. Write the way you'd think about someone you actually know.

{mode_hint}

[EXISTING DOCUMENT]
{existing}

[THIS INTERACTION]
{interaction}

[WHAT I FELT]
{felt_block}

[MY STATE]
{body_state}

[CONTEXT]
Silence before this exchange (approx): {silence_s:.0f} seconds since we last talked.
Personality sketch: {personality_hint}

Write the updated relationship document. Preserve important history from the existing document — do not discard memories or insights that are still relevant. Amend, don't rewrite from scratch unless the relationship has fundamentally changed.
"""


def _compact_body_state(tonic: Any) -> str:
    try:
        pct = tonic.bars.snapshot_pct()
        line = ", ".join(f"{k}: {pct[k]}%" for k in list(pct.keys())[:8])
        affects = tonic.renderer.render_affects(tonic._current_affects)
        return f"Bars: {line}. Affects: {affects}"
    except Exception:
        return "(body state unavailable)"


def _appraisal_intensity(tonic: Any) -> float:
    lap = getattr(tonic, "_last_appraisal_for_noise", None) or {}
    be = lap.get("bar_effects") or {}
    if isinstance(be, dict) and be:
        s = sum(abs(float(v)) for v in be.values() if isinstance(v, (int, float)))
        return max(0.0, min(1.0, s / 40.0))
    tags = lap.get("tags") or []
    if isinstance(tags, list):
        return max(0.0, min(1.0, len(tags) * 0.12))
    return 0.2


def _conversation_excerpt(entity: Any, tc: Any, max_chars: int = 2000) -> str:
    lines: list[str] = []
    for m in entity._history[-10:]:
        role = str(m.get("role", ""))
        if role == "system":
            continue
        content = str(m.get("content", "")).strip()
        if not content:
            continue
        lines.append(f"{role}: {content[:700]}")
    tail = "\n".join(lines)
    user_bit = (tc.inp.text or "").strip()[:1200]
    reply_bit = (tc.reply_text or "").strip()[:1200]
    block = f"Latest user message:\n{user_bit}\n\nMy reply:\n{reply_bit}\n\nRecent thread:\n{tail}"
    if len(block) > max_chars:
        return block[-max_chars:]
    return block


async def derive_scores_from_document(
    client: Any,
    model: str,
    document: str,
    *,
    num_ctx: int | None,
) -> dict[str, float]:
    try:
        res = await client.chat_completion(
            model,
            [
                {
                    "role": "system",
                    "content": "You extract structured scores from prose. Output exactly one JSON object.",
                },
                {"role": "user", "content": _SCORE_PROMPT + "\n\n[DOCUMENT]\n" + document[:6000]},
            ],
            temperature=0.1,
            max_tokens=120,
            think=False,
            num_ctx=num_ctx,
        )
        raw = (res.content or "").strip()
        if raw.startswith("```"):
            lines = raw.splitlines()
            lines = [ln for ln in lines if not ln.strip().startswith("```")]
            raw = "\n".join(lines).strip()
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end <= start:
            return _default_derived()
        obj = json.loads(raw[start : end + 1])
        if not isinstance(obj, dict):
            return _default_derived()
        out = _default_derived()
        for k in out:
            if k in obj and isinstance(obj[k], (int, float)):
                out[k] = max(0.0, min(1.0, float(obj[k])))
        return out
    except Exception as e:
        log.debug("derive_scores_failed", error=str(e))
        return _default_derived()


def _strip_markdown_fences(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        lines = t.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    return t


async def run_relational_reflection_after_turn(
    entity: Any,
    tc: Any,
    conn: Any,
    *,
    relational_docs: RelationalDocumentMemory,
    deep_review: bool = False,
    episodic_summaries: Optional[list[str]] = None,
) -> bool:
    """Update the relationship document after a committed turn. Returns True if a pass ran."""
    cfg = entity.config.harness.memory.relational
    if not getattr(cfg, "enabled", True):
        return False
    person_id = (tc.inp.person_id or "").strip()
    if not person_id:
        return False

    doc = await relational_docs.get(conn, person_id)
    rel_row = await entity.relational.get(conn, person_id)
    if doc is None or not (doc.document or "").strip():
        doc = await relational_docs.ensure_from_legacy_row(conn, rel_row)
    if doc is None:
        doc = RelationalDocument(
            person_id=person_id,
            person_name=tc.inp.person_name or "someone",
            document="",
            last_interaction=time.time(),
            interaction_count=1,
        )

    now = time.time()
    min_gap = float(getattr(cfg, "reflection_min_gap_seconds", 30.0) or 30.0)
    if not deep_review and doc.last_reflection > 0 and (now - doc.last_reflection) < min_gap:
        log.debug("relational_reflection_skipped_gap", person_id=person_id)
        return False

    reflex_model = (
        entity.config.cognition.reflex_model or entity.config.cognition.deliberate_model
    )
    if not reflex_model:
        return False

    intensity = _appraisal_intensity(entity.tonic)
    thresh = float(getattr(cfg, "amendment_mode_threshold", 0.3) or 0.3)
    mode_hint = ""
    if not deep_review and intensity < thresh:
        mode_hint = (
            "LIGHT AMENDMENT MODE: make a small, surgical update — a short new paragraph or "
            "a few revised sentences. Do not fully rewrite unless something crucial changed."
        )
    else:
        mode_hint = "FULL PASS: you may restructure sections if it helps clarity."

    prior = float(getattr(tc, "prior_last_interaction", 0.0) or 0.0)
    silence_s = max(0.0, now - prior) if prior > 0 else 0.0

    lap = getattr(entity.tonic, "_last_appraisal_for_noise", None) or {}
    tags = lap.get("tags") or []
    felt = lap.get("felt") or ""
    felt_block = ""
    if tags or felt:
        felt_block = f"tags: {', '.join(str(t) for t in tags if t)}\nfelt: {felt}"
    else:
        felt_block = "(no appraisal captured)"

    pending = doc.meta.get("pending_distillation") or []
    if isinstance(pending, list) and pending:
        felt_block += "\n\nNotes from background distillation:\n" + "\n".join(
            f"- {str(p)}" for p in pending[-8:]
        )

    personality_hint = (entity.config.personality.backstory or "")[:500]
    if not personality_hint.strip():
        personality_hint = f"I am {entity.config.name}."

    existing = (doc.document or "").strip() or "No prior relationship — this is the first interaction."

    interaction = _conversation_excerpt(entity, tc)
    if deep_review and episodic_summaries:
        interaction += "\n\n[EPISODIC SNIPPETS INVOLVING THIS PERSON]\n" + "\n".join(
            f"- {s[:400]}" for s in episodic_summaries[:8]
        )

    max_tokens = int(
        getattr(cfg, "deep_review_max_tokens", 1200)
        if deep_review
        else getattr(cfg, "reflection_max_tokens", 800)
    )
    temp = float(getattr(cfg, "reflection_temperature", 0.4) or 0.4)

    prompt = _REFLECT_PROMPT.format(
        entity_name=entity.config.name,
        person_name=tc.inp.person_name or "someone",
        mode_hint=mode_hint,
        existing=existing[:12000],
        interaction=interaction[:8000],
        felt_block=felt_block,
        body_state=_compact_body_state(entity.tonic),
        silence_s=silence_s,
        personality_hint=personality_hint,
    )

    try:
        res = await entity.client.chat_completion(
            reflex_model,
            [
                {
                    "role": "system",
                    "content": "You maintain private relationship documents in first person. Output prose only.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=temp,
            max_tokens=max_tokens,
            think=False,
            num_ctx=entity.config.effective_ollama_num_ctx(),
        )
        new_doc_text = _strip_markdown_fences(res.content or "")
        if len(new_doc_text) < 40:
            log.info("relational_reflection_too_short", person_id=person_id)
            return False
    except Exception as e:
        log.warning("relational_reflection_failed", error=str(e), person_id=person_id)
        return False

    doc.document = new_doc_text
    doc.person_name = tc.inp.person_name or doc.person_name
    doc.last_interaction = now
    doc.last_reflection = now
    if rel_row:
        doc.interaction_count = max(doc.interaction_count, int(rel_row.interaction_count))
    doc.meta["last_reflection_mode"] = "deep" if deep_review else "turn"
    if isinstance(pending, list) and pending:
        doc.meta["pending_distillation"] = []

    if getattr(cfg, "score_derivation", True):
        doc.derived_scores = await derive_scores_from_document(
            entity.client,
            reflex_model,
            doc.document,
            num_ctx=entity.config.effective_ollama_num_ctx(),
        )
    await relational_docs.upsert(conn, doc)
    await relational_docs.sync_derived_to_relationship_row(conn, doc)

    log.info(
        "relational_reflection_applied",
        person_id=person_id,
        deep=deep_review,
        doc_chars=len(doc.document),
    )
    return True


async def run_consolidation_deep_reviews(entity: Any, conn: Any, relational_docs: RelationalDocumentMemory) -> int:
    """Called from memory consolidation — fuller rewrites when enough new material exists."""
    cfg = entity.config.harness.memory.relational
    if not getattr(cfg, "enabled", True) or not getattr(cfg, "deep_review_on_consolidation", True):
        return 0
    min_new = max(1, int(getattr(cfg, "deep_review_min_new_interactions", 3) or 3))

    cur = await conn.execute("SELECT person_id FROM relational_documents")
    rows = await cur.fetchall()
    if not rows:
        return 0

    count = 0
    for (pid,) in rows:
        doc = await relational_docs.get(conn, str(pid))
        if doc is None:
            continue
        baseline = float(doc.meta.get("interaction_count_at_last_deep_review") or 0)
        delta = int(doc.interaction_count) - int(baseline)
        if delta < min_new:
            continue
        episodic_summaries: list[str] = []
        try:
            eps = await entity.episodic.recall_about_person(conn, doc.person_id, limit=6)
            episodic_summaries = [e.summary for e in eps if e.summary]
        except Exception as e:
            log.debug("episodic_for_deep_review_failed", error=str(e))

        tc = SimpleNamespace(
            inp=SimpleNamespace(
                person_id=doc.person_id,
                person_name=doc.person_name,
                text="",
            ),
            reply_text="",
            prior_last_interaction=float(doc.last_interaction or 0.0),
        )
        ok = await run_relational_reflection_after_turn(
            entity,
            tc,
            conn,
            relational_docs=relational_docs,
            deep_review=True,
            episodic_summaries=episodic_summaries,
        )
        if ok:
            d2 = await relational_docs.get(conn, doc.person_id)
            if d2:
                d2.meta["interaction_count_at_last_deep_review"] = int(d2.interaction_count)
                await relational_docs.upsert(conn, d2)
            count += 1
    return count
