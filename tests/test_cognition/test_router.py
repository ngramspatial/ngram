import pytest

from ngram.config import HarnessConfig, entity_from_dict
from ngram.cognition.router import CognitionRouter, ContextPackage
from ngram.inference.types import ChatCompletionResult
from ngram.models import EmotionalState, Input
from ngram.utils.ollama_client import OllamaClient


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
        "cognition": {},
        "presence": {"platforms": [{"type": "cli"}], "daemon": {}},
    }
    return entity_from_dict(h, data)


def test_heuristic_reflex_greeting(entity_config):
    r = CognitionRouter(entity_config, OllamaClient())
    inp = Input(text="hi", person_id="u", person_name="U")
    emo = EmotionalState()
    assert r.heuristic_route(inp, emo) == "reflex"


def test_heuristic_deliberate_long(entity_config):
    r = CognitionRouter(entity_config, OllamaClient())
    inp = Input(text="x" * 300, person_id="u", person_name="U")
    emo = EmotionalState()
    assert r.heuristic_route(inp, emo) == "deliberate"


def test_heuristic_identity_questions_not_reflex_short_q(entity_config):
    r = CognitionRouter(entity_config, OllamaClient())
    emo = EmotionalState()
    for text in (
        "what is your name?",
        "who are you?",
        "what should I call you?",
    ):
        inp = Input(text=text, person_id="u", person_name="U")
        assert r.heuristic_route(inp, emo) == "deliberate"


def test_heuristic_capability_questions_use_deliberate(entity_config):
    r = CognitionRouter(entity_config, OllamaClient())
    emo = EmotionalState()
    inp = Input(text="what can you do?", person_id="u", person_name="U")
    assert r.heuristic_route(inp, emo) == "deliberate"


def test_heuristic_readme_line_file_questions_use_deliberate(entity_config):
    r = CognitionRouter(entity_config, OllamaClient())
    emo = EmotionalState()
    for text in (
        "whats the readme about?",
        "what's on line 35?",
        "read config.yaml for me",
        "whats in your workspace right now?",
        "what files are in the repo?",
        "show me the current directory contents",
    ):
        inp = Input(text=text, person_id="u", person_name="U")
        assert r.heuristic_route(inp, emo) == "deliberate"


@pytest.mark.asyncio
async def test_route_short_casual_skips_classifier_escalation(entity_config):
    r = CognitionRouter(entity_config, OllamaClient())
    emo = EmotionalState()
    ctx = ContextPackage(emotional_state=emo)
    inp = Input(text="nah nuthin much here either, wagwan", person_id="u", person_name="U")
    kind, _ = await r.route(inp, emo, ctx)
    assert kind == "reflex"


class _JudgeClient:
    def __init__(self, label: str):
        self.label = label

    async def chat_completion(
        self,
        model,
        messages,
        *,
        temperature=0.2,
        max_tokens=8,
        think=False,
        num_ctx=None,
        tools=None,
        stream=False,
    ):
        _ = (model, messages, temperature, max_tokens, think, num_ctx, tools, stream)
        return ChatCompletionResult(content=self.label)


@pytest.mark.asyncio
async def test_route_uses_grounding_judgment_for_non_keyword_tool_task(entity_config):
    r = CognitionRouter(entity_config, _JudgeClient("GROUNDED"))
    emo = EmotionalState()
    ctx = ContextPackage(emotional_state=emo)
    inp = Input(
        text="can you verify whether you've got anything saved about yourself on disk?",
        person_id="u",
        person_name="U",
    )
    kind, _ = await r.route(inp, emo, ctx)
    assert kind == "deliberate"


@pytest.mark.asyncio
async def test_route_uses_exactness_judgment_for_precise_value_request(entity_config):
    r = CognitionRouter(entity_config, _JudgeClient("EXACT"))
    emo = EmotionalState()
    ctx = ContextPackage(emotional_state=emo)
    inp = Input(
        text="tell me the exact output, not the gist",
        person_id="u",
        person_name="U",
    )
    kind, _ = await r.route(inp, emo, ctx)
    assert kind == "deliberate"


@pytest.mark.asyncio
async def test_route_tiny_what_stays_reflex(entity_config):
    r = CognitionRouter(entity_config, OllamaClient())
    emo = EmotionalState()
    ctx = ContextPackage(emotional_state=emo)
    inp = Input(text="WHAT?", person_id="u", person_name="U")
    kind, _ = await r.route(inp, emo, ctx)
    assert kind == "reflex"
