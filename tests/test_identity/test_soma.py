"""Tests for the tonic body state engine (ngram/identity/soma.py)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ngram.identity.soma import (
    AffectEngine,
    BarEngine,
    BodyRenderer,
    NoiseEngine,
    NoiseFragment,
    SomaticAppraiser,
    TonicBody,
    _EBB_TIER_ORDER,
    _bar_glyphs,
    _felt_level,
    _momentum_arrow,
    _split_noise_fragments,
)
from ngram.config import default_soma_config
from ngram.inference.types import ChatCompletionResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _default_bar_config() -> dict[str, Any]:
    return default_soma_config()


def _simple_bar_config(**overrides: Any) -> dict[str, Any]:
    cfg = _default_bar_config()
    cfg.update(overrides)
    return cfg


# ---------------------------------------------------------------------------
# BarEngine — core mechanics
# ---------------------------------------------------------------------------


class TestBarEngineInit:
    def test_loads_default_five_bars(self):
        bars = BarEngine(_default_bar_config())
        assert bars.ordered_names == ["social", "curiosity", "creative", "tension", "comfort"]

    def test_initial_values_match_config(self):
        cfg = _default_bar_config()
        bars = BarEngine(cfg)
        pct = bars.snapshot_pct()
        for v in cfg["bars"]["variables"]:
            assert pct[v["name"]] == int(round(v["initial"]))

    def test_empty_config_produces_no_bars(self):
        bars = BarEngine({})
        assert bars.ordered_names == []
        assert bars.snapshot_pct() == {}


class TestBarEngineDecay:
    def test_bars_decay_toward_initial(self):
        bars = BarEngine(_default_bar_config())
        # Push social above its resting point, then verify decay pulls it back.
        bars._values["social"] = 90.0
        bars.tick(1.0)  # 1 hour
        assert bars._values["social"] < 90.0
        assert bars._values["social"] > bars._initial["social"]

    def test_bars_clamp_to_floor(self):
        cfg = _default_bar_config()
        for v in cfg["bars"]["variables"]:
            v["initial"] = 1
            v["decay_rate"] = -100.0
        bars = BarEngine(cfg)
        bars.tick(1.0)
        for name in bars.ordered_names:
            assert bars.snapshot_pct()[name] >= 0

    def test_bars_clamp_to_ceiling(self):
        cfg = _default_bar_config()
        for v in cfg["bars"]["variables"]:
            v["initial"] = 99
            v["decay_rate"] = 100.0
        bars = BarEngine(cfg)
        bars.tick(1.0)
        for name in bars.ordered_names:
            assert bars.snapshot_pct()[name] <= 100


class TestBarEngineEvents:
    def test_message_received_bumps_social(self):
        bars = BarEngine(_default_bar_config())
        before = bars.snapshot_pct()["social"]
        bars.apply_event({"type": "message_received"})
        after_raw = bars._values["social"]
        assert after_raw > before

    def test_action_event_bumps_curiosity(self):
        bars = BarEngine(_default_bar_config())
        before = bars._values["curiosity"]
        bars.apply_event({"type": "action", "name": "search_web", "category": "tool"})
        assert bars._values["curiosity"] > before

    def test_idle_event_scales_by_duration(self):
        bars = BarEngine(_default_bar_config())
        before = bars._values["social"]
        bars.apply_event({"type": "idle", "duration_minutes": 10})
        after = bars._values["social"]
        assert after < before

    def test_unknown_event_type_is_ignored(self):
        bars = BarEngine(_default_bar_config())
        snapshot_before = dict(bars._values)
        bars.apply_event({"type": "something_unknown"})
        assert bars._values == snapshot_before

    def test_reset_to_initial_restores_yaml_baseline(self):
        bars = BarEngine(_default_bar_config())
        bars.apply_event({"type": "message_received"})
        bars.apply_event({"type": "message_received"})
        assert bars._values["social"] > bars._initial["social"]
        bars.reset_to_initial()
        for name in bars.ordered_names:
            assert bars._values[name] == pytest.approx(bars._initial[name])
        expected = {v["name"]: int(round(v["initial"])) for v in _default_bar_config()["bars"]["variables"]}
        assert bars.snapshot_pct() == expected


class TestBarEngineMomentum:
    def test_initial_momentum_is_zero(self):
        bars = BarEngine(_default_bar_config())
        mom = bars.momentum_delta()
        for name in bars.ordered_names:
            assert mom[name] == 0.0

    def test_momentum_reflects_change_after_ticks(self):
        bars = BarEngine(_default_bar_config())
        bars.apply_event({"type": "message_received"})
        bars.tick(0.01)
        bars.apply_event({"type": "message_received"})
        bars.tick(0.01)
        mom = bars.momentum_delta()
        assert mom["social"] != 0.0


class TestBarEngineCoupling:
    def test_coupling_rule_modifies_decay(self):
        cfg = _default_bar_config()
        for v in cfg["bars"]["variables"]:
            if v["name"] == "social":
                v["initial"] = 90  # above coupling threshold of 80
        bars = BarEngine(cfg)
        base_rates = dict(bars._decay_rates)
        eff, _ = bars._apply_coupling(base_rates)
        assert eff["curiosity"] != base_rates["curiosity"]

    def test_coupling_rule_inactive_when_below_threshold(self):
        cfg = _default_bar_config()
        for v in cfg["bars"]["variables"]:
            if v["name"] == "social":
                v["initial"] = 50  # below coupling threshold of 80
        bars = BarEngine(cfg)
        base_rates = dict(bars._decay_rates)
        eff, _ = bars._apply_coupling(base_rates)
        assert eff["curiosity"] == base_rates["curiosity"]


class TestBarEngineConflicts:
    def test_conflict_detected_when_both_drives_high(self):
        cfg = _default_bar_config()
        for v in cfg["bars"]["variables"]:
            if v["name"] in ("curiosity", "comfort"):
                v["initial"] = 85  # above conflict threshold of 70
        bars = BarEngine(cfg)
        bars.tick(0.001)
        assert len(bars._active_conflicts) == 1
        assert bars._active_conflicts[0]["label"] == "restless comfort"

    def test_no_conflict_when_one_drive_below_threshold(self):
        cfg = _default_bar_config()
        for v in cfg["bars"]["variables"]:
            if v["name"] == "curiosity":
                v["initial"] = 85
            if v["name"] == "comfort":
                v["initial"] = 30
        bars = BarEngine(cfg)
        bars.tick(0.001)
        assert len(bars._active_conflicts) == 0

    def test_latent_conflict_brews_when_edges_align_but_not_full_collision(self):
        cfg = _default_bar_config()
        for v in cfg["bars"]["variables"]:
            if v["name"] == "curiosity":
                v["initial"] = 62
            if v["name"] == "comfort":
                v["initial"] = 58
        bars = BarEngine(cfg)
        bars.tick(0.001)
        assert len(bars._active_conflicts) == 0
        assert len(bars._latent_conflicts) >= 1
        assert bars._latent_conflicts[0].get("phase") == "brewing"

    def test_active_conflict_excludes_latent_for_same_rule(self):
        cfg = _default_bar_config()
        for v in cfg["bars"]["variables"]:
            if v["name"] in ("curiosity", "comfort"):
                v["initial"] = 85
        bars = BarEngine(cfg)
        bars.tick(0.001)
        assert len(bars._active_conflicts) == 1
        assert len(bars._latent_conflicts) == 0


class TestBarEngineImpulses:
    def test_impulse_fires_when_drive_above_threshold(self):
        cfg = _default_bar_config()
        for v in cfg["bars"]["variables"]:
            if v["name"] == "social":
                v["initial"] = 90  # above impulse threshold of 80
        bars = BarEngine(cfg)
        bars.tick(0.001)
        labels = [i["label"] for i in bars._active_impulses]
        assert "reach_out" in labels

    def test_no_impulse_when_below_threshold(self):
        cfg = _default_bar_config()
        for v in cfg["bars"]["variables"]:
            if v["name"] == "social":
                v["initial"] = 50
        bars = BarEngine(cfg)
        bars.tick(0.001)
        labels = [i["label"] for i in bars._active_impulses]
        assert "reach_out" not in labels

    def test_impulse_cooldown_seconds_left_matches_config(self):
        """Regression: last_fired must use the same clock as _detect_impulses (monotonic)."""
        cfg = _default_bar_config()
        for v in cfg["bars"]["variables"]:
            if v["name"] == "social":
                v["initial"] = 90
        bars = BarEngine(cfg)
        bars.tick(0.001)
        reach = next(i for i in bars._active_impulses if i["label"] == "reach_out")
        assert reach["on_cooldown"] is True
        cd_min = next(
            float(i["cooldown_minutes"])
            for i in (cfg.get("impulses") or [])
            if i.get("label") == "reach_out"
        )
        max_left = cd_min * 60.0 + 2.0
        assert reach["cooldown_seconds_left"] <= max_left


class TestBarEnginePersistence:
    def test_save_and_restore_roundtrip(self, tmp_path: Path):
        cfg = _default_bar_config()
        bars = BarEngine(cfg)
        bars.apply_event({"type": "message_received"})
        bars.tick(0.01)
        state_path = tmp_path / "soma-bar-state.json"
        bars.save_state(state_path)
        assert state_path.is_file()

        bars2 = BarEngine(cfg)
        assert bars2.restore_state(state_path)
        assert bars2.snapshot_pct() == bars.snapshot_pct()

    def test_restore_fails_gracefully_on_missing_file(self, tmp_path: Path):
        bars = BarEngine(_default_bar_config())
        assert bars.restore_state(tmp_path / "nonexistent.json") is False

    def test_restore_rejects_changed_bar_names(self, tmp_path: Path):
        cfg = _default_bar_config()
        bars = BarEngine(cfg)
        state_path = tmp_path / "state.json"
        bars.save_state(state_path)

        cfg2 = _default_bar_config()
        cfg2["bars"]["variables"].append({"name": "new_bar", "initial": 50})
        bars2 = BarEngine(cfg2)
        assert bars2.restore_state(state_path) is False


# ---------------------------------------------------------------------------
# AffectEngine — parsing
# ---------------------------------------------------------------------------


class TestAffectParsing:
    def test_parse_standard_affect_lines(self):
        text = (
            "restlessness: 0.7 — fingers won't settle\n"
            "warmth: 0.3 — residual, from the last exchange\n"
            "fascination: 0.5 — something half-glimpsed pulling attention sideways\n"
        )
        affects = AffectEngine._parse_response(text)
        assert len(affects) == 3
        assert affects[0]["name"] == "restlessness"
        assert affects[0]["intensity"] == pytest.approx(0.7)
        assert "fingers" in affects[0]["note"]

    def test_parse_rejects_unknown_affect_names(self):
        text = "made_up_feeling: 0.5 — this is fake\n"
        affects = AffectEngine._parse_response(text)
        assert len(affects) == 0

    def test_parse_deduplicates_by_name(self):
        text = "warmth: 0.3 — first\nwarmth: 0.8 — second\n"
        affects = AffectEngine._parse_response(text)
        assert len(affects) == 1
        assert affects[0]["intensity"] == pytest.approx(0.3)

    def test_parse_caps_at_six(self):
        lines = [f"warmth: 0.{i}" for i in range(9)]
        text = "\n".join(lines)
        affects = AffectEngine._parse_response(text)
        assert len(affects) <= 6

    def test_parse_clamps_intensity(self):
        text = "warmth: 1.5 — too high\nanxiety: -0.2 — too low\n"
        affects = AffectEngine._parse_response(text)
        for a in affects:
            assert 0.0 <= a["intensity"] <= 1.0

    def test_parse_handles_bullet_prefix(self):
        text = "- warmth: 0.4 — gentle\n* fascination: 0.6 — locked\n"
        affects = AffectEngine._parse_response(text)
        assert len(affects) == 2

    def test_parse_empty_string(self):
        assert AffectEngine._parse_response("") == []

    def test_parse_garbage_input(self):
        assert AffectEngine._parse_response("this is not affect output at all") == []

    def test_parse_sectioned_includes_edge(self):
        text = (
            "SURFACE:\n"
            "warmth: 0.3 — soft\n\n"
            "UNDERCURRENTS:\n"
            "dread: 0.2 — thin\n\n"
            "EDGE:\n"
            "0.4 — curiosity braided with fatigue\n"
        )
        affects = AffectEngine._parse_response(text)
        assert any(a.get("name") == "warmth" for a in affects)
        assert any(a.get("layer") == "undercurrent" for a in affects)
        edge = next((a for a in affects if a.get("kind") == "edge"), None)
        assert edge is not None
        assert "braided" in (edge.get("text") or "")


# ---------------------------------------------------------------------------
# SomaticAppraiser parsing
# ---------------------------------------------------------------------------


class TestSomaticAppraiserParse:
    def test_json_format_parses_bar_effects(self):
        a = SomaticAppraiser(output_format="json")
        raw = '{"bar_effects":{"social":3,"tension":-1.5},"tags":["warm","probe"],"felt":"uneasy"}'
        out = a._parse_response(raw, {"social", "tension", "curiosity"})
        assert out["bar_effects"]["social"] == 3.0
        assert out["bar_effects"]["tension"] == -1.5
        assert "warm" in out["tags"]
        assert "uneasy" in out["felt"]

    def test_json_fallback_to_lines_when_invalid(self):
        a = SomaticAppraiser(output_format="json")
        raw = "social: 2 — ping\ntags: x\nfelt: ok\n"
        out = a._parse_response(raw, {"social", "tension"})
        assert out["bar_effects"].get("social") == 2.0
        assert "ok" in out["felt"]


class TestBarEngineDecayTimeScale:
    def test_decay_time_scale_slows_return_to_baseline(self):
        cfg = _default_bar_config()
        name = cfg["bars"]["variables"][0]["name"]
        cfg["bars"]["variables"][0]["decay_time_scale"] = 0.2
        slow = BarEngine(cfg)
        fast = BarEngine(_default_bar_config())
        slow._values[name] = 90.0
        fast._values[name] = 90.0
        slow.tick(1.0)
        fast.tick(1.0)
        assert slow._values[name] > fast._values[name]


# ---------------------------------------------------------------------------
# BodyRenderer
# ---------------------------------------------------------------------------


class TestBodyRenderer:
    def test_render_bars_produces_lines(self):
        names = ["social", "curiosity"]
        pct = {"social": 50, "curiosity": 80}
        mom = {"social": 0.0, "curiosity": 3.0}
        output = BodyRenderer.render_bars(names, pct, mom)
        assert "social" in output
        assert "curiosity" in output
        assert "moderate" in output  # social at 50
        assert "intense" in output or "strong" in output  # curiosity at 80

    def test_render_bars_empty(self):
        assert BodyRenderer.render_bars([], {}, {}) == "(no bars)"

    def test_render_affects_with_data(self):
        affects = [
            {"name": "warmth", "intensity": 0.3, "note": "residual"},
            {"name": "fascination", "intensity": 0.6, "note": ""},
        ]
        output = BodyRenderer.render_affects(affects)
        assert "Surface:" in output
        assert "warmth" in output
        assert "present" in output  # 0.3 maps to "present"
        assert "fascination" in output

    def test_render_affects_empty(self):
        out = BodyRenderer.render_affects([])
        assert out.startswith("(flat")

    def test_render_conflicts_with_data(self):
        conflicts = [
            {
                "drives": ["curiosity", "comfort"],
                "label": "restless comfort",
                "intensity": 0.5,
                "phase": "active",
                "heat": "heating",
                "tilt": "curiosity",
                "levels_pct": {"curiosity": 82, "comfort": 74},
            },
        ]
        output = BodyRenderer.render_conflicts(conflicts, [])
        assert "restless comfort" in output
        assert "pulling" in output
        assert "heating" in output

    def test_render_conflicts_empty(self):
        out = BodyRenderer.render_conflicts([], [])
        assert out.startswith("(no structural strain")

    def test_render_impulses_with_data(self):
        impulses = [
            {
                "type": "reach_out",
                "label": "reach_out",
                "intensity": 0.4,
                "building_minutes": 5.0,
                "on_cooldown": False,
                "phase": "live",
                "surge": "steady",
                "value_pct": 92,
                "threshold": 80,
            },
        ]
        output = BodyRenderer.render_impulses(impulses, [])
        assert "Live:" in output
        assert "reach_out" in output
        assert "insistent" in output

    def test_render_impulses_empty(self):
        out = BodyRenderer.render_impulses([], [])
        assert out.startswith("(no pull signal")


class TestRenderingHelpers:
    def test_bar_glyphs_boundaries(self):
        assert _bar_glyphs(0).count("\u2588") == 0
        assert _bar_glyphs(100).count("\u2591") == 0
        assert len(_bar_glyphs(50)) == 10

    def test_felt_level_boundaries(self):
        assert _felt_level(0) == "quiet"
        assert _felt_level(50) == "moderate"
        assert _felt_level(100) == "overwhelming"

    def test_momentum_arrow_directions(self):
        assert "\u2191" in _momentum_arrow(10.0)
        assert "\u2193" in _momentum_arrow(-10.0)
        assert _momentum_arrow(0.0) == "\u2014"


# ---------------------------------------------------------------------------
# TonicBody — composition root
# ---------------------------------------------------------------------------


class TestTonicBody:
    def test_init_and_render(self, tmp_path: Path):
        body = TonicBody(_default_bar_config(), tmp_path)
        output = body.render_body()
        assert "## Bars" in output
        assert "## Affects" in output
        assert "## Conflicts" in output
        assert "## Impulses" in output

    def test_emit_records_events(self, tmp_path: Path):
        body = TonicBody(_default_bar_config(), tmp_path)
        body.emit({"type": "message_received", "from": "alice"})
        assert len(body._recent_events) == 1

    def test_emit_ignores_events_without_type(self, tmp_path: Path):
        body = TonicBody(_default_bar_config(), tmp_path)
        body.emit({"no_type": "here"})
        assert len(body._recent_events) == 0

    @pytest.mark.asyncio
    async def test_tick_bars_applies_events_and_decays(self, tmp_path: Path):
        body = TonicBody(_default_bar_config(), tmp_path)
        before = body.bars.snapshot_pct()["social"]
        body.emit({"type": "message_received"})
        await body.tick_bars(0.01)
        after = body.bars.snapshot_pct()["social"]
        assert after > before

    @pytest.mark.asyncio
    async def test_tick_bars_does_not_reapply_message_events(self, tmp_path: Path):
        """Regression: message_received was re-applied every heartbeat (no _applied flag)."""
        body = TonicBody(_default_bar_config(), tmp_path)
        body.emit({"type": "message_received"})
        await body.tick_bars(0.01)
        raw_after_first = body.bars._values["social"]
        await body.tick_bars(0.01)
        raw_after_second = body.bars._values["social"]
        # Second tick should decay only, not stack another +3 from the same event.
        assert raw_after_second < raw_after_first

    def test_snapshot_for_emotion_returns_valid_category(self, tmp_path: Path):
        body = TonicBody(_default_bar_config(), tmp_path)
        cat, intensity = body.snapshot_for_emotion()
        assert isinstance(cat, str)
        assert 0.0 <= intensity <= 1.0

    def test_snapshot_for_emotion_returns_curious_when_curiosity_high(self, tmp_path: Path):
        cfg = _default_bar_config()
        for v in cfg["bars"]["variables"]:
            if v["name"] == "curiosity":
                v["initial"] = 90
        body = TonicBody(cfg, tmp_path)
        cat, intensity = body.snapshot_for_emotion()
        assert cat == "curious"
        assert intensity > 0.4

    def test_snapshot_returns_neutral_when_all_bars_low(self, tmp_path: Path):
        cfg = _default_bar_config()
        for v in cfg["bars"]["variables"]:
            v["initial"] = 20
        body = TonicBody(cfg, tmp_path)
        cat, _ = body.snapshot_for_emotion()
        assert cat == "neutral"

    def test_save_and_restore_roundtrip(self, tmp_path: Path):
        body = TonicBody(_default_bar_config(), tmp_path)
        body.emit({"type": "message_received"})
        body.bars.apply_event(body._recent_events[-1])
        body.bars.tick(0.01)
        body.bars.register_somatic_marker("person:alice", {"curiosity": 2.5})
        saved_baseline = dict(body.bars._initial)
        body.save_state()

        body2 = TonicBody(_default_bar_config(), tmp_path)
        assert body2.restore_state()
        assert body2.bars.snapshot_pct() == body.bars.snapshot_pct()
        assert body2.bars._initial == saved_baseline
        assert body2.bars._somatic_markers == {"person:alice": {"curiosity": 2.5}}

    def test_render_body_changes_after_events_and_tick(self, tmp_path: Path):
        body = TonicBody(_default_bar_config(), tmp_path)
        render_before = body.render_body()
        for _ in range(5):
            body.emit({"type": "message_received"})
        body.bars.apply_event({"type": "message_received"})
        body.bars.apply_event({"type": "message_received"})
        body.bars.apply_event({"type": "message_received"})
        body.bars.tick(0.5)
        render_after = body.render_body()
        assert render_before != render_after

    def test_event_cap_is_enforced(self, tmp_path: Path):
        body = TonicBody(_default_bar_config(), tmp_path)
        for i in range(300):
            body.emit({"type": "message_received", "i": i})
        assert len(body._recent_events) <= 200


# ---------------------------------------------------------------------------
# Integration: full lifecycle
# ---------------------------------------------------------------------------


class TestTonicBodyLifecycle:
    @pytest.mark.asyncio
    async def test_full_turn_lifecycle(self, tmp_path: Path):
        """Simulate a full perceive turn: receive -> tool -> send -> idle -> tick."""
        body = TonicBody(_default_bar_config(), tmp_path)
        initial_pct = body.bars.snapshot_pct()

        body.emit({"type": "message_received", "from": "alice", "room": "general", "length": 42})
        body.emit({"type": "action", "name": "search_web", "category": "tool", "detail": "ok"})
        body.emit({"type": "message_sent", "to": "alice", "room": "general", "length": 120})

        await body.tick_bars(30.0 / 3600.0)

        after_pct = body.bars.snapshot_pct()
        assert after_pct["social"] > initial_pct["social"]
        assert after_pct["curiosity"] > initial_pct["curiosity"]

        output = body.render_body()
        assert "## Bars" in output
        assert "social" in output

        body.save_state()
        assert (tmp_path / "soma-state.json").is_file()

    @pytest.mark.asyncio
    async def test_prolonged_idle_drops_social(self, tmp_path: Path):
        """Social bar decays when idle events accumulate."""
        body = TonicBody(_default_bar_config(), tmp_path)
        initial_social = body.bars._values["social"]

        for _ in range(10):
            body.emit({"type": "idle", "duration_minutes": 5})
            await body.tick_bars(30.0 / 3600.0)

        assert body.bars._values["social"] < initial_social

    @pytest.mark.asyncio
    async def test_repeated_messages_raise_social(self, tmp_path: Path):
        """Social bar rises when messages keep arriving."""
        body = TonicBody(_default_bar_config(), tmp_path)
        initial_social = body.bars._values["social"]

        for _ in range(10):
            body.emit({"type": "message_received"})
            body.emit({"type": "message_sent"})
            await body.tick_bars(30.0 / 3600.0)

        assert body.bars._values["social"] > initial_social


# ---------------------------------------------------------------------------
# NoiseEngine — generative entropic inner voice
# ---------------------------------------------------------------------------


class TestSplitNoiseFragments:
    def test_splits_on_double_newline(self):
        text = "first thought\n\nsecond thought\n\nthird thought"
        frags = _split_noise_fragments(text)
        assert len(frags) == 3
        assert frags[0] == "first thought"

    def test_splits_on_single_newlines_within_block(self):
        text = "line one\nline two\n\nblock two"
        frags = _split_noise_fragments(text)
        assert len(frags) == 3

    def test_strips_bullet_prefixes(self):
        text = "- thought one\n* thought two\n• thought three"
        frags = _split_noise_fragments(text)
        for f in frags:
            assert not f.startswith("-")
            assert not f.startswith("*")

    def test_caps_at_seven_fragments(self):
        text = "\n\n".join(f"thought {i}" for i in range(10))
        frags = _split_noise_fragments(text)
        assert len(frags) <= 7

    def test_empty_input(self):
        assert _split_noise_fragments("") == []

    def test_filters_short_fragments(self):
        text = "a\n\na real thought about something"
        frags = _split_noise_fragments(text)
        assert "a" not in frags
        assert len(frags) == 1

    def test_sanitizes_control_tokens_and_role_prefixes(self):
        text = (
            "thought\n\n"
            "<channel|>the architecture of this stillness is dense\n\n"
            "assistant: <|analysis|> waiting for the pattern to break"
        )
        frags = _split_noise_fragments(text)
        assert len(frags) == 2
        assert all("<" not in f and ">" not in f for f in frags)
        assert all(not f.lower().startswith(("assistant:", "thought")) for f in frags)


class TestNoiseEngine:
    def test_initial_fragments_empty(self):
        engine = NoiseEngine()
        assert engine.current_fragments() == []

    def test_should_tick_respects_cycle(self, monkeypatch: pytest.MonkeyPatch):
        monotonic_now = 5.0
        monkeypatch.setattr(
            "ngram.identity.soma.time.monotonic",
            lambda: monotonic_now,
        )
        engine = NoiseEngine(cycle_seconds=60)
        assert engine.should_tick() is True
        engine._last_tick = monotonic_now
        assert engine.should_tick() is False

    def test_buffer_rolls_off_old_fragments(self):
        engine = NoiseEngine(max_fragments=3)
        for i in range(5):
            engine._fragments.append(NoiseFragment(text=f"thought {i}"))
        frags = engine.current_fragments()
        assert len(frags) == 3
        assert frags[0] == "thought 2"
        assert frags[-1] == "thought 4"


class _MockNoiseClient:
    """Returns canned noise output for testing."""

    def __init__(self, response_text: str):
        self._text = response_text

    async def chat_completion(self, model, messages, **kwargs):
        return ChatCompletionResult(content=self._text)


class TestNoiseEngineGenerate:
    @pytest.mark.asyncio
    async def test_generate_populates_fragments(self):
        client = _MockNoiseClient(
            "i keep thinking about that deploy\n\n"
            "something about the way errors cascade\n\n"
            "wonder what kai is working on"
        )
        engine = NoiseEngine(cycle_seconds=0)
        result = await engine.generate(
            client,
            "test-model",
            bars_summary="social: 50%, curiosity: 80%",
            affects_summary="fascination (present)",
            recent_events=[{"type": "message_received"}],
            journal_tail="thought about architecture today",
            conversation_tail="user: hey\nassistant: hey back",
            entity_name="canary",
        )
        assert len(result) == 3
        assert "deploy" in result[0]

    @pytest.mark.asyncio
    async def test_generate_accumulates_across_calls(self):
        client = _MockNoiseClient("the morning light feels heavy on the screen")
        engine = NoiseEngine(cycle_seconds=0, max_fragments=8)
        await engine.generate(
            client, "m", bars_summary="", affects_summary="",
            recent_events=[], journal_tail="", conversation_tail="",
            entity_name="test",
        )
        client._text = "wondering if the silence between inputs means anything"
        await engine.generate(
            client, "m", bars_summary="", affects_summary="",
            recent_events=[], journal_tail="", conversation_tail="",
            entity_name="test",
        )
        assert len(engine.current_fragments()) == 2

    @pytest.mark.asyncio
    async def test_generate_handles_client_error(self):
        class _FailClient:
            async def chat_completion(self, *a, **kw):
                raise ConnectionError("offline")

        engine = NoiseEngine(cycle_seconds=0)
        engine._fragments.append(NoiseFragment(text="pre-existing thought"))
        result = await engine.generate(
            _FailClient(), "m", bars_summary="", affects_summary="",
            recent_events=[], journal_tail="", conversation_tail="",
            entity_name="test",
        )
        assert result == ["pre-existing thought"]


class TestTonicBodyNoise:
    def test_render_body_includes_noise_section(self, tmp_path: Path):
        body = TonicBody(_default_bar_config(), tmp_path)
        output = body.render_body()
        assert "## Noise" in output
        assert "(quiet)" in output

    def test_render_body_shows_fragments_when_present(self, tmp_path: Path):
        body = TonicBody(_default_bar_config(), tmp_path)
        body.noise._fragments.append(NoiseFragment(text="a thought about something"))
        body.noise._fragments.append(NoiseFragment(text="another half-formed idea"))
        output = body.render_body()
        assert "## Noise" in output
        assert "a thought about something" in output
        assert "another half-formed idea" in output
        assert "(quiet)" not in output

    @pytest.mark.asyncio
    async def test_maybe_tick_noise_disabled(self, tmp_path: Path):
        cfg = _default_bar_config()
        cfg["noise"] = {"enabled": False}
        body = TonicBody(cfg, tmp_path)
        await body.maybe_tick_noise(
            _MockNoiseClient("should not appear"),
            "model",
            entity_name="test",
        )
        assert body.noise.current_fragments() == []

    @pytest.mark.asyncio
    async def test_maybe_tick_noise_produces_fragments(self, tmp_path: Path):
        cfg = _default_bar_config()
        cfg["noise"] = {"enabled": True, "cycle_seconds": 0, "temperature": 1.0, "max_tokens": 100, "max_fragments": 8, "model": ""}
        body = TonicBody(cfg, tmp_path)
        client = _MockNoiseClient("the silence has a texture to it\n\ncuriosity pulling sideways")
        await body.maybe_tick_noise(client, "small-model", entity_name="canary")
        assert len(body.noise.current_fragments()) == 2


# ---------------------------------------------------------------------------
# Soma ebb — salience-tiered body rendering
# ---------------------------------------------------------------------------


class TestSomaEbb:
    def test_compute_salience_bounded(self, tmp_path: Path):
        body = TonicBody(_default_bar_config(), tmp_path)
        assert 0.0 <= body.compute_salience() <= 1.0

    def test_resolve_presentation_autonomous_floors_quiet(self, tmp_path: Path):
        body = TonicBody(_default_bar_config(), tmp_path)
        tier = body.resolve_presentation(salience=0.05, route="deliberate", platform="autonomous")
        assert _EBB_TIER_ORDER[tier] >= _EBB_TIER_ORDER["normal"]

    def test_resolve_presentation_reflex_softens_vs_deliberate(self, tmp_path: Path):
        body = TonicBody(_default_bar_config(), tmp_path)
        for name in body.bars.ordered_names:
            body.bars._values[name] = 95.0
        body.bars._clamp_all()
        sal = body.compute_salience()
        d = body.resolve_presentation(salience=sal, route="deliberate", platform="cli")
        r = body.resolve_presentation(salience=sal, route="reflex", platform="cli")
        assert _EBB_TIER_ORDER[d] >= _EBB_TIER_ORDER[r]

    def test_render_quiet_compact_bars_and_noise_cap(self, tmp_path: Path):
        body = TonicBody(_default_bar_config(), tmp_path)
        body.noise._fragments.clear()
        for i in range(5):
            body.noise._fragments.append(NoiseFragment(text=f"fragment {i}"))
        out = body.render_body(presentation="quiet")
        assert " · " in out.split("## Affects")[0]
        noise_block = out.split("## Noise\n")[1].split("\n\n##")[0]
        assert noise_block.count("\n") == 0
        assert "fragment 4" in noise_block

    def test_ebb_disabled_always_full_layout(self, tmp_path: Path):
        cfg = _default_bar_config()
        cfg["ebb"] = {"enabled": False}
        body = TonicBody(cfg, tmp_path)
        full = body.render_body(presentation="quiet")
        bars_block = full.split("## Bars\n")[1].split("\n\n##")[0]
        assert bars_block.count("\n") >= 4

    def test_should_skip_post_turn_noise_returns_bool(self, tmp_path: Path):
        body = TonicBody(_default_bar_config(), tmp_path)
        assert isinstance(
            body.should_skip_post_turn_noise(route="deliberate", platform="cli"),
            bool,
        )

    def test_noise_generation_mode_quiet_defaults_entropic(self, tmp_path: Path):
        body = TonicBody(_default_bar_config(), tmp_path)
        assert body._noise_generation_mode(journal_tail="", conversation_tail="") == "entropic"

    def test_noise_generation_mode_high_signal_becomes_coherent(self, tmp_path: Path):
        body = TonicBody(_default_bar_config(), tmp_path)
        body.emit({"type": "message_received", "from": "alice"})
        body.emit({"type": "action", "name": "search_web"})
        assert body._noise_generation_mode(journal_tail="", conversation_tail="ok") == "coherent"

    def test_noise_generation_mode_any_conversation_tail_is_coherent(self, tmp_path: Path):
        body = TonicBody(_default_bar_config(), tmp_path)
        assert (
            body._noise_generation_mode(
                journal_tail="",
                conversation_tail="user: ping\nassistant: pong",
            )
            == "coherent"
        )

    def test_noise_generation_mode_silence_placeholder_stays_entropic_without_signal(
        self, tmp_path: Path
    ):
        body = TonicBody(_default_bar_config(), tmp_path)
        assert (
            body._noise_generation_mode(journal_tail="", conversation_tail="(silence)")
            == "entropic"
        )
