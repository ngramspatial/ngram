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
        self.tool_sets = []
        self.entered = asyncio.Event()
        self.release = None

    async def chat_completion(self, _model, messages, **_kwargs):
        self.prompts.append(messages)
        self.tool_sets.append({t["function"]["name"]: t["function"] for t in _kwargs.get("tools", [])})
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


def transition(name, **args):
    return calls((name, args))


@pytest.mark.asyncio
async def test_explicit_work_review_fix_review_completion_without_extra_end_turn_calls(tmp_path):
    entity, manager, _, operations = setup(tmp_path, [
        calls(("run_command", {"command": "implementation tests"})),
        transition("goal_submit_for_verification", summary="Parser implemented", evidence=["e1"]),
        calls(("run_command", {"command": "fail"})),
        transition("goal_request_changes", summary="Empty input fails", next_steps="Handle empty input"),
        calls(("run_command", {"command": "fixed tests"})),
        transition("goal_submit_for_verification", summary="Empty input fixed", evidence=["e3"]),
        calls(("run_command", {"command": "review current parser"})),
        transition("goal_complete", summary="Criteria verified", evidence=["e4"], message="Parser fixed and verified."),
    ])
    receipt = await manager.submit("Fix parser", request())
    await settle(manager)
    state = manager.status(receipt["task_id"])
    assert state["status"] == "complete" and state["phase"] == 4
    assert operations == ["implementation tests", "fail", "fixed tests", "review current parser"]
    assert len(entity.client.prompts) == 8, "validated transitions must not spend a model call on end_turn"
    assert "goal_complete" not in entity.client.tool_sets[0]
    assert "goal_submit_for_verification" not in entity.client.tool_sets[2]
    assert "write_file" not in entity.client.tool_sets[2]
    assert all("say" not in tools and "code_task_checkpoint" not in tools for tools in entity.client.tool_sets)
    assert "use say() to share findings" not in json.dumps(entity.client.prompts)
    assert state["final_message"] == "Parser fixed and verified."


@pytest.mark.asyncio
async def test_verification_handoff_preserves_mode_notes_and_requires_fresh_evidence(tmp_path):
    entity, manager, _, _ = setup(tmp_path, [
        calls(("run_command", {"command": "implementation checked"})),
        transition("goal_submit_for_verification", summary="Ready", evidence=["e1"], notes="Do not rebuild; inspect parser.py", artifacts=["parser.py"]),
        transition("goal_complete", summary="Premature", evidence=["e1"]),
        calls(("run_command", {"command": "review first criterion"})),
        transition("goal_checkpoint", summary="First criterion verified", next_steps="Inspect empty input", notes="First criterion passed; empty input remains", evidence=["e2"]),
        calls(("run_command", {"command": "review empty input"})),
        transition("goal_complete", summary="Both criteria verified", evidence=["e2", "e3"]),
    ])
    receipt = await manager.submit("Fix parser", request())
    await settle(manager)
    state = manager.status(receipt["task_id"])
    assert state["status"] == "complete" and state["phase"] == 3
    context = json.loads(entity.client.prompts[5][1]["content"].split("\n", 1)[1])
    assert context["working_notes"] == "First criterion passed; empty input remains"
    assert context["artifacts"] == ["parser.py"]
    assert "goal_complete" in entity.client.tool_sets[5]
    assert "goal_request_changes" in entity.client.tool_sets[5]


@pytest.mark.asyncio
async def test_transition_fences_later_mutations_in_same_model_batch(tmp_path):
    _, manager, _, operations = setup(tmp_path, [
        calls(("run_command", {"command": "tests"})),
        calls(("goal_submit_for_verification", {"summary": "Ready", "evidence": ["e1"]}),
              ("write_file", {"path": "must-not-write", "content": "bad"})),
        calls(("run_command", {"command": "review"})),
        transition("goal_complete", summary="Verified", evidence=["e2"]),
    ])
    receipt = await manager.submit("Fix parser", request())
    await settle(manager)
    assert manager.status(receipt["task_id"])["status"] == "complete"
    assert operations == ["tests", "review"]


