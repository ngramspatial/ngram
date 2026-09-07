"""Cross-platform proactive messaging tool."""

from __future__ import annotations

import json

from ngram.presence.tools.registry import tool
from ngram.presence.tools.runtime import require_tool_runtime


def _known_contacts(entity: object, platform: str = "") -> list[dict[str, object]]:
    lister = getattr(entity, "list_known_person_routes", None)
    if not callable(lister):
        return []
    try:
        rows = lister(platform)
    except Exception:
        rows = []
    out: list[dict[str, object]] = []
    for r in rows:
        out.append(
            {
                "person_name": str(r.get("person_name") or ""),
                "person_id": str(r.get("person_id") or ""),
                "platform": str(r.get("platform") or ""),
                "chat_type": str(r.get("chat_type") or ""),
                "target": str(r.get("channel") or ""),
                "last_seen_at": float(r.get("at") or 0.0),
            }
        )
    return out


@tool(
    name="send_message_to",
    description=(
        "Send a message to someone on a platform (not the current chat unless that is the resolved target). "
        "You can provide explicit platform+target, or target_person to resolve from known contacts. "
        "For target_person, first call returns a confirmation payload; call again with confirm=true to send.\n\n"
        "Voice vs text: If the user asks for a speech, voice message, voice note, read aloud, say it out loud, "
        "TTS, or audio for that person, you MUST set as_voice=true (Edge-TTS; requires ngram[voice]). "
        "Do not treat 'speech' or 'tell them … in your voice' as plain text — that is voice delivery. "
        "Repeat as_voice and voice_id on the confirmation call when using target_person.\n\n"
        "Do not use speak() for another person; speak only reaches the current chat. Use this tool with as_voice."
    ),
)
async def send_message_to(
    message: str,
    platform: str = "",
    target: str = "",
    target_person: str = "",
    confirm: bool = False,
    prefer_private: bool = True,
    as_voice: bool = False,
    voice_id: str = "",
) -> str:
    msg = (message or "").strip()
    if not msg:
        return json.dumps({"error": "message is required"})

    pf = (platform or "").strip().lower()
    if pf == "auto":
        pf = ""
    tgt = (target or "").strip()
    person = (target_person or "").strip()

    ctx = require_tool_runtime()
    entity = ctx.entity

    # Friendly shorthand: if only target is provided with no platform, treat it as a person reference.
    if not person and tgt and not pf:
        person = tgt
        tgt = ""

    resolved: dict[str, object] | None = None
    if person:
        resolver = getattr(entity, "resolve_person_route", None)
        if not callable(resolver):
            return json.dumps({"error": "entity route resolver unavailable"})
        route = resolver(person, platform=pf, prefer_private=bool(prefer_private))
        if not isinstance(route, dict):
            return json.dumps(
                {
                    "ok": False,
                    "error": f"unknown target_person: {person}",
                    "known_contacts": _known_contacts(entity, pf),
                },
                ensure_ascii=False,
            )
        pf = str(route.get("platform") or "").strip().lower()
        tgt = str(route.get("channel") or "").strip()
        resolved = {
            "person_name": str(route.get("person_name") or ""),
            "person_id": str(route.get("person_id") or ""),
            "chat_type": str(route.get("chat_type") or ""),
        }
        if not bool(confirm):
            return json.dumps(
                {
                    "ok": False,
                    "needs_confirmation": True,
                    "platform": pf,
                    "target": tgt,
                    "resolved": resolved,
                    "message_preview": msg[:300],
                    "as_voice": bool(as_voice),
                    "voice_id": (voice_id or "").strip(),
                    "instruction": (
                        "Call send_message_to again with the same arguments (including as_voice and voice_id) "
                        "and confirm=true to send."
                    ),
                },
                ensure_ascii=False,
            )

    if not pf or not tgt:
        return json.dumps(
            {
                "error": "Need destination. Provide platform+target, or provide target_person.",
                "known_contacts": _known_contacts(entity, pf),
            },
            ensure_ascii=False,
        )

    sender = getattr(entity, "send_message_to_platform", None)
    if not callable(sender):
        return json.dumps({"error": "entity messaging bridge unavailable"})
    try:
        await sender(
            pf,
            tgt,
            msg,
            as_voice=bool(as_voice),
            voice_id=(voice_id or "").strip(),
        )
        return json.dumps(
            {
                "ok": True,
                "platform": pf,
                "target": tgt,
                "resolved": resolved,
                "as_voice": bool(as_voice),
                "voice_id": (voice_id or "").strip() or None,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e), "platform": pf, "target": tgt})


@tool(
    name="list_known_contacts",
    description="List known people/routes this entity can currently message based on prior interactions.",
)
async def list_known_contacts(platform: str = "") -> str:
    ctx = require_tool_runtime()
    entity = ctx.entity
    pf = (platform or "").strip().lower()
    return json.dumps(
        {
            "contacts": _known_contacts(entity, pf),
            "platform_filter": pf,
        },
        ensure_ascii=False,
    )


