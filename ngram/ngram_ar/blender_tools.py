"""Bridge execution-host Blender projects to the connected Spatial body."""

from __future__ import annotations

import asyncio
import json
import re
import uuid
import base64
import hashlib
from typing import Any

from ngram.ngram_ar.spatial_sessions import connected_spatial_session
from ngram.presence.tools.execution_rpc import get_execution_client
from ngram.presence.tools.runtime import require_tool_runtime
from ngram.inference.visual_results import visual_result


async def ar_blender(command: str, payload: dict[str, Any] | None = None) -> str:
    if command not in {"capabilities", "create", "execute", "publish", "render", "status", "stop", "show"}:
        return json.dumps({"ok": False, "error": "Use capabilities for Blender commands"})
    if payload is not None and not isinstance(payload, dict):
        return json.dumps({"ok": False, "error": "payload must be an object"})
    ctx = require_tool_runtime()
    client = get_execution_client()
    args = dict(payload or {})
    args["command"] = "status" if command == "show" else command
    if command in {"execute", "publish", "render"}:
        args.setdefault("request_id", uuid.uuid4().hex)
    project = args.get("project_id")
    last_revision = -1
    last_state = None
    preview_result = None

    async def show(snapshot):
        nonlocal last_revision, last_state, preview_result
        session = connected_spatial_session(ctx.entity, ctx.inp)
        if not session or not re.fullmatch(r"[a-z0-9-]+", session.shell_slug):
            return
        revision = (snapshot.get("snapshot") or {}).get("revision", 0)
        if revision < 1 or (revision == last_revision and snapshot.get("state") == last_state):
            return
        base = f"/api/shells/{session.shell_slug}/blender/{project}"
        preview_result = await session.dispatch({"type": "action:world", "command": "blender", "payload": {
            "projectId": project, "name": snapshot.get("name"), "revision": revision,
            "base": base, "position": args.get("position"), "state": snapshot.get("state"),
            "stateOnly": command in {"stop", "status"},
        }})
        last_revision = revision
        last_state = snapshot.get("state")

    from ngram.work_activity import safe_label, work_progress

    last_observed = None

    async def report_blender(result):
        nonlocal last_observed
        # A successful status poll is connectivity, not new Blender progress.
        snapshot = result.get("snapshot") or {}
        observed = (result.get("state"), snapshot.get("revision"), result.get("updated"), len(result.get("renders") or []))
        if observed != last_observed:
            last_observed = observed
            await work_progress(project=safe_label(project or ""), revision=snapshot.get("revision"))

    try:
        result = await client.call("blender", args)
        await report_blender(result)
        project = result.get("project_id") or project
        if project and command in {"execute", "publish", "show", "stop", "status"}:
            await show(result)
        # This is transport progress, not an agent polling turn. No model calls.
        while result.get("ok") and result.get("state") == "working" and command in {"execute", "publish", "render"}:
            await asyncio.sleep(0.5)
            result = await client.call("blender", {"command": "status", "project_id": project})
            await report_blender(result)
            if not result.get("ok"):
                break
            if result.get("job_id") != args["request_id"]:
                return json.dumps({"ok": False, "error": "Blender edit was superseded by another job"})
            if command != "render":
                await show(result)
    except asyncio.CancelledError:
        if project and command in {"execute", "publish", "render"}:
            await asyncio.shield(client.call("blender", {"command": "stop", "project_id": project, "request_id": args.get("request_id")}))
        raise
    if preview_result is not None:
        result["spatial_delivery"] = preview_result
    if command in {"render", "execute"} and result.get("state") == "ready" and result.get("renders"):
        images = []
        for render in result["renders"][-4:]:
            data = bytearray()
            while True:
                chunk = await client.call("blender", {"command": "artifact", "project_id": project, "render_id": render["render_id"], "offset": len(data)})
                size = chunk.get("size")
                if not chunk.get("ok") or type(size) is not int or not 0 < size <= 4*1024*1024 or chunk.get("offset") != len(data):
                    raise ValueError("Blender render transfer failed")
                decoded = base64.b64decode(chunk.get("data", ""), validate=True)
                if not decoded or len(data) + len(decoded) > size:
                    raise ValueError("Invalid Blender render chunk")
                data.extend(decoded)
                if len(data) == size:
                    break
            if hashlib.sha256(data).hexdigest() != render.get("sha256"):
                raise ValueError("Blender render checksum mismatch")
            images.append({"url": "data:image/jpeg;base64," + base64.b64encode(data).decode(), "label": f"Blender {render['style']} review · {render['render_id']} · Z-up"})
        return visual_result(result, images)
    return json.dumps(result, ensure_ascii=False)


def register_blender_tools(registry):
    registry.register_fn(
        "ar_blender",
        "Author REAL Blender projects on your execution computer and show live GLB previews in Spatial. "
        "The existing hybrid execution configuration selects the computer, independently of your model. "
        "Call capabilities to check installation; if missing, install Blender with your shell tools on that host. "
        "create makes a persistent project; execute runs Python with bpy and auto-publishes the result. "
        "Call publish() inside a long Python script to show intermediate geometry as you work. "
        "Use result = {...} for structured inspection. status inspects; show reconnects an existing project to Spatial; "
        "stop terminates Blender while retaining the last saved checkpoint. Each project keeps the same Spatial object, "
        "so human placement/scale survive edits. ar_world programs/controls can interact with the resulting asset. "
        "render gives YOU actual images in the current tool result. Call capabilities for camera, object isolation, studio/scene/clay lighting and resolution options. "
        "Use render_view(**options) inside execute to return up to four working views. Then use ar_request_capture for the actual Spatial appearance. "
        "Inspect visually before and after material/geometry edits; successful loading alone does not verify appearance. "
        "The executable path is remembered per project. No model calls are needed for preview delivery. Use status only when needed, never a continuous model polling loop.",
        ar_blender,
        parameters_schema={"type": "object", "properties": {
            "command": {"type": "string", "enum": ["capabilities", "create", "execute", "publish", "render", "status", "stop", "show"]},
            "payload": {"type": "object", "description": "create: {name,project_id?,blend_file?}. execute: {project_id,source,executable?,timeout?,request_id?,position?:[x,y,z]}. Other commands: {project_id}. Coordinates are metres; source is Blender Z-up, preview is Spatial Y-up. position only places the initial preview."},
        }, "required": ["command"]},
    )