@pytest.mark.asyncio
async def test_repeated_timer_observations_do_not_reset_progress_guard(tmp_path):
    responses = []
    for _ in range(4):
        responses += [calls(("run_command", {"command": "inspect timer"})),
                      transition("goal_checkpoint", summary="Still inspecting", next_steps="Inspect timer again")]
    entity, manager, _, _ = setup(tmp_path, responses)
    sequence = 0

    async def timer(command: str):
        nonlocal sequence
        sequence += 1
        return json.dumps({"ok": True, "exit_code": 0, "stdout": f"timer={sequence}"})

    entity.tools.register_fn("run_command", "Inspect", timer)
    receipt = await manager.submit("Fix parser", request())
    await settle(manager)
    state = manager.status(receipt["task_id"])
    assert state["status"] == "paused" and state["phase"] == 4
    assert len(entity.client.prompts) == 8
    assert state["stalled_phases"] == 3
    assert "repeated observations" in state["reason"]


@pytest.mark.asyncio
async def test_reassurance_only_loop_stops_within_one_phase(tmp_path):
    entity, manager, _, _ = setup(tmp_path, [
        transition("goal_progress", message=message)
        for message in ["Checking now", "Still checking", "Continuing the check"]
    ])
    receipt = await manager.submit("Fix parser", request())
    await settle(manager)
    state = manager.status(receipt["task_id"])
    assert state["status"] == "paused" and state["phase"] == 1
    assert len(entity.client.prompts) == 3 and state["sequence"] == 0
    assert "repeated status" in state["reason"]


@pytest.mark.asyncio
async def test_repeated_tool_polling_stops_within_one_phase(tmp_path):
    entity, manager, _, _ = setup(tmp_path, [
        calls(("run_command", {"command": "inspect clock"})) for _ in range(7)
    ])
    tick = 0

    async def clock(command: str):
        nonlocal tick
        tick += 1
        return json.dumps({"exit_code": 0, "stdout": f"clock={tick}"})

    entity.tools.register_fn("run_command", "Inspect clock", clock)
    receipt = await manager.submit("Fix parser", request(), steps_per_phase=64)
    await settle(manager)
    state = manager.status(receipt["task_id"])
    assert state["status"] == "paused" and state["phase"] == 1
    assert len(entity.client.prompts) == 7
    assert "Six tool rounds repeated" in state["reason"]


@pytest.mark.asyncio
async def test_changed_tool_outcome_resets_polling_guard(tmp_path):
    responses = [calls(("run_command", {"command": "inspect test"})) for _ in range(7)]
    responses += [transition("goal_submit_for_verification", summary="Tests pass", evidence=["e7"]),
                  calls(("run_command", {"command": "review"})),
                  transition("goal_complete", summary="Verified", evidence=["e8"])]
    entity, manager, _, _ = setup(tmp_path, responses)
    tick = 0

    async def test_result(command: str):
        nonlocal tick
        tick += 1
        return json.dumps({"exit_code": 1 if tick < 7 else 0})

    entity.tools.register_fn("run_command", "Inspect tests", test_result)
    receipt = await manager.submit("Fix parser", request())
    await settle(manager)
    assert manager.status(receipt["task_id"])["status"] == "complete"


@pytest.mark.asyncio
async def test_steering_during_inference_fences_stale_response_and_preserves_context(tmp_path):
    entity, manager, _, operations = setup(tmp_path, [
        calls(("write_file", {"path": "stale.txt", "content": "old instructions"})),
        transition("goal_checkpoint", summary="Applied guidance", next_steps="Verify new constraint", notes="Keep the existing tablet"),
    ])
    entity.client.release = asyncio.Event()
    receipt = await manager.submit("Fix parser", request(), max_phases=2)
    await entity.client.entered.wait()
    answer = await manager.steer(receipt["task_id"], "Preserve the tablet; tokens on chain 4663 only")
    assert answer["ok"]
    entity.client.release.set()
    await settle(manager)
    assert operations == []
    assert "tokens on chain 4663 only" in entity.client.prompts[1][1]["content"]
    state = manager.status(receipt["task_id"])
    assert state["status"] == "paused" and state["working_notes"] == "Keep the existing tablet"
    assert state["task_record"] == receipt["task_record"]


@pytest.mark.asyncio
async def test_steering_during_tool_finishes_it_but_fences_rest_of_batch(tmp_path):
    entity, manager, _, operations = setup(tmp_path, [
        calls(("run_command", {"command": "started"}), ("run_command", {"command": "stale second command"})),
        transition("goal_checkpoint", summary="Guidance applied", next_steps="Continue safely"),
    ])
    entered, release = asyncio.Event(), asyncio.Event()

    async def command(command: str):
        operations.append(command)
        entered.set()
        await release.wait()
        return '{"ok":true,"exit_code":0}'

    entity.tools.register_fn("run_command", "Run", command)
    receipt = await manager.submit("Fix parser", request(), max_phases=2)
    await entered.wait()
    await manager.steer(receipt["task_id"], "New constraint: no further edits")
    release.set()
    await settle(manager)
    state = manager.status(receipt["task_id"])
    assert operations == ["started"]
    assert state["in_flight"] is None and state["sequence"] == 1
    assert "New constraint" in entity.client.prompts[1][1]["content"]


