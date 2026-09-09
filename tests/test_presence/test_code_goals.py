"""Exercise the real goal scheduler, tool loop, persistence, and lifecycle offline."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from ngram.config import HarnessConfig, entity_from_dict
from ngram.inference.types import ChatCompletionResult, ToolCallSpec
from ngram.models import Input
from ngram.presence.code_goals import CodeTaskManager, _successful_result
from ngram.presence.tools import agency
from ngram.presence.tools.code_task import code_task_session
from ngram.presence.tools.registry import ToolRegistry
from ngram.presence.tools.runtime import (
    ToolRuntimeContext, get_tool_runtime, reset_tool_runtime, set_tool_runtime,
)


def calls(*specs):
    return ChatCompletionResult(content="", tool_calls=[
        ToolCallSpec(name=name, arguments=args, id=f"t{index}")
        for index, (name, args) in enumerate(specs)
    ])


def checkpoint(status="continue", summary="Implemented the parser", next_steps="Run tests", evidence=""):
    return calls(("code_task_checkpoint", dict(
        status=status, summary=summary, next_steps=next_steps, evidence=evidence,
    )), ("end_turn", {}))


class Provider:
    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []
        self.entered = asyncio.Event()
        self.release = None

    async def chat_completion(self, _model, messages, **_kwargs):
        self.prompts.append(messages)
        self.entered.set()
        if self.release is not None:
            await self.release.wait()
        if not self.responses:
            raise AssertionError("unexpected model call")
        return self.responses.pop(0)


class Files:
    def __init__(self):
        self.files = {}

    async def call(self, action, args):
        if action == "read_file":
            if args["path"] in self.files:
                return {"ok": True, "content": self.files[args["path"]]}
            return {"ok": False, "error": "path not found"}
        assert action == "write_file"
        self.files[args["path"]] = args["content"]
        return {"ok": True}


def setup(tmp_path, responses):
    entity = SimpleNamespace(
        config=entity_from_dict(HarnessConfig(), {"name": "CodingTest"}),
        tools=ToolRegistry(), client=Provider(responses), inference_paused=False,
        current_platform=object(), _platforms={}, _turn_lock=asyncio.Lock(),
    )
    entity.config.cognition.history_compression.enabled = False
    entity.tools.register_decorated(agency.think)
    entity.tools.register_decorated(agency.end_turn)
    operations = []

    async def run_command(command: str):
        ctx = get_tool_runtime()
        assert ctx.entity is entity and ctx.inp.platform == "code_task"
        operations.append(command)
        return json.dumps({"ok": True, "exit_code": 1 if command == "fail" else 0, "stdout": command})

    async def write_file(path: str, content: str):
        operations.append(path)
        return json.dumps({"ok": True})

    entity.tools.register_fn("run_command", "Run command", run_command)
    entity.tools.register_fn("write_file", "Write file", write_file)
    files = Files()
    manager = CodeTaskManager(entity, root=tmp_path / "goals", client=files)
    entity._code_task_manager = manager
    return entity, manager, files, operations


async def settle(manager):
    tasks = list(manager.tasks.values())
    await asyncio.wait_for(asyncio.gather(*tasks), 3)
    await asyncio.sleep(0)


def request():
    return Input(text="Fix the parser in repo; preserve other edits", person_id="u", person_name="You", channel="browser", platform="ngram_ar")


@pytest.mark.asyncio
async def test_launch_under_parent_lock_survives_parent_cancellation_and_verifies(tmp_path):
    entity, manager, files, operations = setup(tmp_path, [
        calls(("run_command", {"command": "tests pass"})),
        checkpoint("complete", evidence="e1"),
        calls(("run_command", {"command": "review diff and tests"})),
        checkpoint("complete", summary="All criteria verified", next_steps="", evidence="e2"),
    ])
    entity.client.release = asyncio.Event()
    launched = asyncio.Future()
    original_registry, original_platform = entity.tools, entity.current_platform

    async def parent():
        async with entity._turn_lock:
            token = set_tool_runtime(ToolRuntimeContext(entity=entity, inp=request()))
            try:
                launched.set_result(json.loads(await code_task_session("Fix parser")))
                await asyncio.Event().wait()
            finally:
                reset_tool_runtime(token)

    caller = asyncio.create_task(parent())
    receipt = await asyncio.wait_for(launched, 1)
    assert receipt["status"] == "queued"
    await asyncio.wait_for(entity.client.entered.wait(), 1)
    caller.cancel()
    await asyncio.gather(caller, return_exceptions=True)
    # Chat is independent while the coding worker is in inference.
    async with entity._turn_lock:
        assert entity.tools is original_registry
        assert entity.current_platform is original_platform
    assert get_tool_runtime() is None
    entity.client.release.set()
    await settle(manager)
    state = manager.status(receipt["task_id"])
    assert state["status"] == "complete" and state["phase"] == 2
    assert operations == ["tests pass", "review diff and tests"]
    assert "complete" in files.files[receipt["task_record"]]
    assert "VERIFICATION" in entity.client.prompts[2][1]["content"]


@pytest.mark.asyncio
async def test_failed_test_and_fabricated_evidence_cannot_finish_goal(tmp_path):
    entity, manager, _, _ = setup(tmp_path, [
        calls(("run_command", {"command": "fail"})),
        checkpoint("complete", evidence="e1"),
        checkpoint("complete", evidence="invented"),
        checkpoint("continue", summary="Test failing", next_steps="Repair parser"),
    ])
    receipt = await manager.submit("Fix parser", request(), max_phases=1)
    await settle(manager)
    state = manager.status(receipt["task_id"])
    assert state["status"] == "paused" and state["mode"] == "work"
    assert state["receipts"][0]["success"] is False
    assert state["next_steps"] == "Repair parser"
    assert len(entity.client.prompts) == 4


@pytest.mark.asyncio
async def test_verifier_requires_fresh_evidence_and_can_return_fixes(tmp_path):
    entity, manager, _, _ = setup(tmp_path, [
        calls(("run_command", {"command": "tests"})), checkpoint("complete", evidence="e1"),
        checkpoint("complete", evidence="e1"),
        calls(("run_command", {"command": "fail"})),
        checkpoint("continue", summary="Found missing edge case", next_steps="Fix empty input"),
    ])
    receipt = await manager.submit("Fix parser", request(), max_phases=2)
    await settle(manager)
    state = manager.status(receipt["task_id"])
    assert state["status"] == "paused" and state["mode"] == "work"
    assert state["next_steps"] == "Fix empty input"
    assert len(entity.client.prompts) == 5


@pytest.mark.asyncio
async def test_loops_beyond_old_eight_phases_and_keeps_original_goal(tmp_path):
    responses = []
    for index in range(10):
        responses += [calls(("run_command", {"command": f"step {index}"})), checkpoint()]
    entity, manager, _, _ = setup(tmp_path, responses)
    receipt = await manager.submit("Fix parser", request(), max_phases=10, success_criteria="Empty input is accepted")
    await settle(manager)
    state = manager.status(receipt["task_id"])
    assert state["phase"] == 10 and state["status"] == "paused"
    assert all("Empty input is accepted" in prompt[1]["content"] for prompt in entity.client.prompts)


@pytest.mark.asyncio
async def test_resume_keeps_record_and_does_not_overwrite_legacy_files(tmp_path):
    entity, manager, files, _ = setup(tmp_path, [checkpoint()])
    files.files["code_tasks/legacy.md"] = "existing user notes"
    refused = await manager.submit("Legacy", request(), task_record_path="legacy.md")
    assert refused["ok"] is False
    assert files.files["code_tasks/legacy.md"] == "existing user notes"
    receipt = await manager.submit("Fix parser", request(), max_phases=1)
    await settle(manager)
    entity.client.responses = [calls(("run_command", {"command": "repair"})), checkpoint()]
    await manager.resume(receipt["task_id"], "Also handle tabs", 60)
    await settle(manager)
    state = manager.status(receipt["task_id"])
    assert state["phase"] == 2 and len(state["phase_log"]) == 2
    assert state["task_record"] == receipt["task_record"]
    assert state["amendments"] == ["Also handle tabs"]


@pytest.mark.asyncio
async def test_duplicate_launches_share_a_job_and_goals_are_serialized(tmp_path):
    entity, manager, _, _ = setup(tmp_path, [checkpoint(), checkpoint()])
    entity.client.release = asyncio.Event()
    first, duplicate = await asyncio.gather(
        manager.submit("one", request(), max_phases=1), manager.submit("one", request(), max_phases=1),
    )
    second = await manager.submit("two", request(), max_phases=1)
    await entity.client.entered.wait()
    assert first["task_id"] == duplicate["task_id"]
    assert manager.status(second["task_id"])["status"] == "queued"
    assert len(entity.client.prompts) == 1
    entity.client.release.set()
    await settle(manager)
    assert len(entity.client.prompts) == 2


@pytest.mark.asyncio
async def test_shutdown_and_restart_recovers_without_inheriting_browser_state(tmp_path):
    entity, manager, files, _ = setup(tmp_path, [])
    entity.client.release = asyncio.Event()
    receipt = await manager.submit("Fix parser", request())
    await entity.client.entered.wait()
    await manager.shutdown()
    assert manager.status(receipt["task_id"])["status"] == "queued"
    entity.client = Provider([checkpoint("blocked", next_steps="Need fixture") for _ in range(3)])
    restored = CodeTaskManager(entity, root=manager.root, client=files)
    restored.start()
    await settle(restored)
    state = restored.status(receipt["task_id"])
    assert state["status"] == "blocked" and state["blocker_count"] == 3
    assert "Worker restarted" in entity.client.prompts[0][1]["content"]


@pytest.mark.asyncio
async def test_inference_pause_does_not_spin_or_report_completion(tmp_path):
    entity, manager, _, _ = setup(tmp_path, [])
    entity.inference_paused = True
    receipt = await manager.submit("Fix parser", request())
    await settle(manager)
    assert manager.status(receipt["task_id"])["status"] == "paused"
    assert not entity.client.prompts


@pytest.mark.asyncio
async def test_brief_pause_during_tool_requires_explicit_goal_resume(tmp_path):
    entity, manager, _, _ = setup(tmp_path, [calls(("run_command", {"command": "slow"}))])
    entered, release = asyncio.Event(), asyncio.Event()

    async def run_command(command: str):
        entered.set()
        await release.wait()
        return '{"ok": true, "exit_code": 0}'

    entity.tools.register_fn("run_command", "Run", run_command)
    receipt = await manager.submit("Fix parser", request())
    await entered.wait()
    entity.inference_paused = True
    manager.pause_active()
    entity.inference_paused = False
    release.set()
    await settle(manager)
    assert manager.status(receipt["task_id"])["status"] == "paused"
    assert len(entity.client.prompts) == 1


@pytest.mark.asyncio
async def test_cancel_during_tool_finishes_that_tool_and_schedules_no_more(tmp_path):
    entity, manager, _, _ = setup(tmp_path, [
        calls(("run_command", {"command": "slow"}), ("run_command", {"command": "must not run"})),
    ])
    entered, release = asyncio.Event(), asyncio.Event()
    operations = []

    async def run_command(command: str):
        operations.append(command)
        entered.set()
        await release.wait()
        return '{"ok": true, "exit_code": 0}'

    entity.tools.register_fn("run_command", "Run", run_command)
    receipt = await manager.submit("Fix parser", request())
    await asyncio.wait_for(entered.wait(), 1)
    stopped = await manager.cancel(receipt["task_id"])
    assert stopped["status"] == "cancelling"
    release.set()
    await settle(manager)
    assert operations == ["slow"]
    assert manager.status(receipt["task_id"])["status"] == "cancelled"


@pytest.mark.asyncio
async def test_immediate_cancel_is_terminal_even_before_runner_starts(tmp_path):
    _, manager, _, _ = setup(tmp_path, [])
    receipt = await manager.submit("Fix parser", request())
    await manager.cancel(receipt["task_id"])
    await asyncio.gather(*manager.tasks.values(), return_exceptions=True)
    await asyncio.sleep(0)
    assert manager.status(receipt["task_id"])["status"] == "cancelled"


@pytest.mark.asyncio
async def test_no_progress_pauses_after_three_phases(tmp_path):
    _, manager, _, _ = setup(tmp_path, [checkpoint() for _ in range(3)])
    receipt = await manager.submit("Fix parser", request())
    await settle(manager)
    state = manager.status(receipt["task_id"])
    assert state["status"] == "paused" and state["phase"] == 3


@pytest.mark.asyncio
async def test_timeout_preserves_unknown_tool_outcome_for_recovery(tmp_path):
    entity, manager, _, _ = setup(tmp_path, [calls(("run_command", {"command": "slow"}))])

    async def run_command(command: str):
        await asyncio.Event().wait()

    entity.tools.register_fn("run_command", "Run", run_command)
    receipt = await manager.submit("Fix parser", request())
    manager.records[receipt["task_id"]]["remaining_seconds"] = 0.02
    await settle(manager)
    state = manager.status(receipt["task_id"])
    assert state["status"] == "paused" and state["remaining_seconds"] == 0
    assert state["in_flight"]["arguments"] == {"command": "slow"}


@pytest.mark.asyncio
async def test_markdown_cannot_mark_complete_and_forbidden_tools_cannot_escape_subset(tmp_path):
    entity, manager, files, _ = setup(tmp_path, [
        calls(("delegate_task", {"objective": "escape"})), checkpoint(),
    ])

    async def forbidden(objective: str):
        raise AssertionError("parent tool must not execute")

    entity.tools.register_fn("delegate_task", "Delegate", forbidden)
    receipt = await manager.submit("Fix parser", request(), max_phases=1)
    files.files[receipt["task_record"]] = "## Status\nCOMPLETE"
    await settle(manager)
    assert manager.status(receipt["task_id"])["status"] == "paused"


@pytest.mark.asyncio
async def test_actual_files_and_subprocess_validation_across_two_phases(tmp_path, monkeypatch):
    import sys

    from ngram.presence.tools import filesystem, shell
    from ngram.presence.tools.execution_rpc import ExecutionRPCClient

    command = f'"{sys.executable}" -c "import parser; assert parser.parse(\'\') == []; assert parser.parse(\'1,2\') == [1,2]"'
    entity, manager, _, _ = setup(tmp_path, [
        calls(("write_file", {"path": "parser.py", "content": "def parse(text):\n    return [int(value) for value in text.split(',')] if text else []\n"})),
        calls(("run_command", {"command": command})),
        checkpoint("complete", evidence="e2"),
        calls(("read_file", {"path": "parser.py"}), ("run_command", {"command": command})),
        checkpoint("complete", evidence="e3,e4", next_steps=""),
    ])
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = ExecutionRPCClient("", "/rpc", "", 10, True, workspace)
    monkeypatch.setattr(shell, "get_execution_client", lambda: client)
    monkeypatch.setattr(filesystem, "get_execution_client", lambda: client)
    monkeypatch.setattr(filesystem, "read_only_workspace_fs_allowed", lambda _entity: True)
    for fn in (shell.run_command, filesystem.write_file, filesystem.read_file):
        entity.tools.register_decorated(fn)
    manager.client = client
    receipt = await manager.submit("Fix parser", request())
    await settle(manager)
    state = manager.status(receipt["task_id"])
    assert state["status"] == "complete"
    assert (workspace / "parser.py").is_file()
    assert state["receipts"][-1]["success"] is True
    assert (workspace / receipt["task_record"]).is_file()


@pytest.mark.asyncio
async def test_crash_recovery_retains_pending_action_and_accounts_downtime(tmp_path):
    import time

    entity, manager, files, _ = setup(tmp_path, [checkpoint()])
    entity.inference_paused = True
    receipt = await manager.submit("Fix parser", request(), max_phases=2)
    await settle(manager)
    record = manager.records[receipt["task_id"]]
    record.update(status="running", phase_deadline=time.time() + 20, remaining_seconds=100)
    record["in_flight"] = {"tool": "run_command", "arguments": {"command": "possibly committed"}}
    manager._save(record)
    entity.inference_paused = False
    restored = CodeTaskManager(entity, root=manager.root, client=files)
    restored.start()
    assert restored.records[receipt["task_id"]]["remaining_seconds"] <= 20
    # Keep this recovery test to one new phase.
    restored.records[receipt["task_id"]]["max_phases"] = 1
    await settle(restored)
    assert "possibly committed" in entity.client.prompts[0][1]["content"]
    assert "before retrying" in entity.client.prompts[0][0]["content"]


@pytest.mark.asyncio
async def test_failed_progress_delivery_cannot_abort_goal(tmp_path):
    entity, manager, _, _ = setup(tmp_path, [
        calls(("say", {"message": "Working on the parser"})), checkpoint(),
    ])

    async def disconnected(_channel, _message):
        raise ConnectionError("offline")

    entity._platforms["telegram"] = SimpleNamespace(send_message=disconnected)
    inp = request()
    inp.platform = "telegram"
    receipt = await manager.submit("Fix parser", inp, max_phases=1)
    await settle(manager)
    assert manager.status(receipt["task_id"])["status"] == "paused"


@pytest.mark.asyncio
async def test_spatial_goal_say_delivers_each_update_and_completion_to_its_body(tmp_path):
    from ngram.ngram_ar.spatial_sessions import SpatialSession, SpatialSessions

    entity, manager, files, _ = setup(tmp_path, [
        calls(("say", {"message": "First screen rendered."})),
        calls(("say", {"message": "First screen rendered."})),
        calls(("say", {"message": "Checking the second screen."})),
        calls(("run_command", {"command": "screens pass"})),
        checkpoint("complete", summary="Screens verified", evidence="e1"),
        calls(("run_command", {"command": "fresh review"})),
        checkpoint("complete", summary="Screens verified", next_steps="", evidence="e2"),
    ])
    sent = []

    async def send(actions):
        action = actions[0]
        sent.append(action)
        session.acknowledge({"completedActionId": action["actionId"], "status": "accepted"})

    session = SpatialSession("browser", send, dict, "rook")
    entity._ngram_ar_sessions = SpatialSessions()
    entity._ngram_ar_sessions.register(session)
    receipt = await manager.submit("Verify screens", request())
    await settle(manager)
    assert manager.status(receipt["task_id"])["status"] == "complete"
    assert [a["text"] for a in sent[:2]] == ["First screen rendered.", "Checking the second screen."]
    assert len(sent) == 3 and sent[2]["text"].startswith("Coding goal complete.")
    assert all(a["type"] == "action:speak" and a["sessionId"] == "browser" for a in sent)
    assert "accepted" in manager.status(receipt["task_id"])["last_spatial_delivery"]
    assert "Checking the second screen." in json.dumps(entity.client.prompts)
    assert "completion is not confirmed" in json.dumps(entity.client.prompts)
    assert "Screens verified" in files.files[receipt["task_record"]]


@pytest.mark.asyncio
async def test_spatial_progress_rebinds_safely_without_replaying_uncertain_speech(tmp_path):
    from ngram.ngram_ar.spatial_sessions import SpatialSession, SpatialSessions

    entity, manager, _, _ = setup(tmp_path, [checkpoint()])
    receipt = await manager.submit("Verify screens", request(), max_phases=1)
    await settle(manager)
    record = manager.records[receipt["task_id"]]
    record["spatial_route"] = {"session_id": "browser", "shell_slug": "rook"}
    entity._ngram_ar_sessions = SpatialSessions()
    sent = []

    async def disconnected(actions):
        sent.extend(actions)
        raise ConnectionError("lost socket after write")

    entity._ngram_ar_sessions.register(SpatialSession("unrelated", disconnected, dict, "other"))
    assert "not sent" in await manager._notify(record, "Screen rendered.")
    assert sent == []
    entity._ngram_ar_sessions.register(SpatialSession("refresh", disconnected, dict, "rook"))
    assert "execution unknown" in await manager._notify(record, "Screen rendered.")
    assert "duplicate" in await manager._notify(record, "Screen rendered.")
    assert len(sent) == 1 and sent[0]["sessionId"] == "refresh"
    entity._ngram_ar_sessions.register(SpatialSession("another-tab", disconnected, dict, "rook"))
    assert "ambiguous" in await manager._notify(record, "Next screen rendered.")
    assert len(sent) == 1


def test_goal_numeric_parameters_have_numeric_tool_schemas():
    registry = ToolRegistry()
    registry.register_decorated(code_task_session)
    schema = registry.tool_discovery_detail("code_task_session")["parameters"]
    assert schema["properties"]["max_runtime_seconds"]["type"] == "integer"
    assert schema["properties"]["steps_per_phase"]["type"] == "integer"


@pytest.mark.asyncio
async def test_spatial_goal_keeps_authoring_tools_and_images_across_real_tool_rounds(tmp_path):
    import base64
    from ngram.inference.visual_results import VisualResult
    from ngram.ngram_ar.spatial_sessions import SpatialSession, SpatialSessions
    from ngram.ngram_ar.spatial_tools import register_ngram_ar_spatial_tools

    entity, manager, _, _ = setup(tmp_path, [
        calls(("ar_world", {"command": "capabilities"})),
        calls(("ar_request_capture", {"options": {"target": "tablet"}})),
        checkpoint("complete", evidence="e2"),
        calls(("ar_request_capture", {"options": {"target": "tablet"}})),
        checkpoint("complete", evidence="e3"),
    ])
    register_ngram_ar_spatial_tools(entity.tools)
    image = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff" + b"image" * 20).decode()
    dispatched = []

    async def send(actions):
        action = actions[0]
        dispatched.append(action)
        result = {"ok": True, "images": [{"url": image, "label": "Tablet in room"}]} if action["type"] == "action:request_capture" else {"ok": True}
        session.acknowledge({"completedActionId": action["actionId"], "status": "completed", "result": result})

    session = SpatialSession("browser", send, dict, "test-agent")
    entity._ngram_ar_sessions = SpatialSessions()
    entity._ngram_ar_sessions.register(session)
    receipt = await manager.submit("Verify the tablet in Spatial", request())
    await settle(manager)
    state = manager.status(receipt["task_id"])
    assert state["status"] == "complete" and state["phase"] == 2
    assert [a["type"] for a in dispatched] == ["action:world", "action:request_capture", "action:request_capture", "action:speak"]
    visual = [m["content"] for m in entity.client.prompts[2] if isinstance(m.get("content"), VisualResult)]
    assert len(visual) == 1 and visual[0].images[0]["url"] == image
    assert "Evidence ID: e2" in visual[0]
    assert image not in (manager.root / f"{receipt['task_id']}.json").read_text()
    assert state["spatial_route"] == {"session_id": "browser", "shell_slug": "test-agent"}


def test_goal_session_rebinds_only_to_an_unambiguous_replacement_of_its_own_shell():
    from ngram.ngram_ar.spatial_sessions import SpatialSession, SpatialSessions, connected_spatial_session
    entity = SimpleNamespace(_ngram_ar_sessions=SpatialSessions())
    inp = Input(text="", person_id="u", person_name="You", platform="code_task",
                metadata={"code_task_spatial": {"session_id": "original", "shell_slug": "rook"}})
    unrelated = SpatialSession("other", None, dict, "other-agent")
    entity._ngram_ar_sessions.register(unrelated)
    assert connected_spatial_session(entity, inp) is None
    replacement = SpatialSession("refresh", None, dict, "rook")
    entity._ngram_ar_sessions.register(replacement)
    assert connected_spatial_session(entity, inp) is replacement
    duplicate = SpatialSession("second-tab", None, dict, "rook")
    entity._ngram_ar_sessions.register(duplicate)
    assert connected_spatial_session(entity, inp) is None
    original = SpatialSession("original", None, dict, "rook")
    entity._ngram_ar_sessions.register(original)
    assert connected_spatial_session(entity, inp) is original


@pytest.mark.parametrize("result", [
    {"status": "accepted"}, {"status": "failed", "result": {}},
    {"status": "completed", "result": {"ok": False, "error": "missing object"}},
    {"ok": True, "state": "working"}, {"ok": True, "state": "failed"},
])
def test_unfinished_or_failed_spatial_actions_are_not_completion_evidence(result):
    assert not _successful_result(json.dumps(result))


@pytest.mark.asyncio
async def test_goal_progress_includes_summary_and_terminal_reason_without_another_model_call(tmp_path):
    entity, manager, _, _ = setup(tmp_path, [
        calls(("say", {"message": "Rendered the first screen; verifying readability next."})),
        checkpoint("continue", summary="Screen rendered", next_steps="Inspect in the room"),
    ])
    reports = []

    async def emit(phase, inp, *, work):
        assert inp.person_id == request().person_id
        reports.append(work)

    entity._emit_turn_activity = emit
    receipt = await manager.submit("Build the tablet", request(), max_phases=1)
    await settle(manager)
    assert any(r["summary"].startswith("Rendered the first screen") for r in reports)
    assert reports[-1]["status"] == "paused"
    assert "budget" in reports[-1]["reason"].lower()
    assert reports[-1]["nextSteps"] == "Inspect in the room"
    assert len(entity.client.prompts) == 2
    assert manager.status(receipt["task_id"])["status"] == "paused"


@pytest.mark.asyncio
async def test_unexpected_runner_failure_cannot_leave_a_dead_task_marked_running(tmp_path):
    _, manager, _, _ = setup(tmp_path, [])
    record = {"task_id": "dead", "status": "running"}
    manager.records["dead"] = record

    async def fail():
        raise RuntimeError("runner failed before goal cleanup")

    task = asyncio.create_task(fail())
    await asyncio.gather(task, return_exceptions=True)
    manager._finished("dead", task)
    assert record["status"] == "failed"
    assert "resume" in record["reason"]


@pytest.mark.asyncio
async def test_cancelled_runner_before_start_cannot_leave_a_goal_queued_forever(tmp_path):
    _, manager, _, _ = setup(tmp_path, [])
    record = {"task_id": "cancelled", "status": "queued"}
    manager.records["cancelled"] = record
    task = asyncio.create_task(asyncio.sleep(1))
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    manager._finished("cancelled", task)
    assert record["status"] == "paused"


@pytest.mark.asyncio
async def test_http_controls_authenticate_and_use_the_same_durable_goal(tmp_path):
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from ngram.ngram_ar.code_task_routes import register_code_task_routes

    entity, manager, _, _ = setup(tmp_path, [])
    entity.client.release = asyncio.Event()
    receipt = await manager.submit("Fix parser", request())
    app = web.Application()
    app["entity"] = entity
    register_code_task_routes(app, lambda req: req.headers.get("Authorization") == "Bearer test-only")
    headers = {"Authorization": "Bearer test-only"}
    async with TestClient(TestServer(app)) as client:
        path = f"/code-tasks/{receipt['task_id']}"
        assert (await client.get(path)).status == 403
        response = await client.get(path, headers=headers)
        assert response.headers["Cache-Control"] == "no-store"
        assert (await response.json())["task_id"] == receipt["task_id"]
        assert (await client.post(path + "/cancel")).status == 403
        assert (await client.post(path + "/cancel", headers=headers)).status == 200
        await settle(manager)
        assert (await (await client.get(path, headers=headers)).json())["status"] == "cancelled"
        assert (await client.post(path + "/resume", headers=headers, json=[])).status == 400
        entity.client.release.set()
        entity.client.responses = [checkpoint()]
        manager.records[receipt["task_id"]]["max_phases"] = 1
        assert (await client.post(path + "/resume", headers=headers, json={"instructions": "Handle tabs"})).status == 200
        await settle(manager)
        assert manager.status(receipt["task_id"])["amendments"] == ["Handle tabs"]
