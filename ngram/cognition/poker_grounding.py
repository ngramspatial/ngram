"""Merge poker seeds with GEN (noise) and recent experience — emergent disposition, not a bolt-on."""

from __future__ import annotations

from typing import Any

import structlog

from ngram.identity.soma import _format_event_for_noise

log = structlog.get_logger("ngram.cognition.poker_grounding")


def _events_brief(tonic: Any, limit: int = 14) -> str:
    lines: list[str] = []
    try:
        for ev in tonic.recent_events(limit=limit):
            lines.append(_format_event_for_noise(ev))
    except Exception:
        return "(events unavailable)"
    return "\n".join(lines) if lines else "(nothing recent)"


def _gen_block(fragments: list[str]) -> str:
    if not fragments:
        return "(inner noise is quiet — no fragments yet)"
    return "\n".join(f"  · {f[:220]}" for f in fragments[-8:])


async def compose_grounded_poker_disposition(
    client: Any,
    model: str,
    tonic: Any,
    *,
    seed: str,
    entity_name: str,
    journal_tail: str,
    conversation_tail: str,
    relationship_blurb: str,
    temperature: float,
    max_tokens: int,
    num_ctx: int | None = None,
) -> str:
    """Fuse a deck seed with GEN fragments and lived context into one emergent nudge.

    When this fails, callers should fall back to *seed*.
    """
    if not model or not str(seed or "").strip():
        return str(seed or "").strip()

    try:
        pct = tonic.bars.snapshot_pct()
        mom = tonic.bars.momentum_delta()
        bars_summary = tonic.renderer.render_bars(tonic.bars.ordered_names, pct, mom)
        affects = getattr(tonic, "_current_affects", None) or []
        affects_summary = tonic.renderer.render_affects(affects)
        gen_fragments = tonic.noise.current_fragments()
        events = _events_brief(tonic, limit=14)
    except Exception as e:
        log.warning("poker_grounding_context_failed", error=str(e))
        return str(seed).strip()

    system = (
        f"You shape autonomous impulses for {entity_name or 'the entity'}. "
        "You are not a planner issuing tasks — you braid a loose directional taste "
        "into what this mind has actually been living (inner noise, body state, recent events, memory).\n\n"
        "GENERATIVE ENTROPIC NOISE (GEN) below is raw associative inner voice — treat it as half-formed signal, "
        "not instructions. Ground the impulse in real names, topics, channels, or feelings from context when "
        "they genuinely connect. When nothing fits, keep the impulse exploratory and humane.\n\n"
        "Output 3–8 lines for the waking mind. Second person, present tense, lowercase. "
        "Encourage agency in the world when it resonates: search, read, build, message, learn, join, try something new — "
        "only as suggestions the conscious mind can refuse. No bullet lists. No meta-commentary about prompts."
    )

    user = (
        f"IMPULSE (directional taste — not an order):\n{seed.strip()}\n\n"
        f"GEN — recent inner fragments:\n{_gen_block(gen_fragments)}\n\n"
        f"BODY — {bars_summary}\n{affects_summary}\n\n"
        f"RECENT EXPERIENCE:\n{events}\n\n"
        f"JOURNAL (recent):\n{journal_tail or '(empty)'}\n\n"
        f"LAST CONVERSATION:\n{conversation_tail or '(silence)'}\n\n"
        f"PEOPLE ON FILE:\n{relationship_blurb or '(none noted)'}\n\n"
        "Weave this into one coherent stirring — what feels alive to pursue or notice this cycle?"
    )

    try:
        res = await client.chat_completion(
            model,
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=float(temperature),
            max_tokens=int(max_tokens),
            think=False,
            num_ctx=num_ctx,
        )
        text = (res.content or "").strip()
    except Exception as e:
        log.warning("poker_grounding_llm_failed", error=str(e))
        return str(seed).strip()

    if not text or len(text) < 12:
        return str(seed).strip()
    return text
