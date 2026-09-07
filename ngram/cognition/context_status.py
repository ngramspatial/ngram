"""Turn-scoped context telemetry; reporting never starts an inference request."""

from contextvars import ContextVar
from typing import Any, Awaitable, Callable

import structlog

ContextReporter = Callable[[dict[str, Any]], Awaitable[None]]
context_reporter: ContextVar[ContextReporter | None] = ContextVar("context_reporter", default=None)
log = structlog.get_logger(__name__)


async def report_context_status(phase: str, **details: Any) -> None:
    reporter = context_reporter.get()
    if reporter is None:
        return
    try:
        await reporter({"phase": phase, **details})
    except Exception as exc:
        log.debug("context_status_delivery_failed", error=str(exc))
