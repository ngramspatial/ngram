import json

import pytest

from ngram.models import Input
from ngram.presence.tools.discovery import describe_tool, search_tools
from ngram.presence.tools.registry import ToolRegistry
from ngram.presence.tools.runtime import ToolRuntimeContext, reset_tool_runtime, set_tool_runtime


async def _stub_tool() -> str:
    return json.dumps({"ok": True})


@pytest.fixture
def discovery_entity() -> object:
    reg = ToolRegistry()
    reg.register_fn("fetch_url", "Fetch and read a web URL", _stub_tool)
    reg.register_fn("get_current_time", "Return the current local date and time", _stub_tool)
    reg.register_decorated(search_tools)
    reg.register_decorated(describe_tool)

    class _E:
        tools = reg

    return _E()


def _ctx_token(entity: object):
    inp = Input(text="hi", person_id="1", person_name="You", channel="cli", platform="cli")
    return set_tool_runtime(ToolRuntimeContext(entity=entity, inp=inp, platform=None))


@pytest.mark.asyncio
async def test_search_tools_filters_by_query(discovery_entity: object) -> None:
    tok = _ctx_token(discovery_entity)
    try:
        out = await search_tools(query="url", limit=10)
        data = json.loads(out)
        assert data["ok"] is True
        names = [t["name"] for t in data["tools"]]
        assert "fetch_url" in names
    finally:
        reset_tool_runtime(tok)


@pytest.mark.asyncio
async def test_search_tools_empty_query_lists_tools(discovery_entity: object) -> None:
    tok = _ctx_token(discovery_entity)
    try:
        out = await search_tools(query="", limit=50)
        data = json.loads(out)
        assert data["ok"] is True
        assert data["returned"] >= 2
        names = {t["name"] for t in data["tools"]}
        assert "describe_tool" in names
        assert "search_tools" in names
    finally:
        reset_tool_runtime(tok)


@pytest.mark.asyncio
async def test_describe_tool_returns_schema(discovery_entity: object) -> None:
    tok = _ctx_token(discovery_entity)
    try:
        out = await describe_tool(tool_name="fetch_url")
        data = json.loads(out)
        assert data["ok"] is True
        assert data["tool"]["name"] == "fetch_url"
        assert "parameters" in data["tool"]
    finally:
        reset_tool_runtime(tok)


@pytest.mark.asyncio
async def test_describe_tool_case_insensitive(discovery_entity: object) -> None:
    tok = _ctx_token(discovery_entity)
    try:
        out = await describe_tool(tool_name="FETCH_URL")
        data = json.loads(out)
        assert data["ok"] is True
        assert data["tool"]["name"] == "fetch_url"
    finally:
        reset_tool_runtime(tok)


@pytest.mark.asyncio
async def test_describe_tool_unknown_suggestions(discovery_entity: object) -> None:
    tok = _ctx_token(discovery_entity)
    try:
        out = await describe_tool(tool_name="nope_not_a_tool")
        data = json.loads(out)
        assert data["ok"] is False
        assert "suggestions" in data
    finally:
        reset_tool_runtime(tok)
