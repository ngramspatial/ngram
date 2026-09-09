"""One resumable model context, with explicit mode-specific goal transitions."""

from __future__ import annotations

import json
import time
from typing import Any

from ngram.cognition.deliberate import DeliberateCognition
from ngram.inference.visual_results import VisualResult
from ngram.models import Input
from ngram.presence.code_goal_protocol import (
    GOAL_CONTINUATION, GOAL_HANDOFF, GOAL_PROMPT, evidence_ids, observation,
)
from ngram.presence.tools.runtime import ToolRuntimeContext, reset_tool_runtime, set_tool_runtime


class GoalPhase:
    def __init__(self, manager, record, allowed):
        self.manager, self.record = manager, record
        self.mode = record["mode"]
        self.guidance = record.get("guidance_version", 0)
        self.decision = None
        self.new_progress = False
        self.communication_rounds = 0
        self.round_communicated = self.round_worked = False
        self.observed_operations: set[str] = set()
        self.stagnant_rounds = 0
        self.round_advanced = False
        self.empty_replies = 0
        self.sub = manager.entity.tools.subset(allowed - {"think", "end_turn"})
        if self.mode == "verify":
            for name in {"write_file", "append_file", "apply_patch", "rollback_checkpoint", "ar_figment_physics", "ar_figment_behavior", "ar_environment"}:
                self.sub.unregister(name)
        self.state: dict[str, Any] = {}

    def after_tools(self):
        if self.decision or self.changed():
            return True
        self.communication_rounds = self.communication_rounds + 1 if self.round_communicated and not self.round_worked else 0
        if self.round_worked:
            self.stagnant_rounds = 0 if self.round_advanced else self.stagnant_rounds + 1
        self.round_communicated = self.round_worked = False
        self.round_advanced = False
        if self.communication_rounds >= 3:
            self.transition("stalled", self.record["summary"] or "No task work was performed.",
                            "The model repeated status updates without performing work. Resume with a concrete next action.")
            return True
        if self.stagnant_rounds >= 6:
            self.transition("stalled", self.record["summary"] or "Repeated observations without a changed operation or outcome.",
                            "Six tool rounds repeated observations without a new operation or outcome. Inspect pending work and resume with a concrete next action.")
            return True
        return False

    def changed(self):
        return self.guidance != self.record.get("guidance_version", 0)

    def validate_evidence(self, value, *, required=False, fresh=False):
        ids = evidence_ids(value)
        receipts = [self.manager.evidence(self.record, id) for id in ids]
        if (required and not ids) or any(not r or not r["evidence"] for r in receipts):
            raise ValueError("Cite successful observed evidence IDs; accepted or unfinished execution is not verification")
        if fresh and not any(r["phase"] == self.record["phase"] for r in receipts):
            raise ValueError("Obtain fresh direct evidence of the current result in this verification phase")
        return ids

    def transition(self, status, summary, next_steps="", evidence=None, notes="", artifacts=None, message=""):
        try:
            if not isinstance(summary, str) or not summary.strip() or len(summary) > 12000:
                raise ValueError("Supply a concrete summary of at most 12000 characters")
            if status in {"handoff", "changes", "blocked"} and not next_steps.strip():
                raise ValueError("Supply the next concrete action or the external dependency")
            if status == "submit" and self.mode != "work":
                raise ValueError("Already in verification: use goal_complete, goal_request_changes, or goal_checkpoint")
            if status in {"complete", "changes"} and self.mode != "verify":
                raise ValueError("Use goal_submit_for_verification first; completion is available only during verification")
            ids = self.validate_evidence(evidence or [], required=status in {"submit", "complete"}, fresh=status == "complete")
            if not isinstance(notes, str) or len(notes) > 8000:
                raise ValueError("notes must be a string of at most 8000 characters")
            artifacts = artifacts or []
            if not isinstance(artifacts, list) or len(artifacts) > 40 or any(not isinstance(p, str) or len(p) > 1024 for p in artifacts):
                raise ValueError("artifacts must contain up to 40 workspace paths")
            if not isinstance(message, str) or len(message) > 500:
                raise ValueError("The final user message must be at most 500 characters; keep details in summary")
            if notes:
                self.record["working_notes"] = notes
            if artifacts:
                self.record["artifacts"] = list(dict.fromkeys(artifacts + self.record.get("artifacts", [])))[:40]
            self.decision = {"status": status, "summary": summary.strip(), "next_steps": next_steps[:8000], "evidence": ids, "message": message.strip()}
            self.record["checkpoint"] = self.decision
            # A durable decision can be applied after a crash without re-running
            # the phase or asking the model to repeat its handoff.
            self.record["pending_decision"] = {"phase": self.record["phase"], "guidance_version": self.guidance, **self.decision}
            self.manager._save(self.record)
            return json.dumps({"ok": True, "transition": status, "phase_ended": True})
        except (ValueError, TypeError, AttributeError) as exc:
            return json.dumps({"ok": False, "error": str(exc)})

    def register_tools(self):
        string = {"type": "string"}
        ids = {"type": "array", "items": {"type": "string", "pattern": "^e[1-9][0-9]*$"}, "maxItems": 24}
        artifacts = {"type": "array", "items": string, "maxItems": 40}

        def register(name, description, fn, properties, required):
            self.sub.register_fn(name, description, fn, parameters_schema={
                "type": "object", "properties": properties, "required": required, "additionalProperties": False,
            })

        async def handoff(summary: str, next_steps: str, notes: str = "", artifacts=None, evidence=None):
            return self.transition("handoff", summary, next_steps, evidence, notes, artifacts)

        async def submit(summary: str, evidence, notes: str = "", artifacts=None):
            return self.transition("submit", summary, evidence=evidence, notes=notes, artifacts=artifacts)

        async def complete(summary: str, evidence, message: str = ""):
            return self.transition("complete", summary, evidence=evidence, message=message)

        async def changes(summary: str, next_steps: str, evidence=None):
            return self.transition("changes", summary, next_steps, evidence)

        async def blocked(summary: str, blocker: str):
            return self.transition("blocked", summary, blocker)

        async def read_evidence(ids):
            try:
                requested = evidence_ids(ids)
                if len(requested) > 12:
                    raise ValueError("Read at most 12 evidence IDs per call")
                return json.dumps({"evidence": [self.manager.evidence(self.record, id) or {"id": id, "error": "Evidence unavailable"} for id in requested]}, ensure_ascii=False)
            except ValueError as exc:
                return json.dumps({"ok": False, "error": str(exc)})

        async def progress(message: str, milestone_id: str = "", evidence=None):
            if not isinstance(message, str) or not message.strip() or len(message) > 1000:
                return json.dumps({"ok": False, "error": "Supply a concise progress message of at most 1000 characters"})
            if milestone_id:
                try:
                    refs = self.validate_evidence(evidence or [], required=True)
                except ValueError as exc:
                    return json.dumps({"ok": False, "error": str(exc)})
            else:
                refs = []
            await self.manager.progress(self.record, message.strip(), milestone_id, refs)
            return "[progress recorded; continue the work without repeating this update]"

        register("goal_checkpoint", "Save a context handoff and resume the SAME mode automatically. Preserve working notes, artifact paths, evidence and the next concrete action. Ends this phase; no end_turn needed.", handoff,
                 {"summary": string, "next_steps": string, "notes": string, "artifacts": artifacts, "evidence": ids}, ["summary", "next_steps"])
        if self.mode == "work":
            register("goal_submit_for_verification", "The implementation is ready: submit it for a fresh verification phase. This does not mark the goal complete. Ends this phase automatically.", submit,
                     {"summary": string, "evidence": ids, "notes": string, "artifacts": artifacts}, ["summary", "evidence"])
        else:
            register("goal_complete", "Finish the goal after verifying the actual result in this phase. Cite fresh observed evidence. message is the concise final user update; do not announce completion separately.", complete,
                     {"summary": string, "evidence": ids, "message": {"type": "string", "maxLength": 500}}, ["summary", "evidence"])
            register("goal_request_changes", "Verification found a defect. Return to implementation with concrete findings and the required fix. Ends this phase automatically.", changes,
                     {"summary": string, "next_steps": string, "evidence": ids}, ["summary", "next_steps"])
        register("goal_blocked", "Stop on an external dependency after independent work is done. The runner waits for explicit guidance/resume; it does not schedule repeated blocker checks.", blocked,
                 {"summary": string, "blocker": string}, ["summary", "blocker"])
        register("goal_read_evidence", "Retrieve up to 12 earlier observed tool results by ID, including evidence outside the recent context. Reading historical receipts does not count as fresh verification.", read_evidence, {"ids": {**ids, "maxItems": 12}}, ["ids"])
        register("goal_progress", "Update visible task status silently. Optional milestone_id plus observed evidence requests one short announcement for a meaningful NEW result. Routine work and phase changes need no speech.", progress,
                 {"message": {"type": "string", "maxLength": 1000}, "milestone_id": string, "evidence": ids}, ["message"])
        self.progress_tool = progress

    async def run(self):
        from ngram.work_activity import safe_label, work_progress, work_step

        record, manager = self.record, self.manager
        await work_progress("preparing", phase=record["phase"], mode=self.mode)
        self.register_tools()
        if not any(n in self.sub._tools for n in {"read_file", "run_command", "ar_request_capture", "ar_inspect_surface"}):
            raise RuntimeError("No coding inspection or execution tools are enabled")
        inp = Input(text=record["objective"], person_id=record["origin"]["person_id"],
                    person_name=record["origin"]["person_name"], channel=record["task_id"], platform="code_task",
                    metadata={"code_task": True, "code_task_max_steps": record["steps_per_phase"], "code_task_spatial": manager._spatial_route(record)})

        async def execute(spec):
            manager._check_running(record)
            if self.changed():
                return "[new user guidance received; stale tool call not executed; reloading goal context]"
            if self.decision:
                return "[phase already ended; additional tool call not executed]"
            # Unadvertised compatibility for old transcripts and restored workers.
            if spec.name == "code_task_checkpoint":
                self.round_communicated = True
                args = dict(spec.arguments)
                status = args.pop("status", "").lower()
                mapped = {"continue": "changes" if self.mode == "verify" else "handoff", "complete": "complete" if self.mode == "verify" else "submit", "blocked": "blocked"}
                if status not in mapped:
                    return '{"ok":false,"error":"Use the explicit goal transition tools"}'
                return self.transition(mapped[status], **args)
            if spec.name == "say":
                self.round_communicated = True
                return await self.progress_tool(spec.arguments.get("message", ""))
            if spec.name in {"end_turn", "think"}:
                self.round_communicated = True
                return "[use the appropriate goal transition tool; no separate end_turn is needed]"
            if self.sub.resolve_tool_name(spec.name) != spec.name:
                self.round_communicated = True
                return json.dumps({"ok": False, "error": "Tool unavailable in this goal mode; use its advertised transition tools"})
            if self.mode == "verify" and spec.name.startswith("ar_") and spec.arguments.get("command") in {
                "apply", "attach", "configure", "detach", "create", "execute", "publish", "import", "place", "clear", "reset", "restore",
            }:
                self.round_communicated = True
                return '{"ok":false,"error":"Return findings through goal_request_changes before editing the scene"}'
            control = spec.name.startswith("goal_")
            if control and spec.name != "goal_read_evidence":
                self.round_communicated = True
            else:
                self.round_worked = True
                self.empty_replies = 0
                if control:
                    key = "history:" + json.dumps(spec.arguments, sort_keys=True)
                    self.round_advanced |= key not in self.observed_operations
                    self.observed_operations.add(key)
            if not control:
                record["in_flight"] = {"tool": spec.name, "arguments": spec.arguments, "started_at": time.time()}
                manager._save(record)
            token = set_tool_runtime(ToolRuntimeContext(entity=manager.entity, inp=inp, state=self.state))
            manager.executing.add(record["task_id"])
            try:
                async with work_step("tool_running", tool=safe_label(spec.name)):
                    raw = await self.sub.execute(spec)
                    out = str(raw)
            finally:
                manager.executing.discard(record["task_id"])
                reset_tool_runtime(token)
            if not control:
                details = observation(spec.name, spec.arguments, out, visual=isinstance(raw, VisualResult))
                keys = record.setdefault("progress_keys", [])
                key = details["progress_key"]
                self.round_advanced |= key not in self.observed_operations
                self.observed_operations.add(key)
                if details["evidence"] or details["outcome"] in {"observed", "completed"}:
                    self.new_progress |= key not in keys
                    if key not in keys:
                        keys.append(key)
                record["sequence"] += 1
                receipt = {"id": f"e{record['sequence']}", "phase": record["phase"], "tool": spec.name,
                           "arguments": json.dumps(spec.arguments, ensure_ascii=False)[:8000], "result": out[:16000],
                           **details, "signature": key}
                manager.archive_evidence(record, receipt)
                record["receipts"].append(receipt)
                record["receipts"] = record["receipts"][-80:]
                record["in_flight"] = None
                manager._save(record)
                out = f"Evidence ID: {receipt['id']} (outcome={details['outcome']}, evidence={details['evidence']})\n{out}"
            manager._check_running(record)
            return VisualResult(out, raw.images) if isinstance(raw, VisualResult) else out

        async def finish(_text, _result, _state):
            self.empty_replies += 1
            if not self.decision and not self.changed() and self.empty_replies >= 3:
                self.transition("stalled", record["summary"] or "The model did not provide a valid goal transition.",
                                "Three replies failed to perform work or save a valid transition. Inspect the saved goal before resuming.")
            return bool(self.decision) or self.changed(), "Use the appropriate explicit goal transition: checkpoint to hand off context, submit to start review, complete only in verification, or blocked for an external dependency."

        context = {key: record.get(key) for key in (
            "objective", "success_criteria", "request_context", "amendments", "phase", "summary", "next_steps",
            "working_notes", "artifacts", "progress", "last_announcement", "recovery_note", "in_flight", "reason", "stalled_phases",
        )}
        context["recent_evidence"] = [{**r, "arguments": r["arguments"][:600], "result": r["result"][:2000]} for r in record["receipts"][-8:]]
        context["candidate_evidence"] = [{**r, "arguments": r["arguments"][:600], "result": r["result"][:2000]} for id in record.get("candidate_evidence", []) if (r := manager.evidence(record, id))]
        if record["stalled_phases"]:
            context["progress_warning"] = "Recent phases repeated observations without a new operation or outcome. Do not poll or rephrase the same handoff. Make a concrete change, submit the finished work, or identify the external blocker."
        cognition = DeliberateCognition(manager.entity.config, manager.entity.client)
        final_text = ""
        async for event in cognition.iter_responses(
            inp, GOAL_PROMPT, [{"role": "user", "content": f"{'VERIFICATION' if self.mode == 'verify' else 'IMPLEMENTATION'} phase\n" + json.dumps(context, ensure_ascii=False)}],
            tools=self.sub.openai_tools(), tool_executor=execute, final_checker=finish,
            stop_after_tools=self.after_tools, tool_continuation=GOAL_CONTINUATION,
            budget_handoff=GOAL_HANDOFF,
        ):
            if event.kind == "final":
                final_text = event.display_text
        manager._check_running(record)
        if self.changed():
            record.pop("pending_decision", None)
            return {"status": "handoff", "summary": record["summary"], "next_steps": "Apply the latest user guidance to the saved work; inspect any action that was in flight.", "evidence": []}
        record["stalled_phases"] = 0 if self.new_progress else record["stalled_phases"] + 1
        return self.decision or {"status": "handoff", "summary": final_text[:12000] or record["summary"],
                                 "next_steps": record["next_steps"], "evidence": []}
