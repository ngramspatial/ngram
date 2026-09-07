"""Persist inbound platform blobs (images/audio) to the configured attachment store."""

from __future__ import annotations

import binascii
import hashlib
from dataclasses import replace
from typing import TYPE_CHECKING, Any

import structlog

from ngram.models import Input

if TYPE_CHECKING:
    from ngram.storage.protocol import AttachmentBlobStore

log = structlog.get_logger("ngram.storage.attachment_ingestion")

# Align with Discord/Telegram platform caps (bytes).
_MAX_IMAGE = 12_000_000
_MAX_AUDIO = 8_000_000


def _b64_to_bytes(raw: str | bytes) -> bytes | None:
    if raw is None or raw == b"":
        return None
    if isinstance(raw, bytes):
        s = raw
    else:
        t = str(raw).strip()
        if not t:
            return None
        try:
            s = t.encode("ascii")
        except UnicodeEncodeError:
            return None
    try:
        return binascii.a2b_base64(s)
    except binascii.Error:
        return None


def _content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:24]


async def persist_incoming_attachments(store: AttachmentBlobStore, inp: Input) -> Input:
    """Write each image/audio payload that carries ``base64`` into the blob store.

    Sets ``storage_ref`` on each part dict (canonical URI/path from ``put``) and
    ``inp.metadata["attachment_storage_refs"]`` to the list of refs (for episodes / audits).
    On failure for one blob, logs and continues so perception can still proceed in-memory.
    """
    refs: list[str] = []
    new_images: list[dict[str, Any]] = []
    for i, img in enumerate(inp.images):
        d = dict(img)
        if d.get("storage_ref"):
            new_images.append(d)
            refs.append(str(d["storage_ref"]))
            continue
        b64 = d.get("base64")
        if not b64:
            new_images.append(d)
            continue
        data = _b64_to_bytes(str(b64))
        if not data:
            log.warning("attachment_image_decode_failed", platform=inp.platform, index=i)
            new_images.append(d)
            continue
        if len(data) > _MAX_IMAGE:
            log.warning("attachment_image_too_large", platform=inp.platform, index=i, size=len(data))
            new_images.append(d)
            continue
        mime = str(d.get("mime") or d.get("mime_type") or "image/jpeg")
        key = f"{inp.platform}:{inp.channel}:{inp.person_id}:img:{i}:{_content_hash(data)}"
        try:
            ref = await store.put(key, data, mime)
            d["storage_ref"] = ref
            refs.append(ref)
        except Exception as e:
            log.warning(
                "attachment_image_store_failed",
                platform=inp.platform,
                index=i,
                error=str(e),
            )
        new_images.append(d)

    new_audio: list[dict[str, Any]] = []
    for i, aud in enumerate(inp.audio):
        d = dict(aud)
        if d.get("storage_ref"):
            new_audio.append(d)
            refs.append(str(d["storage_ref"]))
            continue
        b64 = d.get("base64")
        if not b64:
            new_audio.append(d)
            continue
        data = _b64_to_bytes(str(b64))
        if not data:
            log.warning("attachment_audio_decode_failed", platform=inp.platform, index=i)
            new_audio.append(d)
            continue
        if len(data) > _MAX_AUDIO:
            log.warning("attachment_audio_too_large", platform=inp.platform, index=i, size=len(data))
            new_audio.append(d)
            continue
        fmt = str(d.get("format") or "ogg").lower().lstrip(".")
        if fmt in ("ogg", "oga", "opus"):
            ct = "audio/ogg"
        elif fmt in ("mp3", "mpeg"):
            ct = "audio/mpeg"
        elif fmt == "wav":
            ct = "audio/wav"
        elif fmt == "m4a":
            ct = "audio/mp4"
        else:
            ct = "application/octet-stream"
        key = f"{inp.platform}:{inp.channel}:{inp.person_id}:aud:{i}:{_content_hash(data)}"
        try:
            ref = await store.put(key, data, ct)
            d["storage_ref"] = ref
            refs.append(ref)
        except Exception as e:
            log.warning(
                "attachment_audio_store_failed",
                platform=inp.platform,
                index=i,
                error=str(e),
            )
        new_audio.append(d)

    meta = {**inp.metadata, "attachment_storage_refs": refs}
    return replace(inp, images=new_images, audio=new_audio, metadata=meta)


async def load_stored_attachment(store: AttachmentBlobStore, storage_ref: str) -> bytes | None:
    """Load bytes for a ``storage_ref`` previously returned by ``put`` (outbound / replay)."""
    if not storage_ref:
        return None
    try:
        return await store.get(storage_ref)
    except Exception as e:
        log.warning("attachment_load_failed", ref=storage_ref[:120], error=str(e))
        return None


def attachment_refs_episode_note(refs: list[str]) -> str:
    """Compact suffix for episodic ``raw_context`` (bounded)."""
    if not refs:
        return ""
    shown = refs[:6]
    tail = f" (+{len(refs) - 6} more)" if len(refs) > 6 else ""
    return "\n[attachment_storage: " + "; ".join(s[:500] for s in shown) + tail + "]"
