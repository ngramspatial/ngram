"""Spatial tools for the entity's connected body, from any conversation surface."""

from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any
from urllib.parse import parse_qs, urlparse

from ngram.ngram_ar.spatial_sessions import SpatialSessions, connected_spatial_session
from ngram.ngram_ar.figment_tools import FIGMENT_TOOLS, register_figment_tools
from ngram.presence.tools.registry import ToolRegistry
from ngram.presence.tools.runtime import get_tool_runtime

_MOVE_TARGETS = frozenset({"user", "left", "right", "forward", "away", "random"})
_SPEEDS = frozenset({"walk", "fast"})
_GESTURES = frozenset(
    {
        "wave",
        "greet",
        "nod",
        "point",
        "shrug",
        "celebrate",
        "explain",
        "dance",
        "texting",
        "coding",
        "enteringCode",
        "thinking",
        "no",
        "handRaising",
        "terrified",
        "drunkWalk",
        "breakdancing",
        "twerking",
        "macarena",
        "hipHop",
        "twistDance",
        "cheering",
        "clapping",
    }
)
_EMOTIONS = frozenset(
    {
        "happy",
        "excited",
        "curious",
        "thoughtful",
        "concerned",
        "attentive",
        "calm",
    }
)
_LOOK = frozenset({"user", "away"})
_PANEL_TYPES = frozenset({"card", "markdown", "code", "image", "chart", "html"})
_ENVIRONMENTS = frozenset({"default", "workshop", "cozy", "nature", "space", "party", "focus", "night"})
_TEXT_POSITIONS = frozenset({"here", "left", "right", "above", "front"})
_SCENE_OBJECT_SHAPES = frozenset({"cube", "sphere", "cylinder", "cone", "torus", "plane"})
_TOY_TYPES = frozenset({"ball", "bouncy_ball", "beach_ball", "dice", "marble"})
_MOTION_ROOT_TARGETS = frozenset({"stationary", "user", "forward", "left", "right"})
_SPATIAL_CAPABILITIES = (
    *FIGMENT_TOOLS,
    "ar_blender",
    "ar_environment",
    "ar_world",
    "ar_inspect_surface",
    "ar_move_to",
    "ar_gesture",
    "ar_emote",
    "ar_look_at",
    "ar_go_idle",
    "ar_speak",
    "ar_show_panel",
    "ar_hide_panel",
    "ar_terminal",
    "ar_open_browser",
    "ar_play_youtube",
    "ar_spawn_object",
    "ar_spawn_toy",
    "ar_spawn_text",
    "ar_remove_object",
    "ar_clear_objects",
    "ar_draw_annotation",
    "ar_set_environment",
    "ar_request_capture",
    "ar_generate_motion",
)


def _spatial_available() -> bool:
    ctx = get_tool_runtime()
    return ctx is not None and (
        connected_spatial_session(ctx.entity, ctx.inp) is not None
        or (
            ctx.inp is not None and ctx.inp.platform == "ngram_ar"
            and not isinstance(getattr(ctx.entity, "_ngram_ar_sessions", None), SpatialSessions)
        )
    )


async def _dispatch(action: dict[str, Any]) -> str | None:
    ctx = get_tool_runtime()
    session = connected_spatial_session(ctx.entity, ctx.inp)
    if session is not None:
        return await session.dispatch(action)
    if (
        ctx.inp is None or ctx.inp.platform != "ngram_ar"
        or isinstance(getattr(ctx.entity, "_ngram_ar_sessions", None), SpatialSessions)
    ):
        return "[spatial: unavailable; no connected Spatial session]"
    ts = int(time.time() * 1000)
    sid = str(ctx.inp.channel or "") if ctx.inp else ""
    action.setdefault("timestamp", ts)
    action.setdefault("sessionId", sid)
    sender = getattr(ctx.platform, "send_spatial_action", None)
    if callable(sender):
        try:
            if await sender(dict(action)):
                return
        except Exception:
            # Preserve the action for the end-of-turn bridge response when a
            # live surface disconnects or rejects the immediate delivery.
            pass
    ctx.state.setdefault("_ngram_ar_spatial_actions", []).append(action)


