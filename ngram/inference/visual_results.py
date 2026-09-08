"""Ephemeral tool images: real multimodal input, small textual logs and history."""

from __future__ import annotations

import base64
import json
import re
from typing import Any

MAX_IMAGE_BYTES = 4 * 1024 * 1024


class VisualResult(str):
    """Behaves as a text receipt everywhere except the inference adapter."""

    def __new__(cls, text: str, images: list[dict[str, Any]]):
        instance = super().__new__(cls, text)
        instance.images = images
        return instance


def visual_result(receipt: dict[str, Any], images: list[dict[str, Any]]) -> VisualResult:
    if not isinstance(images, list) or not 1 <= len(images) <= 4:
        raise ValueError("A visual result needs 1..4 images")
    checked = []
    for image in images:
        url = image.get("url", "")
        if not isinstance(url, str) or len(url) > MAX_IMAGE_BYTES * 4 // 3 + 100:
            raise ValueError("Visual image exceeds 4 MB")
        match = re.fullmatch(r"data:(image/(?:png|jpeg|webp));base64,([A-Za-z0-9+/]*={0,2})", url)
        if not match:
            raise ValueError("Visual images must be inline PNG, JPEG or WebP")
        data = base64.b64decode(match[2], validate=True)
        if not data or len(data) > MAX_IMAGE_BYTES:
            raise ValueError("Invalid visual image size")
        signature = (data.startswith(b"\x89PNG\r\n\x1a\n") if match[1] == "image/png" else
                     data.startswith(b"\xff\xd8\xff") if match[1] == "image/jpeg" else
                     data.startswith(b"RIFF") and data[8:12] == b"WEBP")
        if not signature:
            raise ValueError("Visual image content does not match its format")
        checked.append({"url": url, "label": str(image.get("label", "View"))[:300]})
    text = json.dumps({**receipt, "images": [{"label": i["label"], "attached": True} for i in checked]}, ensure_ascii=False)
    return VisualResult(text, checked)


def content_blocks(result: VisualResult) -> list[dict[str, Any]]:
    blocks = [{"type": "text", "text": str(result)}]
    for image in result.images:
        blocks.extend([
            {"type": "text", "text": image["label"]},
            {"type": "image_url", "image_url": {"url": image["url"], "detail": "high"}},
        ])
    return blocks


def text_history(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**m, "content": str(m["content"])} if isinstance(m.get("content"), VisualResult) else m for m in messages]


def expire_visuals(messages: list[dict[str, Any]], keep: int = 2) -> list[dict[str, Any]]:
    """Keep the two latest inspection calls in the active loop, never in saved history."""
    left = keep
    result = []
    for message in reversed(messages):
        if isinstance(message.get("content"), VisualResult):
            left -= 1
            if left < 0:
                message = {**message, "content": str(message["content"]) + " [Image expired; capture again if needed.]"}
        result.append(message)
    return list(reversed(result))


def chat_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Chat Completions accepts images in user content, after all tool receipts."""
    result, pending = [], []
    for message in messages:
        if message.get("role") != "tool" and pending:
            result.append({"role": "user", "content": pending})
            pending = []
        content = message.get("content")
        if isinstance(content, VisualResult):
            pending.extend(content_blocks(content))
            message = {**message, "content": str(content)}
        result.append(message)
    if pending:
        result.append({"role": "user", "content": pending})
    return result
