"""Images reach inference without inflating durable history or crossing tool boundaries."""

import json

import pytest

from ngram.inference.openai_transport import OpenAICompatibleTransport, OpenAIResponsesTransport
from ngram.inference.visual_results import VisualResult, expire_visuals, text_history, visual_result

PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a3ioAAAAASUVORK5CYII="


def receipt():
    return visual_result({"ok": True, "revision": 3}, [{"url": PNG, "label": "Front"}])


def messages():
    return [
        {"role": "user", "content": "Inspect the sword"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "view", "type": "function", "function": {"name": "ar_request_capture", "arguments": "{}"}},
            {"id": "state", "type": "function", "function": {"name": "ar_world", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "view", "content": receipt()},
        {"role": "tool", "tool_call_id": "state", "content": '{"revision":3}'},
        {"role": "user", "content": "Continue"},
    ]


def test_responses_gets_native_image_tool_output():
    transport = OpenAIResponsesTransport(base_url="https://api.example")
    payload = transport._build_responses_payload("model", messages())
    outputs = [item for item in payload["input"] if item.get("type") == "function_call_output"]
    assert outputs[0]["call_id"] == "view"
    assert outputs[0]["output"][-1] == {"type": "input_image", "image_url": PNG, "detail": "high"}
    assert isinstance(outputs[1]["output"], str)


def test_chat_images_follow_all_tool_receipts_before_continuation():
    transport = OpenAICompatibleTransport(base_url="https://api.example", gemma_shaping=False)
    payload = transport._build_chat_completions_payload("model", messages())
    turns = payload["messages"]
    assert [turn["role"] for turn in turns] == ["user", "assistant", "tool", "tool", "user", "user"]
    assert PNG not in turns[2]["content"]
    assert turns[4]["content"][-1]["image_url"]["url"] == PNG


def test_history_and_json_logs_have_only_text_receipts():
    active = messages()
    stored = text_history(active)
    assert type(stored[2]["content"]) is str
    assert isinstance(active[2]["content"], VisualResult)
    assert PNG not in json.dumps(active)
    assert PNG not in json.dumps(stored)
    assert json.loads(stored[2]["content"])["images"] == [{"label": "Front", "attached": True}]


def test_expiration_bounds_active_images_without_mutating_previous_messages():
    active = [{"role": "tool", "content": receipt()} for _ in range(5)]
    expired = expire_visuals(active)
    assert sum(isinstance(m["content"], VisualResult) for m in expired) == 2
    assert all(isinstance(m["content"], VisualResult) for m in active)
    assert "expired" in expired[0]["content"]


@pytest.mark.parametrize("images", [[], [{}], [{"url": "https://example.com/image.png"}], [{"url": "data:image/jpeg;base64,aGVsbG8="}], [{"url": PNG}] * 5])
def test_invalid_images_are_rejected(images):
    with pytest.raises(ValueError):
        visual_result({}, images)
