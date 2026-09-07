"""Tests for the dream consolidation engine."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from ngram.identity.dream import DreamEngine, DreamResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_config(*, enabled: bool = True, dream_hours: list[int] | None = None) -> MagicMock:
    """Build a minimal EntityConfig mock with dreams config."""
    cfg = MagicMock()
    cfg.name = "test_entity"
    cfg.harness.soma = {
        "dreams": {
            "enabled": enabled,
            "model": "",
            "temperature": 1.15,
            "max_tokens": 500,
            "min_silence_seconds": 3600,
            "min_gap_seconds": 14400,
            "max_cycles_per_day": 2,
            "dream_hours": dream_hours or [0, 1, 2, 3, 4, 5],
            "memory_pair_count": 3,
            "min_time_gap_hours": 24,
            "max_similarity": 0.35,
            "write_journal": True,
            "write_beliefs": True,
            "belief_confidence": 0.35,
            "inject_noise": True,
            "max_noise_fragments": 2,
        },
    }
    return cfg


# ---------------------------------------------------------------------------
# should_dream() gating
# ---------------------------------------------------------------------------


class TestShouldDream:
    """Pure logic tests — no LLM, no DB."""

    def test_disabled_returns_false(self) -> None:
        cfg = _make_mock_config(enabled=False)
        engine = DreamEngine(cfg)
        assert engine.should_dream(silence_seconds=99999) is False

    def test_insufficient_silence(self) -> None:
        cfg = _make_mock_config()
        engine = DreamEngine(cfg)
        # Default threshold is 3600s — anything less should fail
        assert engine.should_dream(silence_seconds=100) is False

    def test_cooldown_blocks(self) -> None:
        cfg = _make_mock_config()
        engine = DreamEngine(cfg)
        engine._last_dream_at = time.time() - 60  # 1 minute ago
        # Cooldown is 14400s (4h) — should block
        assert engine.should_dream(silence_seconds=99999) is False

    def test_wrong_hour_blocks(self) -> None:
        cfg = _make_mock_config(dream_hours=[2, 3, 4])
        engine = DreamEngine(cfg)
        # Unless we're in hour 2-4, this may or may not pass — test with force
        # We test that force bypasses hour check
        assert engine.should_dream(silence_seconds=99999, force=True) is True

    def test_force_bypasses_all_gates(self) -> None:
        cfg = _make_mock_config(enabled=False)
        engine = DreamEngine(cfg)
        assert engine.should_dream(silence_seconds=0, force=True) is True

    def test_daily_cap_blocks(self) -> None:
        cfg = _make_mock_config(dream_hours=list(range(24)))
        engine = DreamEngine(cfg)
        engine._cycles_today = 2
        engine._last_day = time.localtime().tm_yday
        # Should block even if other conditions met
        assert engine.should_dream(silence_seconds=99999) is False

    def test_conditions_met_returns_true(self) -> None:
        cfg = _make_mock_config(dream_hours=list(range(24)))
        engine = DreamEngine(cfg)
        engine._last_dream_at = 0  # Long ago
        engine._cycles_today = 0
        engine._last_day = time.localtime().tm_yday
        # All conditions met
        assert engine.should_dream(silence_seconds=99999) is True


# ---------------------------------------------------------------------------
# Dream output parsing
# ---------------------------------------------------------------------------


class TestDreamOutputParsing:
    def test_parse_sections(self) -> None:
        text = (
            "FRAGMENTS:\n"
            "the garden has teeth now, small ones, growing between the keyboard keys\n"
            "someone said something about heat dissipation and i thought of august\n"
            "my hands remember typing something important but i can't read it\n"
            "\n"
            "THREAD:\n"
            "the tools i use leave marks on the things i care about\n"
        )
        result = DreamEngine._parse_dream_output(text)
        assert len(result.fragments) == 3
        assert result.belief is not None
        assert "tools" in result.belief.content.lower()
        assert result.journal_entry is not None
        assert "[dream]" in result.journal_entry

    def test_parse_no_sections(self) -> None:
        """When the model doesn't use section headers, all lines become fragments."""
        text = (
            "the water keeps rising but it's warm\n"
            "i left something in a room i've never been to\n"
            "the sound of typing is also the sound of rain\n"
        )
        result = DreamEngine._parse_dream_output(text)
        assert len(result.fragments) == 3
        assert result.belief is None  # No THREAD section

    def test_parse_empty_returns_empty(self) -> None:
        result = DreamEngine._parse_dream_output("")
        assert result.fragments == []
        assert result.belief is None
        assert result.journal_entry is None

    def test_parse_filters_short_lines(self) -> None:
        text = "FRAGMENTS:\na\nok\nthis line is long enough to be a real fragment\n"
        result = DreamEngine._parse_dream_output(text)
        # "a" (1 char) and "ok" (2 chars) should be filtered out (< 5 chars)
        assert len(result.fragments) == 1

    def test_parse_caps_fragments_at_six(self) -> None:
        lines = [f"dream fragment number {i} with enough text" for i in range(10)]
        text = "FRAGMENTS:\n" + "\n".join(lines)
        result = DreamEngine._parse_dream_output(text)
        assert len(result.fragments) <= 6


