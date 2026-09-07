from __future__ import annotations

import json
import math
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ngram.models import EmotionCategory, Episode
from ngram.presence.tools.memory_search import search_past_conversations
from ngram.presence.tools.runtime import ToolRuntimeContext, reset_tool_runtime, set_tool_runtime


class _Store:
    @asynccontextmanager
    async def session(self):
        yield object()


def _episode(eid: str, summary: str, embedding: list[float]) -> Episode:
    return Episode(
        id=eid,
        timestamp=123.0,
        summary=summary,
        participants=["operator"],
        emotional_imprint=EmotionCategory.CURIOUS,
        emotional_intensity=0.4,
        significance=0.6,
        tags=["conversation"],
        embedding=embedding,
    )


def _runtime(pairs: list[tuple[Episode, list]], query_embedding: list[float]):
    recall = AsyncMock(return_value=pairs)
    entity = SimpleNamespace(
        config=SimpleNamespace(
            harness=SimpleNamespace(models=SimpleNamespace(embedding="test-embedding"))
        ),
        client=SimpleNamespace(embed=AsyncMock(return_value=query_embedding)),
        emotions=SimpleNamespace(
            get_state=lambda: SimpleNamespace(primary=EmotionCategory.NEUTRAL)
        ),
        store=_Store(),
        episodic=SimpleNamespace(recall=recall),
    )
    token = set_tool_runtime(ToolRuntimeContext(entity=entity))
    return entity, recall, token


@pytest.mark.asyncio
async def test_memory_search_returns_only_confident_matches_with_scores() -> None:
    strong = _episode("strong", "We planned the railway deployment.", [0.8, 0.6])
    weak = _episode(
        "weak",
        "An unrelated nearest neighbor.",
        [0.49, math.sqrt(1.0 - 0.49**2)],
    )
    entity, recall, token = _runtime([(weak, []), (strong, [])], [1.0, 0.0])
    try:
        payload = json.loads(await search_past_conversations("railway deployment", limit=50))
    finally:
        reset_tool_runtime(token)

    assert payload["ok"] is True
    assert payload["query"] == "railway deployment"
    assert payload["outcome"] == "matches"
    assert payload["no_confident_match"] is False
    assert payload["confidence_threshold"] == 0.5
    assert payload["top_similarity"] == 0.8
    assert payload["candidate_count"] == 2
    assert [row["id"] for row in payload["results"]] == ["strong"]
    assert payload["results"][0]["summary"] == strong.summary
    assert payload["results"][0]["similarity"] == 0.8
    assert payload["results"][0]["confidence"] == "high"
    entity.client.embed.assert_awaited_once_with("test-embedding", "railway deployment")
    assert recall.await_args.kwargs["limit"] == 20


@pytest.mark.asyncio
async def test_memory_search_explicitly_rejects_arbitrary_nearest_neighbor() -> None:
    weak = _episode(
        "weak",
        "A plausible-looking but unrelated incident report.",
        [0.4, math.sqrt(1.0 - 0.4**2)],
    )
    _entity, _recall, token = _runtime([(weak, [])], [1.0, 0.0])
    try:
        raw = await search_past_conversations("power grid budget reallocations")
        payload = json.loads(raw)
    finally:
        reset_tool_runtime(token)

    assert payload["ok"] is True
    assert payload["outcome"] == "no_confident_match"
    assert payload["no_confident_match"] is True
    assert payload["top_similarity"] == 0.4
    assert payload["candidate_count"] == 1
    assert payload["results"] == []
    assert "Do not infer" in payload["message"]
    assert weak.summary not in raw


@pytest.mark.asyncio
async def test_memory_search_reports_no_confident_match_when_memory_is_empty() -> None:
    _entity, _recall, token = _runtime([], [1.0, 0.0])
    try:
        payload = json.loads(await search_past_conversations("something never discussed"))
    finally:
        reset_tool_runtime(token)

    assert payload["outcome"] == "no_confident_match"
    assert payload["no_confident_match"] is True
    assert payload["top_similarity"] is None
    assert payload["candidate_count"] == 0
    assert payload["results"] == []
