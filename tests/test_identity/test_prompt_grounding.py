"""Regression tests for identity and autonomous-wake grounding contracts."""

from __future__ import annotations

from types import SimpleNamespace

from ngram.config import HarnessConfig, entity_from_dict
from ngram.identity.personality import PersonalityEngine
from ngram.identity.soma import WakeVoice
from ngram.models import EmotionalState, EmotionCategory


def _entity(*, fast: bool) -> object:
    return entity_from_dict(
        HarnessConfig(),
        {
            "name": "TestRook",
            "values": ["truth over a cleaner story"],
            "boundaries": [
                "never fabricate a tool result, memory, permission, or completed action"
            ],
            "personality": {
                "core_traits": {"curiosity": 0.8},
                "behavioral_patterns": {"correction": "admit_the_exact_error"},
                "voice": {"sentence_style": "terse", "quirks": ["defaults to lowercase"]},
                "backstory": "The Northstar outage taught TestRook to distrust clean reports.",
            },
            "cognition": {"fast_deliberate_mode": fast},
        },
    )


async def test_fast_identity_prompt_includes_yaml_commitments_and_labels_backstory() -> None:
    prompt = await PersonalityEngine(_entity(fast=True)).compile_system_prompt(
        EmotionalState(primary=EmotionCategory.CURIOUS, intensity=0.6),
        {},
        client=None,
    )

    assert "truth over a cleaner story" in prompt
    assert "never fabricate a tool result, memory, permission, or completed action" in prompt
    assert "[Grounding — memory, tools, and shared history]" in prompt
    assert "Identity framing (character origins and perspective" in prompt
    assert "not evidence of any shared conversation, file, or incident" in prompt


class _CaptureClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.messages: list[dict[str, str]] = []

    async def chat_completion(self, model, messages, **kwargs):
        self.messages = messages
        return SimpleNamespace(content=self.response)


async def test_generated_identity_payload_preserves_grounding_contract() -> None:
    client = _CaptureClient(
        "i know who i am, and i keep the distinction between what shaped me and what actually "
        "happened in this conversation. i stay terse, curious, and honest about missing evidence."
    )
    prompt = await PersonalityEngine(_entity(fast=False)).compile_system_prompt(
        EmotionalState(primary=EmotionCategory.CURIOUS, intensity=0.4),
        {},
        client=client,
    )

    synthesis_request = client.messages[1]["content"]
    assert '"values": [' in synthesis_request
    assert '"boundaries": [' in synthesis_request
    assert '"identity_framing_backstory"' in synthesis_request
    assert '"backstory_usage"' in synthesis_request
    assert '"backstory_excerpt"' not in synthesis_request
    assert "never invent memories or tool results" in synthesis_request
    assert "truth over a cleaner story" in prompt
    assert "Never fabricate or embellish a memory" in prompt


async def test_wake_voice_allows_only_supplied_retrospective_evidence() -> None:
    client = _CaptureClient("maybe i'm imagining a brittle rollback path")
    result = await WakeVoice().compose(
        client,
        "test-model",
        entity_name="TestRook",
        bars_summary="curiosity: 80%",
        affects_summary="restless",
        noise_fragments=["that old incident report keeps tugging"],
        impulses_summary="look outward",
        conflicts_summary="none",
        journal_tail="",
        conversation_tail="",
        relationship_blurb="trusted operator",
        last_mood="restless",
        minutes_since_wake=60,
        minutes_since_conversation=180,
        voice_variant=3,
    )

    system = client.messages[0]["content"]
    assert "RECENT JOURNAL and LAST CONVERSATION" in system
    assert "the only factual retrospective evidence" in system
    assert "NOISE" in system and "not proof" in system
    assert "Never invent or imply a shared conversation" in system
    assert "report, incident, deployment, migration, outage" in system
    assert "make no retrospective factual claims" in system
    assert "mark it as imagination or possibility rather than memory" in system
    assert result == "maybe i'm imagining a brittle rollback path"