@pytest.mark.asyncio
async def test_evidence_survives_recent_window_eviction_and_restart(tmp_path):
    responses = [calls(*[("run_command", {"command": f"inspect artifact {i}"}) for i in range(85)]),
                 transition("goal_checkpoint", summary="Artifacts inspected", next_steps="Review first artifact", evidence=["e1"])]
    entity, manager, files, _ = setup(tmp_path, responses)
    receipt = await manager.submit("Inspect artifacts", request(), max_phases=1)
    await settle(manager)
    record = manager.records[receipt["task_id"]]
    assert len(record["receipts"]) == 80 and record["receipts"][0]["id"] == "e6"
    restored = CodeTaskManager(entity, root=manager.root, client=files)
    restored.start()
    assert restored.evidence(restored.records[receipt["task_id"]], "e1")["result"].find("artifact 0") >= 0
    with pytest.raises(ValueError):
        restored.evidence(record, "../../secret")
    entity.client.responses = [
        transition("goal_read_evidence", ids=["e1"]),
        transition("goal_submit_for_verification", summary="Ready", evidence=["e1"]),
    ]
    await restored.resume(receipt["task_id"])
    await settle(restored)
    assert restored.status(receipt["task_id"])["mode"] == "verify"
    assert "artifact 0" in json.dumps(entity.client.prompts[-1])


@pytest.mark.asyncio
async def test_recovery_applies_saved_submit_without_repeating_implementation(tmp_path):
    from ngram.presence.code_goal_phase import GoalPhase
    from ngram.presence.code_goals import _ALLOW

    entity, manager, files, operations = setup(tmp_path, [
        calls(("run_command", {"command": "implementation checked"})), checkpoint(),
    ])
    receipt = await manager.submit("Fix parser", request(), max_phases=1)
    await settle(manager)
    record = manager.records[receipt["task_id"]]
    record.update(status="running", max_phases=0, phase=2)
    phase = GoalPhase(manager, record, _ALLOW)
    assert json.loads(phase.transition("submit", "Implementation ready", evidence=["e1"]))["ok"]
    entity.client.responses = [calls(("run_command", {"command": "fresh verification"})),
                               transition("goal_complete", summary="Verified", evidence=["e2"])]
    restored = CodeTaskManager(entity, root=manager.root, client=files)
    restored.start()
    await settle(restored)
    state = restored.status(receipt["task_id"])
    assert state["status"] == "complete" and state["phase"] == 3
    assert operations == ["implementation checked", "fresh verification"]
    assert "VERIFICATION" in entity.client.prompts[-2][1]["content"]


def test_result_outcomes_distinguish_acceptance_running_services_and_finished_work():
    from ngram.presence.code_goal_protocol import observation

    accepted = observation("ar_world", {"command": "inspect"}, '{"status":"accepted","result":{"ok":true}}')
    assert accepted["outcome"] == "accepted" and not accepted["evidence"]
    running = observation("check_process", {}, '{"ok":true,"running":true,"exit_code":null}')
    assert running["outcome"] == "running" and running["success"] and running["evidence"]
    launched = observation("run_background", {}, '{"ok":true,"exit_code":null,"process_id":"bg_x"}')
    assert launched["success"] and not launched["evidence"]
    failed = observation("run_command", {}, '{"ok":true,"exit_code":1}')
    assert failed["outcome"] == "failed" and not failed["success"]