def _dm_targets(entity: object) -> list[dict[str, object]]:
    """Distinct Telegram/Discord users from known routes with ids suitable for send_dm."""
    lister = getattr(entity, "list_known_person_routes", None)
    if not callable(lister):
        return []
    try:
        rows = lister("")
    except Exception:
        rows = []
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, object]] = []
    for r in rows:
        pf = str(r.get("platform") or "").strip().lower()
        if pf not in ("telegram", "discord"):
            continue
        pid = str(r.get("person_id") or "").strip()
        if not pid:
            continue
        key = (pf, pid)
        if key in seen:
            continue
        seen.add(key)
        note = (
            "Telegram: use user_id as the DM chat_id (user must have started the bot)."
            if pf == "telegram"
            else "Discord: user_id is the account snowflake; user must share a server with the bot."
        )
        out.append(
            {
                "user_id": pid,
                "display_name": str(r.get("person_name") or ""),
                "platform": pf,
                "last_seen_chat_type": str(r.get("chat_type") or ""),
                "last_interaction_at": float(r.get("at") or 0.0),
                "note": note,
            }
        )
    out.sort(key=lambda x: float(x.get("last_interaction_at") or 0.0), reverse=True)
    return out


@tool(
    name="send_dm",
    description=(
        "Send a private direct message by user id, or list_dm_targets first. "
        "Use list_targets=true to fetch user_id values from people this entity has seen on Telegram/Discord. "
        "On Telegram, user_id is the numeric Telegram user id (same as private chat_id). "
        "On Discord, user_id is the member snowflake. "
        "First send attempt returns needs_confirmation; call again with confirm=true to deliver.\n\n"
        "Voice vs text: If the user asks for a speech, voice message, voice note, read aloud, TTS, or audio "
        "to that DM recipient, you MUST set as_voice=true (Edge-TTS; requires ngram[voice]). "
        "Phrases like 'send them a good night speech' mean voice, not a text bubble. "
        "Repeat as_voice and voice_id on the confirmation call.\n\n"
        "Do not use speak() for another user; speak only reaches the current chat. Use send_dm with as_voice."
    ),
)
async def send_dm(
    message: str = "",
    user_id: str = "",
    platform: str = "",
    target_person: str = "",
    confirm: bool = False,
    list_targets: bool = False,
    as_voice: bool = False,
    voice_id: str = "",
) -> str:
    ctx = require_tool_runtime()
    entity = ctx.entity
    inp = ctx.inp

    if list_targets:
        targets = _dm_targets(entity)
        return json.dumps(
            {
                "ok": True,
                "targets": targets,
                "count": len(targets),
                "hint": "Pick user_id + platform from this list, then call send_dm with message, user_id, platform, confirm=true.",
            },
            ensure_ascii=False,
        )

    msg = (message or "").strip()
    if not msg:
        return json.dumps({"error": "message is required (or set list_targets=true to list ids)"})

    pf = (platform or "").strip().lower()
    if pf == "auto":
        pf = ""
    if not pf and inp is not None:
        pf = str(inp.platform or "").strip().lower()
    if pf not in ("telegram", "discord"):
        return json.dumps(
            {
                "ok": False,
                "error": "platform must be telegram or discord (or omit to use current chat platform)",
                "targets": _dm_targets(entity),
            },
            ensure_ascii=False,
        )

    uid = (user_id or "").strip()
    person = (target_person or "").strip()
    if not uid and not person:
        return json.dumps(
            {
                "ok": False,
                "error": "Provide user_id or target_person, or list_targets=true.",
                "targets": _dm_targets(entity),
            },
            ensure_ascii=False,
        )

    resolved_name = ""
    if not uid and person:
        resolver = getattr(entity, "resolve_person_route", None)
        if not callable(resolver):
            return json.dumps({"error": "entity route resolver unavailable"})
        route = resolver(person, platform=pf, prefer_private=True)
        if not isinstance(route, dict):
            return json.dumps(
                {
                    "ok": False,
                    "error": f"unknown target_person: {person}",
                    "targets": _dm_targets(entity),
                },
                ensure_ascii=False,
            )
        uid = str(route.get("person_id") or "").strip()
        resolved_name = str(route.get("person_name") or "")
        rpf = str(route.get("platform") or "").strip().lower()
        if rpf and rpf != pf:
            return json.dumps(
                {
                    "ok": False,
                    "error": f"resolved contact is on {rpf}, not {pf}",
                    "resolved": route,
                },
                ensure_ascii=False,
            )
        if not uid:
            return json.dumps({"ok": False, "error": "resolved route has no person_id"})

    sender = getattr(entity, "send_dm_to_user", None)
    if not callable(sender):
        return json.dumps({"error": "entity does not support send_dm_to_user"})

    if not confirm:
        return json.dumps(
            {
                "ok": False,
                "needs_confirmation": True,
                "platform": pf,
                "user_id": uid,
                "resolved_name": resolved_name,
                "message_preview": msg[:300],
                "as_voice": bool(as_voice),
                "voice_id": (voice_id or "").strip(),
                "instruction": (
                    "Call send_dm again with the same message, user_id, platform, as_voice, voice_id, "
                    "and confirm=true."
                ),
            },
            ensure_ascii=False,
        )

    try:
        await sender(
            pf,
            uid,
            msg,
            as_voice=bool(as_voice),
            voice_id=(voice_id or "").strip(),
        )
        return json.dumps(
            {
                "ok": True,
                "platform": pf,
                "user_id": uid,
                "resolved_name": resolved_name,
                "as_voice": bool(as_voice),
                "voice_id": (voice_id or "").strip() or None,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps(
            {"ok": False, "error": str(e), "platform": pf, "user_id": uid},
            ensure_ascii=False,
        )
