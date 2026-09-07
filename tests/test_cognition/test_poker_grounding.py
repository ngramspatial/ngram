from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ngram.cognition.poker_grounding import compose_grounded_poker_disposition
from ngram.inference.types import ChatCompletionResult


class _FakeBars:
    ordered_names = ["social", "curiosity"]

    def snapshot_pct(self) -> dict[str, float]:
        return {"social": 50.0, "curiosity": 40.0}

    def momentum_delta(self) -> dict[str, float]:
        return {"social": 0.0, "curiosity": 0.0}


class _FakeRenderer:
    @staticmethod
    def render_bars(names: list[str], pct: dict[str, float], mom: dict[str, float]) -> str:
        return "bars: ok"

    @staticmethod
    def render_affects(affects: list) -> str:
        return "affects: calm"


class _FakeNoise:
    def current_fragments(self) -> list[str]:
        return ["wonder about the thread", "unfinished thought about blue"]


@pytest.mark.asyncio
async def test_compose_grounded_calls_llm_and_returns_content() -> None:
    tonic = SimpleNamespace(
        bars=_FakeBars(),
        renderer=_FakeRenderer(),
        _current_affects=[],
        noise=_FakeNoise(),
        recent_events=lambda limit: [{"type": "idle", "duration_minutes": 3}],
    )
    client = SimpleNamespace(
        chat_completion=AsyncMock(
            return_value=ChatCompletionResult(content="you might actually open that repo today")
        )
    )
    out = await compose_grounded_poker_disposition(
        client,
        "m",
        tonic,
        seed="go build something tiny",
        entity_name="x",
        journal_tail="",
        conversation_tail="",
        relationship_blurb="",
        temperature=0.7,
        max_tokens=100,
        num_ctx=None,
    )
    assert "open that repo" in out
    client.chat_completion.assert_awaited_once()


@pytest.mark.asyncio
async def test_compose_grounded_falls_back_on_empty_response() -> None:
    tonic = SimpleNamespace(
        bars=_FakeBars(),
        renderer=_FakeRenderer(),
        _current_affects=[],
        noise=_FakeNoise(),
        recent_events=lambda limit: [],
    )
    client = SimpleNamespace(
        chat_completion=AsyncMock(return_value=ChatCompletionResult(content="  "))
    )
    seed = "go learn one thing"
    out = await compose_grounded_poker_disposition(
        client,
        "m",
        tonic,
        seed=seed,
        entity_name="x",
        journal_tail="",
        conversation_tail="",
        relationship_blurb="",
        temperature=0.7,
        max_tokens=50,
        num_ctx=None,
    )
    assert out == seed
