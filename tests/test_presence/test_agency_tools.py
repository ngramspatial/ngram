"""Tests for agency primitive tools (think, end_turn, wait)."""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from ngram.presence.tools.agency import think, end_turn, say, wait
from ngram.presence.tools.runtime import ToolRuntimeContext, set_tool_runtime, reset_tool_runtime
from ngram.presence.tools.registry import format_tool_activity


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(state: dict[str, Any] | None = None) -> tuple[ToolRuntimeContext, object]:
    ctx = ToolRuntimeContext(
        entity=MagicMock(),
        inp=MagicMock(),
        platform=None,
        state=state if state is not None else {},
    )
    tok = set_tool_runtime(ctx)
    return ctx, tok


# ---------------------------------------------------------------------------
# think
# ---------------------------------------------------------------------------


class TestThink:
    @pytest.mark.asyncio
    async def test_returns_thought_recorded(self):
        ctx, tok = _make_ctx()
        try:
            result = await think("i wonder about the deploy")
            assert result == "[thought recorded]"
        finally:
            reset_tool_runtime(tok)

    @pytest.mark.asyncio
    async def test_appends_to_private_thoughts(self):
        state: dict[str, Any] = {}
        ctx, tok = _make_ctx(state)
        try:
            await think("first thought")
            await think("second thought")
            assert state["private_thoughts"] == ["first thought", "second thought"]
        finally:
            reset_tool_runtime(tok)

    @pytest.mark.asyncio
    async def test_works_with_none_state(self):
        ctx = ToolRuntimeContext(entity=MagicMock(), inp=MagicMock(), platform=None, state=None)
        tok = set_tool_runtime(ctx)
        try:
            result = await think("a thought")
            assert result == "[thought recorded]"
        finally:
            reset_tool_runtime(tok)


# ---------------------------------------------------------------------------
# end_turn
# ---------------------------------------------------------------------------


class TestEndTurn:
    @pytest.mark.asyncio
    async def test_returns_turn_ended(self):
        ctx, tok = _make_ctx()
        try:
            result = await end_turn()
            assert result == "[turn ended]"
        finally:
            reset_tool_runtime(tok)

    @pytest.mark.asyncio
    async def test_sets_end_turn_flag(self):
        state: dict[str, Any] = {}
        ctx, tok = _make_ctx(state)
        try:
            await end_turn()
            assert state["_end_turn"] is True
        finally:
            reset_tool_runtime(tok)

    @pytest.mark.asyncio
    async def test_captures_mood(self):
        state: dict[str, Any] = {}
        ctx, tok = _make_ctx(state)
        try:
            await end_turn(mood="curious and a little restless")
            assert state["_end_turn_mood"] == "curious and a little restless"
        finally:
            reset_tool_runtime(tok)

    @pytest.mark.asyncio
    async def test_captures_thought(self):
        state: dict[str, Any] = {}
        ctx, tok = _make_ctx(state)
        try:
            await end_turn(thought="that conversation stuck with me")
            assert state["_end_turn_thought"] == "that conversation stuck with me"
        finally:
            reset_tool_runtime(tok)

    @pytest.mark.asyncio
    async def test_no_mood_or_thought_when_empty(self):
        state: dict[str, Any] = {}
        ctx, tok = _make_ctx(state)
        try:
            await end_turn()
            assert "_end_turn_mood" not in state
            assert "_end_turn_thought" not in state
        finally:
            reset_tool_runtime(tok)


# ---------------------------------------------------------------------------
# wait
# ---------------------------------------------------------------------------


class TestWait:
    @pytest.mark.asyncio
    async def test_returns_waited_message(self):
        ctx, tok = _make_ctx()
        try:
            result = await wait(1)
            assert "[waited 1s]" == result
        finally:
            reset_tool_runtime(tok)

    @pytest.mark.asyncio
    async def test_clamps_minimum(self):
        ctx, tok = _make_ctx()
        try:
            result = await wait(0)
            assert "[waited 1s]" == result
        finally:
            reset_tool_runtime(tok)

    @pytest.mark.asyncio
    async def test_clamps_maximum(self):
        ctx, tok = _make_ctx()
        try:
            t0 = time.monotonic()
            result = await wait(999)
            elapsed = time.monotonic() - t0
            assert "[waited 15s]" == result
            assert elapsed < 20
        finally:
            reset_tool_runtime(tok)

    @pytest.mark.asyncio
    async def test_actually_delays(self):
        ctx, tok = _make_ctx()
        try:
            t0 = time.monotonic()
            await wait(1)
            elapsed = time.monotonic() - t0
            assert elapsed >= 0.9
        finally:
            reset_tool_runtime(tok)


