import pytest

from ngram.config import HarnessConfig, entity_from_dict
from ngram.cognition.deliberate import AgentLoopState, DeliberateCognition
from ngram.inference.types import ChatCompletionResult, ToolCallSpec
from ngram.models import Input


class _SequenceProvider:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    async def chat_completion(
        self,
        model,
        messages,
        *,
        tools=None,
        tool_choice=None,
        temperature=0.7,
        max_tokens=1024,
        think=False,
        stream=False,
        num_ctx=None,
    ):
        _ = (model, messages, tools, tool_choice, temperature, max_tokens, think, stream, num_ctx)
        if self.calls >= len(self._responses):
            raise AssertionError("chat_completion called more times than expected")
        res = self._responses[self.calls]
        self.calls += 1
        return res


@pytest.mark.asyncio
async def test_tool_image_reaches_next_model_step_but_not_saved_history(entity_config):
    from ngram.inference.visual_results import VisualResult, visual_result

    url = 'data:image/jpeg;base64,/9j/eA=='
    visual = visual_result({'ok': True}, [{'url': url, 'label': 'Front'}])
    observed = []
    class Provider(_SequenceProvider):
        async def chat_completion(self, model, messages, **kwargs):
            observed.append(messages)
            return await super().chat_completion(model, messages, **kwargs)
    provider = Provider([
        ChatCompletionResult(content='', tool_calls=[ToolCallSpec(name='ar_request_capture', arguments={}, id='view')]),
        ChatCompletionResult(content='The blade has a raised ridge.'),
    ])
    async def execute(spec):
        return visual
    inp = Input(text='Inspect the blade', person_id='test', person_name='Test')
    cognition = DeliberateCognition(entity_config, provider)
    events = [event async for event in cognition.iter_responses(
        inp, 'system', [{'role': 'user', 'content': inp.text}],
        tools=[{'type': 'function', 'function': {'name': 'ar_request_capture', 'parameters': {}}}], tool_executor=execute)]
    assert len(observed) == 2
    tool = next(m for m in observed[1] if m['role'] == 'tool')
    assert isinstance(tool['content'], VisualResult)
    assert tool['content'].images[0]['url'] == url
    stored = [m for e in events for m in e.history_entries if m['role'] == 'tool']
    assert type(stored[0]['content']) is str
    assert url not in stored[0]['content']


