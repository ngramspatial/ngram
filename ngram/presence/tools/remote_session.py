"""Remote Linux desktop session tools routed through the execution backend."""

from __future__ import annotations

import base64
import json
from typing import Any

from ngram.presence.tools.execution_rpc import get_execution_client
from ngram.presence.tools.registry import tool
from ngram.presence.tools.runtime import require_tool_runtime


def _json(res: dict[str, Any]) -> str:
    return json.dumps(res, ensure_ascii=False)


def _session_from_ctx() -> dict[str, Any]:
    ctx = require_tool_runtime()
    meta = ctx.inp.metadata if ctx.inp and isinstance(ctx.inp.metadata, dict) else {}
    sess = meta.get("desktop_session")
    return sess if isinstance(sess, dict) else {}


def _active_session_id() -> str:
    ctx = require_tool_runtime()
    sid = str(ctx.state.get("desktop_session_id") or "").strip()
    if sid:
        return sid
    meta_sid = str(_session_from_ctx().get("session_id") or "").strip()
    if meta_sid:
        ctx.state["desktop_session_id"] = meta_sid
        return meta_sid
    if ctx.platform is not None and ctx.inp is not None:
        get_active = getattr(ctx.platform, "get_active_remote_session", None)
        if callable(get_active):
            sess = get_active(ctx.inp.channel) or {}
            if isinstance(sess, dict):
                sid = str(sess.get("session_id") or "").strip()
                if sid:
                    ctx.state["desktop_session_id"] = sid
                    return sid
    return ""


async def _sync_platform_session(res: dict[str, Any], *, clear: bool = False) -> None:
    ctx = require_tool_runtime()
    if ctx.platform is None or ctx.inp is None:
        return
    setter = getattr(ctx.platform, "set_active_remote_session", None)
    if not callable(setter):
        return
    if clear:
        await setter(ctx.inp.channel, None)
        clearer = getattr(ctx.platform, "clear_remote_session_card", None)
        if callable(clearer):
            await clearer(ctx.inp.channel)
        ctx.state.pop("desktop_session_id", None)
        return
    session_id = str(res.get("session_id") or _active_session_id()).strip()
    if not session_id:
        return
    ctx.state["desktop_session_id"] = session_id
    merged = dict(_session_from_ctx())
    if ctx.platform is not None:
        get_active = getattr(ctx.platform, "get_active_remote_session", None)
        if callable(get_active):
            current = get_active(ctx.inp.channel)
            if isinstance(current, dict):
                merged.update(current)
    merged.update({k: v for k, v in res.items() if k != "image_base64"})
    merged["session_id"] = session_id
    await setter(ctx.inp.channel, merged)


def _decode_image_bytes(res: dict[str, Any]) -> bytes | None:
    raw = ""
    for key in ("image_base64", "screenshot_base64", "frame_base64"):
        val = res.get(key)
        if isinstance(val, str) and val.strip():
            raw = val.strip()
            break
    if not raw:
        return None
    try:
        return base64.b64decode(raw, validate=False)
    except Exception:
        return None


async def _maybe_update_card(res: dict[str, Any]) -> None:
    ctx = require_tool_runtime()
    if ctx.platform is None or ctx.inp is None:
        return
    updater = getattr(ctx.platform, "upsert_remote_session_card", None)
    if not callable(updater):
        return
    image_bytes = _decode_image_bytes(res)
    if not image_bytes:
        return
    session = dict(_session_from_ctx())
    session.update({k: v for k, v in res.items() if k != "image_base64"})
    await updater(
        ctx.inp.channel,
        image_bytes=image_bytes,
        caption=str(session.get("caption") or session.get("summary") or "Remote session"),
        session=session,
    )


def _require_session_id() -> str:
    sid = _active_session_id()
    if sid:
        return sid
    raise RuntimeError("no active desktop session in this chat. Use /session_start in Telegram first.")


