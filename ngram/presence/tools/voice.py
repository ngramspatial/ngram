"""Voice output tool using Edge-TTS."""

from __future__ import annotations

import json
import tempfile
import uuid
from pathlib import Path
from typing import Any

from ngram.presence.tools.registry import tool
from ngram.presence.tools.runtime import require_tool_runtime


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = {**base}
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _voice_cfg(entity: Any) -> dict[str, Any]:
    base = entity.config.harness.tools if isinstance(entity.config.harness.tools, dict) else {}
    over = entity.config.raw.get("tools") if isinstance(entity.config.raw, dict) else {}
    merged = _deep_merge(base, over if isinstance(over, dict) else {})
    return merged.get("voice") if isinstance(merged.get("voice"), dict) else {}


def _voice_id_for_entity(entity: Any) -> str:
    cfg = _voice_cfg(entity)
    return str(cfg.get("voice_id") or "en-US-GuyNeural")


def _voice_id_default() -> str:
    ctx = require_tool_runtime()
    return _voice_id_for_entity(ctx.entity)


def _set_runtime_voice_id(entity: Any, voice_id: str) -> bool:
    raw = entity.config.raw if isinstance(entity.config.raw, dict) else None
    if raw is None:
        return False
    tools_cfg = raw.get("tools")
    if not isinstance(tools_cfg, dict):
        tools_cfg = {}
        raw["tools"] = tools_cfg
    voice_cfg = tools_cfg.get("voice")
    if not isinstance(voice_cfg, dict):
        voice_cfg = {}
        tools_cfg["voice"] = voice_cfg
    voice_cfg["voice_id"] = voice_id
    return True


def _voice_source_for_entity(entity: Any) -> str:
    raw = entity.config.raw if isinstance(entity.config.raw, dict) else {}
    tools_cfg = raw.get("tools") if isinstance(raw, dict) else None
    voice_cfg = tools_cfg.get("voice") if isinstance(tools_cfg, dict) else None
    if isinstance(voice_cfg, dict) and str(voice_cfg.get("voice_id") or "").strip():
        return "entity_runtime_override"
    return "config_default"


async def synthesize_tts_to_file(entity: Any, text: str, voice_id: str = "") -> Path:
    """Render ``text`` with Edge-TTS to a temp ``.mp3``. Caller must delete the file when done."""
    try:
        import edge_tts  # type: ignore[import-not-found]
    except ImportError as e:
        raise RuntimeError("edge-tts not installed. Install with: pip install ngram[voice]") from e
    msg = (text or "").strip()
    if not msg:
        raise ValueError("empty message for TTS")
    vid = (voice_id or "").strip() or _voice_id_for_entity(entity)
    out_path = Path(tempfile.gettempdir()) / f"ngram_voice_{uuid.uuid4().hex[:12]}.mp3"
    com = edge_tts.Communicate(text=msg, voice=vid)
    await com.save(str(out_path))
    return out_path


async def _list_edge_tts_voices() -> list[dict[str, str]]:
    import edge_tts  # type: ignore[import-not-found]

    rows = await edge_tts.list_voices()
    out: list[dict[str, str]] = []
    for row in rows:
        voice_id = str(row.get("ShortName") or "").strip()
        if not voice_id:
            continue
        out.append(
            {
                "voice_id": voice_id,
                "name": str(row.get("FriendlyName") or "").strip(),
                "locale": str(row.get("Locale") or "").strip(),
                "gender": str(row.get("Gender") or "").strip(),
            }
        )
    out.sort(key=lambda r: r["voice_id"])
    return out


@tool(
    name="get_tts_voice",
    description="Return the currently active TTS voice id and where it comes from (config default vs runtime override).",
)
async def get_tts_voice() -> str:
    ctx = require_tool_runtime()
    entity = ctx.entity
    return json.dumps(
        {
            "ok": True,
            "voice_id": _voice_id_for_entity(entity),
            "source": _voice_source_for_entity(entity),
        },
        ensure_ascii=False,
    )


