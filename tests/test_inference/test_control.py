import asyncio
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ngram.entity import Entity
from ngram.inference.control import ControlledInferenceProvider, InferenceControl, InferencePausedError
from ngram.models import Input
from ngram.presence.daemon import PresenceDaemon


@pytest.mark.asyncio
async def test_pause_blocks_chat_and_embeddings_and_survives_restart(tmp_path):
    control = InferenceControl(tmp_path / 'paused')
    raw = SimpleNamespace(chat_completion=AsyncMock(return_value='reply'), embed=AsyncMock(return_value=[1.0]))
    provider = ControlledInferenceProvider(raw, control)
    control.set_paused(True)
    for call in (lambda: provider.chat_completion('model', []), lambda: provider.embed('model', 'text')):
        with pytest.raises(InferencePausedError):
            await call()
    raw.chat_completion.assert_not_called()
    raw.embed.assert_not_called()
    restored = InferenceControl(control.marker)
    assert restored.paused
    restored.set_paused(False)
    assert await provider.chat_completion('model', []) == 'reply'
    assert await provider.embed('model', 'text') == [1.0]


@pytest.mark.asyncio
async def test_pause_cancels_active_requests_without_cancelling_listener(tmp_path):
    control = InferenceControl(tmp_path / 'paused')
    started = asyncio.Event()
    closed = asyncio.Event()

    async def request():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()

    pending = asyncio.create_task(control.run(request))
    await started.wait()
    control.set_paused(True)
    with pytest.raises(InferencePausedError):
        await asyncio.wait_for(pending, 1)
    assert closed.is_set()
    assert not asyncio.current_task().cancelling()
    assert not control._requests


@pytest.mark.asyncio
async def test_pause_closes_stream_and_rejects_late_provider_results(tmp_path):
    control = InferenceControl(tmp_path / 'paused')
    started = asyncio.Event()
    closed = asyncio.Event()

    async def stream():
        try:
            yield 'first'
            started.set()
            await asyncio.Event().wait()
        finally:
            closed.set()

    raw = SimpleNamespace(chat_completion=AsyncMock(return_value=stream()))
    source = await ControlledInferenceProvider(raw, control).chat_completion('model', [], stream=True)
    assert await anext(source) == 'first'
    pending = asyncio.create_task(anext(source))
    await started.wait()
    control.set_paused(True)
    control.set_paused(False)
    with pytest.raises(InferencePausedError):
        await pending
    assert closed.is_set()


@pytest.mark.asyncio
async def test_entity_pause_stops_turn_and_blocks_queued_turns(tmp_path):
    entity = object.__new__(Entity)
    entity.inference_control = InferenceControl(tmp_path / 'paused')
    entity._active_perception = None
    entity._turn_lock = asyncio.Lock()
    started = asyncio.Event()
    cancelled = asyncio.Event()
    calls = 0

    async def perceive_once(self, inp, **kwargs):
        nonlocal calls
        calls += 1
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    entity._perceive_once = MethodType(perceive_once, entity)
    inp = Input(text='work', person_id='test', person_name='Test', platform='cli')
    active = asyncio.create_task(entity.perceive(inp))
    await started.wait()
    queued = asyncio.create_task(entity.perceive(inp))
    entity.set_inference_paused(True)
    replies = await asyncio.wait_for(asyncio.gather(active, queued), 1)
    assert all('paused' in text for text, _ in replies)
    assert cancelled.is_set()
    assert calls == 1
    assert not entity._turn_lock.locked()


@pytest.mark.asyncio
async def test_paused_daemon_skips_both_legacy_initiative_and_consolidation():
    daemon = object.__new__(PresenceDaemon)
    daemon.entity = SimpleNamespace(inference_paused=True, tick=AsyncMock())
    daemon._consolidation = SimpleNamespace(run_for_daemon=AsyncMock())
    await daemon._heartbeat_tick()
    await daemon._consolidation_tick()
    daemon.entity.tick.assert_not_called()
    daemon._consolidation.run_for_daemon.assert_not_called()
