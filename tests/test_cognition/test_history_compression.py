import pytest

from ngram.config import HarnessConfig, entity_from_dict
from ngram.cognition.history_compression import (
    format_history_messages_for_summary,
    merge_rolling_summary,
)
from ngram.entity import Entity
from ngram.inference.types import ChatCompletionResult


@pytest.fixture
def entity_config():
    h = HarnessConfig()
    data = {
        "name": "Test",
        "personality": {
            "core_traits": {k: 0.5 for k in ["curiosity", "warmth", "assertiveness", "humor", "openness", "neuroticism", "conscientiousness"]},
            "behavioral_patterns": {},
            "voice": {},
            "backstory": "Test being.",
        },
        "drives": {"curiosity_topics": [], "attachment_threshold": 5, "restlessness_decay": 3600, "initiative_cooldown": 1800},
        # Harness enforces minimum 8; use 8 and overflow with 10 messages to force trim.
        "cognition": {"rolling_history_max_messages": 8},
        "presence": {"platforms": [{"type": "cli"}], "daemon": {}},
    }
    return entity_from_dict(h, data)


class _MergeStub:
    def __init__(self, out: str = "merged rolling summary line") -> None:
        self.out = out
        self.calls = 0

    async def chat_completion(self, *args, **kwargs):
        self.calls += 1
        return ChatCompletionResult(content=self.out)


def test_format_history_tool_line():
    s = format_history_messages_for_summary(
        [{"role": "tool", "name": "fetch_url", "content": "hello world"}],
        per_msg_cap=100,
    )
    assert "tool_result(fetch_url)" in s
    assert "hello world" in s


@pytest.mark.asyncio
async def test_merge_rolling_summary_returns_capped(entity_config):
    stub = _MergeStub("x" * 6000)
    out = await merge_rolling_summary(
        stub,
        "m",
        entity_name=entity_config.name,
        prior_summary="",
        dropped_messages=[{"role": "user", "content": "hi"}],
        per_msg_cap=500,
        max_merge_input_chars=5000,
        merge_max_tokens=400,
        summary_max_chars=100,
    )
    assert len(out) <= 100
    assert out.endswith("… [trimmed]")


@pytest.mark.asyncio
async def test_trim_history_invokes_merge_and_prefixes_messages(entity_config):
    ent = Entity(entity_config)
    stub = _MergeStub("we were discussing planets")
    ent.client = stub
    ent._history = []
    for i in range(5):
        ent._history.append({"role": "user", "content": f"u{i}"})
        ent._history.append({"role": "assistant", "content": f"a{i}"})
    await ent._trim_history_with_compression()
    assert len(ent._history) == 8
    assert stub.calls == 1
    assert "planets" in ent._history_rolling_summary
    um = {"role": "user", "content": "latest"}
    msgs = ent._messages_for_model(um)
    assert msgs[0]["role"] == "user"
    assert "Earlier conversation summary" in str(msgs[0]["content"])
    assert "planets" in str(msgs[0]["content"])
    assert msgs[-1] == um


def test_messages_for_model_drops_duplicate_trailing_user(entity_config):
    ent = Entity(entity_config)
    ent._history_rolling_summary = ""
    ent.config.cognition.history_compression.enabled = False
    ent._history = [{"role": "user", "content": "same"}]
    um = {"role": "user", "content": "same"}
    msgs = ent._messages_for_model(um)
    assert len(msgs) == 1
    assert msgs[0]["content"] == "same"


def test_messages_for_model_drops_duplicate_when_current_has_turn_context_preamble(entity_config):
    """History stores plain user text; the live turn wraps it in [Turn context]…--- preamble."""
    ent = Entity(entity_config)
    ent._history_rolling_summary = ""
    ent.config.cognition.history_compression.enabled = False
    ent._history = [{"role": "user", "content": "Did you glitch?"}]
    um = {
        "role": "user",
        "content": "[Turn context]\n[Body]\n(soma snapshot)\n\n---\n\nDid you glitch?",
    }
    msgs = ent._messages_for_model(um)
    assert len(msgs) == 1
    assert msgs[0] == um


@pytest.mark.asyncio
async def test_trim_skips_merge_when_disabled(entity_config):
    entity_config.cognition.history_compression.enabled = False
    ent = Entity(entity_config)
    stub = _MergeStub()
    ent.client = stub
    ent._history = [{"role": "user", "content": f"x{i}"} for i in range(10)]
    await ent._trim_history_with_compression()
    assert len(ent._history) == 8
    assert stub.calls == 0
    assert ent._messages_for_model({"role": "user", "content": "u"})[-2]["content"] == "x9"


@pytest.mark.asyncio
@pytest.mark.parametrize("compression_enabled", [True, False])
async def test_history_limit_preserves_parallel_tool_group(entity_config, compression_enabled):
    from ngram.inference.openai_transport import OpenAIResponsesTransport

    entity_config.cognition.history_compression.enabled = compression_enabled
    ent = Entity(entity_config)
    ent.client = _MergeStub()
    group = [
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": call_id, "function": {"name": "inspect", "arguments": "{}"}}
            for call_id in ("first", "second")
        ]},
        {"role": "tool", "tool_call_id": "first", "content": "First result"},
        {"role": "tool", "tool_call_id": "second", "content": "Second result"},
    ]
    ent._history = [{"role": "user", "content": "Earlier request"},
                    {"role": "assistant", "content": "Earlier answer"}] + group + [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"Recent {i}"}
        for i in range(7)
    ]
    # The former [-8:] trim began at the second result, dropping its call.
    await ent._trim_history_with_compression()
    assert ent._history[:3] == group
    assert len(ent._history) == 10
    assert ent.client.calls == int(compression_enabled)
    items = OpenAIResponsesTransport()._responses_input(
        ent._messages_for_model({"role": "user", "content": "Continue"})
    )
    assert [i["call_id"] for i in items if i.get("type") == "function_call"] == ["first", "second"]
    assert [i["output"] for i in items if i.get("type") == "function_call_output"] == ["First result", "Second result"]
