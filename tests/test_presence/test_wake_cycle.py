from __future__ import annotations

import json
import math
from types import SimpleNamespace
from unittest.mock import patch

from ngram.config import AutonomySettings
from ngram.presence.wake_cycle import (
    WakeCycleEngine,
    _meta_leak_detected,
    _proactive_outbound_since,
    _verified_conversation_tail,
)


def _engine(*, allow_tools: bool) -> WakeCycleEngine:
    auto = AutonomySettings(allow_tool_calls_on_wake=allow_tools)
    cfg = SimpleNamespace(harness=SimpleNamespace(autonomy=auto))
    return WakeCycleEngine(cfg)  # type: ignore[arg-type]


def _engine_deterministic_intent() -> WakeCycleEngine:
    auto = AutonomySettings(
        allow_tool_calls_on_wake=True,
        wake_intent_softmax_temperature=0.0,
        wake_entropy_score_noise=0.0,
    )
    cfg = SimpleNamespace(harness=SimpleNamespace(autonomy=auto))
    return WakeCycleEngine(cfg)  # type: ignore[arg-type]


def _entity() -> SimpleNamespace:
    return SimpleNamespace(_platforms={"telegram": object(), "discord": object()})


def test_schedule_next_timer_spacing_extra_when_wakes_clustered() -> None:
    auto = AutonomySettings(
        base_wake_interval_min=20,
        base_wake_interval_max=45,
        wake_spacing_extra_minutes_max=60.0,
        wake_spacing_gap_hours_tau=3.0,
    )
    cfg = SimpleNamespace(harness=SimpleNamespace(autonomy=auto))
    eng = WakeCycleEngine(cfg)  # type: ignore[arg-type]
    gap_sec = 600.0
    gh = gap_sec / 3600.0
    tau = 3.0
    expected_extra = 60.0 * 60.0 * math.exp(-gh / tau)
    with patch("ngram.presence.wake_cycle.time.time", return_value=1_000_000.0), patch(
        "ngram.presence.wake_cycle.random.uniform",
        return_value=100.0,
    ):
        t = eng._schedule_next_timer(gap_seconds_since_previous_wake=gap_sec)
    assert t == 1_000_000.0 + 100.0 + expected_extra


def test_schedule_next_timer_spacing_extra_negligible_when_gap_large() -> None:
    auto = AutonomySettings(
        base_wake_interval_min=20,
        base_wake_interval_max=45,
        wake_spacing_extra_minutes_max=60.0,
        wake_spacing_gap_hours_tau=3.0,
    )
    cfg = SimpleNamespace(harness=SimpleNamespace(autonomy=auto))
    eng = WakeCycleEngine(cfg)  # type: ignore[arg-type]
    gap_sec = 86400.0 * 7
    with patch("ngram.presence.wake_cycle.time.time", return_value=1_000_000.0), patch(
        "ngram.presence.wake_cycle.random.uniform",
        return_value=100.0,
    ):
        t = eng._schedule_next_timer(gap_seconds_since_previous_wake=gap_sec)
    assert abs(t - (1_000_000.0 + 100.0)) < 1e-6


def test_schedule_next_timer_no_spacing_when_disabled() -> None:
    auto = AutonomySettings(
        base_wake_interval_min=20,
        base_wake_interval_max=45,
        wake_spacing_extra_minutes_max=0.0,
        wake_spacing_gap_hours_tau=3.0,
    )
    cfg = SimpleNamespace(harness=SimpleNamespace(autonomy=auto))
    eng = WakeCycleEngine(cfg)  # type: ignore[arg-type]
    with patch("ngram.presence.wake_cycle.time.time", return_value=1_000_000.0), patch(
        "ngram.presence.wake_cycle.random.uniform",
        return_value=100.0,
    ):
        t = eng._schedule_next_timer(gap_seconds_since_previous_wake=60.0)
    assert t == 1_000_000.0 + 100.0


def test_build_context_mentions_tool_venture_when_enabled() -> None:
    eng = _engine(allow_tools=True)
    text = eng._build_context(_entity(), tonic=None, stirring="stir", reason="timer")
    assert "venture out with tools" in text
    assert "Use any relevant tools" in text


def test_build_context_requires_retrospective_evidence() -> None:
    eng = _engine(allow_tools=True)
    text = eng._build_context(_entity(), tonic=None, stirring="an incident-report association", reason="timer")
    assert "[Evidence boundary]" in text
    assert "not evidence that an event, conversation, file, or tool result existed" in text
    assert "never as shared history" in text