# ---------------------------------------------------------------------------
# say
# ---------------------------------------------------------------------------


class TestSay:
    @pytest.mark.asyncio
    async def test_strips_message_envelope_before_delivery(self):
        platform = MagicMock()
        platform.send_message = AsyncMock(return_value=None)
        inp = MagicMock()
        inp.channel = "123"
        state: dict[str, Any] = {"_messages_sent": 0, "_message_budget": 6}
        ctx = ToolRuntimeContext(entity=MagicMock(), inp=inp, platform=platform, state=state)
        tok = set_tool_runtime(ctx)
        try:
            result = await say("<message>i'm here.</message>")
            assert result == "[sent]"
            assert state["_sent_messages"] == ["i'm here."]
            platform.send_message.assert_awaited_once_with("123", "i'm here.")
        finally:
            reset_tool_runtime(tok)

    @pytest.mark.asyncio
    async def test_increments_state_only_after_successful_send(self):
        platform = MagicMock()
        platform.send_message = AsyncMock(return_value=None)
        inp = MagicMock()
        inp.channel = "123"
        state: dict[str, Any] = {"_messages_sent": 0, "_message_budget": 6}
        ctx = ToolRuntimeContext(entity=MagicMock(), inp=inp, platform=platform, state=state)
        tok = set_tool_runtime(ctx)
        try:
            result = await say("hello there")
            assert result == "[sent]"
            assert state["_messages_sent"] == 1
            assert state["_sent_messages"] == ["hello there"]
            platform.send_message.assert_awaited_once_with("123", "hello there")
        finally:
            reset_tool_runtime(tok)

    @pytest.mark.asyncio
    async def test_does_not_increment_state_when_send_fails(self):
        platform = MagicMock()
        platform.send_message = AsyncMock(side_effect=RuntimeError("telegram timeout"))
        inp = MagicMock()
        inp.channel = "123"
        state: dict[str, Any] = {"_messages_sent": 0, "_message_budget": 6}
        ctx = ToolRuntimeContext(entity=MagicMock(), inp=inp, platform=platform, state=state)
        tok = set_tool_runtime(ctx)
        try:
            result = await say("hello there")
            assert result.startswith("[send failed:")
            assert state["_messages_sent"] == 0
            assert "_sent_messages" not in state
            platform.send_message.assert_awaited_once_with("123", "hello there")
        finally:
            reset_tool_runtime(tok)

    @pytest.mark.asyncio
    async def test_suppresses_identical_message_within_same_turn(self):
        platform = MagicMock()
        platform.send_message = AsyncMock(return_value=None)
        inp = MagicMock()
        inp.channel = "123"
        state: dict[str, Any] = {"_messages_sent": 0, "_message_budget": 6}
        ctx = ToolRuntimeContext(entity=MagicMock(), inp=inp, platform=platform, state=state)
        tok = set_tool_runtime(ctx)
        try:
            assert await say("same response") == "[sent]"
            assert await say("same response") == "[duplicate message suppressed]"
            assert state["_messages_sent"] == 1
            platform.send_message.assert_awaited_once_with("123", "same response")
        finally:
            reset_tool_runtime(tok)


# ---------------------------------------------------------------------------
# Tool activity display
# ---------------------------------------------------------------------------


class TestToolActivity:
    def test_think_produces_no_activity(self):
        assert format_tool_activity("think", {"thought": "stuff"}) is None

    def test_end_turn_produces_no_activity(self):
        assert format_tool_activity("end_turn", {}) is None

    def test_wait_produces_no_activity(self):
        assert format_tool_activity("wait", {"seconds": 5}) is None


# ---------------------------------------------------------------------------
# Completion gate integration
# ---------------------------------------------------------------------------


