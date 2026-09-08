"""Execution client: hybrid must not use the home PC as shell/fs host when RPC is unset."""

from __future__ import annotations

import pytest

from ngram.config import HarnessConfig, entity_from_dict
from ngram.presence.tools import execution_rpc
from ngram.presence.tools.execution_rpc import (
    local_tool_block_message,
    self_update_host_permitted,
    should_fallback_rpc_to_local,
)
from ngram.presence.tools.runtime import ToolRuntimeContext, reset_tool_runtime, set_tool_runtime

_MIN_ENTITY = {
    "name": "ExecClientTest",
    "personality": {
        "core_traits": {k: 0.5 for k in ["curiosity", "warmth", "assertiveness", "humor", "openness", "neuroticism", "conscientiousness"]},
        "behavioral_patterns": {},
        "voice": {},
        "backstory": "",
    },
    "drives": {"curiosity_topics": [], "attachment_threshold": 5, "restlessness_decay": 3600, "initiative_cooldown": 1800},
    "cognition": {},
    "presence": {"platforms": [], "daemon": {}},
}


class _EntityHolder:
    __slots__ = ("config",)

    def __init__(self, config: object) -> None:
        self.config = config


@pytest.fixture(autouse=True)
def _clear_execution_clients() -> None:
    execution_rpc._CLIENTS.clear()
    yield
    execution_rpc._CLIENTS.clear()


@pytest.mark.parametrize(
    ("deployment_mode", "railway_env", "raw_tools", "expect_local"),
    [
        ("hybrid_railway", "", {}, False),
        ("hybrid_railway", "production", {}, True),
        ("cloud", "", {}, False),
        ("cloud", "production", {}, True),
        ("hybrid_railway", "", {"tools": {"execution": {"allow_local": True}}}, True),
        ("local", "", {}, True),
    ],
)
def test_allow_local_backend_hybrid_only_on_railway_or_opt_in(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    deployment_mode: str,
    railway_env: str,
    raw_tools: dict,
    expect_local: bool,
) -> None:
    monkeypatch.delenv("NGRAM_EXECUTION_RPC_URL", raising=False)
    if railway_env:
        monkeypatch.setenv("RAILWAY_ENVIRONMENT", railway_env)
    else:
        monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)

    h = HarnessConfig()
    h.deployment.mode = deployment_mode
    h.memory.database_path = str(tmp_path / "m.db")
    data = {**_MIN_ENTITY, **raw_tools}
    ec = entity_from_dict(h, data)
    holder = _EntityHolder(ec)
    tok = set_tool_runtime(ToolRuntimeContext(entity=holder, inp=None))
    try:
        client = execution_rpc.get_execution_client()
        assert client.allow_local_backend is expect_local
        assert execution_rpc.local_body_host_permitted(holder) is expect_local
    finally:
        reset_tool_runtime(tok)


def test_workspace_root_can_be_pinned_by_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("NGRAM_EXECUTION_RPC_URL", raising=False)
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    workspace = tmp_path / "railway-workspace"
    workspace.mkdir()
    monkeypatch.setenv("NGRAM_EXECUTION_WORKSPACE_DIR", str(workspace))

    h = HarnessConfig()
    h.deployment.mode = "local"
    h.memory.database_path = str(tmp_path / "m.db")
    ec = entity_from_dict(h, _MIN_ENTITY)
    holder = _EntityHolder(ec)
    tok = set_tool_runtime(ToolRuntimeContext(entity=holder, inp=None))
    try:
        client = execution_rpc.get_execution_client()
        assert client.workspace_root == workspace.resolve()
    finally:
        reset_tool_runtime(tok)


@pytest.mark.parametrize(
    ("deployment_mode", "railway_env", "raw_tools", "expect_local"),
    [
        ("local", "", {}, False),
        ("local", "", {"tools": {"execution": {"allow_local": True}}}, False),
        ("hybrid_railway", "", {}, False),
        ("hybrid_railway", "production", {}, True),
    ],
)
def test_require_railway_blocks_off_railway_local_execution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    deployment_mode: str,
    railway_env: str,
    raw_tools: dict,
    expect_local: bool,
) -> None:
    monkeypatch.delenv("NGRAM_EXECUTION_RPC_URL", raising=False)
    monkeypatch.setenv("NGRAM_EXECUTION_REQUIRE_RAILWAY", "true")
    if railway_env:
        monkeypatch.setenv("RAILWAY_ENVIRONMENT", railway_env)
    else:
        monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)

    h = HarnessConfig()
    h.deployment.mode = deployment_mode
    h.memory.database_path = str(tmp_path / "m.db")
    data = {**_MIN_ENTITY, **raw_tools}
    ec = entity_from_dict(h, data)
    holder = _EntityHolder(ec)
    tok = set_tool_runtime(ToolRuntimeContext(entity=holder, inp=None))
    try:
        client = execution_rpc.get_execution_client()
        assert client.allow_local_backend is expect_local
    finally:
        reset_tool_runtime(tok)


