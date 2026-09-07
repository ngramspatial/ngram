import pytest

from ngram.config import HarnessConfig, entity_from_dict
from ngram.cognition.deliberate import AgentLoopState
from ngram.entity import Entity, format_user_visible_failure
from ngram.inference.types import ChatCompletionResult
from ngram.models import Input


def test_format_user_visible_failure_for_tool_error() -> None:
    msg = format_user_visible_failure(
        tool_state={
            "last_tool_error": {
                "tool": "search_web",
                "error": "no search results (network, block, or missing ddgs). pip install ddgs",
            }
        }
    )
    low = msg.lower()
    assert "search web" in low
    assert "came back empty" in low


def test_format_user_visible_failure_for_inference_disconnect() -> None:
    msg = format_user_visible_failure("connection refused while reaching ollama backend")
    low = msg.lower()
    assert "inference backend" in low
    assert "model server" not in low


def test_format_user_visible_failure_for_railway_lock() -> None:
    msg = format_user_visible_failure(
        tool_state={
            "last_tool_error": {
                "tool": "run_command",
                "error": "Tool disabled: execution is locked to Railway. This Python process is not running inside the Railway container.",
            }
        }
    )
    low = msg.lower()
    assert "run command" in low
    assert "railway" in low


class _JudgeProvider:
    def __init__(self, verdict: str):
        self.verdict = verdict

    async def chat_completion(
        self,
        model,
        messages,
        *,
        tools=None,
        temperature=0.0,
        max_tokens=40,
        think=False,
        stream=False,
        num_ctx=None,
    ):
        _ = (model, messages, tools, temperature, max_tokens, think, stream, num_ctx)
        return ChatCompletionResult(content=self.verdict)

    async def embed(self, model: str, text: str) -> list[float]:
        _ = (model, text)
        return [0.01] * 8

    async def health(self):
        return {"ok": True}

    async def list_models(self):
        return ["judge"]

    async def close(self):
        return None

    async def ensure_models(self, *names: str):
        _ = names
        return True, []


def _entity_config():
    h = HarnessConfig()
    data = {
        "name": "JudgeEntity",
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
            "backstory": "Judge entity.",
        },
        "drives": {
            "curiosity_topics": [],
            "attachment_threshold": 5,
            "restlessness_decay": 3600,
            "initiative_cooldown": 1800,
        },
        "cognition": {},
        "presence": {"platforms": [], "daemon": {}},
    }
    return entity_from_dict(h, data)


@pytest.mark.asyncio
async def test_grounded_completion_judge_can_force_continue(monkeypatch) -> None:
    monkeypatch.setattr(
        "ngram.entity.build_inference_provider",
        lambda _h: _JudgeProvider("CONTINUE: the candidate is vague and does not report the tool outputs"),
    )
    ent = Entity(_entity_config())
    try:
        done, reason = await ent._judge_grounded_completion(
            Input(text="use tools and tell me the exact path", person_id="u", person_name="U"),
            "done",
            ChatCompletionResult(content="done"),
            AgentLoopState(tool_rounds=1, tool_calls_seen=1, last_tool_failed=False),
            {
                "tool_output_previews": [
                    {"tool": "list_directory", "ok": True, "preview": '{"path": "/app", "entries": []}'}
                ]
            },
        )
        assert done is False
        assert reason is not None
        assert "same turn." in reason.lower()
    finally:
        await ent.shutdown()


@pytest.mark.asyncio
async def test_grounded_completion_judge_can_accept_grounded_answer(monkeypatch) -> None:
    monkeypatch.setattr(
        "ngram.entity.build_inference_provider",
        lambda _h: _JudgeProvider("DONE: the candidate answers directly from the tool outputs"),
    )
    ent = Entity(_entity_config())
    try:
        done, reason = await ent._judge_grounded_completion(
            Input(text="use tools and tell me the exact path", person_id="u", person_name="U"),
            "/app",
            ChatCompletionResult(content="/app"),
            AgentLoopState(tool_rounds=1, tool_calls_seen=1, last_tool_failed=False),
            {
                "tool_output_previews": [
                    {"tool": "list_directory", "ok": True, "preview": '{"path": "/app", "entries": []}'}
                ]
            },
        )
        assert done is True
        assert reason is None
    finally:
        await ent.shutdown()