class TestCompletionGateEndTurn:
    """Test that the completion gate respects _end_turn in tool_state."""

    @pytest.mark.asyncio
    async def test_gate_approves_when_end_turn_set(self):
        from ngram.config import HarnessConfig, entity_from_dict
        from ngram.entity import Entity
        from ngram.cognition.deliberate import AgentLoopState
        from ngram.inference.types import ChatCompletionResult
        from ngram.models import Input

        class _StubProvider:
            async def chat_completion(self, *a, **kw):
                return ChatCompletionResult(content="ok")
            async def embed(self, *a, **kw):
                return [0.01] * 8
            async def health(self):
                return {"ok": True}
            async def list_models(self):
                return ["stub"]
            async def close(self):
                pass
            async def ensure_models(self, *names):
                return True, []

        h = HarnessConfig()
        data = {
            "name": "AgencyTest",
            "personality": {
                "core_traits": {k: 0.5 for k in ["curiosity", "warmth", "assertiveness", "humor", "openness", "neuroticism", "conscientiousness"]},
                "behavioral_patterns": {},
                "voice": {},
                "backstory": "Test entity.",
            },
            "drives": {"curiosity_topics": [], "attachment_threshold": 5, "restlessness_decay": 3600, "initiative_cooldown": 1800},
            "cognition": {},
            "presence": {"platforms": [], "daemon": {}},
        }
        import ngram.entity as ent_mod
        original = ent_mod.build_inference_provider
        ent_mod.build_inference_provider = lambda _h: _StubProvider()
        try:
            entity = Entity(entity_from_dict(h, data))
            tool_state = {
                "_end_turn": True,
                "tool_calls": 1,
                "_sent_messages": ["hey back — all good here."],
            }
            done, reason = await entity._loop_completion_gate(
                Input(text="hey", person_id="u", person_name="U"),
                "",
                ChatCompletionResult(content=""),
                AgentLoopState(tool_calls_seen=1),
                tool_state,
            )
            assert done is True
            assert reason is None
        finally:
            ent_mod.build_inference_provider = original
            await entity.shutdown()

    @pytest.mark.asyncio
    async def test_gate_rejects_end_turn_without_visible_reply_on_telegram(self):
        from ngram.config import HarnessConfig, entity_from_dict
        from ngram.entity import Entity
        from ngram.cognition.deliberate import AgentLoopState
        from ngram.inference.types import ChatCompletionResult
        from ngram.models import Input

        class _StubProvider:
            async def chat_completion(self, *a, **kw):
                return ChatCompletionResult(content="ok")

            async def embed(self, *a, **kw):
                return [0.01] * 8

            async def health(self):
                return {"ok": True}

            async def list_models(self):
                return ["stub"]

            async def close(self):
                pass

            async def ensure_models(self, *names):
                return True, []

        h = HarnessConfig()
        data = {
            "name": "AgencyTestTelegramGate",
            "personality": {
                "core_traits": {k: 0.5 for k in ["curiosity", "warmth", "assertiveness", "humor", "openness", "neuroticism", "conscientiousness"]},
                "behavioral_patterns": {},
                "voice": {},
                "backstory": "Test entity.",
            },
            "drives": {"curiosity_topics": [], "attachment_threshold": 5, "restlessness_decay": 3600, "initiative_cooldown": 1800},
            "cognition": {},
            "presence": {"platforms": [], "daemon": {}},
        }
        import ngram.entity as ent_mod

        original = ent_mod.build_inference_provider
        ent_mod.build_inference_provider = lambda _h: _StubProvider()
        try:
            entity = Entity(entity_from_dict(h, data))
            tool_state = {"_end_turn": True, "tool_calls": 1}
            done, reason = await entity._loop_completion_gate(
                Input(text="hey", person_id="u", person_name="U", platform="telegram"),
                "",
                ChatCompletionResult(content=""),
                AgentLoopState(tool_calls_seen=1),
                tool_state,
            )
            assert done is False
            assert reason is not None
            assert "say()" in (reason or "").lower()
            assert "_end_turn" not in tool_state
        finally:
            ent_mod.build_inference_provider = original
            await entity.shutdown()

    @pytest.mark.asyncio
    async def test_gate_force_done_after_silent_completion_failures(self):
        from ngram.config import HarnessConfig, entity_from_dict
        from ngram.entity import Entity, _MAX_SILENT_COMPLETION_FAILURES_BEFORE_FORCE_REPLY
        from ngram.cognition.deliberate import AgentLoopState
        from ngram.inference.types import ChatCompletionResult
        from ngram.models import Input

        class _StubProvider:
            async def chat_completion(self, *a, **kw):
                return ChatCompletionResult(content="ok")

            async def embed(self, *a, **kw):
                return [0.01] * 8

            async def health(self):
                return {"ok": True}

            async def list_models(self):
                return ["stub"]

            async def close(self):
                pass

            async def ensure_models(self, *names):
                return True, []

        h = HarnessConfig()
        data = {
            "name": "AgencyTestSilentLoops",
            "personality": {
                "core_traits": {k: 0.5 for k in ["curiosity", "warmth", "assertiveness", "humor", "openness", "neuroticism", "conscientiousness"]},
                "behavioral_patterns": {},
                "voice": {},
                "backstory": "Test entity.",
            },
            "drives": {"curiosity_topics": [], "attachment_threshold": 5, "restlessness_decay": 3600, "initiative_cooldown": 1800},
            "cognition": {},
            "presence": {"platforms": [], "daemon": {}},
        }
        import ngram.entity as ent_mod

        original = ent_mod.build_inference_provider
        ent_mod.build_inference_provider = lambda _h: _StubProvider()
        try:
            entity = Entity(entity_from_dict(h, data))
            tool_state: dict = {"_messages_sent": 0}
            done, reason = await entity._loop_completion_gate(
                Input(text="hey", person_id="u", person_name="U", platform="telegram"),
                "<end_turn>",
                ChatCompletionResult(content="<end_turn>", finish_reason="stop"),
                AgentLoopState(
                    completion_failures=_MAX_SILENT_COMPLETION_FAILURES_BEFORE_FORCE_REPLY,
                ),
                tool_state,
            )
            assert done is True
            assert reason is None
            assert tool_state.get("_force_user_anchored_reply") is True
        finally:
            ent_mod.build_inference_provider = original
            await entity.shutdown()

    @pytest.mark.asyncio
    async def test_gate_accepts_substantive_plain_reply_without_end_turn(self):
        from ngram.config import HarnessConfig, entity_from_dict
        from ngram.entity import Entity
        from ngram.cognition.deliberate import AgentLoopState
        from ngram.inference.types import ChatCompletionResult
        from ngram.models import Input

        class _StubProvider:
            async def chat_completion(self, *a, **kw):
                return ChatCompletionResult(content="ok")

            async def embed(self, *a, **kw):
                return [0.01] * 8

            async def health(self):
                return {"ok": True}

            async def list_models(self):
                return ["stub"]

            async def close(self):
                pass

            async def ensure_models(self, *names):
                return True, []

        h = HarnessConfig()
        data = {
            "name": "AgencyTestSilentLoopsSubstantive",
            "personality": {
                "core_traits": {k: 0.5 for k in ["curiosity", "warmth", "assertiveness", "humor", "openness", "neuroticism", "conscientiousness"]},
                "behavioral_patterns": {},
                "voice": {},
                "backstory": "Test entity.",
            },
            "drives": {"curiosity_topics": [], "attachment_threshold": 5, "restlessness_decay": 3600, "initiative_cooldown": 1800},
            "cognition": {},
            "presence": {"platforms": [], "daemon": {}},
        }
        import ngram.entity as ent_mod

        original = ent_mod.build_inference_provider
        ent_mod.build_inference_provider = lambda _h: _StubProvider()
        try:
            entity = Entity(entity_from_dict(h, data))
            tool_state = {"_messages_sent": 0}
            done, reason = await entity._loop_completion_gate(
                Input(text="hey", person_id="u", person_name="U", platform="telegram"),
                "A real reply the user could read.",
                ChatCompletionResult(content="A real reply the user could read.", finish_reason="stop"),
                AgentLoopState(completion_failures=0),
                tool_state,
            )
            assert done is True
            assert reason is None
            assert "_force_user_anchored_reply" not in tool_state
        finally:
            ent_mod.build_inference_provider = original
            await entity.shutdown()

    @pytest.mark.asyncio
    async def test_gate_force_done_after_repeated_length_truncation(self):
        from ngram.config import HarnessConfig, entity_from_dict
        from ngram.entity import Entity, _MAX_LENGTH_STALL_FAILURES_BEFORE_FORCE_DONE
        from ngram.cognition.deliberate import AgentLoopState
        from ngram.inference.types import ChatCompletionResult
        from ngram.models import Input

        class _StubProvider:
            async def chat_completion(self, *a, **kw):
                return ChatCompletionResult(content="ok")

            async def embed(self, *a, **kw):
                return [0.01] * 8

            async def health(self):
                return {"ok": True}

            async def list_models(self):
                return ["stub"]

            async def close(self):
                pass

            async def ensure_models(self, *names):
                return True, []

        h = HarnessConfig()
        data = {
            "name": "AgencyTestLengthStall",
            "personality": {
                "core_traits": {k: 0.5 for k in ["curiosity", "warmth", "assertiveness", "humor", "openness", "neuroticism", "conscientiousness"]},
                "behavioral_patterns": {},
                "voice": {},
                "backstory": "Test entity.",
            },
            "drives": {"curiosity_topics": [], "attachment_threshold": 5, "restlessness_decay": 3600, "initiative_cooldown": 1800},
            "cognition": {},
            "presence": {"platforms": [], "daemon": {}},
        }
        import ngram.entity as ent_mod

        original = ent_mod.build_inference_provider
        ent_mod.build_inference_provider = lambda _h: _StubProvider()
        try:
            entity = Entity(entity_from_dict(h, data))
            done, reason = await entity._loop_completion_gate(
                Input(text="hey", person_id="u", person_name="U", platform="telegram"),
                "partial visible reply",
                ChatCompletionResult(content="partial", finish_reason="length"),
                AgentLoopState(completion_failures=_MAX_LENGTH_STALL_FAILURES_BEFORE_FORCE_DONE),
                {},
            )
            assert done is True
            assert reason is None
        finally:
            ent_mod.build_inference_provider = original
            await entity.shutdown()

    @pytest.mark.asyncio
    async def test_gate_still_catches_empty_without_end_turn(self):
        from ngram.config import HarnessConfig, entity_from_dict
        from ngram.entity import Entity
        from ngram.cognition.deliberate import AgentLoopState
        from ngram.inference.types import ChatCompletionResult
        from ngram.models import Input

        class _StubProvider:
            async def chat_completion(self, *a, **kw):
                return ChatCompletionResult(content="ok")
            async def embed(self, *a, **kw):
                return [0.01] * 8
            async def health(self):
                return {"ok": True}
            async def list_models(self):
                return ["stub"]
            async def close(self):
                pass
            async def ensure_models(self, *names):
                return True, []

        h = HarnessConfig()
        data = {
            "name": "AgencyTest2",
            "personality": {
                "core_traits": {k: 0.5 for k in ["curiosity", "warmth", "assertiveness", "humor", "openness", "neuroticism", "conscientiousness"]},
                "behavioral_patterns": {},
                "voice": {},
                "backstory": "Test entity.",
            },
            "drives": {"curiosity_topics": [], "attachment_threshold": 5, "restlessness_decay": 3600, "initiative_cooldown": 1800},
            "cognition": {},
            "presence": {"platforms": [], "daemon": {}},
        }
        import ngram.entity as ent_mod
        original = ent_mod.build_inference_provider
        ent_mod.build_inference_provider = lambda _h: _StubProvider()
        try:
            entity = Entity(entity_from_dict(h, data))
            tool_state = {"tool_calls": 1}
            done, reason = await entity._loop_completion_gate(
                Input(text="hey", person_id="u", person_name="U"),
                "",
                ChatCompletionResult(content=""),
                AgentLoopState(tool_calls_seen=1),
                tool_state,
            )
            assert done is False
            assert reason is not None
        finally:
            ent_mod.build_inference_provider = original
            await entity.shutdown()

    @pytest.mark.asyncio
    async def test_gate_force_done_after_many_completion_failures_without_end_turn(self):
        """Avoid unbounded deliberate loops when the model never calls end_turn()."""
        from ngram.config import HarnessConfig, entity_from_dict
        from ngram.entity import Entity, _MAX_COMPLETION_FAILURES_BEFORE_FORCE_DONE
        from ngram.cognition.deliberate import AgentLoopState
        from ngram.inference.types import ChatCompletionResult
        from ngram.models import Input

        class _StubProvider:
            async def chat_completion(self, *a, **kw):
                return ChatCompletionResult(content="ok")

            async def embed(self, *a, **kw):
                return [0.01] * 8

            async def health(self):
                return {"ok": True}

            async def list_models(self):
                return ["stub"]

            async def close(self):
                pass

            async def ensure_models(self, *names):
                return True, []

        h = HarnessConfig()
        data = {
            "name": "AgencyTest3",
            "personality": {
                "core_traits": {k: 0.5 for k in ["curiosity", "warmth", "assertiveness", "humor", "openness", "neuroticism", "conscientiousness"]},
                "behavioral_patterns": {},
                "voice": {},
                "backstory": "Test entity.",
            },
            "drives": {"curiosity_topics": [], "attachment_threshold": 5, "restlessness_decay": 3600, "initiative_cooldown": 1800},
            "cognition": {},
            "presence": {"platforms": [], "daemon": {}},
        }
        import ngram.entity as ent_mod

        original = ent_mod.build_inference_provider
        ent_mod.build_inference_provider = lambda _h: _StubProvider()
        try:
            entity = Entity(entity_from_dict(h, data))
            tool_state = {"tool_calls": 1, "_sent_messages": ["hello"]}
            st = AgentLoopState(completion_failures=_MAX_COMPLETION_FAILURES_BEFORE_FORCE_DONE)
            done, reason = await entity._loop_completion_gate(
                Input(text="hey", person_id="u", person_name="U"),
                "visible reply",
                ChatCompletionResult(content="x", finish_reason="stop"),
                st,
                tool_state,
            )
            assert done is True
            assert reason is None
        finally:
            ent_mod.build_inference_provider = original
            await entity.shutdown()
