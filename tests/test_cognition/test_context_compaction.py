"""Tests for the proactive context compaction system."""

import json

import pytest

from ngram.config import HarnessConfig, HistoryCompressionSettings, entity_from_dict
from ngram.cognition.history_compression import (
    COMPACTION_SUMMARY_PREFIX,
    estimate_message_tokens,
    estimate_context_tokens,
    prune_old_tool_results,
    find_compaction_boundaries,
    serialize_for_summary,
    generate_structured_summary,
    sanitize_tool_pairs,
    extract_knowledge_for_flush,
)
from ngram.entity import Entity
from ngram.inference.types import ChatCompletionResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _msg(role: str, content: str, **kw) -> dict:
    m = {"role": role, "content": content}
    m.update(kw)
    return m


class _Stub:
    """Inference stub that returns a fixed string."""

    def __init__(self, out: str = "stub output") -> None:
        self.out = out
        self.calls: list[dict] = []

    async def chat_completion(self, model, messages, **kw):
        self.calls.append({"model": model, "messages": messages, **kw})
        return ChatCompletionResult(content=self.out)

    async def embed(self, model, text):
        return [0.0] * 768

    async def health(self):
        return {"ok": True}

    async def list_models(self):
        return ["stub"]

    async def close(self):
        pass


@pytest.fixture
def entity_config():
    h = HarnessConfig()
    data = {
        "name": "TestCompaction",
        "personality": {
            "core_traits": {k: 0.5 for k in [
                "curiosity", "warmth", "assertiveness", "humor",
                "openness", "neuroticism", "conscientiousness",
            ]},
            "behavioral_patterns": {},
            "voice": {},
            "backstory": "Test entity.",
        },
        "drives": {"curiosity_topics": [], "attachment_threshold": 5},
        "cognition": {
            "rolling_history_max_messages": 40,
            "max_context_tokens": 4096,
        },
        "presence": {"platforms": [{"type": "cli"}], "daemon": {}},
    }
    return entity_from_dict(h, data)


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------

class TestTokenEstimation:
    def test_basic_message(self):
        tok = estimate_message_tokens({"role": "user", "content": "Hello world"})
        assert tok > 0
        assert tok == len("Hello world") // 4 + 10

    def test_empty_content(self):
        tok = estimate_message_tokens({"role": "user", "content": ""})
        assert tok >= 10

    def test_tool_call_arguments_counted(self):
        msg = {
            "role": "assistant",
            "content": "let me check",
            "tool_calls": [
                {"id": "call_1", "function": {"name": "run_command", "arguments": "x" * 400}},
            ],
        }
        tok = estimate_message_tokens(msg)
        base = estimate_message_tokens({"role": "assistant", "content": "let me check"})
        assert tok > base

    def test_context_tokens_sums_parts(self):
        sys = "You are helpful." * 10
        msgs = [
            _msg("user", "hello " * 50),
            _msg("assistant", "world " * 50),
        ]
        total = estimate_context_tokens(sys, msgs)
        assert total > 0
        sys_tok = len(sys) // 4
        assert total >= sys_tok

    def test_context_tokens_with_tools_block(self):
        base = estimate_context_tokens("sys", [_msg("user", "hi")])
        with_tools = estimate_context_tokens("sys", [_msg("user", "hi")], tools_block="x" * 1000)
        assert with_tools > base


# ---------------------------------------------------------------------------
# Tool result pruning
# ---------------------------------------------------------------------------

class TestToolPruning:
    def test_prunes_large_results_outside_tail(self):
        msgs = [
            _msg("user", "do something"),
            _msg("assistant", "ok"),
            _msg("tool", "x" * 500, name="run_command", tool_call_id="c1"),
            _msg("user", "now what"),
            _msg("assistant", "done"),
        ]
        pruned, count = prune_old_tool_results(msgs, protect_tail_count=2)
        assert count == 1
        assert pruned[2]["content"] == "[Old tool output cleared to save context space]"

    def test_preserves_small_results(self):
        msgs = [
            _msg("tool", "ok", name="t1", tool_call_id="c1"),
            _msg("user", "hi"),
        ]
        pruned, count = prune_old_tool_results(msgs, protect_tail_count=1)
        assert count == 0
        assert pruned[0]["content"] == "ok"

    def test_preserves_tail_results(self):
        msgs = [
            _msg("tool", "x" * 500, name="t1", tool_call_id="c1"),
            _msg("user", "hi"),
            _msg("tool", "y" * 500, name="t2", tool_call_id="c2"),
        ]
        pruned, count = prune_old_tool_results(msgs, protect_tail_count=2)
        assert count == 1
        assert pruned[0]["content"] != "x" * 500
        assert pruned[2]["content"] == "y" * 500

    def test_empty_messages(self):
        pruned, count = prune_old_tool_results([], protect_tail_count=5)
        assert pruned == []
        assert count == 0


