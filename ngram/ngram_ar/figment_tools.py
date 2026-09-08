"""Figment authoring, interaction, physics and portable publishing tools."""

from __future__ import annotations

import json
from typing import Any

from ngram.ngram_ar.spatial_sessions import connected_spatial_session
from ngram.presence.tools.registry import ToolRegistry
from ngram.presence.tools.runtime import get_tool_runtime

FIGMENT_TOOLS = {
    "ar_figment": (
        ["capabilities", "inspect", "attach", "configure", "detach"],
        "Turn existing Spatial objects or Blender models into programmable Figments. "
        "Call capabilities for exact schemas and editable presets, then attach with payload {id,definition,preset?,physics?,start?}. "
        "Named parts, anchors, grips, typed properties, actions and behavior belong to the object. "
        "configure merges top-level definition fields; maps replace in full. New behavior starts paused by default.",
    ),
    "ar_figment_physics": (
        ["physics"],
        "Adjust a Figment or Spatial object's physical properties using payload {id,physics}. "
        "Mass, gravity, friction, bounce (restitution), linear/angular damping, axis locks and compound colliders are editable. "
        "GLB models require explicit colliders. Collider sensor=true generates enter/exit events. "
        "Use ar_world apply for hinge, slider, ball, rope and spring joints; bind their IDs in definition.joints.",
    ),
    "ar_figment_behavior": (
        ["behavior"],
        "Write arbitrary local sandboxed JavaScript for a Figment using payload {id,source,hz?,start?}, "
        "or control it using {id,action:pause|resume|reset}. Return tick/event handlers. "
        "Use api.self, part(name), anchor(name), property(name), setProperty, patch, impulse, motor and signal. "
        "Code runs in the room without model calls. No network/DOM/imports. Human grabs take priority.",
    ),
    "ar_figment_interact": (
        ["properties", "action"],
        "Interact with a Figment using the same properties/actions as the human. "
        "properties payload {id,values:{name:value}}; action payload {id,action,data?}. "
        "Inspect the Figment first for available names. Actions are local events; they never start another model turn.",
    ),
    "ar_figment_library": (
        ["library", "publish", "export", "import", "place"],
        "Publish and share portable versioned Figments. publish {id} freezes a self-contained local library version; "
        "export {packageId} downloads a .figment.json file on the human's device, returning a small receipt. "
        "import {url,place?,position?} or {package,place?,position?} verifies a package; place {packageId,position?} creates an editable copy with new IDs, paused. "
        "Packages include model assets, code, physics, properties, grips and optional editable .blend source. "
        "No public upload occurs. A title/version is immutable; bump the version for revisions. "
        "Do not stream binary assets through model output, repeatedly poll, or replay an uncertain placement.",
    ),
}


def register_figment_tools(registry: ToolRegistry) -> None:
    for name, (commands, description) in FIGMENT_TOOLS.items():
        def handler_factory(allowed: list[str]):
            async def handler(command: str, payload: dict[str, Any] | None = None) -> str:
                if command not in allowed:
                    return "[spatial: invalid Figment command]"
                if payload is not None and not isinstance(payload, dict):
                    return "[spatial: payload must be a JSON object]"
                if len(json.dumps(payload or {})) > 2_000_000:
                    return "[spatial: payload too large; import packages by URL or through the Objects panel]"
                ctx = get_tool_runtime()
                session = connected_spatial_session(ctx.entity, ctx.inp) if ctx else None
                if session is None:
                    return "[spatial: unavailable; no connected Spatial session]"
                return await session.dispatch(
                    {"type": "action:world", "command": "figment", "payload": {**(payload or {}), "command": command}},
                    timeout=120 if command in {"publish", "export", "import", "place"} else 8,
                )
            return handler

        registry.register_fn(
            name, description, handler_factory(commands),
            parameters_schema={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "enum": commands},
                    "payload": {"type": "object", "description": "Command payload. Call ar_figment capabilities for the complete Figment/physics/behavior contract."},
                },
                "required": ["command"],
            },
        )