def test_verified_conversation_tail_excludes_proactive_self_claims() -> None:
    history = [
        {"role": "user", "content": "What are we working on?"},
        {"role": "assistant", "content": "[ngram:proactive_outbound] I re-read an old incident report."},
        {"role": "assistant", "content": "A normal, user-prompted answer."},
        {"role": "assistant", "content": "[proactive message you sent] We discussed a power grid."},
        {"role": "user", "content": "We never discussed that."},
    ]
    tail = _verified_conversation_tail(history, limit=3)
    assert "incident report" not in tail
    assert "power grid" not in tail
    assert "What are we working on?" in tail
    assert "A normal, user-prompted answer." in tail
    assert "We never discussed that." in tail


def test_proactive_outbound_since_extracts_only_new_delivered_speech() -> None:
    history = [
        {"role": "assistant", "content": "[ngram:proactive_outbound] old"},
        {"role": "assistant", "content": "ordinary final text"},
        {"role": "assistant", "content": "[ngram:proactive_outbound] first new message"},
        {"role": "assistant", "content": "[proactive message you sent] second new message"},
    ]
    assert _proactive_outbound_since(history, 1) == "first new message\nsecond new message"


def test_build_context_includes_internal_disposition_when_poker_set() -> None:
    eng = _engine(allow_tools=True)
    text = eng._build_context(
        _entity(),
        tonic=None,
        stirring="stir",
        reason="timer",
        poker_disposition="notice the loose end",
    )
    assert "[Internal disposition" in text
    assert "loose permission" in text
    assert "generative noise (GEN)" in text
    assert "notice the loose end" in text
    assert "[Your subconscious stirring]" in text


def test_build_context_constrains_tools_when_disabled() -> None:
    eng = _engine(allow_tools=False)
    text = eng._build_context(_entity(), tonic=None, stirring="stir", reason="timer")
    assert "constrained right now" in text
    assert "do not venture into tool use" in text


def test_resolve_wake_delivery_prefers_last_conversation_route() -> None:
    eng = _engine(allow_tools=True)
    tg = object()
    entity = SimpleNamespace(
        _platforms={"telegram": tg, "discord": object()},
        _last_conversation={"platform": "telegram", "channel": "12345"},
    )
    ch, pf = eng._resolve_wake_delivery(entity)
    assert ch == "12345"
    assert pf is tg


def test_resolve_wake_delivery_falls_back_to_platform_last_channel() -> None:
    eng = _engine(allow_tools=True)
    dc = SimpleNamespace(last_channel_id="777")
    entity = SimpleNamespace(
        _platforms={"cli": object(), "discord": dc},
        _last_conversation={"platform": "telegram", "channel": "12345"},
    )
    ch, pf = eng._resolve_wake_delivery(entity)
    assert ch == "777"
    assert pf is dc


def test_meta_leak_detector_flags_control_flow_speech() -> None:
    assert _meta_leak_detected("The user has seen my summary. I'll just end the turn.")
    assert not _meta_leak_detected("i found two interesting posts and saved notes")


def test_meta_leak_detector_flags_history_tag_echo() -> None:
    assert _meta_leak_detected("[you sent this unprompted] hello")
    assert _meta_leak_detected("echo ngram:proactive_outbound from context")