async def _ar_inspect_surface() -> str:
    """Return the live AR contract without confusing it with workspace files."""
    if not _spatial_available():
        return "[spatial: unavailable; no connected Spatial session]"
    ctx = get_tool_runtime()
    metadata = ctx.inp.metadata if ctx.inp and isinstance(ctx.inp.metadata, dict) else {}
    live_context = metadata.get("ngram_ar_context")
    session = connected_spatial_session(ctx.entity, ctx.inp)
    if session is not None:
        live_context = session.context()
    return json.dumps(
        {
            "surface": "ngram_ar",
            "sessionId": session.session_id if session else str(ctx.inp.channel or ""),
            "connected": session is not None,
            "routing": "Prefer the originating connected body; otherwise the newest connected body.",
            "spatialTools": list(_SPATIAL_CAPABILITIES),
            "contract": (
                "These are live API capabilities supplied by the embodied surface. "
                "They are not files in the entity data workspace."
            ),
            "liveContext": live_context if session else str(live_context or "")[:8000],
        },
        ensure_ascii=False,
    )


async def _ar_move_to(target: str, speed: str = "walk") -> str:
    if not _spatial_available():
        return "[spatial: unavailable; no connected Spatial session]"
    t = (target or "").strip().lower()
    if t not in _MOVE_TARGETS:
        return f"[invalid target: use one of {sorted(_MOVE_TARGETS)}]"
    sp = (speed or "walk").strip().lower()
    if sp not in _SPEEDS:
        sp = "walk"
    receipt = await _dispatch({"type": "action:move_to", "target": t, "speed": sp})
    return receipt or "[spatial: move_to queued]"


async def _ar_gesture(gesture: str) -> str:
    if not _spatial_available():
        return "[spatial: unavailable; no connected Spatial session]"
    g = (gesture or "").strip()
    if g not in _GESTURES:
        return f"[invalid gesture: use one of {sorted(_GESTURES)}]"
    receipt = await _dispatch({"type": "action:gesture", "gesture": g})
    return receipt or "[spatial: gesture queued]"


async def _ar_emote(emotion: str, intensity: float = 0.6) -> str:
    if not _spatial_available():
        return "[spatial: unavailable; no connected Spatial session]"
    e = (emotion or "").strip().lower()
    if e not in _EMOTIONS:
        return f"[invalid emotion: use one of {sorted(_EMOTIONS)}]"
    try:
        x = float(intensity)
    except (TypeError, ValueError):
        x = 0.6
    x = max(0.0, min(1.0, x))
    receipt = await _dispatch({"type": "action:emote", "emotion": e, "intensity": x})
    return receipt or "[spatial: emote queued]"


async def _ar_look_at(target: str) -> str:
    if not _spatial_available():
        return "[spatial: unavailable; no connected Spatial session]"
    t = (target or "").strip().lower()
    if t not in _LOOK:
        return f"[invalid look_at target: use one of {sorted(_LOOK)}]"
    receipt = await _dispatch({"type": "action:look_at", "target": t})
    return receipt or "[spatial: look_at queued]"


async def _ar_go_idle() -> str:
    if not _spatial_available():
        return "[spatial: unavailable; no connected Spatial session]"
    receipt = await _dispatch({"type": "action:go_idle"})
    return receipt or "[spatial: go_idle queued]"


async def _ar_speak(text: str) -> str:
    """Mid-turn speech in AR (TTS + lip sync), before final reply."""
    if not _spatial_available():
        return "[spatial: unavailable; no connected Spatial session]"
    t = (text or "").strip()
    if not t:
        return "[empty ar_speak]"
    receipt = await _dispatch({"type": "action:speak", "text": t})
    return receipt or "[spatial: speak queued]"


async def _ar_show_panel(
    panel_id: str,
    content: str,
    title: str = "",
    panel_type: str = "markdown",
) -> str:
    if not _spatial_available():
        return "[spatial: unavailable; no connected Spatial session]"
    pid = (panel_id or "panel").strip()[:80]
    body = (content or "").strip()[:20_000]
    kind = (panel_type or "markdown").strip().lower()
    if kind not in _PANEL_TYPES:
        kind = "markdown"
    if not body:
        return "[empty panel content]"
    receipt = await _dispatch(
        {
            "type": "action:show_panel",
            "panel": {
                "id": pid,
                "type": kind,
                "title": (title or "").strip()[:160],
                "content": body,
            },
        }
    )
    return receipt or f"[spatial: panel {pid} queued]"