@pytest.mark.parametrize(
    ("deployment_mode", "railway_env", "require_railway_env", "expect_self_update"),
    [
        ("hybrid_railway", "", None, True),
        ("hybrid_railway", "", "true", False),
        ("hybrid_railway", "production", "true", True),
        ("local", "", None, True),
        ("local", "", "true", False),
    ],
)
def test_self_update_permits_hybrid_home_without_allow_local(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    deployment_mode: str,
    railway_env: str,
    require_railway_env: str | None,
    expect_self_update: bool,
) -> None:
    monkeypatch.delenv("NGRAM_EXECUTION_RPC_URL", raising=False)
    if require_railway_env:
        monkeypatch.setenv("NGRAM_EXECUTION_REQUIRE_RAILWAY", require_railway_env)
    else:
        monkeypatch.delenv("NGRAM_EXECUTION_REQUIRE_RAILWAY", raising=False)
    if railway_env:
        monkeypatch.setenv("RAILWAY_ENVIRONMENT", railway_env)
    else:
        monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)

    h = HarnessConfig()
    h.deployment.mode = deployment_mode
    h.memory.database_path = str(tmp_path / "m.db")
    ec = entity_from_dict(h, _MIN_ENTITY)
    holder = _EntityHolder(ec)
    assert self_update_host_permitted(holder) is expect_self_update


def test_require_railway_block_message_is_explicit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("NGRAM_EXECUTION_REQUIRE_RAILWAY", "true")
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)

    h = HarnessConfig()
    h.deployment.mode = "local"
    h.memory.database_path = str(tmp_path / "m.db")
    ec = entity_from_dict(h, _MIN_ENTITY)
    holder = _EntityHolder(ec)
    msg = local_tool_block_message(holder)
    assert "locked to Railway" in msg


@pytest.mark.asyncio
async def test_local_write_creates_checkpoint_and_can_roll_back(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("NGRAM_EXECUTION_RPC_URL", raising=False)
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("NGRAM_EXECUTION_WORKSPACE_DIR", str(workspace))

    h = HarnessConfig()
    h.deployment.mode = "local"
    h.memory.database_path = str(tmp_path / "m.db")
    ec = entity_from_dict(h, _MIN_ENTITY)
    holder = _EntityHolder(ec)
    tok = set_tool_runtime(ToolRuntimeContext(entity=holder, inp=None))
    try:
        client = execution_rpc.get_execution_client()
        first = await client.call("write_file", {"path": "note.txt", "content": "first"})
        cp_first = str(first.get("checkpoint_id") or "")
        assert cp_first.startswith("cp_")
        second = await client.call("write_file", {"path": "note.txt", "content": "second"})
        cp_second = str(second.get("checkpoint_id") or "")
        assert cp_second.startswith("cp_")
        rolled = await client.call("rollback_checkpoint", {"checkpoint_id": cp_second})
        assert rolled["ok"] is True
        assert (workspace / "note.txt").read_text(encoding="utf-8") == "first"
        rolled_delete = await client.call("rollback_checkpoint", {"checkpoint_id": cp_first})
        assert rolled_delete["ok"] is True
        assert not (workspace / "note.txt").exists()
        cps = await client.call("list_checkpoints", {"limit": 10})
        assert cps["ok"] is True
        ids = {str(row.get("id") or "") for row in cps["checkpoints"]}
        assert cp_first in ids
        assert cp_second in ids
        env = await client.call("get_execution_context", {})
        assert env["ok"] is True
        assert env["environment"]["workspace_root"] == str(workspace.resolve())
    finally:
        reset_tool_runtime(tok)


@pytest.mark.parametrize(
    ("error", "expect"),
    [
        ("Cannot connect to host example.com port 443", True),
        ("rpc http 503", True),
        ("rpc http 404", False),
        ("rpc http 401", False),
        ("something unknown", False),
    ],
)
def test_should_fallback_rpc_to_local(error: str, expect: bool) -> None:
    assert should_fallback_rpc_to_local({"ok": False, "error": error}) is expect
