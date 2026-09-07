import asyncio
from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from ngram.entity import Entity
from ngram.inference.control import InferenceControl
from ngram.ngram_ar.bridge_server import create_bridge_app


@pytest.mark.asyncio
async def test_spatial_controls_pause_and_resume_shared_entity_without_inference(monkeypatch, tmp_path):
    monkeypatch.delenv('NGRAM_AR_ENTITY_BRIDGE_TOKEN', raising=False)
    entity = object.__new__(Entity)
    entity._turn_activity_sinks = []
    entity._active_perception = None
    entity.inference_control = InferenceControl(tmp_path / 'paused')
    entity.perceive = AsyncMock()
    async with TestClient(TestServer(await create_bridge_app(entity))) as client:
        ws = await client.ws_connect('/')
        await ws.send_json({'type': 'session.start', 'sessionId': 'test'})
        await ws.receive_json()
        for command, expected in [('pause', True), ('status', True), ('resume', False)]:
            await ws.send_json({'type': 'session.event', 'id': command, 'event': {
                'type': 'event:inference_control', 'command': command,
            }})
            async with asyncio.timeout(1):
                while True:
                    response = await ws.receive_json()
                    if response.get('replyTo') == command:
                        break
            assert response['actions'][0]['type'] == 'action:inference_status'
            assert response['actions'][0]['paused'] is expected
            assert entity.inference_paused is expected
        entity.perceive.assert_not_called()
