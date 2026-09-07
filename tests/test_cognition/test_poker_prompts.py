from __future__ import annotations

import random
from datetime import datetime

from ngram.cognition.poker_prompts import (
    PokerPromptEntry,
    default_deck_path,
    energy_weights_for_hour,
    load_poker_deck,
    select_poker_prompt,
)


def test_energy_weights_sum_to_one() -> None:
    for h in range(24):
        a, b, c = energy_weights_for_hour(h)
        assert abs(a + b + c - 1.0) < 1e-6


def test_select_respects_time_weighting_distribution() -> None:
    deck = [
        PokerPromptEntry("L", "low"),
        PokerPromptEntry("M", "medium"),
        PokerPromptEntry("H", "high"),
    ]
    rng = random.Random(0)
    # Night hour — expect mostly low when many samples
    picks = [select_poker_prompt(deck, now=datetime(2026, 1, 1, 2, 0, 0), time_weighted=True, rng=rng) for _ in range(200)]
    lows = sum(1 for p in picks if p and p.energy == "low")
    assert lows > 80


def test_bundled_default_deck_loads_many_prompts() -> None:
    deck = load_poker_deck(default_deck_path())
    assert len(deck) >= 100
    assert all(e.text.strip() for e in deck)


def test_select_uniform_when_not_time_weighted() -> None:
    deck = [PokerPromptEntry("L", "low"), PokerPromptEntry("M", "medium")]
    rng = random.Random(1)
    seen = {select_poker_prompt(deck, time_weighted=False, rng=rng).text for _ in range(40)}
    assert seen == {"L", "M"}
