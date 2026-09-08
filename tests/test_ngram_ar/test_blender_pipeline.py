"""Execution routing, artifacts, and cancellation without a local Blender install."""

import asyncio
import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from ngram.ngram_ar.blender_tools import ar_blender
from ngram.ngram_ar.blender_routes import register_blender_routes
from ngram.ngram_ar.spatial_sessions import SpatialSession, SpatialSessions
from ngram.presence.tools.blender_runtime import BlenderRuntime
from ngram.presence.tools.execution_rpc import ExecutionRPCClient
from ngram.presence.tools.runtime import ToolRuntimeContext, reset_tool_runtime, set_tool_runtime


@pytest.mark.asyncio
@pytest.mark.parametrize('tool_name', ['ar_world', 'ar_blender'])
async def test_spatial_build_steps_continue_but_identical_polling_is_bounded(tool_name):
    from ngram.entity import Entity
    from ngram.inference.types import ToolCallSpec

    entity = object.__new__(Entity)
    entity.tools = SimpleNamespace(execute=AsyncMock(return_value='{"ok":true}'))
    entity.current_platform = None
    entity.tonic = SimpleNamespace(emit=Mock())
    entity.self_model = SimpleNamespace(record_tool_result=AsyncMock())
    state = {}
    for command in ['capabilities', 'observe', 'apply', 'program', 'resume', 'observe']:
        result = await entity._tool_exec(ToolCallSpec(name=tool_name, arguments={'command': command}), state=state)
        assert json.loads(result)['ok']
    assert entity.tools.execute.await_count == 6
    state = {}
    for _ in range(3):
        await entity._tool_exec(ToolCallSpec(name=tool_name, arguments={'command': 'status', 'payload': {}}), state=state)
    blocked = await entity._tool_exec(ToolCallSpec(name=tool_name, arguments={'payload': {}, 'command': 'status'}), state=state)
    assert 'same' in json.loads(blocked)['error']
    assert entity.tools.execute.await_count == 9
    # A substantive new request is allowed after the duplicate polling guard fires.
    result = await entity._tool_exec(ToolCallSpec(name=tool_name, arguments={'command': 'stop'}), state=state)
    assert json.loads(result)['ok']


@pytest.mark.asyncio
async def test_blender_never_falls_back_to_another_computer(tmp_path, monkeypatch):
    client = ExecutionRPCClient('https://execution.invalid', '/rpc', '', 5, True, tmp_path)
    remote = AsyncMock(return_value={"ok": False, "error": "connection refused"})
    monkeypatch.setattr(client, '_call_http', remote)
    monkeypatch.setattr(client, '_call_local', lambda *a: pytest.fail('Must not execute locally'))
    assert not (await client.call('blender', {"command": "create"}))['ok']
    remote.assert_awaited_once()


def test_artifact_access_only_published_project_files(tmp_path):
    runtime = BlenderRuntime(tmp_path)
    runtime.command({"command": "create", "project_id": "test", "name": "Test"})
    folder = runtime.directory('test') / 'revisions' / '1'
    folder.mkdir(parents=True)
    data = b'glTF' + b'x' * (600 * 1024)
    (folder / 'preview.glb').write_bytes(data)
    with pytest.raises(ValueError, match='not published'):
        runtime.artifact('test', 1, 'preview.glb')
    (folder / 'snapshot.json').write_text('{}')
    a = runtime.command({"command": "artifact", "project_id": "test", "revision": 1, "name": "preview.glb"})
    b = runtime.command({"command": "artifact", "project_id": "test", "revision": 1, "name": "preview.glb", "offset": 512 * 1024})
    assert base64.b64decode(a['data']) + base64.b64decode(b['data']) == data
    for ident, name in [('../test', 'preview.glb'), ('test', '../../project.json'), ('test', 'secrets.env')]:
        with pytest.raises(ValueError):
            runtime.artifact(ident, 1, name)
    with pytest.raises(ValueError, match='already exists'):
        runtime.command({"command": "create", "project_id": "test"})


