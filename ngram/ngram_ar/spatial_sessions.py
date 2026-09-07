"""Entity-owned live bodies, independent of the platform that starts a turn."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any


class SpatialSession:
    def __init__(
        self,
        session_id: str,
        send: Callable[[list[dict[str, Any]]], Awaitable[None]],
        context: Callable[[], dict[str, Any]],
    ) -> None:
        self.session_id = session_id
        self.send = send
        self.context = context
        self.connected = True
        self.pending: dict[str, asyncio.Future[dict[str, Any]]] = {}

    async def dispatch(self, action: dict[str, Any], *, timeout: float = 8.0) -> str:
        if not self.connected:
            return "[spatial: unavailable; Spatial session disconnected]"
        action_id = uuid.uuid4().hex
        outbound = {
            **action,
            "actionId": action_id,
            "sessionId": self.session_id,
            "timestamp": int(time.time() * 1000),
        }
        receipt = asyncio.get_running_loop().create_future()
        self.pending[action_id] = receipt
        label = str(action.get("type", "action")).removeprefix("action:")
        try:
            # Bound socket writes as well as the renderer acknowledgment. Never
            # replay uncertain deliveries: a toy may already have been created.
            async with asyncio.timeout(timeout):
                await self.send([outbound])
                result = await receipt
        except TimeoutError:
            return f"[spatial: {label} confirmation timed out; execution unknown]"
        except Exception:
            return f"[spatial: {label} delivery interrupted; execution unknown]"
        finally:
            self.pending.pop(action_id, None)
        status = result["status"]
        detail = str(result.get("error") or "")[:300]
        if status == "accepted":
            detail = "surface accepted the request; completion is not confirmed"
        return f"[spatial: {label} {status} in session {self.session_id}" + (
            f"; {detail}]" if detail else "]"
        )

    def acknowledge(self, event: dict[str, Any]) -> None:
        receipt = self.pending.get(str(event.get("completedActionId") or ""))
        status = event.get("status", "completed")
        if receipt is not None and not receipt.done() and status in {
            "accepted", "completed", "failed",
        }:
            receipt.set_result({"status": status, "error": event.get("error")})

    def close(self) -> None:
        self.connected = False
        for receipt in self.pending.values():
            if not receipt.done():
                receipt.set_result({
                    "status": "disconnected",
                    "error": "execution unknown; action will not be replayed",
                })
        self.pending.clear()


class SpatialSessions:
    def __init__(self) -> None:
        self.sessions: dict[str, SpatialSession] = {}

    def register(self, session: SpatialSession) -> None:
        previous = self.sessions.pop(session.session_id, None)
        if previous is not None:
            previous.close()
        self.sessions[session.session_id] = session

    def unregister(self, session: SpatialSession) -> None:
        session.close()
        if self.sessions.get(session.session_id) is session:
            self.sessions.pop(session.session_id)

    def select(self, session_id: str = "") -> SpatialSession | None:
        if session_id:
            session = self.sessions.get(session_id)
            return session if session and session.connected else None
        return next((s for s in reversed(self.sessions.values()) if s.connected), None)


def connected_spatial_session(entity: Any, inp: Any = None) -> SpatialSession | None:
    sessions = getattr(entity, "_ngram_ar_sessions", None)
    if not isinstance(sessions, SpatialSessions):
        return None
    # Prefer the originating body when it is connected. API/message sessions
    # can also use the ngram_ar platform without owning a renderer.
    session_id = str(inp.channel or "") if inp and inp.platform == "ngram_ar" else ""
    return sessions.select(session_id) or sessions.select()
