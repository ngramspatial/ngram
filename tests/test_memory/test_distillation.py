"""Tests for the experience distillation engine."""

from __future__ import annotations

import json
import time


from ngram.config import DistillationSettings
from ngram.memory.distillation import (
    ExperienceDistiller,
    _parse_result,
)


# ---------------------------------------------------------------------------
# should_distill — trigger logic
# ---------------------------------------------------------------------------


class TestShouldDistill:
    def test_disabled_returns_false(self):
        s = DistillationSettings(enabled=False)
        d = ExperienceDistiller(s)
        assert d.should_distill(100, 0.5) is False

    def test_below_min_turns_absolute(self):
        s = DistillationSettings(min_turns_absolute=4, cycle_seconds=9999)
        d = ExperienceDistiller(s)
        d._last_tick = time.monotonic()
        assert d.should_distill(3, 0.5) is False

    def test_turn_threshold_triggers(self):
        s = DistillationSettings(min_turns=6, min_turns_absolute=4, cycle_seconds=9999)
        d = ExperienceDistiller(s)
        d._last_tick = time.monotonic()
        assert d.should_distill(6, 0.0) is True

    def test_time_threshold_triggers(self):
        s = DistillationSettings(min_turns=999, min_turns_absolute=4, cycle_seconds=0.01)
        d = ExperienceDistiller(s)
        d._last_tick = time.monotonic() - 1.0  # 1 second ago
        assert d.should_distill(5, 0.0) is True

    def test_soma_urgency_lowers_turn_threshold(self):
        s = DistillationSettings(
            min_turns=8, min_turns_absolute=4, cycle_seconds=9999,
            soma_urgency_divisor=2.0,
        )
        d = ExperienceDistiller(s)
        d._last_tick = time.monotonic()  # prevent time trigger
        # Without urgency: need 8 turns
        assert d.should_distill(6, 0.0) is False
        # With max intensity: 8/2 = 4 turns
        assert d.should_distill(5, 1.0) is True

    def test_soma_urgency_respects_absolute_min(self):
        s = DistillationSettings(
            min_turns=8, min_turns_absolute=4, cycle_seconds=9999,
            soma_urgency_divisor=10.0,
        )
        d = ExperienceDistiller(s)
        d._last_tick = time.monotonic()
        # Even with extreme divisor, min_turns_absolute = 4 is floor
        assert d.should_distill(3, 1.0) is False
        assert d.should_distill(4, 1.0) is True


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------


class TestParseResult:
    def test_clean_json(self):
        raw = json.dumps({
            "knowledge": [{"title": "Alice's job", "body": "Alice works at NASA"}],
            "relational": [{"person_id": "u1", "person_name": "Alice", "note": "passionate about space", "warmth_delta": 0.02, "trust_delta": 0.01}],
            "beliefs": [{"category": "world_fact", "content": "NASA is hiring", "confidence": 0.7}],
            "journal_entry": "I feel energized by this conversation",
        })
        result = _parse_result(raw)
        assert result is not None
        assert len(result.knowledge) == 1
        assert result.knowledge[0].title == "Alice's job"
        assert len(result.relational) == 1
        assert result.relational[0].person_name == "Alice"
        assert len(result.beliefs) == 1
        assert result.beliefs[0].confidence == 0.7
        assert result.journal_entry == "I feel energized by this conversation"

    def test_markdown_fenced_json(self):
        raw = "```json\n" + json.dumps({
            "knowledge": [{"title": "test", "body": "content"}],
            "relational": [],
            "beliefs": [],
            "journal_entry": None,
        }) + "\n```"
        result = _parse_result(raw)
        assert result is not None
        assert len(result.knowledge) == 1

    def test_empty_result_returns_none(self):
        raw = json.dumps({
            "knowledge": [],
            "relational": [],
            "beliefs": [],
            "journal_entry": None,
        })
        result = _parse_result(raw)
        assert result is None

    def test_malformed_json_returns_none(self):
        assert _parse_result("not json at all") is None

    def test_empty_string_returns_none(self):
        assert _parse_result("") is None

    def test_skips_malformed_items(self):
        raw = json.dumps({
            "knowledge": [
                {"title": "good", "body": "content"},
                {"title": "", "body": "no title"},  # skipped
                {"wrong_key": "value"},  # skipped
            ],
            "relational": [],
            "beliefs": [],
            "journal_entry": None,
        })
        result = _parse_result(raw)
        assert result is not None
        assert len(result.knowledge) == 1

    def test_clamps_confidence(self):
        raw = json.dumps({
            "knowledge": [],
            "relational": [],
            "beliefs": [{"category": "fact", "content": "something", "confidence": 5.0}],
            "journal_entry": None,
        })
        result = _parse_result(raw)
        assert result is not None
        assert result.beliefs[0].confidence == 1.0

    def test_person_id_defaults_to_name(self):
        raw = json.dumps({
            "knowledge": [],
            "relational": [{"person_name": "Bob", "note": "friendly"}],
            "beliefs": [],
            "journal_entry": None,
        })
        result = _parse_result(raw)
        assert result is not None
        assert result.relational[0].person_id == "Bob"


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_same_window_hash(self):
        d = ExperienceDistiller(DistillationSettings())
        window = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]
        h1 = d._window_hash(window)
        h2 = d._window_hash(window)
        assert h1 == h2

    def test_different_window_hash(self):
        d = ExperienceDistiller(DistillationSettings())
        w1 = [{"role": "user", "content": "hello"}]
        w2 = [{"role": "user", "content": "goodbye"}]
        assert d._window_hash(w1) != d._window_hash(w2)
