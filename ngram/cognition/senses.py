"""Multimodal input normalization for Gemma / Ollama (images, optional audio)."""

from __future__ import annotations

import base64
import mimetypes
from typing import Any

from ngram.models import Input
from ngram.inference.protocol import InferenceProvider
from ngram.inference.types import ChatCompletionResult


def _guess_mime_from_path(path: str) -> str | None:
    t, _ = mimetypes.guess_type(path)
    return t


def _image_detail_for_budget(image_token_budget: int) -> str:
    """OpenAI-compat ``detail`` hint; backends may ignore it."""
    if image_token_budget <= 200:
        return "low"
    if image_token_budget <= 400:
        return "auto"
    return "high"


def input_to_message_content(
    inp: Input,
    image_token_budget: int = 280,
    *,
    speaker_prefix: str = "",
) -> str | list[dict[str, Any]]:
    """Build OpenAI-style content: text plus optional image_url / input_audio parts."""
    detail = _image_detail_for_budget(image_token_budget)
    text0 = f"{speaker_prefix}{inp.text}" if speaker_prefix else inp.text
    parts: list[dict[str, Any]] = [{"type": "text", "text": text0}]

    for img in inp.images[:3]:
        b64 = img.get("base64")
        mime = img.get("mime") or img.get("mime_type")
        path = img.get("path")
        if not b64 and path:
            try:
                with open(path, "rb") as f:
                    b64 = base64.standard_b64encode(f.read()).decode("ascii")
                if not mime:
                    mime = _guess_mime_from_path(str(path)) or "image/jpeg"
            except OSError:
                continue
        if not b64:
            continue
        if not mime:
            mime = "image/jpeg"
        parts.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{mime};base64,{b64}",
                    "detail": detail,
                },
            }
        )

    for aud in inp.audio[:1]:
        b64a = aud.get("base64")
        if not b64a:
            continue
        fmt = (aud.get("format") or "wav").lower().lstrip(".")
        if fmt in ("oga", "ogg", "opus"):
            fmt = "wav" if fmt == "opus" else "ogg"
        parts.append(
            {
                "type": "input_audio",
                "input_audio": {"data": b64a, "format": fmt},
            }
        )

    if len(parts) == 1:
        return text0
    return parts


def clip_text_for_budget(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


async def transcribe_audio_attachment(
    client: InferenceProvider,
    model: str,
    *,
    base64_audio: str,
    audio_format: str = "ogg",
    max_tokens: int = 512,
    num_ctx: int | None = None,
) -> str:
    """
    Best-effort speech-to-text via the reflex-sized model with native audio, if the backend supports it.
    Returns empty string on failure.
    """
    fmt = (audio_format or "wav").lower().lstrip(".")
    if fmt in ("oga", "opus"):
        fmt = "ogg"
    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                "Transcribe the speech. Output only the spoken words in plain text, "
                "no preamble or labels."
            ),
        },
        {"type": "input_audio", "input_audio": {"data": base64_audio, "format": fmt}},
    ]
    try:
        res = await client.chat_completion(
            model,
            [{"role": "user", "content": content}],
            temperature=0.2,
            max_tokens=max_tokens,
            think=False,
            num_ctx=num_ctx,
        )
        if not isinstance(res, ChatCompletionResult):
            return ""
        return (res.content or "").strip()
    except Exception:
        return ""