async def _ar_hide_panel(panel_id: str) -> str:
    if not _spatial_available():
        return "[spatial: unavailable; no connected Spatial session]"
    pid = (panel_id or "").strip()[:80]
    if not pid:
        return "[missing panel id]"
    receipt = await _dispatch({"type": "action:hide_panel", "panelId": pid})
    return receipt or f"[spatial: hide panel {pid} queued]"


async def _ar_terminal(
    output: str,
    tool: str = "",
    error: bool = False,
    clear: bool = False,
) -> str:
    if not _spatial_available():
        return "[spatial: unavailable; no connected Spatial session]"
    receipt = await _dispatch(
        {
            "type": "action:terminal_output",
            "output": (output or "")[:4000],
            "tool": (tool or "").strip()[:120],
            "error": bool(error),
            "clear": bool(clear),
        }
    )
    return receipt or "[spatial: terminal update queued]"


async def _ar_open_browser(url: str, title: str = "") -> str:
    if not _spatial_available():
        return "[spatial: unavailable; no connected Spatial session]"
    target = (url or "").strip()[:2048]
    if not target.startswith(("https://", "http://")):
        return "[browser URL must use http or https]"
    receipt = await _dispatch(
        {
            "type": "action:open_browser",
            "url": target,
            "title": (title or "").strip()[:160],
        }
    )
    return receipt or "[spatial: browser queued]"


