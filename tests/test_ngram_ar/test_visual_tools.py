"""Correlated capture and bounded Blender render transfer, without a local Blender process."""

import base64
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ngram.inference.visual_results import VisualResult
from ngram.ngram_ar.visual_tools import request_capture
from ngram.ngram_ar.blender_tools import ar_blender
from ngram.ngram_ar.spatial_sessions import SpatialSession, SpatialSessions
from ngram.presence.tools.blender_runtime import BlenderRuntime
from ngram.presence.tools.blender_visuals import render_options
from ngram.presence.tools.runtime import ToolRuntimeContext, reset_tool_runtime, set_tool_runtime

JPEG = b'\xff\xd8\xff' + b'x' * 900
URL = 'data:image/jpeg;base64,' + base64.b64encode(JPEG).decode()


@pytest.mark.asyncio
@pytest.mark.parametrize('enabled', [True, False])
async def test_capture_returns_one_correlated_result_without_new_input(enabled):
    sessions, sent = SpatialSessions(), []
    async def send(actions):
        sent.extend(actions)
        session.acknowledge({'completedActionId': actions[0]['actionId'],
                             'status': 'completed' if enabled else 'failed',
                             'error': None if enabled else 'View sharing is off',
                             'result': {'images': [{'url': URL, 'label': 'Scene'}], 'revision': 7} if enabled else None})
    session = SpatialSession('test', send, dict, shell_slug='test-agent')
    sessions.register(session)
    token = set_tool_runtime(ToolRuntimeContext(entity=SimpleNamespace(_ngram_ar_sessions=sessions)))
    try:
        result = await request_capture(options={'target': 'sword', 'views': ['front', 'right']})
    finally:
        reset_tool_runtime(token)
    assert len(sent) == 1
    assert sent[0]['type'] == 'action:request_capture'
    assert sent[0]['options']['target'] == 'sword'
    if enabled:
        assert isinstance(result, VisualResult)
        assert result.images[0]['url'] == URL
        assert URL not in str(result)
    else:
        assert type(result) is str
        assert json.loads(result)['status'] == 'failed'


@pytest.mark.asyncio
@pytest.mark.parametrize('corrupt', [False, True])
async def test_blender_render_returns_verified_image_without_republishing(monkeypatch, corrupt):
    chunks = iter([JPEG[:400], JPEG[400:]])
    calls = []
    async def call(action, args):
        calls.append(args)
        if args['command'] == 'render':
            return {'ok': True, 'state': 'ready', 'project_id': 'test', 'renders': [
                {'render_id': 'a' * 32, 'style': 'studio', 'sha256': 'bad' if corrupt else hashlib.sha256(JPEG).hexdigest()}]}
        return {'ok': True, 'size': len(JPEG), 'offset': args['offset'], 'data': base64.b64encode(next(chunks)).decode()}
    monkeypatch.setattr('ngram.ngram_ar.blender_tools.get_execution_client', lambda: SimpleNamespace(call=call))
    send = AsyncMock()
    monkeypatch.setattr('ngram.ngram_ar.blender_tools.connected_spatial_session', lambda *args: SimpleNamespace(dispatch=send))
    token = set_tool_runtime(ToolRuntimeContext(entity=SimpleNamespace()))
    try:
        if corrupt:
            with pytest.raises(ValueError, match='checksum'):
                await ar_blender('render', {'project_id': 'test'})
        else:
            result = await ar_blender('render', {'project_id': 'test'})
            assert isinstance(result, VisualResult)
            assert result.images[0]['url'] == URL
            assert URL not in str(result)
    finally:
        reset_tool_runtime(token)
    send.assert_not_awaited()
    assert [c['command'] for c in calls] == ['render', 'artifact', 'artifact']


def test_render_artifacts_require_completed_manifest_and_safe_id(tmp_path):
    runtime = BlenderRuntime(tmp_path)
    runtime.command({'command': 'create', 'project_id': 'test'})
    folder = runtime.directory('test') / 'renders' / ('a' * 32)
    folder.mkdir(parents=True)
    (folder / 'view.jpg').write_bytes(JPEG)
    args = {'command': 'artifact', 'project_id': 'test', 'render_id': 'a' * 32}
    with pytest.raises(ValueError):
        runtime.command(args)
    (folder / 'render.json').write_text('{}')
    assert base64.b64decode(runtime.command(args)['data']) == JPEG
    with pytest.raises(ValueError):
        runtime.command({**args, 'render_id': '../project.json'})


@pytest.mark.asyncio
async def test_render_http_route_is_authenticated_and_returns_jpeg(tmp_path, monkeypatch):
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from ngram.ngram_ar.blender_routes import register_blender_routes
    runtime = BlenderRuntime(tmp_path)
    runtime.command({'command':'create','project_id':'test'})
    folder = runtime.directory('test') / 'renders' / ('b' * 32)
    folder.mkdir(parents=True)
    (folder / 'view.jpg').write_bytes(JPEG)
    (folder / 'render.json').write_text('{}')
    async def call(action, args):
        return runtime.command(args)
    monkeypatch.setattr('ngram.ngram_ar.blender_routes.get_execution_client_for_entity', lambda _: SimpleNamespace(call=call))
    app = web.Application()
    app['entity'] = object()
    register_blender_routes(app, lambda r: r.headers.get('Authorization') == 'Bearer test-only')
    path = '/blender/test/renders/' + 'b' * 32 + '/view.jpg'
    async with TestClient(TestServer(app)) as client:
        assert (await client.get(path)).status == 403
        response = await client.get(path, headers={'Authorization':'Bearer test-only'})
        assert response.status == 200
        assert response.headers['Content-Type'] == 'image/jpeg'
        assert await response.read() == JPEG


def test_capabilities_find_remembered_executable_without_launching_it(tmp_path, monkeypatch):
    runtime = BlenderRuntime(tmp_path)
    runtime.command({'command': 'create', 'project_id': 'test'})
    path = runtime.directory('test') / 'project.json'
    path.write_text(json.dumps({**json.loads(path.read_text()), 'executable': '/portable/blender'}))
    monkeypatch.setattr('ngram.presence.tools.blender_runtime.shutil.which', lambda p: p if p == '/portable/blender' else None)
    result = runtime.command({'command': 'capabilities'})
    assert result['installed']
    assert result['executable'] == '/portable/blender'
    assert result['projects'][0]['project_id'] == 'test'


@pytest.mark.parametrize('value', [False, [], {'size': True}, {'samples': 257}, {'objects': []}, {'position': [0, 0, float('nan')]}, {'orbit': [0, 91]}, {'style': 'invented'}, {'unknown': 1}])
def test_render_options_reject_invalid_input(value):
    with pytest.raises(ValueError):
        render_options(value)