@pytest.mark.asyncio
async def test_progress_separates_visible_status_from_proven_milestones_and_one_final_result(tmp_path):
    from ngram.ngram_ar.spatial_sessions import SpatialSession, SpatialSessions

    entity, manager, _, _ = setup(tmp_path, [
        transition("goal_progress", message="Inspecting the parser"),
        calls(("run_command", {"command": "parser tests pass"})),
        transition("goal_progress", message="Parser tests pass.", milestone_id="parser-tested", evidence=["e1"]),
        transition("goal_progress", message="The parser tests are passing.", milestone_id="parser-tested", evidence=["e1"]),
        calls(("run_command", {"command": "integration test passes"})),
        transition("goal_progress", message="Integration passes.", milestone_id="integration-tested", evidence=["e2"]),
        transition("goal_submit_for_verification", summary="Implementation done with detailed receipts", evidence=["e1", "e2"]),
        calls(("run_command", {"command": "fresh review"})),
        transition("goal_complete", summary="Long technical review. " * 100, evidence=["e3"], message="The parser is fixed and verified."),
    ])
    sent, activity = [], []

    async def send(actions):
        sent.extend(actions)
        session.acknowledge({"completedActionId": actions[0]["actionId"], "status": "accepted"})

    async def emit(_phase, _inp, **kwargs):
        activity.append(kwargs.get("work", {}))

    entity._emit_turn_activity = emit
    session = SpatialSession("browser", send, dict, "rook")
    entity._ngram_ar_sessions = SpatialSessions()
    entity._ngram_ar_sessions.register(session)
    receipt = await manager.submit("Fix parser", request())
    await settle(manager)
    state = manager.status(receipt["task_id"])
    assert state["status"] == "complete"
    assert [s["text"] for s in sent] == ["Parser tests pass.", "The parser is fixed and verified."]
    assert [s["notification"]["kind"] for s in sent] == ["progress", "terminal"]
    assert any(e.get("summary") == "Integration passes." for e in activity), "cooldown must not hide visible progress"
    assert state["summary"].startswith("Long technical review")
    assert len(state["last_spatial_notification"]) < 100


@pytest.mark.asyncio
async def test_recovered_completion_does_not_call_model_or_announce_twice(tmp_path):
    from ngram.presence.code_goal_phase import GoalPhase
    from ngram.presence.code_goals import _ALLOW

    entity, manager, files, _ = setup(tmp_path, [calls(("run_command", {"command": "review"})), checkpoint()])
    receipt = await manager.submit("Fix parser", request(), max_phases=1)
    await settle(manager)
    record = manager.records[receipt["task_id"]]
    record.update(status="verifying", mode="verify", last_applied_phase=0)
    phase = GoalPhase(manager, record, _ALLOW)
    assert json.loads(phase.transition("complete", "Verified current files", evidence=["e1"], message="Fixed."))["ok"]
    entity.client.responses = []
    count = len(entity.client.prompts)
    restored = CodeTaskManager(entity, root=manager.root, client=files)
    restored.start()
    await settle(restored)
    assert restored.status(receipt["task_id"])["status"] == "complete"
    assert len(entity.client.prompts) == count
    again = CodeTaskManager(entity, root=manager.root, client=files)
    again.start()
    assert not again.tasks


@pytest.mark.asyncio
async def test_cancellation_wins_over_a_saved_but_unapplied_transition_on_restart(tmp_path):
    from ngram.presence.code_goal_phase import GoalPhase
    from ngram.presence.code_goals import _ALLOW

    entity, manager, files, _ = setup(tmp_path, [calls(("run_command", {"command": "review"})), checkpoint()])
    receipt = await manager.submit("Fix parser", request(), max_phases=1)
    await settle(manager)
    record = manager.records[receipt["task_id"]]
    record.update(status="verifying", mode="verify", last_applied_phase=0)
    phase = GoalPhase(manager, record, _ALLOW)
    assert json.loads(phase.transition("complete", "Verified", evidence=["e1"]))["ok"]
    await manager.cancel(receipt["task_id"])
    restored = CodeTaskManager(entity, root=manager.root, client=files)
    restored.start()
    assert restored.status(receipt["task_id"])["status"] == "cancelled"
    assert not restored.tasks


@pytest.mark.asyncio
async def test_invalid_modes_and_unobserved_evidence_cannot_complete(tmp_path):
    entity, manager, _, _ = setup(tmp_path, [
        transition("goal_complete", summary="Wrong mode", evidence=[]),
        calls(("run_command", {"command": "fail"})),
        transition("goal_submit_for_verification", summary="False success", evidence=["e1"]),
        transition("goal_submit_for_verification", summary="Fake proof", evidence=["e999"]),
        transition("goal_checkpoint", summary="Need repair", next_steps="Fix failing test"),
    ])
    receipt = await manager.submit("Fix parser", request(), max_phases=1)
    await settle(manager)
    state = manager.status(receipt["task_id"])
    assert state["status"] == "paused" and state["mode"] == "work"
    assert "candidate_evidence" not in state
    assert len(entity.client.prompts) == 5


