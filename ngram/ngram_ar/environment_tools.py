"""Author skies and lighting through the connected renderer's confirmed results."""

from __future__ import annotations

from typing import Any

from ngram.ngram_ar.spatial_sessions import connected_spatial_session
from ngram.presence.tools.runtime import get_tool_runtime


async def ar_environment(command: str, payload: dict[str, Any] | None = None) -> str:
    if command not in {"capabilities", "inspect", "configure", "clear"}:
        return "[spatial: invalid environment command; use capabilities]"
    if payload is not None and not isinstance(payload, dict):
        return "[spatial: payload must be a JSON object]"
    ctx = get_tool_runtime()
    session = connected_spatial_session(ctx.entity, ctx.inp) if ctx else None
    if session is None:
        return "[spatial: unavailable; no connected Spatial session]"
    return await session.dispatch(
        {"type": "action:world", "command": "environment", "payload": {**(payload or {}), "command": command}},
        timeout=120 if command == "configure" else 8,
    )


def register_environment_tools(registry):
    registry.register_fn(
        "ar_environment",
        "Create immersive skies and control actual scene lighting. capabilities gives the exact contract. "
        "configure sets a procedural atmospheric sun/sky or a 360-degree panorama (JPG/PNG/WebP/HDR/EXR), "
        "PBR environment reflections, sky rotation, brightness, blur, key/fill/rim/hemisphere/ambient lights, "
        "fog, exposure and ground visibility. inspect returns the applied state; clear restores the preset. "
        "Build scenery with Blender and render it using ar_blender render options projection=equirectangular, "
        "style=scene. Pass its returned skybox to configure; hybrid assets stay on your execution host and "
        "stream through Spatial, with no external image storage. Keep nearby interactive geometry as GLB/Figments. "
        "XR keeps real-world passthrough unless sky.immersive is explicitly enabled. "
        "Changes load atomically; a failed load retains the old environment. Use ar_request_capture to see the result. "
        "Never poll continuously or start model loops to watch the sky.",
        ar_environment,
        parameters_schema={"type": "object", "properties": {
            "command": {"type": "string", "enum": ["capabilities", "inspect", "configure", "clear"]},
            "payload": {"type": "object", "description": "{sky?,lighting?,fog?,exposure?,ground?} per capabilities. Omitted blocks stay unchanged; supplied blocks replace; null resets a block."},
        }, "required": ["command"]},
    )
