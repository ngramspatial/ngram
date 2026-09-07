import asyncio
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from ngram.entity import Entity
from ngram.ngram_ar.bridge_server import create_bridge_app
from ngram.ngram_ar.context_telemetry import context_snapshot


def configure(entity):
    entity.config = SimpleNamespace(
        effective_deliberate_model=lambda: 'gpt-6-astra',
        effective_context_tokens=lambda: 256000,
        harness=SimpleNamespace(inference=SimpleNamespace(provider='openai')),
    )
    entity._history = [{'role': 'user', 'content': 'Test history'}]
    entity._history_rolling_summary = ''
    entity.deliberate = SimpleNamespace(latest_context_status=None)
    return entity


def test_snapshot_marks_history_only_and_never_reuses_another_model(monkeypatch):
    monkeypatch.setattr('ngram.ngram_ar.context_telemetry.effective_inference_provider_name', lambda _: 'openai')
    entity = configure(SimpleNamespace())
    history = context_snapshot(entity)
    assert history['source'] == 'history'
    assert history['estimatedTokens'] > 0
    assert 'Test history' not in str(history)
    latest = {**history, 'source': 'prompt', 'estimatedTokens': 50000}
    entity.deliberate.latest_context_status = latest
    assert context_snapshot(entity)['estimatedTokens'] == 50000
    entity.config.effective_deliberate_model = lambda: 'another-model'
    assert context_snapshot(entity)['source'] == 'history'


@pytest.mark.asyncio
async def test_context_query_works_while_turn_lock_is_held_without_inference(monkeypatch):
    monkeypatch.delenv('NGRAM_AR_ENTITY_BRIDGE_TOKEN', raising=False)
    monkeypatch.setattr('ngram.ngram_ar.context_telemetry.effective_inference_provider_name', lambda _: 'openai')
    entity = configure(object.__new__(Entity))
    entity._turn_lock = asyncio.Lock()
    entity._turn_activity_sinks = []
    await entity._turn_lock.acquire()
    async with TestClient(TestServer(await create_bridge_app(entity))) as client:
        ws = await client.ws_connect('/')
        await ws.send_json({'type': 'session.start', 'sessionId': 'meter-test'})
        await ws.receive_json()
        await ws.send_json({'type': 'session.event', 'id': 'snapshot', 'event': {'type': 'event:context_status'}})
        async with asyncio.timeout(2):
            result = await ws.receive_json()
        assert result['replyTo'] == 'snapshot'
        action = result['actions'][0]
        assert action['type'] == 'action:context_status'
        assert action['model'] == 'gpt-6-astra'
        assert action['inputBudgetTokens'] == 256000
        assert entity._turn_lock.locked()
        await ws.close()
    entity._turn_lock.release()