@pytest.mark.asyncio
async def test_verifier_cannot_use_direct_editing_tools_before_requesting_changes(tmp_path):
    _, manager, _, operations = setup(tmp_path, [
        calls(("run_command", {"command": "tests"})),
        transition("goal_submit_for_verification", summary="Ready", evidence=["e1"]),
        calls(("write_file", {"path": "bad.py", "content": "new code during review"})),
        transition("goal_request_changes", summary="Missing case", next_steps="Fix missing case"),
    ])
    receipt = await manager.submit("Fix parser", request(), max_phases=2)
    await settle(manager)
    assert operations == ["tests"]
    assert manager.status(receipt["task_id"])["mode"] == "work"


@pytest.mark.asyncio
async def test_phase_budget_requests_handoff_without_extra_model_rounds(tmp_path):
    responses = [calls(("run_command", {"command": f"operation {i}"})) for i in range(3)]
    responses.append(transition("goal_checkpoint", summary="Three operations done", next_steps="Check result", notes="Continue without repeating operations"))
    entity, manager, _, _ = setup(tmp_path, responses)
    receipt = await manager.submit("Fix parser", request(), max_phases=1, steps_per_phase=5)
    await settle(manager)
    assert len(entity.client.prompts) == 4
    assert "at most two model rounds left" in json.dumps(entity.client.prompts[-1])
    assert manager.status(receipt["task_id"])["working_notes"].startswith("Continue without repeating")


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
    assert state["status"] == "blocked" and state["blocker_count"] == 1
    assert "Worker restarted" in entity.client.prompts[0][1]["content"]


@pytest.mark.asyncio
async def test_rephrased_blockers_stop_without_consuming_more_model_calls(tmp_path):
    entity, manager, _, _ = setup(tmp_path, [
        checkpoint("blocked", next_steps="Need the supported live publisher."),
        checkpoint("blocked", next_steps="Host-to-Spatial publication is unavailable."),
        checkpoint("blocked", next_steps="Supply a documented way to publish live quotes."),
    ])
    receipt = await manager.submit("Update the live display", request())
    await settle(manager)
    state = manager.status(receipt["task_id"])
    assert state["status"] == "blocked" and state["phase"] == 1
    assert state["blocker_count"] == 1 and len(entity.client.prompts) == 1
    assert state["reason"] == "Need the supported live publisher."


@pytest.mark.asyncio
async def test_explicit_resume_after_blocker_preserves_work_and_stops_on_new_blocker(tmp_path):
    entity, manager, _, _ = setup(tmp_path, [
        checkpoint("blocked", next_steps="Need publisher"),
        calls(("run_command", {"command": "independent tests pass"})),
        checkpoint("continue", next_steps="Verify the local display"),
        checkpoint("blocked", next_steps="Need publisher"),
    ])
    receipt = await manager.submit("Update the live display", request(), max_phases=4)
    await settle(manager)
    assert manager.status(receipt["task_id"])["status"] == "blocked"
    await manager.resume(receipt["task_id"], "Publisher is fixed; continue with saved work")
    await settle(manager)
    state = manager.status(receipt["task_id"])
    assert state["status"] == "blocked" and state["phase"] == 3
    assert state["blocker_count"] == 1


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
async def test_legacy_say_is_silent_status_and_completion_is_one_concise_body_notice(tmp_path):
    from ngram.ngram_ar.spatial_sessions import SpatialSession, SpatialSessions

    entity, manager, files, _ = setup(tmp_path, [
        calls(("say", {"message": "First screen rendered."})),
        calls(("say", {"message": "First screen rendered."})),
        calls(("run_command", {"command": "screens pass"})),
        calls(("say", {"message": "Checking the second screen."})),
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
    assert [a["text"] for a in sent] == ["Screens verified"]
    assert sent[0]["notification"] == {"goalId": receipt["task_id"], "kind": "terminal"}
    assert all(a["type"] == "action:speak" and a["sessionId"] == "browser" for a in sent)
    assert "accepted" in manager.status(receipt["task_id"])["last_spatial_delivery"]
    assert "Checking the second screen." in json.dumps(entity.client.prompts)
    assert "use say() to share findings" not in json.dumps(entity.client.prompts)
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
    assert "ambiguous" in await manager._notify(record, "Next screen rendered.", kind="terminal")
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
        assert (await client.post(path + "/steer", json={"instructions": "Handle spaces"})).status == 403
        assert (await client.post(path + "/steer", headers=headers, json={"instructions": []})).status == 400
        assert (await client.post(path + "/steer", headers=headers, json={"instructions": ""})).status == 400
        response = await client.post(path + "/steer", headers=headers, json={"instructions": "Handle spaces"})
        assert response.status == 200
        assert (await response.json())["guidance_version"] == 1
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
        assert manager.status(receipt["task_id"])["amendments"] == ["Handle spaces", "Handle tabs"]