def _youtube_video_id(value: str) -> str:
    """Accept a YouTube video ID or URL and return a safe video ID."""
    candidate = (value or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{6,32}", candidate):
        return candidate
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return ""
    host = (parsed.hostname or "").lower().removeprefix("www.")
    video_id = ""
    if host == "youtu.be":
        video_id = parsed.path.strip("/").split("/")[0]
    elif host in {
        "youtube.com",
        "music.youtube.com",
        "m.youtube.com",
        "youtube-nocookie.com",
    }:
        video_id = (parse_qs(parsed.query).get("v") or [""])[0]
        if not video_id:
            parts = [part for part in parsed.path.split("/") if part]
            if len(parts) >= 2 and parts[0] in {"embed", "shorts", "live"}:
                video_id = parts[1]
    return video_id if re.fullmatch(r"[A-Za-z0-9_-]{6,32}", video_id) else ""


async def _ar_play_youtube(
    video: str,
    title: str = "",
    volume: float = 50,
    start_at: float = 0,
) -> str:
    """Open a specific video in the surface's dedicated media player."""
    if not _spatial_available():
        return "[spatial: unavailable; no connected Spatial session]"
    video_id = _youtube_video_id(video)
    if not video_id:
        return "[provide a specific YouTube video ID or watch URL]"
    try:
        normalized_volume = max(0, min(100, round(float(volume))))
    except (TypeError, ValueError):
        normalized_volume = 50
    try:
        normalized_start = max(0, float(start_at))
    except (TypeError, ValueError):
        normalized_start = 0
    receipt = await _dispatch(
        {
            "type": "action:play_youtube",
            "videoId": video_id,
            "title": (title or "").strip()[:160],
            "volume": normalized_volume,
            "startAt": normalized_start,
        }
    )
    return receipt or f"[media: YouTube video {video_id} queued]"


async def _ar_spawn_text(
    object_id: str,
    text: str,
    position: str = "front",
    color: str = "#ffffff",
    size: float = 1.0,
) -> str:
    if not _spatial_available():
        return "[spatial: unavailable; no connected Spatial session]"
    oid = (object_id or "text").strip()[:80]
    body = (text or "").strip()[:2000]
    pos = (position or "front").strip().lower()
    if pos not in _TEXT_POSITIONS:
        pos = "front"
    try:
        scale = max(0.2, min(4.0, float(size)))
    except (TypeError, ValueError):
        scale = 1.0
    if not body:
        return "[empty spatial text]"
    receipt = await _dispatch(
        {
            "type": "action:spawn_text",
            "objectId": oid,
            "text": body,
            "position": pos,
            "color": (color or "#ffffff")[:32],
            "size": scale,
        }
    )
    return receipt or f"[spatial: text {oid} queued]"


async def _ar_spawn_object(
    object_id: str,
    shape: str,
    position: str = "front",
    color: str = "#ffffff",
    size: float = 0.15,
    label: str = "",
    physics: bool = True,
) -> str:
    """Place a geometric primitive in the scene, with physics by default."""
    if not _spatial_available():
        return "[spatial: unavailable; no connected Spatial session]"
    oid = (object_id or "object").strip()[:80]
    shape_name = (shape or "cube").strip().lower()
    if shape_name not in _SCENE_OBJECT_SHAPES:
        return f"[invalid shape: use one of {sorted(_SCENE_OBJECT_SHAPES)}]"
    pos = (position or "front").strip().lower()
    if pos not in _TEXT_POSITIONS:
        pos = "front"
    try:
        normalized_size = max(0.03, min(2.0, float(size)))
    except (TypeError, ValueError):
        normalized_size = 0.15
    action: dict[str, Any] = {
        "type": "action:spawn_object",
        "objectId": oid,
        "shape": shape_name,
        "position": pos,
        "color": (color or "#ffffff")[:32],
        "size": normalized_size,
        "physics": bool(physics),
    }
    clean_label = (label or "").strip()
    if clean_label:
        action["label"] = clean_label[:160]
    receipt = await _dispatch(action)
    return receipt or f"[spatial: {shape_name} {oid} queued with physics={bool(physics)}]"


async def _ar_spawn_toy(
    object_id: str,
    toy_type: str = "ball",
    position: str = "front",
    color: str = "",
    impulse: dict[str, Any] | None = None,
) -> str:
    """Place a ready-to-play physics toy such as a ball or die."""
    if not _spatial_available():
        return "[spatial: unavailable; no connected Spatial session]"
    oid = (object_id or "toy").strip()[:80]
    kind = (toy_type or "ball").strip().lower()
    if kind not in _TOY_TYPES:
        return f"[invalid toy_type: use one of {sorted(_TOY_TYPES)}]"
    pos = (position or "front").strip().lower()
    if pos not in _TEXT_POSITIONS:
        pos = "front"
    action: dict[str, Any] = {
        "type": "action:spawn_toy",
        "objectId": oid,
        "toyType": kind,
        "position": pos,
    }
    if color:
        action["color"] = str(color)[:32]
    if isinstance(impulse, dict):
        try:
            vector = {
                axis: max(-20.0, min(20.0, float(impulse.get(axis, 0.0))))
                for axis in ("x", "y", "z")
            }
        except (TypeError, ValueError):
            return "[impulse must contain numeric x, y, and z values]"
        if any(vector.values()):
            action["impulse"] = vector
    receipt = await _dispatch(action)
    return receipt or f"[spatial: physics toy {kind} {oid} queued]"


async def _ar_remove_object(object_id: str) -> str:
    if not _spatial_available():
        return "[spatial: unavailable; no connected Spatial session]"
    oid = (object_id or "").strip()[:80]
    if not oid:
        return "[object_id is required]"
    receipt = await _dispatch({"type": "action:remove_object", "objectId": oid})
    return receipt or f"[spatial: object {oid} removal queued]"


async def _ar_clear_objects() -> str:
    if not _spatial_available():
        return "[spatial: unavailable; no connected Spatial session]"
    receipt = await _dispatch({"type": "action:clear_objects"})
    return receipt or "[spatial: scene object clearing queued]"


async def _ar_draw_annotation(
    drawing_id: str,
    text: str,
    x: float,
    y: float,
    z: float,
    color: str = "#ffffff",
) -> str:
    if not _spatial_available():
        return "[spatial: unavailable; no connected Spatial session]"
    try:
        position = {"x": float(x), "y": float(y), "z": float(z)}
    except (TypeError, ValueError):
        return "[annotation coordinates must be numbers]"
    receipt = await _dispatch(
        {
            "type": "action:draw_annotation",
            "drawingId": (drawing_id or "annotation").strip()[:80],
            "position": position,
            "text": (text or "").strip()[:1000],
            "color": (color or "#ffffff")[:32],
            "style": "callout",
        }
    )
    return receipt or "[spatial: annotation queued]"


async def _ar_set_environment(preset: str) -> str:
    if not _spatial_available():
        return "[spatial: unavailable; no connected Spatial session]"
    name = (preset or "default").strip().lower()
    if name not in _ENVIRONMENTS:
        return f"[invalid environment: use one of {sorted(_ENVIRONMENTS)}]"
    receipt = await _dispatch({"type": "action:set_environment", "preset": name})
    return receipt or f"[spatial: environment {name} queued]"


async def _ar_request_capture(prompt: str = "", options: dict[str, Any] | None = None) -> str:
    from ngram.ngram_ar.visual_tools import request_capture
    return await request_capture(prompt, options)


async def _ar_generate_motion(
    prompt: str,
    duration_seconds: float = 4.0,
    root_target: str = "stationary",
    loop: bool = False,
) -> str:
    """Request generated humanoid motion from the configured GPU provider."""
    if not _spatial_available():
        return "[spatial: unavailable; no connected Spatial session]"
    text = (prompt or "").strip()[:1000]
    if not text:
        return "[empty motion prompt]"
    try:
        duration = max(0.5, min(30.0, float(duration_seconds)))
    except (TypeError, ValueError):
        duration = 4.0
    target = (root_target or "stationary").strip().lower()
    if target not in _MOTION_ROOT_TARGETS:
        target = "stationary"
    receipt = await _dispatch(
        {
            "type": "action:generate_motion",
            "requestId": uuid.uuid4().hex,
            "prompt": text,
            "durationSeconds": duration,
            "constraints": {"rootTarget": target},
            "loop": bool(loop),
        }
    )
    return receipt or "[spatial: generated motion requested]"


_SCHEMA_MOVE_TO: dict[str, Any] = {
    "type": "object",
    "properties": {
        "target": {
            "type": "string",
            "enum": sorted(_MOVE_TARGETS),
            "description": "Where to move in the scene.",
        },
        "speed": {
            "type": "string",
            "enum": sorted(_SPEEDS),
            "description": "Movement speed.",
        },
    },
    "required": ["target"],
}

_SCHEMA_GESTURE: dict[str, Any] = {
    "type": "object",
    "properties": {
        "gesture": {
            "type": "string",
            "enum": sorted(_GESTURES),
            "description": "Physical gesture to perform.",
        },
    },
    "required": ["gesture"],
}

_SCHEMA_EMOTE: dict[str, Any] = {
    "type": "object",
    "properties": {
        "emotion": {
            "type": "string",
            "enum": sorted(_EMOTIONS),
            "description": "Emotion to express bodily.",
        },
        "intensity": {
            "type": "number",
            "description": "0–1 intensity.",
        },
    },
    "required": ["emotion"],
}

_SCHEMA_LOOK: dict[str, Any] = {
    "type": "object",
    "properties": {
        "target": {
            "type": "string",
            "enum": sorted(_LOOK),
            "description": "Gaze target.",
        },
    },
    "required": ["target"],
}

_SCHEMA_SPEAK: dict[str, Any] = {
    "type": "object",
    "properties": {
        "text": {
            "type": "string",
            "description": "Words to speak in the AR surface (mid-turn).",
        },
    },
    "required": ["text"],
}

_GO_IDLE_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}, "required": []}

