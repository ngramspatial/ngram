from unittest.mock import AsyncMock

import pytest

from ngram.config import HarnessConfig, entity_from_dict
from ngram.cognition import gemma
from ngram.cognition.deliberate import DeliberateCognition, fit_system_prompt_to_budget
from ngram.presence.tools.registry import TOOL_SYSTEM_PROMPT_PREFIX
from ngram.inference.types import ChatCompletionResult, ToolCallSpec
from ngram.models import Input


def _entity_config():
    h = HarnessConfig()
    data = {
        "name": "Test",
        "personality": {
            "core_traits": {
                k: 0.5
                for k in [
                    "curiosity",
                    "warmth",
                    "assertiveness",
                    "humor",
                    "openness",
                    "neuroticism",
                    "conscientiousness",
                ]
            },
            "behavioral_patterns": {},
            "voice": {},
            "backstory": "Test being.",
        },
        "drives": {
            "curiosity_topics": [],
            "attachment_threshold": 5,
            "restlessness_decay": 3600,
            "initiative_cooldown": 1800,
        },
        "cognition": {},
        "presence": {"platforms": [{"type": "cli"}], "daemon": {}},
    }
    return entity_from_dict(h, data)


def test_fit_system_prompt_keeps_tools_suffix_when_truncating():
    prefix = "A" * 500
    suffix = f"{TOOL_SYSTEM_PROMPT_PREFIX} — rest of tools block here."
    full = prefix + suffix
    cap = 400
    out = fit_system_prompt_to_budget(full, cap)
    assert suffix in out
    assert out.endswith("rest of tools block here.")


def test_build_messages_truncation_preserves_tool_appendix():
    ec = _entity_config()
    # _build_messages uses max(2000, cognition.system_prompt_char_limit)
    ec.cognition.system_prompt_char_limit = 2000
    d = DeliberateCognition(ec, client=object())  # type: ignore[arg-type]
    long_prefix = "P" * 3500
    sys_full = long_prefix + f"{TOOL_SYSTEM_PROMPT_PREFIX}\nYou may call: x."
    msgs = d._build_messages(sys_full, [{"role": "user", "content": "hi"}])
    body = msgs[0]["content"]
    assert TOOL_SYSTEM_PROMPT_PREFIX in body
    assert "You may call: x." in body
    assert len(body) <= 2000


def test_build_messages_preserves_user_multimodal_content():
    ec = _entity_config()
    d = DeliberateCognition(ec, client=object())  # type: ignore[arg-type]
    img_payload = [
        {"type": "text", "text": "what do you think of her?"},
        {
            "type": "image_url",
            "image_url": {
                "url": "data:image/jpeg;base64,AAA",
                "detail": "auto",
            },
        },
    ]
    msgs = d._build_messages(
        "sys",
        [
            {"role": "user", "content": img_payload},
            {
                "role": "assistant",
                "content": f"{gemma.CHANNEL_THOUGHT}\nsecret\n{gemma.CHANNEL_END}hi",
            },
        ],
    )
    assert isinstance(msgs[1]["content"], list)
    assert msgs[1]["content"][1]["type"] == "image_url"
    assert "secret" not in msgs[2]["content"]


@pytest.mark.asyncio
async def test_tool_round_intermediate_has_empty_display_text():
    """Gemma often writes <|tool_response|> etc. in content during tool calls — never surface that as chat."""
    ec = _entity_config()
    tc = ToolCallSpec(name="get_weather", arguments={}, id="1")
    first = ChatCompletionResult(
        content=f"scratch {gemma.TOOL_RESPONSE_START} junk",
        tool_calls=[tc],
    )
    second = ChatCompletionResult(content="Final answer.", tool_calls=[])
    client = AsyncMock()
    client.chat_completion = AsyncMock(side_effect=[first, second])
    d = DeliberateCognition(ec, client=client)
    inp = Input(text="hi", person_id="1", person_name="Test")
    events: list = []

    async def _exec(_spec):
        return '{"ok": true}'

    async for ev in d.iter_responses(
        inp,
        "system",
        [{"role": "user", "content": "hi"}],
        tools=[{"type": "function", "function": {"name": "get_weather"}}],
        tool_executor=_exec,
    ):
        events.append(ev)
    inter = [e for e in events if e.kind == "intermediate"]
    assert len(inter) == 1
    assert inter[0].display_text == ""
    finals = [e for e in events if e.kind == "final"]
    assert finals[-1].display_text == "Final answer."