# ---------------------------------------------------------------------------
# Boundary detection
# ---------------------------------------------------------------------------

class TestBoundaryDetection:
    def test_basic_boundaries(self):
        msgs = [_msg("user", f"msg{i}") for i in range(20)]
        start, end = find_compaction_boundaries(msgs, head_n=2, tail_token_budget=500, min_tail_n=4)
        assert start == 2
        assert end <= len(msgs) - 4
        assert end > start

    def test_respects_head_protection(self):
        msgs = [_msg("user", f"m{i}") for i in range(10)]
        start, end = find_compaction_boundaries(msgs, head_n=3, tail_token_budget=200, min_tail_n=3)
        assert start >= 3

    def test_respects_min_tail(self):
        msgs = [_msg("user", f"m{i}") for i in range(10)]
        start, end = find_compaction_boundaries(msgs, head_n=2, tail_token_budget=200, min_tail_n=5)
        assert len(msgs) - end >= 5 or end <= start + 1

    def test_too_few_messages_returns_adjacent(self):
        msgs = [_msg("user", "a"), _msg("assistant", "b"), _msg("user", "c")]
        start, end = find_compaction_boundaries(msgs, head_n=2, tail_token_budget=5000, min_tail_n=2)
        assert end - start <= 1

    def test_skips_tool_group_at_boundary(self):
        msgs = [
            _msg("user", "q1"),
            _msg("user", "q2"),
            _msg("assistant", "let me check", tool_calls=[{"id": "c1", "function": {"name": "t", "arguments": ""}}]),
            _msg("tool", "result", tool_call_id="c1"),
            _msg("user", "q3"),
            _msg("assistant", "done"),
        ]
        start, end = find_compaction_boundaries(msgs, head_n=1, tail_token_budget=500, min_tail_n=2)
        tail = msgs[end:]
        tool_results_in_tail = [m for m in tail if m.get("role") == "tool"]
        for tr in tool_results_in_tail:
            cid = tr.get("tool_call_id")
            assistant_calls = [
                m for m in tail if m.get("role") == "assistant"
                and any(
                    (tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", "")) == cid
                    for tc in (m.get("tool_calls") or [])
                )
            ]
            assert len(assistant_calls) > 0 or cid is None


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

class TestSerialization:
    def test_user_message(self):
        out = serialize_for_summary([_msg("user", "hello there")])
        assert "[USER]:" in out
        assert "hello there" in out

    def test_assistant_with_tool_calls(self):
        msg = _msg(
            "assistant", "checking...",
            tool_calls=[{"id": "c1", "function": {"name": "search_web", "arguments": '{"q":"test"}'}}],
        )
        out = serialize_for_summary([msg])
        assert "[ASSISTANT]:" in out
        assert "search_web" in out

    def test_tool_result(self):
        out = serialize_for_summary([
            _msg("tool", "found it", name="search_web", tool_call_id="c1"),
        ])
        assert "tool_result(search_web)" in out

    def test_truncation(self):
        out = serialize_for_summary([_msg("user", "x" * 10000)], per_msg_cap=100)
        assert len(out) < 10000


# ---------------------------------------------------------------------------
# Structured summary generation
# ---------------------------------------------------------------------------

class TestStructuredSummary:
    @pytest.mark.asyncio
    async def test_first_compaction(self):
        stub = _Stub("## Goal\nTest goal\n## Progress\n### Done\nNothing yet")
        result = await generate_structured_summary(
            stub, "model",
            turns=[_msg("user", "build me a thing"), _msg("assistant", "on it")],
            previous_summary="",
            entity_name="TestBot",
            context_length=32768,
        )
        assert result is not None
        assert COMPACTION_SUMMARY_PREFIX in result
        assert stub.calls

    @pytest.mark.asyncio
    async def test_iterative_update(self):
        stub = _Stub("## Goal\nUpdated goal — building a FastAPI project with tests and auth layer")
        result = await generate_structured_summary(
            stub, "model",
            turns=[_msg("user", "next step"), _msg("assistant", "done")],
            previous_summary="## Goal\nOriginal goal",
            entity_name="TestBot",
            context_length=32768,
        )
        assert result is not None
        prompt_text = stub.calls[0]["messages"][0]["content"]
        assert "PREVIOUS SUMMARY" in prompt_text
        assert "Original goal" in prompt_text

    @pytest.mark.asyncio
    async def test_returns_none_on_failure(self):
        class _FailStub:
            async def chat_completion(self, *a, **kw):
                raise RuntimeError("boom")
        result = await generate_structured_summary(
            _FailStub(), "model",
            turns=[_msg("user", "hi")],
            previous_summary="",
            entity_name="T",
            context_length=32768,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_empty(self):
        stub = _Stub("")
        result = await generate_structured_summary(
            stub, "model",
            turns=[_msg("user", "hi")],
            previous_summary="",
            entity_name="T",
            context_length=32768,
        )
        assert result is None


# ---------------------------------------------------------------------------
# Tool pair sanitization
# ---------------------------------------------------------------------------

class TestSanitizeToolPairs:
    def test_removes_orphaned_results(self):
        msgs = [
            _msg("user", "hi"),
            _msg("tool", "result", tool_call_id="orphan_id"),
            _msg("assistant", "ok"),
        ]
        cleaned = sanitize_tool_pairs(msgs)
        assert not any(m.get("tool_call_id") == "orphan_id" for m in cleaned)

    def test_adds_stub_for_missing_results(self):
        msgs = [
            _msg("assistant", "calling", tool_calls=[
                {"id": "c1", "function": {"name": "test", "arguments": ""}},
            ]),
            _msg("user", "ok"),
        ]
        cleaned = sanitize_tool_pairs(msgs)
        stubs = [m for m in cleaned if m.get("tool_call_id") == "c1"]
        assert len(stubs) == 1
        assert "earlier conversation" in stubs[0]["content"].lower()

    def test_leaves_matched_pairs_intact(self):
        msgs = [
            _msg("assistant", "let me check", tool_calls=[
                {"id": "c1", "function": {"name": "t", "arguments": ""}},
            ]),
            _msg("tool", "result", tool_call_id="c1"),
            _msg("user", "thanks"),
        ]
        cleaned = sanitize_tool_pairs(msgs)
        assert len(cleaned) == len(msgs)


# ---------------------------------------------------------------------------
# Knowledge flush
# ---------------------------------------------------------------------------

class TestKnowledgeFlush:
    @pytest.mark.asyncio
    async def test_extracts_facts(self):
        stub = _Stub(json.dumps([
            {"title": "user preferences", "body": "Prefers dark mode."},
            {"title": "tech stack", "body": "Uses FastAPI and PostgreSQL."},
        ]))
        facts = await extract_knowledge_for_flush(
            stub, "model",
            turns=[_msg("user", "I prefer dark mode and I use FastAPI. " * 10)],
            entity_name="T",
        )
        assert len(facts) == 2
        assert facts[0][0] == "user preferences"
        assert facts[1][1] == "Uses FastAPI and PostgreSQL."

    @pytest.mark.asyncio
    async def test_returns_empty_on_failure(self):
        class _FailStub:
            async def chat_completion(self, *a, **kw):
                raise RuntimeError("down")
        facts = await extract_knowledge_for_flush(
            _FailStub(), "model",
            turns=[_msg("user", "hello")],
            entity_name="T",
        )
        assert facts == []

    @pytest.mark.asyncio
    async def test_returns_empty_for_empty_array(self):
        stub = _Stub("[]")
        facts = await extract_knowledge_for_flush(
            stub, "model",
            turns=[_msg("user", "nothing special")],
            entity_name="T",
        )
        assert facts == []

    @pytest.mark.asyncio
    async def test_handles_markdown_fenced_json(self):
        stub = _Stub('```json\n[{"title": "pref", "body": "val"}]\n```')
        facts = await extract_knowledge_for_flush(
            stub, "model",
            turns=[_msg("user", "some detailed info about preferences and settings " * 5)],
            entity_name="T",
        )
        assert len(facts) == 1

    @pytest.mark.asyncio
    async def test_short_content_skipped(self):
        facts = await extract_knowledge_for_flush(
            _Stub(), "model",
            turns=[_msg("user", "hi")],
            entity_name="T",
        )
        assert facts == []


# ---------------------------------------------------------------------------
# Config wiring
# ---------------------------------------------------------------------------

class TestConfigWiring:
    def test_defaults(self):
        hc = HistoryCompressionSettings()
        assert hc.compaction_threshold_ratio == 0.6
        assert hc.compaction_target_ratio == 0.08
        assert hc.compaction_protect_last_n == 12
        assert hc.compaction_protect_first_n == 2
        assert hc.compaction_max_passes == 3
        assert hc.compaction_flush_to_knowledge is True

    def test_from_yaml(self):
        h = HarnessConfig()
        data = {
            "name": "T",
            "personality": {
                "core_traits": {k: 0.5 for k in [
                    "curiosity", "warmth", "assertiveness", "humor",
                    "openness", "neuroticism", "conscientiousness",
                ]},
            },
            "drives": {},
            "cognition": {
                "history_compression": {
                    "compaction_threshold_ratio": 0.65,
                    "compaction_protect_last_n": 20,
                    "compaction_flush_to_knowledge": False,
                },
            },
            "presence": {"platforms": [{"type": "cli"}]},
        }
        ec = entity_from_dict(h, data)
        hc = ec.cognition.history_compression
        assert hc.compaction_threshold_ratio == 0.65
        assert hc.compaction_protect_last_n == 20
        assert hc.compaction_flush_to_knowledge is False
        assert hc.compaction_target_ratio == 0.08

    def test_clamping(self):
        h = HarnessConfig()
        data = {
            "name": "T",
            "personality": {
                "core_traits": {k: 0.5 for k in [
                    "curiosity", "warmth", "assertiveness", "humor",
                    "openness", "neuroticism", "conscientiousness",
                ]},
            },
            "drives": {},
            "cognition": {
                "history_compression": {
                    "compaction_threshold_ratio": 2.0,
                    "compaction_protect_last_n": 1,
                    "compaction_max_passes": 99,
                },
            },
            "presence": {"platforms": [{"type": "cli"}]},
        }
        ec = entity_from_dict(h, data)
        hc = ec.cognition.history_compression
        assert hc.compaction_threshold_ratio == 0.95
        assert hc.compaction_protect_last_n == 4
        assert hc.compaction_max_passes == 5


# ---------------------------------------------------------------------------
# Entity integration — _ensure_context_budget
# ---------------------------------------------------------------------------

class TestEnsureContextBudget:
    @pytest.mark.parametrize("method", ["preflight", "history_count"])
    @pytest.mark.asyncio
    async def test_failed_summary_keeps_history_and_emits_visible_failure(self, entity_config, method):
        from ngram.cognition.context_status import context_reporter
        from ngram.entity import TurnContext
        from ngram.models import Input

        entity_config.cognition.history_compression.compaction_flush_to_knowledge = False
        entity_config.cognition.history_compression.compaction_protect_last_n = 4
        entity_config.cognition.rolling_history_max_messages = 8
        ent = Entity(entity_config)
        ent.client = _Stub("")
        ent._history = [_msg("user", f"important fact {i} " * 100) for i in range(40)]
        original = list(ent._history)
        events = []

        async def report(status):
            events.append(status)

        token = context_reporter.set(report)
        try:
            if method == "preflight":
                await ent._ensure_context_budget(TurnContext(
                    inp=Input(text="continue", person_id="u", person_name="U"),
                    sys_prompt="system", user_msg=_msg("user", "continue"),
                ))
            else:
                await ent._trim_history_with_compression()
        finally:
            context_reporter.reset(token)
        assert ent._history == original
        assert not ent._history_rolling_summary
        assert [event["phase"] for event in events] == ["compacting", "failed"]
        assert len(ent.client.calls) == 1

    @pytest.mark.asyncio
    async def test_no_compaction_under_threshold(self, entity_config):
        entity_config.cognition.max_context_tokens = 32768
        ent = Entity(entity_config)
        stub = _Stub()
        ent.client = stub
        ent._history = [
            _msg("user", "hello"),
            _msg("assistant", "hi there"),
        ]
        from ngram.entity import TurnContext
        from ngram.models import Input
        tc = TurnContext(
            inp=Input(text="test", person_id="u1", person_name="Tester", platform="cli"),
            sys_prompt="You are helpful.",
            user_msg={"role": "user", "content": "test"},
        )
        await ent._ensure_context_budget(tc)
        assert len(ent._history) == 2
        assert stub.calls == []

    @pytest.mark.asyncio
    async def test_compacts_when_over_threshold(self, entity_config):
        entity_config.cognition.max_context_tokens = 512
        entity_config.cognition.history_compression.compaction_threshold_ratio = 0.50
        entity_config.cognition.history_compression.compaction_protect_first_n = 1
        entity_config.cognition.history_compression.compaction_protect_last_n = 2
        entity_config.cognition.history_compression.compaction_flush_to_knowledge = False

        ent = Entity(entity_config)
        stub = _Stub("## Goal\nTest task — building a FastAPI project with comprehensive test coverage and deployment config")
        ent.client = stub
        ent._history = [_msg("user", f"message number {i} " * 20) for i in range(20)]

        from ngram.entity import TurnContext
        from ngram.models import Input
        tc = TurnContext(
            inp=Input(text="latest", person_id="u1", person_name="Tester", platform="cli"),
            sys_prompt="System prompt " * 10,
            user_msg={"role": "user", "content": "latest"},
        )
        await ent._ensure_context_budget(tc)
        assert len(ent._history) < 20
        assert ent._history_rolling_summary != ""
        assert COMPACTION_SUMMARY_PREFIX in ent._history_rolling_summary

    @pytest.mark.asyncio
    async def test_disabled_skips_compaction(self, entity_config):
        entity_config.cognition.history_compression.enabled = False
        entity_config.cognition.max_context_tokens = 100
        ent = Entity(entity_config)
        stub = _Stub()
        ent.client = stub
        ent._history = [_msg("user", "x" * 1000) for _ in range(10)]

        from ngram.entity import TurnContext
        from ngram.models import Input
        tc = TurnContext(
            inp=Input(text="test", person_id="u1", person_name="Tester", platform="cli"),
            sys_prompt="sys",
            user_msg={"role": "user", "content": "test"},
        )
        await ent._ensure_context_budget(tc)
        assert len(ent._history) == 10
        assert stub.calls == []

    @pytest.mark.asyncio
    async def test_force_compacts_even_under_threshold(self, entity_config):
        entity_config.cognition.max_context_tokens = 32768
        entity_config.cognition.history_compression.compaction_protect_first_n = 1
        entity_config.cognition.history_compression.compaction_protect_last_n = 2
        entity_config.cognition.history_compression.compaction_flush_to_knowledge = False

        ent = Entity(entity_config)
        stub = _Stub("## Goal\nManual compaction handoff summary")
        ent.client = stub
        ent._history = [_msg("user", f"message number {i}") for i in range(20)]

        from ngram.entity import TurnContext
        from ngram.models import Input
        tc = TurnContext(
            inp=Input(text="manual compact", person_id="u1", person_name="Tester", platform="cli"),
            sys_prompt="sys",
            user_msg={"role": "user", "content": "manual compact"},
        )
        await ent._ensure_context_budget(tc, force=True, max_passes_override=1)
        assert len(ent._history) < 20
        assert stub.calls

    @pytest.mark.asyncio
    async def test_compact_context_now_reports_status(self, entity_config):
        entity_config.cognition.max_context_tokens = 32768
        entity_config.cognition.history_compression.compaction_protect_first_n = 1
        entity_config.cognition.history_compression.compaction_protect_last_n = 2
        entity_config.cognition.history_compression.compaction_flush_to_knowledge = False

        ent = Entity(entity_config)
        stub = _Stub("## Goal\nManual compaction status summary")
        ent.client = stub
        ent._history = [_msg("user", f"message number {i}") for i in range(16)]

        result = await ent.compact_context_now(aggressive=False, passes=1)
        assert result["ok"] is True
        assert result["messages_after"] < result["messages_before"]
        assert result["summary_chars_after"] >= result["summary_chars_before"]


def test_astra_context_budget_follows_provider_without_resizing_local_context(entity_config):
    entity_config.cognition.max_context_tokens = 32768
    entity_config.harness.inference.pass_num_ctx = True
    entity_config.harness.inference.provider = "remote_gateway"
    entity_config.cognition.deliberate_model = "gemma4:26b"
    assert entity_config.effective_context_tokens() == 32768
    assert entity_config.effective_ollama_num_ctx() == 32768
    entity_config.harness.inference.provider = "openai"
    entity_config.cognition.deliberate_model = "gpt-6-astra"
    entity_config.harness.inference.pass_num_ctx = False
    assert entity_config.effective_context_tokens() == 256000
    assert entity_config.effective_ollama_num_ctx() is None
    assert entity_config.cognition.max_context_tokens == 32768
    entity_config.cognition.deliberate_model = "some-unknown-api-model"
    assert entity_config.effective_context_tokens() == 32768
    entity_config.harness.inference.provider = "remote_gateway"
    entity_config.cognition.deliberate_model = "gemma4:26b"
    entity_config.harness.inference.pass_num_ctx = True
    assert entity_config.effective_context_tokens() == 32768
    assert entity_config.effective_ollama_num_ctx() == 32768