@tool(
    name="list_tts_voices",
    description="List available Edge-TTS voices. Use this before setting a new TTS voice.",
)
async def list_tts_voices(query: str = "", limit: int = 25) -> str:
    q = (query or "").strip().lower()
    lim = max(1, min(int(limit or 25), 100))
    try:
        rows = await _list_edge_tts_voices()
    except ImportError:
        return json.dumps({"ok": False, "error": "edge-tts not installed. Install with: pip install ngram[voice]"})
    except Exception as e:
        return json.dumps({"ok": False, "error": f"failed to list voices: {e}"})
    if q:
        rows = [
            r
            for r in rows
            if q in r["voice_id"].lower()
            or q in r["name"].lower()
            or q in r["locale"].lower()
            or q in r["gender"].lower()
        ]
    return json.dumps(
        {
            "ok": True,
            "query": q,
            "count": len(rows),
            "voices": rows[:lim],
        },
        ensure_ascii=False,
    )


@tool(
    name="set_tts_voice",
    description="Set the active TTS voice id for this running entity session. Use list_tts_voices first to find valid voice ids.",
)
async def set_tts_voice(voice_id: str) -> str:
    chosen = (voice_id or "").strip()
    if not chosen:
        return json.dumps({"ok": False, "error": "voice_id is required"})
    ctx = require_tool_runtime()
    entity = ctx.entity
    prev = _voice_id_for_entity(entity)

    try:
        rows = await _list_edge_tts_voices()
        known = {r["voice_id"].casefold(): r["voice_id"] for r in rows}
        canon = known.get(chosen.casefold())
        if canon is None:
            suggestions = [
                r["voice_id"]
                for r in rows
                if chosen.casefold() in r["voice_id"].casefold()
                or chosen.casefold() in r["name"].casefold()
            ][:10]
            return json.dumps(
                {
                    "ok": False,
                    "error": f"unknown voice_id: {chosen}",
                    "previous_voice_id": prev,
                    "suggestions": suggestions,
                    "hint": "Call list_tts_voices to browse valid ids.",
                },
                ensure_ascii=False,
            )
        chosen = canon
    except ImportError:
        return json.dumps({"ok": False, "error": "edge-tts not installed. Install with: pip install ngram[voice]"})
    except Exception as e:
        return json.dumps({"ok": False, "error": f"failed to validate voice id: {e}"})

    if not _set_runtime_voice_id(entity, chosen):
        return json.dumps({"ok": False, "error": "voice config unavailable on entity"})

    return json.dumps(
        {
            "ok": True,
            "voice_id": chosen,
            "previous_voice_id": prev,
            "source": "entity_runtime_override",
            "note": "Active for this running entity process.",
        },
        ensure_ascii=False,
    )


@tool(
    name="speak",
    description=(
        "Deliver a voice message (TTS) only in the **current** chat (whoever is talking to the bot here). "
        "To send speech/voice to another person, use send_dm or send_message_to with as_voice=true — "
        "do not use speak for third-party recipients. Optional voice_id overrides for this utterance only. "
        "After calling, continue in plain text (never output HTML/audio tags)."
    ),
)
async def speak(text: str, voice_id: str = "") -> str:
    msg = (text or "").strip()
    if not msg:
        return json.dumps({"error": "empty text"})
    ctx = require_tool_runtime()
    if ctx.platform is None or ctx.inp is None:
        return json.dumps({"error": "no active platform/channel for voice delivery"})
    send_audio = getattr(ctx.platform, "send_audio", None)
    if not callable(send_audio):
        return json.dumps({"error": "current platform does not support send_audio"})
    active_voice_id = (voice_id or "").strip() or _voice_id_default()
    out_path: Path | None = None
    try:
        out_path = await synthesize_tts_to_file(ctx.entity, msg, active_voice_id)
        delivered = await send_audio(ctx.inp.channel, str(out_path))
        if delivered is False:
            return json.dumps(
                {
                    "ok": False,
                    "voice_id": active_voice_id,
                    "error": "platform failed to deliver voice message",
                },
                ensure_ascii=False,
            )
        ctx.state["voice_sent"] = True
        return json.dumps(
            {
                "ok": True,
                "voice_id": active_voice_id,
                "delivered": True,
                "note": "Voice message delivered to active chat.",
            },
            ensure_ascii=False,
        )
    except (RuntimeError, ValueError) as e:
        return json.dumps({"error": str(e), "voice_id": active_voice_id})
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e), "voice_id": active_voice_id})
    finally:
        if out_path is not None:
            try:
                out_path.unlink(missing_ok=True)
            except OSError:
                pass
