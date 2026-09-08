"""Correlated Spatial imagery, returned to the active model tool round."""

import json

from ngram.inference.visual_results import visual_result
from ngram.ngram_ar.spatial_sessions import connected_spatial_session
from ngram.presence.tools.runtime import get_tool_runtime


async def request_capture(prompt: str = "", options: dict | None = None) -> str:
    if options is not None and not isinstance(options, dict):
        return json.dumps({"ok": False, "error": "options must be an object"})
    ctx = get_tool_runtime()
    session = connected_spatial_session(ctx.entity, ctx.inp) if ctx else None
    if not session:
        return "[spatial: unavailable; no connected Spatial session]"
    result = await session.dispatch({"type": "action:request_capture", "prompt": str(prompt)[:500], "options": options or {}}, timeout=45)
    try:
        receipt = json.loads(result)
        content = receipt.get("result", {})
        if receipt.get("status") == "completed" and content.get("images"):
            return visual_result({k: v for k, v in content.items() if k != "images"}, content["images"])
    except (ValueError, TypeError, AttributeError) as error:
        if result.startswith("{"):
            return json.dumps({"ok": False, "error": f"Invalid visual result: {error}"})
    return result


CAPTURE_HELP = (
    "See actual rendered Spatial images in this tool result, in the current turn. "
    "options: {target?:entityId, views?:[front|back|left|right|top|bottom|perspective], "
    "position?:[x,y,z], lookAt?:[x,y,z], orbit?:[azimuthDegrees,elevationDegrees], "
    "distance?:metres, projection?:perspective|orthographic, size?:256..1536, "
    "isolate?:boolean, style?:scene|studio|clay|wireframe, node?:uniqueMeshName, "
    "includeColliders?:boolean}. Omit options to see the user's virtual view. "
    "Targeted inspection uses a separate camera; it never moves the human/headset. "
    "views returns up to four angles. Scene style shows real app lighting; studio/clay/wireframe are diagnostic. "
    "View sharing is controlled by the human. No webcam or passthrough pixels are included. "
    "Use images to inspect before and after edits; no continuous polling."
)