@pytest.fixture
def entity_config():
    h = HarnessConfig()
    data = {
        "name": "LoopTest",
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
            "backstory": "Loop test being.",
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


def test_inference_params_use_entity_sampling_overrides(entity_config):
    entity_config.cognition.temperature = 0.83
    entity_config.cognition.thinking_mode = False
    entity_config.harness.cognition.temperature = 0.11
    entity_config.harness.cognition.thinking_mode = True
    cog = DeliberateCognition(entity_config, _SequenceProvider([]))

    _, _, reflex_think, reflex_temperature = cog._inference_params("reflex")
    _, _, deliberate_think, deliberate_temperature = cog._inference_params("deliberate")

    assert reflex_think is False
    assert reflex_temperature == 0.83
    assert deliberate_think is False
    assert deliberate_temperature == 0.83


@pytest.mark.asyncio
async def test_loop_continues_past_progress_chatter(entity_config):
    provider = _SequenceProvider(
        [
            ChatCompletionResult(content="nah, i don't see one in here... checking the root directory right now."),
            ChatCompletionResult(content="/app\nknowledge.md not found"),
        ]
    )
    cog = DeliberateCognition(entity_config, provider)
    inp = Input(text="do you have a knowledge.md file?", person_id="u", person_name="U")

    async def final_checker(text: str, res: ChatCompletionResult, state: AgentLoopState):
        _ = (res, state)
        if "checking the root directory" in (text or "").lower():
            return False, "Same turn. That was only progress chatter. Continue until you can answer."
        return True, None

    events = []
    async for ev in cog.iter_responses(
        inp,
        "system",
        [{"role": "user", "content": inp.text}],
        final_checker=final_checker,
    ):
        events.append(ev)

    assert len(events) == 2
    assert events[0].kind == "intermediate"
    assert events[0].display_text == ""
    assert events[1].kind == "final"
    assert "/app" in events[1].display_text


@pytest.mark.asyncio
async def test_loop_continues_after_tool_until_grounded_answer(entity_config):
    provider = _SequenceProvider(
        [
            ChatCompletionResult(
                content="",
                tool_calls=[ToolCallSpec(name="list_directory", arguments={"path": "."}, id="tc1")],
            ),
            ChatCompletionResult(content="done"),
            ChatCompletionResult(content="/app\nentries: knowledge.md missing"),
        ]
    )
    cog = DeliberateCognition(entity_config, provider)
    inp = Input(text="use tools and check the workspace", person_id="u", person_name="U")
    tool_calls = []

    async def tool_executor(spec: ToolCallSpec):
        tool_calls.append(spec.name)
        return '{"ok": true, "path": "/app", "entries": []}'

    async def final_checker(text: str, res: ChatCompletionResult, state: AgentLoopState):
        _ = res
        if state.tool_calls_seen > 0 and (text or "").strip().lower() == "done":
            return False, "Same turn. That is not the final grounded answer. Answer from the tool result."
        return True, None

    events = []
    async for ev in cog.iter_responses(
        inp,
        "system",
        [{"role": "user", "content": inp.text}],
        tools=[{"type": "function", "function": {"name": "list_directory", "description": "", "parameters": {}}}],
        tool_executor=tool_executor,
        final_checker=final_checker,
    ):
        events.append(ev)

    assert tool_calls == ["list_directory"]
    assert len(events) == 3
    assert [ev.kind for ev in events] == ["intermediate", "intermediate", "final"]
    assert all(ev.display_text == "" for ev in events[:-1])
    assert "/app" in events[-1].display_text


@pytest.mark.asyncio
async def test_rejected_end_turn_continues_same_directive(entity_config):
    provider = _SequenceProvider(
        [
            ChatCompletionResult(
                content="",
                tool_calls=[ToolCallSpec(name="end_turn", arguments={}, id="end1")],
            ),
            ChatCompletionResult(content="finished after the rejected stop"),
        ]
    )
    cog = DeliberateCognition(entity_config, provider)
    inp = Input(text="do the whole task", person_id="u", person_name="U")
    checks = 0

    async def tool_executor(spec: ToolCallSpec):
        assert spec.name == "end_turn"
        return "[turn ended]"

    async def final_checker(text: str, res: ChatCompletionResult, state: AgentLoopState):
        nonlocal checks
        _ = (text, res, state)
        checks += 1
        return (checks > 1), "Same turn. The directive is unfinished. Continue."

    events = []
    async for ev in cog.iter_responses(
        inp,
        "system",
        [{"role": "user", "content": inp.text}],
        tools=[{"type": "function", "function": {"name": "end_turn"}}],
        tool_executor=tool_executor,
        final_checker=final_checker,
    ):
        events.append(ev)

    assert provider.calls == 2
    assert [event.kind for event in events] == ["intermediate", "final"]
    assert events[-1].display_text == "finished after the rejected stop"


@pytest.mark.asyncio
async def test_delivered_say_and_end_turn_is_a_hard_boundary(entity_config):
    provider = _SequenceProvider(
        [
            ChatCompletionResult(
                content="",
                tool_calls=[
                    ToolCallSpec(
                        name="say",
                        arguments={"message": "The result is ready."},
                        id="say1",
                    ),
                    ToolCallSpec(name="end_turn", arguments={}, id="end1"),
                ],
            )
        ]
    )
    cog = DeliberateCognition(entity_config, provider)
    inp = Input(text="give me the result", person_id="u", person_name="U")
    checks = 0

    async def tool_executor(spec: ToolCallSpec):
        return "[sent]" if spec.name == "say" else "[turn ended]"

    async def final_checker(text: str, res: ChatCompletionResult, state: AgentLoopState):
        nonlocal checks
        _ = (text, res, state)
        checks += 1
        return False, "Same turn. Continue."

    events = []
    async for event in cog.iter_responses(
        inp,
        "system",
        [{"role": "user", "content": inp.text}],
        tools=[
            {"type": "function", "function": {"name": "say"}},
            {"type": "function", "function": {"name": "end_turn"}},
        ],
        tool_executor=tool_executor,
        final_checker=final_checker,
    ):
        events.append(event)

    assert provider.calls == 1
    assert checks == 0
    assert [event.kind for event in events] == ["final"]


@pytest.mark.asyncio
@pytest.mark.parametrize("speech_tool,receipt", [
    ("say", "[sent]"), ("ar_speak", "[spatial: speak queued]"),
])
async def test_delivered_reply_then_separate_end_turn_never_reopens(entity_config, speech_tool, receipt):
    provider = _SequenceProvider([
        ChatCompletionResult(content="", tool_calls=[
            ToolCallSpec(name=speech_tool, arguments={"message": "Done."}, id="say1"),
        ]),
        ChatCompletionResult(content="", tool_calls=[
            ToolCallSpec(name="end_turn", arguments={}, id="end1"),
        ]),
    ])
    cog = DeliberateCognition(entity_config, provider)
    inp = Input(text="give me the result", person_id="u", person_name="U")

    async def execute(spec):
        return receipt if spec.name == speech_tool else "[turn ended]"

    async def reject(*_args):
        pytest.fail("A delivered reply and explicit end must not invoke another completion judge")

    events = [event async for event in cog.iter_responses(
        inp, "system", [{"role": "user", "content": inp.text}],
        tools=[{"type": "function", "function": {"name": name}}
               for name in [speech_tool, "end_turn"]],
        tool_executor=execute, final_checker=reject,
    )]
    assert provider.calls == 2
    assert events[-1].kind == "final"


@pytest.mark.asyncio
@pytest.mark.parametrize("use_end_tool", [False, True])
async def test_rejected_completion_cannot_extend_configured_budget(entity_config, use_end_tool):
    provider = _SequenceProvider([])
    cog = DeliberateCognition(entity_config, provider)
    cap = cog._agent_step_cap()
    provider._responses = [ChatCompletionResult(
        content=f"candidate {index}",
        tool_calls=[ToolCallSpec(name="end_turn", arguments={}, id=f"end{index}")]
        if use_end_tool else [],
    ) for index in range(cap)]
    inp = Input(text="finish the work", person_id="u", person_name="U")

    async def execute(_spec):
        return "[turn ended]"

    async def reject(*_args):
        return False, "Continue the same turn."

    events = [event async for event in cog.iter_responses(
        inp, "system", [{"role": "user", "content": inp.text}],
        tools=[{"type": "function", "function": {"name": "end_turn"}}],
        tool_executor=execute, final_checker=reject,
    )]
    assert provider.calls == cap
    assert events[-1].kind == "final"


@pytest.mark.asyncio
async def test_repetitive_speech_only_rounds_force_turn_completion(entity_config):
    provider = _SequenceProvider(
        [
            ChatCompletionResult(
                content="",
                tool_calls=[
                    ToolCallSpec(
                        name="say",
                        arguments={"message": f"The balls are placed ({index})."},
                        id=f"say-{index}",
                    )
                ],
            )
            for index in range(4)
        ]
    )
    cog = DeliberateCognition(entity_config, provider)
    inp = Input(text="spawn the balls", person_id="u", person_name="U")

    async def tool_executor(spec: ToolCallSpec):
        _ = spec
        return "[sent]"

    events = []
    async for event in cog.iter_responses(
        inp,
        "system",
        [{"role": "user", "content": inp.text}],
        tools=[{"type": "function", "function": {"name": "say"}}],
        tool_executor=tool_executor,
    ):
        events.append(event)

    assert provider.calls == 3
    assert [event.kind for event in events] == [
        "intermediate",
        "intermediate",
        "intermediate",
        "final",
    ]
    assert events[-1].display_text == ""


@pytest.mark.asyncio
async def test_active_turn_compaction_preserves_directive_and_working_state(entity_config):
    entity_config.cognition.max_context_tokens = 2048
    entity_config.cognition.history_compression.compaction_threshold_ratio = 0.5
    provider = _SequenceProvider(
        [
            ChatCompletionResult(
                content=(
                    "Goal: finish the user's directive.\n"
                    "Progress: inspected several files and found the relevant implementation.\n"
                    "Next Steps: apply and verify the remaining change."
                )
            )
        ]
    )
    cog = DeliberateCognition(entity_config, provider)
    initial = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "original directive must survive"},
    ]
    active = []
    for index in range(10):
        call_id = f"tool-{index}"
        active.extend(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {"name": "read_file", "arguments": "{}"},
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": "read_file",
                    "content": "implementation detail " + ("x" * 700),
                },
                {"role": "user", "content": "Same turn. Continue."},
            ]
        )

    compacted, summary, changed = await cog._compact_active_turn_if_needed(
        initial + active,
        initial_message_count=len(initial),
        previous_summary="",
    )

    assert changed is True
    assert summary
    assert compacted[: len(initial)] == initial
    assert any("ACTIVE DIRECTIVE CONTINUES" in str(m.get("content")) for m in compacted)
    assert len(compacted) < len(initial + active)


