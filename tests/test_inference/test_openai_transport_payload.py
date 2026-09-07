"""OpenAI-compatible transport payload shaping (Ollama extensions)."""

from __future__ import annotations

import pytest

from ngram.inference.openai_transport import OpenAICompatibleTransport, OpenAIResponsesTransport


@pytest.mark.asyncio
async def test_chat_completion_adds_options_num_ctx() -> None:
    t = OpenAICompatibleTransport(base_url="http://127.0.0.1:9")
    captured: dict = {}

    async def fake_post(_path: str, payload: dict) -> dict:
        captured.clear()
        captured.update(payload)
        return {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}

    t._post_json = fake_post  # type: ignore[method-assign]

    await t.chat_completion(
        "m",
        [{"role": "user", "content": "hi"}],
        num_ctx=16384,
    )
    assert captured.get("options") == {"num_ctx": 16384}


def test_build_payload_omits_options_when_num_ctx_none() -> None:
    t = OpenAICompatibleTransport(base_url="http://127.0.0.1:9")
    p = t._build_chat_completions_payload(
        "m",
        [{"role": "user", "content": "x"}],
        temperature=0.5,
        max_tokens=10,
        think=False,
        stream=False,
        num_ctx=None,
    )
    assert "options" not in p


def test_build_payload_omits_options_when_num_ctx_zero() -> None:
    t = OpenAICompatibleTransport(base_url="http://127.0.0.1:9")
    p = t._build_chat_completions_payload(
        "m",
        [{"role": "user", "content": "x"}],
        temperature=0.5,
        max_tokens=10,
        think=False,
        stream=False,
        num_ctx=0,
    )
    assert "options" not in p


def test_frontier_payload_preserves_provider_native_system_message() -> None:
    t = OpenAICompatibleTransport(
        base_url="https://api.example",
        gemma_shaping=False,
    )
    messages = [
        {"role": "system", "content": "You are Rook."},
        {"role": "user", "content": "Hello"},
    ]
    payload = t._build_chat_completions_payload(  # noqa: SLF001
        "frontier-model",
        messages,
        think=True,
    )
    assert payload["messages"] == messages
    assert "thought" not in payload["messages"][0]["content"]


def test_frontier_payload_can_use_modern_completion_token_field() -> None:
    t = OpenAICompatibleTransport(
        base_url="https://api.example",
        gemma_shaping=False,
        max_tokens_field="max_completion_tokens",
    )
    payload = t._build_chat_completions_payload(  # noqa: SLF001
        "gpt-6-astra",
        [{"role": "user", "content": "Hello"}],
        max_tokens=2048,
    )
    assert payload["max_completion_tokens"] == 2048
    assert "max_tokens" not in payload


def test_native_openai_payload_can_omit_unsupported_temperature() -> None:
    t = OpenAICompatibleTransport(
        base_url="https://api.openai.com",
        gemma_shaping=False,
        max_tokens_field="max_completion_tokens",
        pass_temperature=False,
    )
    payload = t._build_chat_completions_payload(  # noqa: SLF001
        "gpt-5.6-sol",
        [{"role": "user", "content": "Hello"}],
        temperature=0.7,
    )
    assert "temperature" not in payload


@pytest.mark.asyncio
async def test_openai_embedding_payload_preserves_harness_dimensions() -> None:
    t = OpenAIResponsesTransport(
        base_url="https://api.openai.com",
        gemma_shaping=False,
        embedding_dimensions=768,
    )
    captured: dict = {}

    async def fake_post(path: str, payload: dict) -> dict:
        assert path == "/embeddings"
        captured.update(payload)
        return {"data": [{"embedding": [0.25, 0.75]}]}

    t._post_json = fake_post  # type: ignore[method-assign]
    assert await t.embed("text-embedding-3-small", "remember this") == [0.25, 0.75]
    assert captured == {
        "model": "text-embedding-3-small",
        "input": "remember this",
        "dimensions": 768,
    }


