"""Observed work stages. Heartbeats never call inference or imply new progress."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
import re
import time
from typing import Any, Awaitable, Callable
import uuid

import structlog

log = structlog.get_logger(__name__)
Reporter = Callable[[dict[str, Any]], Awaitable[None]]
current_work: ContextVar[WorkActivity | None] = ContextVar("current_work", default=None)
_DETAILS = {"requestId", "attempt", "maxAttempts", "timeoutMs", "retryDelayMs", "errorType",
            "tool", "operation", "project", "revision", "phase", "mode", "taskId"}


def safe_label(value: Any) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]", "", str(value))[:80]


class WorkActivity:
    def __init__(self, reporter: Reporter, *, scope: str = "turn", run_id: str = "",
                 heartbeat_seconds: float = 10, **details: Any):
        self.reporter = reporter
        self.run_id = run_id or uuid.uuid4().hex
        self.instance_id = uuid.uuid4().hex
        self.scope = scope
        self.heartbeat_seconds = heartbeat_seconds
        self.started = self.stage_started = self.progress_at = time.monotonic()
        self.stage = "preparing"
        self.details = {k: v for k, v in details.items() if k in _DETAILS}
        self.outcome = "complete"
        self.sequence = 0
        self._closed = False

    async def emit(self, status: str = "running", *, heartbeat: bool = False) -> None:
        if self._closed:
            return
        now = time.monotonic()
        self.sequence += 1
        event = {
            **self.details, "runId": self.run_id, "instanceId": self.instance_id, "scope": self.scope,
            "stage": self.stage, "status": status, "sequence": self.sequence,
            "timestamp": int(time.time() * 1000), "heartbeat": heartbeat,
            "elapsedMs": round((now - self.started) * 1000),
            "stageElapsedMs": round((now - self.stage_started) * 1000),
            "idleMs": round((now - self.progress_at) * 1000),
        }
        # These fields contain no prompts, source code, tool arguments, URLs or keys.
        log.info("work_activity", **event)
        try:
            await asyncio.wait_for(self.reporter(event), timeout=0.25)
        except Exception:
            log.debug("work_activity_delivery_failed", run_id=self.run_id)

    async def stage_update(self, stage: str | None = None, **details: Any) -> None:
        if stage and stage != self.stage:
            self.stage = stage
            self.stage_started = time.monotonic()
        self.progress_at = time.monotonic()
        self.details.update({k: v for k, v in details.items() if k in _DETAILS})
        await self.emit()

    async def heartbeat(self) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_seconds)
            await self.emit(heartbeat=True)


@asynccontextmanager
async def work_scope(reporter: Reporter, **options: Any):
    activity = WorkActivity(reporter, **options)
    token = current_work.set(activity)
    pulse = None
    try:
        await activity.emit()
        pulse = asyncio.create_task(activity.heartbeat())
        yield activity
    except asyncio.CancelledError:
        activity.outcome = "cancelled"
        raise
    except Exception:
        activity.outcome = "failed"
        raise
    finally:
        if pulse is not None:
            pulse.cancel()
            await asyncio.gather(pulse, return_exceptions=True)
        await activity.emit(activity.outcome)
        activity._closed = True
        current_work.reset(token)


@asynccontextmanager
async def work_step(stage: str, **details: Any):
    activity = current_work.get()
    if activity is None:
        yield
        return
    previous = (activity.stage, activity.stage_started, dict(activity.details))
    await activity.stage_update(stage, **details)
    try:
        yield
    finally:
        activity.stage, activity.stage_started, activity.details = previous
        # Returning from the top-level operation means processing its result.
        if activity.stage == "preparing":
            activity.stage = "processing"
            activity.stage_started = time.monotonic()
        activity.progress_at = time.monotonic()
        await activity.emit()


async def work_progress(stage: str | None = None, **details: Any) -> None:
    activity = current_work.get()
    if activity is not None:
        await activity.stage_update(stage, **details)
