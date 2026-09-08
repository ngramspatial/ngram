import asyncio
import json
from types import MethodType

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import pytest
from structlog.testing import capture_logs

from ngram.entity import Entity
from ngram.inference.control import InferenceControl
from ngram.inference.openai_transport import OpenAICompatibleTransport
from ngram.models import Input
from ngram.ngram_ar.bridge_server import create_bridge_app
from ngram.work_activity import current_work, work_progress, work_scope, work_step


@pytest.mark.asyncio
async def test_heartbeats_do_not_claim_progress_and_cancel_cleans_up():
    events = []
    started = asyncio.Event()

    async def reporter(event):
        events.append(event)
        if event["heartbeat"]:
            started.set()

    async def run():
        async with work_scope(reporter, heartbeat_seconds=.01):
            async with work_step("model_wait", attempt=1):
                await asyncio.Event().wait()

    task = asyncio.create_task(run())
    await asyncio.wait_for(started.wait(), 1)
    heartbeat = next(e for e in events if e["heartbeat"])
    assert heartbeat["idleMs"] >= 5
    assert heartbeat["stage"] == "model_wait"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert events[-1]["status"] == "cancelled"
    count = len(events)
    await asyncio.sleep(.03)
    assert len(events) == count
    assert current_work.get() is None


@pytest.mark.asyncio
async def test_real_http_timeout_retry_is_visible_and_logs_exclude_payload():
    attempts = 0

    async def reply(request):
        nonlocal attempts
        attempts += 1
        await request.json()
        if attempts == 1:
            await asyncio.sleep(.2)
        return web.json_response({"choices": [{"message": {"content": "done"}, "finish_reason": "stop"}]})

    app = web.Application()
    app.router.add_post('/v1/chat/completions', reply)
    events = []

    async def reporter(event):
        events.append(event)

    async with TestServer(app) as server:
        transport = OpenAICompatibleTransport(str(server.make_url('')).rstrip('/'), timeout=.06,
                                              retry_attempts=2, retry_delay=.001)
        try:
            with capture_logs() as logs:
                async with work_scope(reporter, heartbeat_seconds=.01):
                    result = await transport.chat_completion('test', [{"role": "user", "content": "private-script-secret"}])
                assert result.content == 'done'
            assert attempts == 2
            assert any(e["stage"] == "retry_wait" and e["errorType"] == "TimeoutError" for e in events)
            assert any(e["stage"] == "model_wait" and e.get("attempt") == 2 for e in events)
            assert any(e["event"] == "inference_request_retry" for e in logs)
            assert any(e["event"] == "inference_request_completed" for e in logs)
            assert "private-script-secret" not in json.dumps(logs)
            assert events[-1]["status"] == "complete"
        finally:
            await transport.close()


@pytest.mark.asyncio
async def test_observer_failure_cannot_prevent_real_tool_work():
    async def broken(_event):
        raise ConnectionError('closed')

    async with work_scope(broken):
        async with work_step('tool_running', tool='ar_blender'):
            await work_progress(project='orion', revision=3)
        assert current_work.get().stage == 'processing'
    assert current_work.get() is None


@pytest.mark.asyncio
async def test_concurrent_goals_and_chat_have_isolated_stages():
    events = []
    both = asyncio.Barrier(2)

    async def reporter(event):
        events.append(event)

    async def run(scope, tool):
        async with work_scope(reporter, scope=scope):
            async with work_step('tool_running', tool=tool):
                await both.wait()
                assert current_work.get().details['tool'] == tool

    await asyncio.gather(run('turn', 'ar_blender'), run('code_task', 'run_command'))
    by_scope = {scope: {e['runId'] for e in events if e['scope'] == scope} for scope in ('turn', 'code_task')}
    assert not by_scope['turn'] & by_scope['code_task']


async def receive_type(ws, action_type):
    async with asyncio.timeout(2):
        while True:
            payload = await ws.receive_json()
            for action in payload.get('actions', []):
                if action['type'] == action_type:
                    return action


@pytest.mark.asyncio
async def test_bridge_replays_active_work_and_clears_finished_work_on_reconnect(tmp_path, monkeypatch):
    monkeypatch.delenv('NGRAM_AR_ENTITY_BRIDGE_TOKEN', raising=False)
    monkeypatch.setenv('NGRAM_AR_PERSON_ID', 'ar_user')
    entity = object.__new__(Entity)
    entity._turn_lock = asyncio.Lock()
    entity._turn_activity_sinks = []
    entity.inference_control = InferenceControl(tmp_path / 'paused')
    entity.current_platform = None
    entered = asyncio.Event()
    release = asyncio.Event()

    async def waiting(self, _inp, **_kwargs):
        async with work_step('model_wait', attempt=1, timeoutMs=1800000):
            entered.set()
            await release.wait()
        return 'done', False

    entity._perceive_once = MethodType(waiting, entity)
    async with TestClient(TestServer(await create_bridge_app(entity))) as client:
        task = asyncio.create_task(entity.perceive(Input(text='private', platform='telegram', channel='chat', person_id='person', person_name='You')))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            ws = await client.ws_connect('/')
            await ws.send_json({'type': 'session.start', 'sessionId': 'first', 'shellSlug': 'test', 'workStatus': True})
            await receive_type(ws, 'action:work_status_reset')
            status = await receive_type(ws, 'action:work_status')
            assert status['stage'] == 'model_wait' and status['timeoutMs'] == 1800000
            assert 'private' not in json.dumps(status)
            await ws.close()
            assert not task.done()
            ws = await client.ws_connect('/')
            await ws.send_json({'type': 'session.start', 'sessionId': 'second', 'shellSlug': 'test', 'workStatus': True})
            await receive_type(ws, 'action:work_status_reset')
            replay = await receive_type(ws, 'action:work_status')
            assert replay['runId'] == status['runId']
            release.set()
            await task
            while (await receive_type(ws, 'action:work_status'))['status'] == 'running':
                pass
            await ws.close()
            ws = await client.ws_connect('/')
            await ws.send_json({'type': 'session.start', 'sessionId': 'third', 'shellSlug': 'test', 'workStatus': True})
            await receive_type(ws, 'action:work_status_reset')
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(ws.receive_json(), .03)
            await ws.close()
        finally:
            release.set()
            await task