@pytest.mark.asyncio
async def test_cancel_stops_exact_job_on_execution_host_even_during_initial_rpc(monkeypatch):
    started = asyncio.Event()
    calls = []
    async def call(action, args):
        calls.append((action, args))
        if args['command'] == 'execute':
            started.set()
            await asyncio.Future()
        return {"ok": True, "state": "stopped"}
    monkeypatch.setattr('ngram.ngram_ar.blender_tools.get_execution_client', lambda: SimpleNamespace(call=call))
    token = set_tool_runtime(ToolRuntimeContext(entity=SimpleNamespace()))
    try:
        task = asyncio.create_task(ar_blender('execute', {'project_id': 'test', 'source': 'pass', 'request_id': 'job1'}))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        reset_tool_runtime(token)
    assert calls[-1] == ('blender', {'command': 'stop', 'project_id': 'test', 'request_id': 'job1'})


@pytest.mark.asyncio
async def test_live_preview_uses_connected_shell_and_preserves_position(monkeypatch):
    replies = iter([
        {'ok': True, 'project_id': 'sculpture', 'name': 'Sculpture', 'job_id': 'edit', 'state': 'working', 'snapshot': {'revision': 1}},
        {'ok': True, 'project_id': 'sculpture', 'name': 'Sculpture', 'job_id': 'edit', 'state': 'ready', 'snapshot': {'revision': 2}},
        {'ok': True, 'project_id': 'sculpture', 'name': 'Sculpture', 'job_id': 'edit', 'state': 'stopped', 'snapshot': {'revision': 2}},
    ])
    call = AsyncMock(side_effect=lambda *a: next(replies))
    monkeypatch.setattr('ngram.ngram_ar.blender_tools.get_execution_client', lambda: SimpleNamespace(call=call))
    sessions, sent = SpatialSessions(), []
    async def send(actions):
        sent.extend(actions)
        session.acknowledge({'completedActionId': actions[0]['actionId'], 'status': 'completed', 'result': {'status': 'loading'}})
    session = SpatialSession('live', send, dict, shell_slug='test-agent')
    sessions.register(session)
    token = set_tool_runtime(ToolRuntimeContext(entity=SimpleNamespace(_ngram_ar_sessions=sessions)))
    try:
        result = json.loads(await ar_blender('execute', {'project_id': 'sculpture', 'request_id': 'edit', 'source': 'pass', 'position': [1, 0, -2]}))
        stopped = json.loads(await ar_blender('stop', {'project_id': 'sculpture'}))
    finally:
        reset_tool_runtime(token)
    assert result['state'] == 'ready'
    assert [a['payload']['revision'] for a in sent] == [1, 2, 2]
    assert stopped['state'] == 'stopped'
    assert sent[-1]['payload']['stateOnly'] is True
    assert sent[-1]['payload']['state'] == 'stopped'
    assert sent[0]['payload']['base'] == '/api/shells/test-agent/blender/sculpture'
    assert sent[0]['payload']['position'] == [1, 0, -2]


@pytest.mark.asyncio
async def test_bridge_artifacts_and_stop_are_authenticated_and_do_not_infer(tmp_path, monkeypatch):
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    runtime = BlenderRuntime(tmp_path)
    runtime.command({'command': 'create', 'project_id': 'model'})
    folder = runtime.directory('model') / 'revisions' / '1'
    folder.mkdir(parents=True)
    (folder / 'preview.glb').write_bytes(b'glTFtest')
    (folder / 'snapshot.json').write_text('{}')
    async def call(action, payload):
        try:
            return runtime.command(payload)
        except ValueError as exc:
            return {'ok': False, 'error': str(exc)}
    monkeypatch.setattr('ngram.ngram_ar.blender_routes.get_execution_client_for_entity', lambda e: SimpleNamespace(call=call))
    app = web.Application()
    app['entity'] = object()
    register_blender_routes(app, lambda r: r.headers.get('Authorization') == 'Bearer test-only')
    async with TestClient(TestServer(app)) as client:
        assert (await client.get('/blender/model/1/preview.glb')).status == 403
        response = await client.get('/blender/model/1/preview.glb', headers={'Authorization': 'Bearer test-only'})
        assert response.status == 200
        assert await response.read() == b'glTFtest'
        assert response.headers['Content-Type'] == 'model/gltf-binary'
        assert (await client.get('/blender/model/1/project.json', headers={'Authorization': 'Bearer test-only'})).status == 404
        response = await client.post('/blender/model/stop', headers={'Authorization': 'Bearer test-only'})
        assert (await response.json())['state'] == 'stopped'