def test_load_recent_threads_reads_episode_tail(tmp_path) -> None:
    eng = _engine(allow_tools=True)
    cfg = SimpleNamespace(journal_path=lambda: str(tmp_path / "journal.md"))
    entity = SimpleNamespace(config=cfg)
    p = tmp_path / "wake_episodes.jsonl"
    p.write_text(
        "\n".join(
            [
                json.dumps({"carryover_threads": ["alpha", "beta"]}),
                json.dumps({"carryover_threads": ["beta", "gamma"]}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    threads = eng._load_recent_threads(entity)
    assert threads[:3] == ["beta", "gamma", "alpha"]


def test_build_user_end_card_contains_continuity() -> None:
    eng = _engine(allow_tools=True)
    card = eng._build_user_end_card(
        reason="timer",
        wake_intent="explore_question",
        rounds_completed=3,
        tools_all=["search_web", "fetch_url"],
        carryover=["explore_question from timer"],
    )
    joined = "\n".join(card)
    assert "lean: explore_question" in joined
    assert "next pull" in joined


def test_extract_lingering_sparks_pulls_old_opportunities() -> None:
    eng = _engine(allow_tools=True)
    entity = SimpleNamespace(
        _history=[
            {"role": "user", "content": "hey"},
            {"role": "user", "content": "you should learn how to parse rss feeds later"},
            {"role": "assistant", "content": "we could build a tiny game sometime"},
        ]
    )
    sparks = eng._extract_lingering_sparks(entity)
    assert any("learn how to parse rss feeds" in s for s in sparks)
    assert any("build a tiny game" in s for s in sparks)


def test_session_memory_board_is_bounded_and_structured() -> None:
    eng = _engine(allow_tools=True)
    mem = eng._new_session_memory(
        reason="timer",
        wake_intent="build_something",
        wake_want="I want to build a relay prototype.",
        lingering_sparks=["build that relay", "learn rss parsing"],
        project_lines=["relay [active] — ship parser | next: tests"],
        skill_lines=["rss parsing: parse feed + dedupe entries"],
        continuity=["build_something from timer"],
    )
    eng._update_session_memory(
        mem,
        tool_names=["search_web", "fetch_url", "update_skill"],
        reply_text="found a clean parser pattern. next i should implement retries.",
        round_idx=1,
    )
    board = eng._render_session_memory_block(mem, max_chars=520)
    assert "objective:" in board
    assert "want:" in board
    assert "threads:" in board
    assert "recent_actions:" in board
    assert len(board) <= 520


def test_compose_wake_want_mentions_intent_and_sources() -> None:
    eng = _engine(allow_tools=True)
    want = eng._compose_wake_want(
        wake_intent="learn_something",
        reason="desire:learn:rss",
        lingering_sparks=["learn rss parsing from last chat"],
        project_lines=["feed relay [active] — unfinished"],
        skill_lines=["http retries: backoff + jitter"],
    )
    assert any(
        p in want
        for p in (
            "I want to learn a useful technique",
            "I want to understand something I only gesture at",
            "I want to compress confusion into a skill",
        )
    )
    assert "spark:" in want


def test_maybe_revise_want_pivots_when_stalled() -> None:
    eng = _engine(allow_tools=True)
    mem = eng._new_session_memory(
        reason="timer",
        wake_intent="explore_question",
        wake_want="I want to chase a question and see what I uncover.",
        lingering_sparks=["learn rss parsing from last chat"],
        project_lines=[],
        skill_lines=[],
        continuity=[],
    )
    revised = eng._maybe_revise_want(
        mem,
        tool_names=["search_web", "fetch_url"],
        reply_text="not sure. can't find anything useful. no result.",
        round_idx=2,
    )
    assert revised is not None
    assert "I want to learn one specific missing piece" in str(mem.get("want") or "")
    assert float(mem.get("want_confidence") or 0.0) >= 0.5
    assert int(mem.get("want_revision_count") or 0) >= 1


def test_select_primary_intent_entropy_fear_prefers_outward_over_follow_desire() -> None:
    eng = _engine_deterministic_intent()
    eng._last_desires = [
        {
            "kind": "fear_stir",
            "urgency": 0.88,
            "source": "entropy",
            "why": "entropy:soul",
            "target": "a specific way stalling could hurt you",
        },
    ]
    tonic = SimpleNamespace(bars=SimpleNamespace(snapshot_pct=lambda: {}))
    intent = eng._select_primary_intent(
        "desire:fear_stir:a specific way stalling could hurt you",
        tonic=tonic,
        entity=SimpleNamespace(),
        lingering_sparks=[],
    )
    assert intent == "explore_question"


def test_build_session_continuation_includes_outward_nudge_from_round_two() -> None:
    eng = _engine(allow_tools=True)
    text = eng._build_session_continuation(
        stirring="stir",
        reason="desire:test",
        poker_disposition=None,
        round_idx=2,
        max_rounds=4,
        accumulated_tools=["read_file", "list_directory"],
        last_reply="done",
        wide_mode=False,
        session_memory="board",
        wake_want="want",
    )
    assert "[Outward modality — round 2+]" in text
    assert "fetch_url or search_web" in text


def test_build_session_continuation_skips_outward_nudge_on_round_one() -> None:
    eng = _engine(allow_tools=True)
    text = eng._build_session_continuation(
        stirring="stir",
        reason="desire:test",
        poker_disposition=None,
        round_idx=1,
        max_rounds=4,
        accumulated_tools=[],
        last_reply="",
        wide_mode=False,
        session_memory="",
        wake_want="",
    )
    assert "[Outward modality — round 2+]" not in text


def test_carryover_marks_prior_autonomous_speech_as_unverified() -> None:
    eng = _engine(allow_tools=True)
    threads = eng._derive_carryover_threads(
        reason="timer",
        wake_intent="explore_question",
        tool_names=["say"],
        last_reply="I re-read the migration report.",
    )
    assert any("not evidence; verify before repeating" in thread for thread in threads)