def test_large_per_turn_budgets_are_configurable(entity_config):
    entity_config.cognition.message_budget_per_turn = 48
    entity_config.cognition.tool_continuation_rounds = 48
    cog = DeliberateCognition(entity_config, client=object())

    assert entity_config.cognition.message_budget_per_turn == 48
    assert cog._agent_step_cap() == 54


@pytest.mark.asyncio
@pytest.mark.parametrize("metadata", [
    {}, {"delegation": True, "delegation_max_steps": 150},
    {"code_task": True, "code_task_max_steps": 150},
    {"sustained_session": {"extra_tool_steps": 10}},
])
async def test_productive_work_can_pass_previous_step_caps(entity_config, metadata):
    entity_config.cognition.tool_continuation_rounds = 9994
    provider = _SequenceProvider([
        *[ChatCompletionResult(content="", tool_calls=[ToolCallSpec(
            name="write_file", arguments={"path": f"part-{i}.txt", "content": "done"}, id=f"t{i}",
        )]) for i in range(100)],
        ChatCompletionResult(content="The game is implemented and tested."),
    ])
    cog = DeliberateCognition(entity_config, provider)
    inp = Input(text="Build the game", person_id="u", person_name="U", metadata=metadata)
    executed = []

    async def execute(spec):
        executed.append(spec.arguments["path"])
        return '{"ok": true}'

    events = [event async for event in cog.iter_responses(
        inp, "system", [{"role": "user", "content": inp.text}],
        tools=[{"type": "function", "function": {"name": "write_file", "parameters": {"type": "object"}}}],
        tool_executor=execute,
    )]
    assert len(executed) == 100
    assert provider.calls == 101
    assert events[-1].kind == "final"
    assert events[-1].display_text == "The game is implemented and tested."