@tool(
    name="desktop_session_status",
    description="Inspect the active remote Linux desktop session in this chat.",
)
async def desktop_session_status() -> str:
    try:
        sid = _require_session_id()
    except RuntimeError as e:
        return _json({"ok": False, "error": str(e)})
    client = get_execution_client()
    res = await client.call("desktop_session_status", {"session_id": sid})
    if isinstance(res, dict) and res.get("ok"):
        await _sync_platform_session(res)
    return _json(res if isinstance(res, dict) else {"ok": False, "error": "invalid result"})


@tool(
    name="desktop_session_view",
    description="Capture the current frame from the active remote Linux desktop session.",
)
async def desktop_session_view() -> str:
    try:
        sid = _require_session_id()
    except RuntimeError as e:
        return _json({"ok": False, "error": str(e)})
    client = get_execution_client()
    res = await client.call("desktop_session_capture", {"session_id": sid, "format": "jpeg"})
    if isinstance(res, dict) and res.get("ok"):
        await _sync_platform_session(res)
        await _maybe_update_card(res)
    return _json(res if isinstance(res, dict) else {"ok": False, "error": "invalid result"})


@tool(
    name="desktop_session_type",
    description="Type text into the active remote Linux desktop session.",
)
async def desktop_session_type(text: str) -> str:
    try:
        sid = _require_session_id()
    except RuntimeError as e:
        return _json({"ok": False, "error": str(e)})
    client = get_execution_client()
    res = await client.call(
        "desktop_session_input",
        {"session_id": sid, "kind": "type_text", "text": text or ""},
    )
    if isinstance(res, dict) and res.get("ok"):
        await _sync_platform_session(res)
    return _json(res if isinstance(res, dict) else {"ok": False, "error": "invalid result"})


@tool(
    name="desktop_session_keypress",
    description="Press a keyboard shortcut or key in the active remote Linux desktop session.",
)
async def desktop_session_keypress(keys: str) -> str:
    try:
        sid = _require_session_id()
    except RuntimeError as e:
        return _json({"ok": False, "error": str(e)})
    client = get_execution_client()
    res = await client.call(
        "desktop_session_input",
        {"session_id": sid, "kind": "keypress", "keys": keys or ""},
    )
    if isinstance(res, dict) and res.get("ok"):
        await _sync_platform_session(res)
    return _json(res if isinstance(res, dict) else {"ok": False, "error": "invalid result"})


@tool(
    name="desktop_session_click",
    description="Click at screen coordinates in the active remote Linux desktop session.",
)
async def desktop_session_click(x: int, y: int, button: str = "left") -> str:
    try:
        sid = _require_session_id()
    except RuntimeError as e:
        return _json({"ok": False, "error": str(e)})
    client = get_execution_client()
    res = await client.call(
        "desktop_session_input",
        {"session_id": sid, "kind": "mouse_click", "x": int(x), "y": int(y), "button": button or "left"},
    )
    if isinstance(res, dict) and res.get("ok"):
        await _sync_platform_session(res)
    return _json(res if isinstance(res, dict) else {"ok": False, "error": "invalid result"})


@tool(
    name="desktop_session_open_url",
    description="Open a URL in the browser inside the active remote Linux desktop session.",
)
async def desktop_session_open_url(url: str) -> str:
    try:
        sid = _require_session_id()
    except RuntimeError as e:
        return _json({"ok": False, "error": str(e)})
    client = get_execution_client()
    res = await client.call(
        "desktop_session_input",
        {"session_id": sid, "kind": "open_url", "url": (url or "").strip()},
    )
    if isinstance(res, dict) and res.get("ok"):
        await _sync_platform_session(res)
    return _json(res if isinstance(res, dict) else {"ok": False, "error": "invalid result"})


@tool(
    name="desktop_session_stop",
    description="Stop the active remote Linux desktop session for this chat.",
)
async def desktop_session_stop() -> str:
    try:
        sid = _require_session_id()
    except RuntimeError as e:
        return _json({"ok": False, "error": str(e)})
    client = get_execution_client()
    res = await client.call("desktop_session_stop", {"session_id": sid})
    if isinstance(res, dict) and res.get("ok"):
        await _sync_platform_session(res, clear=True)
    return _json(res if isinstance(res, dict) else {"ok": False, "error": "invalid result"})
