import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from ngram.ngram_ar.environment_tools import ar_environment, register_environment_tools
from ngram.ngram_ar.blender_tools import ar_blender
from ngram.presence.tools.blender_visuals import render_options
from ngram.presence.tools.blender_runtime import BlenderRuntime
from ngram.presence.tools.registry import ToolRegistry


@pytest.mark.asyncio
async def test_environment_returns_renderer_confirmation_with_asset_load_timeout(monkeypatch):
    import ngram.ngram_ar.environment_tools as tools
    session = SimpleNamespace(dispatch=AsyncMock(return_value='{"status":"completed","result":{"revision":1}}'))
    monkeypatch.setattr(tools, "get_tool_runtime", lambda: SimpleNamespace(entity=object(), inp=None))
    monkeypatch.setattr(tools, "connected_spatial_session", lambda *args: session)
    payload = {"sky": {"type": "panorama", "url": "/sky.jpg"}, "command": "clear"}
    result = json.loads(await ar_environment("configure", payload))
    assert result["status"] == "completed"
    args, kwargs = session.dispatch.call_args
    assert args[0]["command"] == "environment"
    assert args[0]["payload"]["command"] == "configure"
    assert kwargs["timeout"] == 120
    assert "invalid" in await ar_environment("unknown")
    assert session.dispatch.await_count == 1
    registry = ToolRegistry()
    register_environment_tools(registry)
    assert registry.tool_discovery_detail("ar_environment")


def test_panorama_options_and_execution_preserve_authored_scene(tmp_path, monkeypatch):
    options = render_options({"projection": "equirectangular"})
    assert options["style"] == "scene"
    assert options["size"] == 2048
    for values in [{"size": 5000}, {"size": 1025}, {"style": "studio"}]:
        with pytest.raises(ValueError):
            render_options({"projection": "equirectangular", **values})
    runtime = BlenderRuntime(tmp_path)
    runtime.command({"command": "create", "project_id": "sky"})
    project = runtime.project("sky")
    execute = Mock(return_value={"ok": True})
    monkeypatch.setattr(project, "execute", execute)
    runtime.command({"command": "render", "project_id": "sky", "options": {"projection": "equirectangular", "position": [0,0,1.6]}})
    payload = execute.call_args.args[0]
    assert payload["publish"] is False
    assert "equirectangular" in payload["source"]
    assert "render_view" in payload["source"]


@pytest.mark.asyncio
async def test_blender_panorama_returns_same_origin_skybox_without_external_host(monkeypatch):
    import ngram.ngram_ar.blender_tools as tools
    render_id = "a" * 32
    client = SimpleNamespace(call=AsyncMock(return_value={"ok": True, "state": "ready", "project_id": "sky", "renders": [{"projection": "equirectangular", "render_id": render_id}]}))
    monkeypatch.setattr(tools, "require_tool_runtime", lambda: SimpleNamespace(entity=object(), inp=None))
    monkeypatch.setattr(tools, "get_execution_client", lambda: client)
    monkeypatch.setattr(tools, "connected_spatial_session", lambda *args: SimpleNamespace(shell_slug="test-agent"))
    result = json.loads(await ar_blender("status", {"project_id": "sky"}))
    assert result["skybox"] == {"sky": {"type": "panorama", "format": "image", "url": f"/api/shells/test-agent/blender/sky/renders/{render_id}/view.jpg"}}
    assert client.call.await_count == 1