_SCHEMA_SHOW_PANEL: dict[str, Any] = {
    "type": "object",
    "properties": {
        "panel_id": {"type": "string", "description": "Stable panel identifier."},
        "content": {"type": "string", "description": "Panel body."},
        "title": {"type": "string", "description": "Optional title."},
        "panel_type": {"type": "string", "enum": sorted(_PANEL_TYPES)},
    },
    "required": ["panel_id", "content"],
}
_SCHEMA_HIDE_PANEL: dict[str, Any] = {
    "type": "object",
    "properties": {"panel_id": {"type": "string"}},
    "required": ["panel_id"],
}
_SCHEMA_TERMINAL: dict[str, Any] = {
    "type": "object",
    "properties": {
        "output": {"type": "string", "description": "Short visible terminal text."},
        "tool": {"type": "string"},
        "error": {"type": "boolean"},
        "clear": {"type": "boolean"},
    },
    "required": ["output"],
}
_SCHEMA_BROWSER: dict[str, Any] = {
    "type": "object",
    "properties": {
        "url": {
            "type": "string",
            "description": (
                "HTTP(S) webpage to open. Do not use this for YouTube playback; "
                "use ar_play_youtube with a specific video instead."
            ),
        },
        "title": {"type": "string"},
    },
    "required": ["url"],
}
_SCHEMA_YOUTUBE: dict[str, Any] = {
    "type": "object",
    "properties": {
        "video": {
            "type": "string",
            "description": "A specific YouTube video ID or watch URL.",
        },
        "title": {"type": "string", "description": "Song or video title."},
        "volume": {"type": "number", "minimum": 0, "maximum": 100},
        "start_at": {"type": "number", "minimum": 0},
    },
    "required": ["video"],
}
_SCHEMA_SPAWN_TEXT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "object_id": {"type": "string"},
        "text": {"type": "string"},
        "position": {"type": "string", "enum": sorted(_TEXT_POSITIONS)},
        "color": {"type": "string"},
        "size": {"type": "number", "minimum": 0.2, "maximum": 4.0},
    },
    "required": ["object_id", "text"],
}
_SCHEMA_SPAWN_OBJECT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "object_id": {"type": "string", "description": "Stable unique id for later removal."},
        "shape": {
            "type": "string",
            "enum": sorted(_SCENE_OBJECT_SHAPES),
            "description": "The actual 3D primitive to create. Use sphere for a geometric ball.",
        },
        "position": {"type": "string", "enum": sorted(_TEXT_POSITIONS)},
        "color": {"type": "string"},
        "size": {"type": "number", "minimum": 0.03, "maximum": 2.0},
        "label": {"type": "string", "description": "Optional caption; not a substitute for the shape."},
        "physics": {"type": "boolean", "description": "Enable gravity and collision. Defaults to true."},
    },
    "required": ["object_id", "shape"],
}
_SCHEMA_SPAWN_TOY: dict[str, Any] = {
    "type": "object",
    "properties": {
        "object_id": {"type": "string", "description": "Stable unique id for later removal."},
        "toy_type": {
            "type": "string",
            "enum": sorted(_TOY_TYPES),
            "description": "Ready-made physics toy. For requests to spawn a ball, use ball or bouncy_ball.",
        },
        "position": {"type": "string", "enum": sorted(_TEXT_POSITIONS)},
        "color": {"type": "string"},
        "impulse": {
            "type": "object",
            "description": "Optional initial velocity for tossing the toy.",
            "properties": {
                "x": {"type": "number"},
                "y": {"type": "number"},
                "z": {"type": "number"},
            },
            "required": ["x", "y", "z"],
            "additionalProperties": False,
        },
    },
    "required": ["object_id", "toy_type"],
}
_SCHEMA_REMOVE_OBJECT: dict[str, Any] = {
    "type": "object",
    "properties": {"object_id": {"type": "string"}},
    "required": ["object_id"],
}
_SCHEMA_ANNOTATION: dict[str, Any] = {
    "type": "object",
    "properties": {
        "drawing_id": {"type": "string"},
        "text": {"type": "string"},
        "x": {"type": "number"},
        "y": {"type": "number"},
        "z": {"type": "number"},
        "color": {"type": "string"},
    },
    "required": ["drawing_id", "text", "x", "y", "z"],
}
_SCHEMA_ENVIRONMENT: dict[str, Any] = {
    "type": "object",
    "properties": {"preset": {"type": "string", "enum": sorted(_ENVIRONMENTS)}},
    "required": ["preset"],
}
_SCHEMA_CAPTURE: dict[str, Any] = {
    "type": "object",
    "properties": {"prompt": {"type": "string"}, "options": {"type": "object", "description": "Independent inspection camera, target and diagnostic views; see tool description."}},
    "required": [],
}
_SCHEMA_MOTION: dict[str, Any] = {
    "type": "object",
    "properties": {
        "prompt": {"type": "string", "description": "Physical motion to generate."},
        "duration_seconds": {
            "type": "number",
            "minimum": 0.5,
            "maximum": 30.0,
        },
        "root_target": {"type": "string", "enum": sorted(_MOTION_ROOT_TARGETS)},
        "loop": {"type": "boolean"},
    },
    "required": ["prompt"],
}