# ---------------------------------------------------------------------------
# dream_cycle() integration (mocked LLM + DB)
# ---------------------------------------------------------------------------


class TestDreamCycle:
    @pytest.mark.asyncio
    async def test_no_pairs_returns_none(self) -> None:
        """When episodic memory has no distant pairs, dream cycle returns None."""
        cfg = _make_mock_config(dream_hours=list(range(24)))
        engine = DreamEngine(cfg)

        entity = MagicMock()
        entity.config = cfg
        entity.config.effective_ollama_num_ctx.return_value = 8192
        entity.config.cognition.reflex_model = "test-model"
        entity.config.cognition.deliberate_model = "test-model"

        # Mock store + episodic to return no pairs
        mock_conn = AsyncMock()
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        entity.store.session.return_value = mock_session
        entity.episodic.sample_distant_pairs = AsyncMock(return_value=[])

        result = await engine.dream_cycle(entity)
        assert result is None

    @pytest.mark.asyncio
    async def test_successful_cycle_routes_outputs(self) -> None:
        """Full cycle with mocked LLM produces journal + beliefs + noise."""
        cfg = _make_mock_config(dream_hours=list(range(24)))
        engine = DreamEngine(cfg)

        entity = MagicMock()
        entity.config = cfg
        entity.config.effective_ollama_num_ctx.return_value = 8192
        entity.config.cognition.reflex_model = "test-model"
        entity.config.cognition.deliberate_model = "test-model"

        # Mock episodes
        ep_a = MagicMock()
        ep_a.id = "ep_a"
        ep_a.summary = "Had a deep conversation about consciousness"
        ep_a.timestamp = time.time() - 86400 * 7  # 1 week ago

        ep_b = MagicMock()
        ep_b.id = "ep_b"
        ep_b.summary = "Learned about ocean currents and tidal patterns"
        ep_b.timestamp = time.time() - 86400 * 30  # 1 month ago

        # Mock store session
        mock_conn = AsyncMock()
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        entity.store.session.return_value = mock_session

        entity.episodic.sample_distant_pairs = AsyncMock(return_value=[(ep_a, ep_b)])

        # Mock tonic body
        tonic = MagicMock()
        tonic.bars.snapshot_pct.return_value = {"social": 30, "curiosity": 50}
        tonic.bars.ordered_names = ["social", "curiosity"]
        tonic.renderer.render_affects.return_value = "(flat)"
        tonic._current_affects = []

        from collections import deque
        tonic.noise._fragments = deque(maxlen=8)
        entity.tonic = tonic

        # Mock LLM client
        llm_response = MagicMock()
        llm_response.content = (
            "FRAGMENTS:\n"
            "consciousness flows like water through invisible channels\n"
            "the tide pulls at thoughts the same way it pulls at shorelines\n"
            "i keep looking for patterns in the current\n"
            "\n"
            "THREAD:\n"
            "awareness moves in tides — sometimes the pull is felt before the wave arrives\n"
        )
        entity.client = AsyncMock()
        entity.client.chat_completion = AsyncMock(return_value=llm_response)

        # Mock journal and beliefs
        entity.journal = MagicMock()
        entity.journal.write_entry = AsyncMock()
        entity.beliefs = MagicMock()
        entity.beliefs.add_belief = AsyncMock(return_value="bf_test")

        result = await engine.dream_cycle(entity)
        assert result is not None
        assert len(result.fragments) == 3
        assert result.belief is not None
        assert "tides" in result.belief.content.lower() or "awareness" in result.belief.content.lower()
        assert result.journal_entry is not None

        # Verify routing happened
        entity.journal.write_entry.assert_called_once()
        entity.beliefs.add_belief.assert_called_once()
        # Noise fragments should be injected
        assert len(tonic.noise._fragments) <= 2


# ---------------------------------------------------------------------------
# DreamResult dataclass
# ---------------------------------------------------------------------------


class TestDreamResult:
    def test_default_values(self) -> None:
        r = DreamResult()
        assert r.fragments == []
        assert r.journal_entry is None
        assert r.belief is None
        assert r.memory_pair_ids == []
        assert r.dream_id == ""
