"""Durable coding goals with isolated, resumable work and verification phases."""

from __future__ import annotations

import asyncio
import contextvars
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from ngram.inference.control import InferencePausedError
from ngram.ngram_ar.spatial_sessions import SpatialSessions, connected_spatial_session
from ngram.models import Input
from ngram.presence.code_goal_protocol import brief_message, evidence_ids
from ngram.presence.tools.code_task import _normalize_task_path, _slug_objective
from ngram.presence.tools.execution_rpc import get_execution_client_for_entity

log = structlog.get_logger(__name__)
_ACTIVE = {"queued", "running", "verifying", "cancelling", "pausing"}
# Tools use per-call runtime context. Keep conversation/memory/registry mutations
# isolated; Spatial authoring resolves the originating body afresh on each call.
_ALLOW = {
    "think", "end_turn", "read_file", "list_directory", "search_files",
    "write_file", "append_file", "apply_patch", "run_command", "run_background",
    "check_process", "kill_process", "execute_python", "execute_javascript",
    "get_execution_context", "list_checkpoints", "rollback_checkpoint",
    "search_web", "fetch_url", "get_current_time",
    "ar_blender", "ar_world", "ar_environment", "ar_inspect_surface", "ar_request_capture",
    "ar_figment", "ar_figment_physics", "ar_figment_behavior", "ar_figment_interact", "ar_figment_library",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _successful_result(raw: str) -> bool:
    try:
        result = json.loads(raw)
    except (ValueError, TypeError):
        return bool(raw.strip()) and not raw.startswith("[")
    if not isinstance(result, dict):
        return bool(result)
    if result.get("status") in {"accepted", "failed", "disconnected"} or result.get("state") in {"working", "failed", "error"}:
        return False
    if isinstance(result.get("result"), dict) and not _successful_result(json.dumps(result["result"])):
        return False
    return not (
        result.get("error") or result.get("ok") is False
        or result.get("running") is True
        or ("exit_code" in result and result["exit_code"] != 0)
    )


class CodeTaskManager:
    """One coding worker per Entity; normal chat retains its own turn lock.

    JSON in the Entity data directory is authoritative and atomically replaced.
    Markdown in the execution workspace is a readable projection, never a control
    channel. A process restart re-observes pending actions rather than replaying
    shell commands. Deploy one worker per Entity data directory.
    """

    def __init__(self, entity: Any, *, root: Path | None = None, client: Any = None) -> None:
        self.entity = entity
        self.root = root or Path(entity.config.journal_path()).parent / ".code_tasks"
        self.client = client or get_execution_client_for_entity(entity)
        self.records: dict[str, dict[str, Any]] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self.executing: set[str] = set()
        self.lock = asyncio.Lock()
        self.submission_lock = asyncio.Lock()
        self.started = False
        self.closing = False

    def _spatial_route(self, record: dict[str, Any]) -> dict[str, str]:
        if record.get("spatial_route"):
            return record["spatial_route"]
        sessions = getattr(self.entity, "_ngram_ar_sessions", None)
        if not isinstance(sessions, SpatialSessions):
            return {}
        origin = record["origin"]
        session = sessions.select(origin["channel"]) if origin["platform"] == "ngram_ar" else None
        if session is None:
            candidates = [s for s in sessions.sessions.values() if s.connected]
            session = candidates[0] if len(candidates) == 1 else None
        if session is None:
            return {}
        record["spatial_route"] = {"session_id": session.session_id, "shell_slug": session.shell_slug}
        return record["spatial_route"]

    def _save(self, record: dict[str, Any]) -> None:
        record["updated_at"] = _now()
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / f"{record['task_id']}.json"
        temporary = target.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as output:
            json.dump(record, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(target)

    def archive_evidence(self, record: dict[str, Any], receipt: dict[str, Any]) -> None:
        directory = self.root / record["task_id"]
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{receipt['id']}.json"
        temporary = target.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as output:
            json.dump(receipt, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(target)

    def evidence(self, record: dict[str, Any], id: str) -> dict[str, Any] | None:
        evidence_ids([id])  # Never turn model-supplied paths into filesystem reads.
        for receipt in reversed(record["receipts"]):
            if receipt["id"] == id:
                return receipt
        try:
            value = json.loads((self.root / record["task_id"] / f"{id}.json").read_text(encoding="utf-8"))
            return value if value.get("id") == id else None
        except (OSError, ValueError, TypeError, AttributeError):
            return None

    def start(self) -> None:
        if self.started or self.closing:
            return
        self.started = True
        if self.root.exists():
            for path in sorted(self.root.glob("*.json")):
                try:
                    record = json.loads(path.read_text(encoding="utf-8"))
                    if record["task_id"] != path.stem or record.get("version") != 1:
                        raise ValueError("invalid code task record")
                    self.records[path.stem] = record
                    # Preserve older records; the protocol upgrade is additive.
                    for receipt in record["receipts"]:
                        if not (self.root / record["task_id"] / f"{receipt['id']}.json").exists():
                            self.archive_evidence(record, receipt)
                    pending = record.get("pending_decision")
                    if pending and record["status"] in {"queued", "running", "verifying"} and pending.get("phase") == record["phase"] and pending.get("guidance_version", 0) == record.get("guidance_version", 0):
                        self._apply_decision(record, pending)
                        self._save(record)
                    elif pending:
                        record.pop("pending_decision", None)
                        self._save(record)
                    if record["status"] in {"cancelling", "pausing"}:
                        record["status"] = "cancelled" if record["status"] == "cancelling" else "paused"
                        self._save(record)
                    elif record["status"] in _ACTIVE:
                        if record.get("phase_deadline"):
                            record["remaining_seconds"] = min(
                                record["remaining_seconds"], max(0, record.pop("phase_deadline") - time.time()),
                            )
                        record["status"] = "queued"
                        record["recovery_note"] = (
                            "Worker restarted. Inspect the workspace and any pending action before "
                            "continuing; an interrupted command may already have taken effect."
                        )
                        self._save(record)
                        self._schedule(record)
                    elif record.get("terminal_notice_pending"):
                        self._schedule(record)
                except (OSError, ValueError, KeyError, TypeError):
                    log.exception("code_task_recovery_failed", record=path.name)

    def _schedule(self, record: dict[str, Any]) -> None:
        task_id = record["task_id"]
        if self.closing or (task_id in self.tasks and not self.tasks[task_id].done()):
            return
        # Never inherit a browser callback or the parent's tool runtime context.
        task = asyncio.create_task(self._run(record), context=contextvars.Context())
        self.tasks[task_id] = task
        task.add_done_callback(lambda finished: self._finished(task_id, finished))

    def _finished(self, task_id: str, task: asyncio.Task) -> None:
        if self.tasks.get(task_id) is task:
            self.tasks.pop(task_id, None)
        if task.cancelled() and self.records[task_id]["status"] in {"cancelling", "pausing"}:
            self.records[task_id]["status"] = (
                "cancelled" if self.records[task_id]["status"] == "cancelling" else "paused"
            )
            self._save(self.records[task_id])
        if not task.cancelled() and task.exception() is not None:
            log.error("code_task_runner_failed", task_id=task_id, error=str(task.exception()))
        record = self.records[task_id]
        if not self.closing and record["status"] in _ACTIVE:
            record.update(status="paused" if task.cancelled() else "failed",
                          reason="Goal runner stopped unexpectedly; inspect saved work and resume.")
            self._save(record)

    def status(self, task_id: str = "") -> dict[str, Any]:
        self.start()
        if task_id:
            record = self.records.get(task_id)
            if record is None:
                return {"ok": False, "error": "unknown task_id"}
            view = {key: value for key, value in record.items() if key not in {
                "request_context", "receipts", "phase_log",
            }}
            view["receipts"] = [
                {**receipt, "arguments": receipt["arguments"][:500], "result": receipt["result"][:1500]}
                for receipt in record["receipts"][-8:]
            ]
            view["phase_log"] = record["phase_log"][-3:]
            view["receipts_total"] = record["sequence"]
            task = self.tasks.get(task_id)
            view["worker_active"] = task is not None and not task.done()
            view["executing_tool"] = task_id in self.executing
            return {"ok": True, **json.loads(json.dumps(view))}
        return {"ok": True, "tasks": [
            {key: record.get(key) for key in (
                "task_id", "objective", "status", "phase", "task_record", "updated_at", "reason",
            )} for record in self.records.values()
        ]}

    async def submit(self, objective: str, inp: Input | None, **options: Any) -> dict[str, Any]:
        async with self.submission_lock:
            return await self._submit(objective, inp, **options)

    async def _submit(
        self, objective: str, inp: Input | None, *, max_phases: int = 0,
        steps_per_phase: int = 64, task_record_path: str = "",
        success_criteria: str = "", max_runtime_seconds: int = 21600,
    ) -> dict[str, Any]:
        self.start()
        if self.closing:
            return {"ok": False, "error": "worker is shutting down"}
        objective = objective.strip()
        if not objective:
            return {"ok": False, "error": "objective is empty"}
        # A launching-tool retry must not create a duplicate job.
        for existing in self.records.values():
            same_owner = existing["origin"].get("person_id") == (inp.person_id if inp else "")
            if same_owner and existing["objective"] == objective and existing["status"] in _ACTIVE:
                return {"ok": True, "existing": True, **self._receipt(existing)}
        rel = _normalize_task_path(task_record_path, slug=_slug_objective(objective))
        if any(r["task_record"] == rel for r in self.records.values()):
            return {"ok": False, "error": "task record already exists; resume using its task_id"}
        previous = await self.client.call("read_file", {"path": rel, "max_bytes": 1024})
        if previous.get("ok"):
            return {"ok": False, "error": "task record already exists; choose a new path"}
        if "not found" not in str(previous.get("error", "")).lower():
            return {"ok": False, "error": previous.get("error") or "cannot inspect task record"}
        record = {
            "version": 1, "task_id": uuid.uuid4().hex, "objective": objective,
            "success_criteria": success_criteria.strip() or objective,
            "origin": {key: getattr(inp, key, "") for key in ("person_id", "person_name", "channel", "platform")},
            "request_context": (inp.text if inp else "")[:12000],
            "status": "queued", "mode": "work", "phase": 0,
            "max_phases": max(0, int(max_phases)),
            "steps_per_phase": max(5, min(10000, int(steps_per_phase))),
            "remaining_seconds": max(1, int(max_runtime_seconds)),
            "task_record": rel, "created_at": _now(), "updated_at": _now(),
            "summary": "", "next_steps": "Inspect the repository and its instructions, then plan and implement.",
            "receipts": [], "sequence": 0, "in_flight": None, "phase_log": [],
            "stalled_phases": 0, "blocker_count": 0, "blocker": "", "errors": 0,
            "working_notes": "", "artifacts": [], "progress_keys": [], "guidance_version": 0, "run_version": 0,
        }
        self._spatial_route(record)
        self._save(record)
        self.records[record["task_id"]] = record
        self._schedule(record)
        return {"ok": True, **self._receipt(record)}

    @staticmethod
    def _receipt(record: dict[str, Any]) -> dict[str, Any]:
        return {key: record[key] for key in ("task_id", "status", "task_record", "objective")}

    async def resume(self, task_id: str, instructions: str = "", additional_seconds: int = 21600) -> dict[str, Any]:
        self.start()
        record = self.records.get(task_id)
        if not record:
            return {"ok": False, "error": "unknown task_id"}
        if self.closing:
            return {"ok": False, "error": "worker is shutting down"}
        if task_id in self.tasks and not self.tasks[task_id].done():
            return {"ok": False, "error": "task is still active", **self._receipt(record)}
        if record["status"] == "complete":
            return {"ok": False, "error": "task is complete; submit a new objective"}
        if instructions.strip():
            record.setdefault("amendments", []).append(instructions.strip())
            record["mode"] = "work"
            record.pop("candidate_evidence", None)
        record["guidance_version"] = record.get("guidance_version", 0) + 1
        record["run_version"] = record.get("run_version", 0) + 1
        record.pop("pending_decision", None)
        record.update(status="queued", reason="", errors=0, stalled_phases=0, blocker_count=0)
        record["remaining_seconds"] += max(1, int(additional_seconds))
        if record["max_phases"]:
            allowance = record.setdefault("phase_allowance", record["max_phases"])
            record["max_phases"] = record["phase"] + allowance
        self._save(record)
        self._schedule(record)
        return {"ok": True, **self._receipt(record)}

    async def steer(self, task_id: str, instructions: str) -> dict[str, Any]:
        """Persist guidance now; fence stale tool calls at the next safe boundary."""
        self.start()
        record = self.records.get(task_id)
        if not record or not instructions.strip():
            return {"ok": False, "error": "Supply a known task_id and concrete instructions"}
        if record["status"] not in {"queued", "running", "verifying"} or self.closing:
            return {"ok": False, "error": "Goal is not active; use resume for an incomplete stopped goal"}
        record.setdefault("amendments", []).append(instructions.strip())
        record["guidance_version"] = record.get("guidance_version", 0) + 1
        record["mode"] = "work"
        record["stalled_phases"] = 0
        record.pop("candidate_evidence", None)
        record.pop("pending_decision", None)
        self._save(record)
        return {"ok": True, "guidance_version": record["guidance_version"], **self._receipt(record)}

    async def cancel(self, task_id: str) -> dict[str, Any]:
        self.start()
        record = self.records.get(task_id)
        if not record:
            return {"ok": False, "error": "unknown task_id"}
        if record["status"] in {"complete", "cancelled"}:
            return {"ok": True, **self._receipt(record)}
        task = self.tasks.get(task_id)
        record.pop("pending_decision", None)
        record["status"] = "cancelling" if task and not task.done() else "cancelled"
        record["reason"] = "Cancelled by request; an executing tool finishes before the worker stops."
        self._save(record)
        if task and task_id not in self.executing:
            task.cancel()
        return {"ok": True, **self._receipt(record)}

    async def shutdown(self) -> None:
        self.closing = True
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def pause_active(self) -> None:
        for task_id, task in tuple(self.tasks.items()):
            record = self.records[task_id]
            if not task.done() and record["status"] in {"queued", "running", "verifying"}:
                record.pop("pending_decision", None)
                record.update(status="pausing", reason="Inference is paused; explicitly resume this goal when ready.")
                self._save(record)
                if task_id not in self.executing:
                    task.cancel()

    async def _mirror(self, record: dict[str, Any]) -> None:
        body = (
            f"# Code task: {record['objective']}\n\n"
            f"Task ID: `{record['task_id']}`\n\n## Status\n\n{record['status']}\n\n"
            f"## Success criteria\n\n{record['success_criteria']}\n\n"
            f"## Latest progress\n\n{record['summary']}\n\n"
            f"## Next steps\n\n{record['next_steps']}\n\n"
            f"## Working notes\n\n{record.get('working_notes', '')}\n\n"
            f"## Artifacts\n\n" + "\n".join(record.get("artifacts", [])) + "\n\n"
            f"## Stop reason\n\n{record.get('reason', '')}\n\n## Phase log\n\n"
            + "\n\n".join(f"### Phase {entry['phase']} ({entry['mode']})\n{entry['summary']}" for entry in record["phase_log"])
        )
        result = await self.client.call("write_file", {"path": record["task_record"], "content": body})
        if not result.get("ok"):
            raise RuntimeError(result.get("error") or "task record write failed")

    async def progress(self, record, message, milestone_id="", evidence=None):
        from ngram.work_activity import work_progress

        record["progress"] = {"message": message, "phase": record["phase"], "at": _now()}
        self._save(record)
        await work_progress()
        if milestone_id:
            await self._notify(record, message, key=milestone_id[:160], evidence=evidence or [])

    async def _notify(self, record: dict[str, Any], message: str, *, kind="progress", key="", evidence=None) -> str:
        # Resolve a durable chat route each time. Never retain an AR socket or
        # use a broadcast fallback when the original surface is disconnected.
        origin = record["origin"]
        message = brief_message(message)
        fingerprint = re.sub(r"\W+", " ", message.casefold()).strip()
        notices = record.setdefault("announcements", [])
        if any((key and notice["key"] == key) or notice["fingerprint"] == fingerprint for notice in notices):
            return "[duplicate announcement suppressed]"
        if kind == "progress" and notices:
            if time.time() - notices[-1]["at"] < 90 or (evidence and set(evidence) <= {id for n in notices for id in n.get("evidence", [])}):
                return "[progress saved silently; no new announcement needed]"
        notice = {"key": key, "kind": kind, "fingerprint": fingerprint, "text": message, "evidence": evidence or [], "at": time.time()}
        if origin["platform"] == "ngram_ar":
            inp = Input(text="", platform="code_task", channel=record["task_id"],
                        person_id=origin["person_id"], person_name=origin["person_name"],
                        metadata={"code_task_spatial": record.get("spatial_route", {})})
            session = connected_spatial_session(self.entity, inp)
            if session is None:
                return "[speech not sent: originating Spatial body is disconnected or ambiguous]"
            if record.get("last_spatial_notification") == message:
                return "[duplicate speech suppressed]"
            # Persist before dispatch: a disconnect or restart must not replay
            # speech whose delivery is uncertain. Resolve a fresh body per call.
            record["last_spatial_notification"] = message
            record["last_spatial_delivery"] = "[dispatching; speech delivery not confirmed]"
            notices.append(notice)
            record["announcements"] = notices[-40:]
            record["last_announcement"] = notice
            self._save(record)
            record["last_spatial_delivery"] = await session.dispatch({
                "type": "action:speak", "text": message,
                "notification": {"goalId": record["task_id"], "kind": kind},
            })
            self._save(record)
            return record["last_spatial_delivery"]
        if origin["platform"] not in {"telegram", "discord"}:
            return "[no connected chat route]"
        platform = getattr(self.entity, "_platforms", {}).get(origin["platform"])
        if platform is None:
            return "[no connected chat route]"
        notices.append(notice)
        record["announcements"] = notices[-40:]
        record["last_announcement"] = notice
        self._save(record)
        try:
            async with asyncio.timeout(5):
                await platform.send_message(origin["channel"], message[:4000])
            return "[sent]"
        except Exception:
            log.warning("code_task_notification_failed", task_id=record["task_id"])
            return "[chat delivery failed; progress remains saved]"

    def _check_running(self, record: dict[str, Any]) -> None:
        if self.closing or record["status"] == "cancelling":
            raise asyncio.CancelledError
        if record["status"] == "pausing" or self.entity.inference_paused:
            raise InferencePausedError

    async def _run(self, record: dict[str, Any]) -> None:
        from ngram.work_activity import work_scope

        inp = Input(text="", platform="code_task", channel=record["task_id"],
                    person_id=record["origin"]["person_id"], person_name=record["origin"]["person_name"])

        async def report(status):
            emit = getattr(self.entity, "_emit_turn_activity", None)
            if emit is not None:
                # User-facing progress belongs to the task's owner. Add it after
                # work_activity logging, which intentionally contains no content.
                await emit("progress", inp, work={**status, "summary": record.get("progress", {}).get("message") or record["summary"],
                    "reason": record.get("reason", ""), "nextSteps": record["next_steps"]})

        async with work_scope(report, scope="code_task", run_id=record["task_id"], taskId=record["task_id"]) as activity:
            try:
                await self._run_goal(record)
            finally:
                activity.outcome = record["status"] if record["status"] not in _ACTIVE else "paused"

    async def _run_goal(self, record: dict[str, Any]) -> None:
        try:
            async with self.lock:
                if record["status"] not in _ACTIVE:
                    return  # Recovered terminal notification; no model work.
                while True:
                    self._check_running(record)
                    if record["remaining_seconds"] <= 0 or (
                        record["max_phases"] and record["phase"] >= record["max_phases"]
                    ):
                        record.update(status="paused", reason="Execution budget exhausted; goal remains incomplete.")
                        break
                    record["status"] = "verifying" if record["mode"] == "verify" else "running"
                    record["phase"] += 1
                    record["phase_deadline"] = time.time() + record["remaining_seconds"]
                    self._save(record)
                    started = time.monotonic()
                    try:
                        async with asyncio.timeout(record["remaining_seconds"]):
                            await self._mirror(record)
                            decision = await self._phase(record)
                        record["errors"] = 0
                        self._apply_decision(record, decision)
                    except (InferencePausedError, asyncio.CancelledError):
                        raise
                    except TimeoutError:
                        record.update(status="paused", reason="Execution time exhausted; inspect any pending action before resuming.")
                    except Exception as exc:
                        record["errors"] += 1
                        record["reason"] = str(exc)[:2000]
                        if record["errors"] >= 3:
                            record["status"] = "failed"
                        else:
                            await asyncio.sleep(min(2 ** record["errors"], record["remaining_seconds"]))
                    finally:
                        record["remaining_seconds"] = max(0, record["remaining_seconds"] - (time.monotonic() - started))
                        record.pop("phase_deadline", None)
                        self._save(record)
                    if record["status"] not in _ACTIVE:
                        break
                    await asyncio.sleep(0)
        except InferencePausedError:
            record.pop("pending_decision", None)
            record.update(status="paused", reason="Inference is paused; explicitly resume this goal when ready.")
        except asyncio.CancelledError:
            requested = record["status"] == "cancelling"
            paused = record["status"] == "pausing"
            record["status"] = "cancelled" if requested else "queued" if self.closing else "paused"
            if paused:
                record.update(status="paused", reason="Inference is paused; explicitly resume this goal when ready.")
            elif not requested:
                record["reason"] = "Worker interrupted; inspect any pending action before continuing."
        except Exception as exc:
            record.update(status="failed", reason=str(exc)[:2000])
        finally:
            self._save(record)
            if not self.closing:
                try:
                    await self._mirror(record)
                except Exception:
                    log.exception("code_task_mirror_failed", task_id=record["task_id"])
                # Internal checkpoints and user-requested stops are status, not
                # speeches. Completion has one concise result, never a readout
                # of the evidence ledger or another phase introduction.
                if record["status"] in {"complete", "blocked", "failed"}:
                    message = record.get("final_message") if record["status"] == "complete" else record.get("reason")
                    message = message or record["summary"] or f"Coding goal {record['status']}."
                    await self._notify(record, message, kind="terminal", key=f"terminal:{record.get('run_version', 0)}:{record['status']}")
                record.pop("terminal_notice_pending", None)
                self._save(record)

    def _apply_decision(self, record: dict[str, Any], decision: dict[str, Any]) -> None:
        if record.get("last_applied_phase") == record["phase"]:
            record.pop("pending_decision", None)
            return
        record["reason"] = ""
        record["summary"] = decision["summary"]
        record["next_steps"] = decision["next_steps"]
        record["phase_log"].append({"phase": record["phase"], "mode": record["mode"], "transition": decision["status"], "summary": decision["summary"]})
        record["phase_log"] = record["phase_log"][-50:]
        if decision["status"] == "submit":
            record["mode"] = "verify"
            record["candidate_evidence"] = decision["evidence"]
            record["blocker_count"] = record["stalled_phases"] = 0
        elif decision["status"] == "complete":
            record.update(status="complete", reason="Verified against success criteria.", terminal_notice_pending=True)
            record["completion_evidence"] = decision["evidence"]
            record["final_message"] = brief_message(decision.get("message") or decision["summary"])
            record["progress"] = {"message": record["final_message"], "phase": record["phase"], "at": _now()}
            record["blocker_count"] = record["stalled_phases"] = 0
        elif decision["status"] == "blocked":
            record["blocker_count"] = 1
            record["blocker"] = decision["next_steps"]
            record.update(status="blocked", reason=decision["next_steps"], terminal_notice_pending=True)
        elif decision["status"] == "changes":
            record["mode"] = "work"
            record.pop("candidate_evidence", None)
            record["blocker_count"] = record["stalled_phases"] = 0
        elif decision["status"] == "stalled":
            record.update(status="paused", reason=decision["next_steps"])
        else:
            record["blocker_count"] = 0
            # A handoff preserves verification; only explicit findings return
            # the goal to implementation.
            if record["stalled_phases"] >= 3:
                record.update(status="paused", reason="Three phases repeated observations without new operations or outcomes. Inspect saved work and resume with a concrete next action.")
        record["last_applied_phase"] = record["phase"]
        record.pop("pending_decision", None)

    async def _phase(self, record: dict[str, Any]) -> dict[str, Any]:
        from ngram.presence.code_goal_phase import GoalPhase

        return await GoalPhase(self, record, _ALLOW).run()