async def _ar_world(command: str, payload: dict[str, Any] | None = None) -> str:
    """Query and program the live world's authoritative renderer, from any chat."""
    if not _spatial_available():
        return "[spatial: unavailable; no connected Spatial session]"
    if command not in {"capabilities", "observe", "apply", "events", "program", "pause", "resume", "save", "export", "import", "load", "fork", "undo", "redo", "workshop", "garden", "perform", "assets"}:
        return "[spatial: invalid world command; call capabilities for the contract]"
    if payload is not None and not isinstance(payload, dict):
        return "[spatial: payload must be a JSON object]"
    ctx = get_tool_runtime()
    session = connected_spatial_session(ctx.entity, ctx.inp)
    if session is None:
        return "[spatial: world commands require a live renderer with result support]"
    return await session.dispatch({"type": "action:world", "command": command, "payload": payload or {}})


def register_ngram_ar_spatial_tools(registry: ToolRegistry) -> None:
    from ngram.ngram_ar.blender_tools import register_blender_tools
    register_blender_tools(registry)
    from ngram.ngram_ar.environment_tools import register_environment_tools
    register_environment_tools(registry)
    register_figment_tools(registry)
    registry.register_fn(
        "ar_world",
        "Create, inspect, edit and PROGRAM persistent spatial creations. Call command=capabilities first for the exact contract and JavaScript API. "
        "Use observe for live object IDs, transforms, materials, physics, controls and program status; apply for validated batched geometry/material/group/body/joint edits; "
        "program to install local JavaScript tick/event handlers with explicit entity IDs, parameters and state; events to poll human grabs, releases, control changes and collisions. "
        "Programs and physics run locally with ZERO per-frame model calls. Human grabs take priority. Pause/resume controls creations. "
        "Save/export/import/undo/redo manage worlds. Workshop builds an example kinetic machine using the same API. "
        "Never poll continuously or loop merely to watch a creation; observe when needed, then end the turn. All coordinates are metres and radians.",
        _ar_world,
        parameters_schema={"type": "object", "properties": {
            "command": {"type": "string", "enum": ["capabilities", "observe", "apply", "events", "program", "pause", "resume", "save", "export", "import", "load", "fork", "undo", "redo", "workshop", "garden", "perform", "assets"]},
            "payload": {"type": "object", "description": "Command payload per capabilities. apply: {requestId,baseRevision?,operations:[...]}. program: {command:install,program:{id,source,entityIds,params,state}}. events: {after:cursor}."},
        }, "required": ["command"]},
    )
    registry.register_fn(
        "ar_inspect_surface",
        "Connected Spatial body (available from any chat) — inspect the current embodied surface contract, live context, "
        "and available spatial API tools. Use this instead of scanning workspace files "
        "when asked what the spatial body can do.",
        _ar_inspect_surface,
        parameters_schema={"type": "object", "properties": {}, "required": []},
    )
    registry.register_fn(
        "ar_move_to",
        "Connected Spatial body (available from any chat) — queue movement in space (walk toward user, step aside, etc.). "
        "Call from tool API when the user asks you to move or reposition. Requires a connected Spatial session.",
        _ar_move_to,
        parameters_schema=_SCHEMA_MOVE_TO,
    )
    registry.register_fn(
        "ar_gesture",
        "Connected Spatial body (available from any chat) — queue a physical gesture (wave, nod, point, dance, …).",
        _ar_gesture,
        parameters_schema=_SCHEMA_GESTURE,
    )
    registry.register_fn(
        "ar_emote",
        "Connected Spatial body (available from any chat) — queue a bodily emotional expression.",
        _ar_emote,
        parameters_schema=_SCHEMA_EMOTE,
    )
    registry.register_fn(
        "ar_look_at",
        "Connected Spatial body (available from any chat) — queue gaze toward the user or away.",
        _ar_look_at,
        parameters_schema=_SCHEMA_LOOK,
    )
    registry.register_fn(
        "ar_go_idle",
        "Connected Spatial body (available from any chat) — return to a relaxed idle stance.",
        _ar_go_idle,
        parameters_schema=_GO_IDLE_SCHEMA,
    )
    registry.register_fn(
        "ar_speak",
        "Connected Spatial body (available from any chat) — speak mid-turn (visible + TTS) before your final reply. "
        "Use for reactions while reasoning; final answer can still go in normal reply text.",
        _ar_speak,
        parameters_schema=_SCHEMA_SPEAK,
    )
    registry.register_fn(
        "ar_show_panel",
        "Connected Spatial body (available from any chat) â€” show a readable spatial panel for substantial information.",
        _ar_show_panel,
        parameters_schema=_SCHEMA_SHOW_PANEL,
    )
    registry.register_fn(
        "ar_hide_panel",
        "Connected Spatial body (available from any chat) â€” close a spatial panel by id.",
        _ar_hide_panel,
        parameters_schema=_SCHEMA_HIDE_PANEL,
    )
    registry.register_fn(
        "ar_terminal",
        "Connected Spatial body (available from any chat) â€” write a concise status or result to the spatial terminal.",
        _ar_terminal,
        parameters_schema=_SCHEMA_TERMINAL,
    )
    registry.register_fn(
        "ar_open_browser",
        "Connected Spatial body (available from any chat) â€” open an HTTP(S) page on the spatial browser surface.",
        _ar_open_browser,
        parameters_schema=_SCHEMA_BROWSER,
    )
    registry.register_fn(
        "ar_play_youtube",
        "Connected Spatial body (available from any chat) - play a specific YouTube video in the dedicated media "
        "player. On desktop this opens the desktop player; in immersive AR the surface "
        "uses its headset-safe playback flow. Search for a specific video first when needed.",
        _ar_play_youtube,
        parameters_schema=_SCHEMA_YOUTUBE,
    )
    registry.register_fn(
        "ar_spawn_object",
        "Connected Spatial body (available from any chat) — spawn a real 3D geometric primitive. Use for requests naming a "
        "shape such as cube, sphere, cylinder, cone, torus, or plane. Physics defaults on. "
        "Do not use ar_spawn_text as a substitute for geometry.",
        _ar_spawn_object,
        parameters_schema=_SCHEMA_SPAWN_OBJECT,
    )
    registry.register_fn(
        "ar_spawn_toy",
        "Connected Spatial body (available from any chat) — spawn an interactive physics toy. Use this for balls, bouncy balls, "
        "beach balls, dice, and marbles; use ar_spawn_object for generic geometry.",
        _ar_spawn_toy,
        parameters_schema=_SCHEMA_SPAWN_TOY,
    )
    registry.register_fn(
        "ar_spawn_text",
        "Connected Spatial body (available from any chat) — place visible lettering in the scene. Use only when the user asks "
        "for text or a label; never use it to represent a requested physical object or shape.",
        _ar_spawn_text,
        parameters_schema=_SCHEMA_SPAWN_TEXT,
    )
    registry.register_fn(
        "ar_remove_object",
        "Connected Spatial body (available from any chat) — remove one previously spawned object, toy, text, image, or model by id.",
        _ar_remove_object,
        parameters_schema=_SCHEMA_REMOVE_OBJECT,
    )
    registry.register_fn(
        "ar_clear_objects",
        "Connected Spatial body (available from any chat) — clear all spawned scene objects when the user asks to reset or clean the space.",
        _ar_clear_objects,
        parameters_schema={"type": "object", "properties": {}, "required": []},
    )
    registry.register_fn(
        "ar_draw_annotation",
        "Connected Spatial body (available from any chat) â€” place a labeled callout at scene coordinates.",
        _ar_draw_annotation,
        parameters_schema=_SCHEMA_ANNOTATION,
    )
    registry.register_fn(
        "ar_set_environment",
        "Connected Spatial body (available from any chat) â€” change the shell's environment preset.",
        _ar_set_environment,
        parameters_schema=_SCHEMA_ENVIRONMENT,
    )
    from ngram.ngram_ar.visual_tools import CAPTURE_HELP
    registry.register_fn(
        "ar_request_capture",
        CAPTURE_HELP,
        _ar_request_capture,
        parameters_schema=_SCHEMA_CAPTURE,
    )
    registry.register_fn(
        "ar_generate_motion",
        "Connected Spatial body (available from any chat) â€” request a novel humanoid motion from the configured external "
        "GPU provider. Use only when generated motion adds clear value.",
        _ar_generate_motion,
        parameters_schema=_SCHEMA_MOTION,
    )
