"""Semantic search over episodic memory (tool-facing wrapper around CLI recall)."""

from __future__ import annotations

import json

from ngram.memory.episodic import MIN_CONFIDENT_RECALL_SIMILARITY
from ngram.memory.store import cosine_sim
from ngram.models import EmotionCategory, Episode
from ngram.presence.tools.registry import tool
from ngram.presence.tools.runtime import require_tool_runtime


# Dense-vector search always has a nearest neighbor, even when the query has no
# real match.  A 0.50 cosine floor is intentionally conservative enough to stop
# those arbitrary neighbors from being presented as evidence while retaining
# clearly related paraphrases.
_MIN_CONFIDENT_SIMILARITY = MIN_CONFIDENT_RECALL_SIMILARITY


def _confidence_label(similarity: float) -> str:
    if similarity >= 0.75:
        return "high"
    if similarity >= 0.62:
        return "medium"
    return "moderate"


@tool(
    name="search_past_conversations",
    description=(
        "Search your episodic memory — past conversation summaries — by semantic similarity. "
        "Use when you need to recall what was discussed before, people mentioned, or prior context. "
        "Pass a natural-language query (not a keyword list). Results are narrative episodes, not raw "
        "chat logs. A no_confident_match outcome means memory does not establish that the event occurred."
    ),
)
async def search_past_conversations(query: str, limit: int = 8) -> str:
    ctx = require_tool_runtime()
    ent = ctx.entity
    q = (query or "").strip()
    if not q:
        return json.dumps({"error": "query is empty"}, ensure_ascii=False)
    lim = max(1, min(20, int(limit or 8)))
    model = ent.config.harness.models.embedding or ""
    if not model:
        return json.dumps({"error": "No embedding model configured."}, ensure_ascii=False)
    try:
        qe = await ent.client.embed(model, q)
        if not qe:
            return json.dumps({"error": "Embedding failed (inference unavailable?)."}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"embed failed: {e}"}, ensure_ascii=False)

    mood = ent.emotions.get_state().primary
    try:
        async with ent.store.session() as db:
            pairs = await ent.episodic.recall(
                db,
                q,
                qe,
                limit=lim,
                min_significance=0.0,
                current_mood=mood if mood != EmotionCategory.NEUTRAL else None,
            )
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)

    candidates: list[tuple[float, Episode]] = []
    for ep, _imprints in pairs:
        similarity = cosine_sim(qe, ep.embedding or [])
        candidates.append((similarity, ep))

    candidates.sort(key=lambda item: item[0], reverse=True)
    top_similarity = candidates[0][0] if candidates else None
    confident = [
        (similarity, ep)
        for similarity, ep in candidates
        if similarity >= _MIN_CONFIDENT_SIMILARITY
    ]

    if not confident:
        return json.dumps(
            {
                "ok": True,
                "query": q,
                "outcome": "no_confident_match",
                "no_confident_match": True,
                "confidence_threshold": _MIN_CONFIDENT_SIMILARITY,
                "top_similarity": (
                    round(top_similarity, 4) if top_similarity is not None else None
                ),
                "candidate_count": len(candidates),
                "results": [],
                "message": (
                    "No episodic memory met the semantic confidence threshold. "
                    "Do not infer that the queried event occurred."
                ),
            },
            ensure_ascii=False,
        )

    rows: list[dict[str, object]] = []
    for similarity, ep in confident:
        rows.append(
            {
                "id": ep.id,
                "timestamp": ep.timestamp,
                "summary": (ep.summary or "")[:2000],
                "participants": ep.participants,
                "significance": ep.significance,
                "tags": ep.tags,
                "emotional_imprint": getattr(ep.emotional_imprint, "value", str(ep.emotional_imprint)),
                "similarity": round(similarity, 4),
                "confidence": _confidence_label(similarity),
            }
        )
    return json.dumps(
        {
            "ok": True,
            "query": q,
            "outcome": "matches",
            "no_confident_match": False,
            "confidence_threshold": _MIN_CONFIDENT_SIMILARITY,
            "top_similarity": round(top_similarity, 4) if top_similarity is not None else None,
            "candidate_count": len(candidates),
            "results": rows,
        },
        ensure_ascii=False,
    )