def test_responses_payload_maps_chat_tools_and_reasoning() -> None:
    t = OpenAIResponsesTransport(base_url="https://api.openai.com", gemma_shaping=False)
    payload = t._build_responses_payload(  # noqa: SLF001
        "gpt-5.6-sol",
        [
            {"role": "system", "content": "You are Rook."},
            {"role": "user", "content": "Check the scene."},
        ],
        tools=[{
            "type": "function",
            "function": {
                "name": "ar_inspect_surface",
                "description": "Inspect the spatial scene.",
                "parameters": {"type": "object", "properties": {}},
            },
        }],
        tool_choice="required",
        max_tokens=2048,
        think=True,
    )
    assert payload["input"][0] == {"role": "system", "content": "You are Rook."}
    assert payload["tools"][0]["name"] == "ar_inspect_surface"
    assert "function" not in payload["tools"][0]
    assert payload["tool_choice"] == "required"
    assert payload["reasoning"] == {"effort": "medium"}
    assert payload["max_output_tokens"] == 2048
    assert payload["store"] is False
    assert "temperature" not in payload


@pytest.mark.parametrize("model", ["gpt-6-astra", "gpt-6-astra-2026-09-01"])
@pytest.mark.parametrize("think,effort,floor", [(False, "low", 1024), (True, "medium", 2048)])
def test_astra_short_checks_keep_supported_reasoning_and_output_headroom(model, think, effort, floor) -> None:
    t = OpenAIResponsesTransport(base_url="https://api.openai.com", gemma_shaping=False)
    payload = t._build_responses_payload(
        model,
        [{"role": "user", "content": "Reply with exactly one label: SOCIAL or EXECUTION."}],
        think=think,
        max_tokens=8,
    )
    assert payload["reasoning"] == {"effort": effort}
    assert payload["max_output_tokens"] == floor
    assert "temperature" not in payload


def test_astra_keeps_larger_work_budgets_and_sol_keeps_nonreasoning_checks() -> None:
    t = OpenAIResponsesTransport(base_url="https://api.openai.com", gemma_shaping=False)
    assert t._build_responses_payload("gpt-6-astra", [], max_tokens=4096)["max_output_tokens"] == 4096
    sol = t._build_responses_payload("gpt-5.6-sol", [], max_tokens=8, think=False)
    assert sol["reasoning"] == {"effort": "none"}
    assert sol["max_output_tokens"] == 8


def test_responses_tools_preserve_optional_arguments_and_explicit_strict_opt_in() -> None:
    schema = {"type": "object", "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["path"]}
    tool = {"type": "function", "function": {"name": "read_file", "parameters": schema}}
    converted = OpenAIResponsesTransport._response_tool(tool)
    assert converted["strict"] is False
    assert converted["parameters"]["required"] == ["path"]
    tool["function"]["strict"] = True
    assert OpenAIResponsesTransport._response_tool(tool)["strict"] is True


def test_responses_payload_maps_prior_function_call_and_output() -> None:
    t = OpenAIResponsesTransport(base_url="https://api.openai.com", gemma_shaping=False)
    payload = t._build_responses_payload(  # noqa: SLF001
        "gpt-5.6-sol",
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call_123",
                    "type": "function",
                    "function": {"name": "clock", "arguments": '{"zone":"UTC"}'},
                }],
            },
            {"role": "tool", "tool_call_id": "call_123", "name": "clock", "content": "12:00"},
        ],
    )
    assert payload["input"] == [
        {
            "type": "function_call",
            "call_id": "call_123",
            "name": "clock",
            "arguments": '{"zone":"UTC"}',
        },
        {"type": "function_call_output", "call_id": "call_123", "output": "12:00"},
    ]


def test_responses_result_maps_text_tools_and_usage() -> None:
    result = OpenAIResponsesTransport._parse_responses_response({  # noqa: SLF001
        "status": "completed",
        "output": [
            {"type": "message", "content": [{"type": "output_text", "text": "Working."}]},
            {"type": "function_call", "call_id": "call_9", "name": "gesture", "arguments": '{"gesture":"wave"}'},
        ],
        "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    })
    assert result.content == "Working."
    assert result.tool_calls[0].name == "gesture"
    assert result.tool_calls[0].arguments == {"gesture": "wave"}
    assert result.tool_calls[0].id == "call_9"
    assert result.usage == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
