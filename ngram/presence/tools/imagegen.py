"""Image generation tool with fal or local HTTP backends."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from typing import Any

import aiohttp

from ngram.presence.tools.execution_rpc import get_execution_client
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


def _imagegen_cfg() -> dict[str, Any]:
    ctx = require_tool_runtime()
    entity = ctx.entity
    base = entity.config.harness.tools if isinstance(entity.config.harness.tools, dict) else {}
    over = entity.config.raw.get("tools") if isinstance(entity.config.raw, dict) else {}
    merged = _deep_merge(base, over if isinstance(over, dict) else {})
    img = merged.get("imagegen")
    return img if isinstance(img, dict) else {}


async def _fetch_bytes(url: str) -> bytes:
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as s:
        async with s.get(url) as r:
            r.raise_for_status()
            return await r.read()


async def _generate_local(prompt: str, width: int, height: int, base_url: str) -> tuple[bytes | None, str | None]:
    api = (base_url or "").strip() or "http://127.0.0.1:7860/sdapi/v1/txt2img"
    payload = {"prompt": prompt, "width": width, "height": height, "steps": 24}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as s:
            async with s.post(api, json=payload) as r:
                txt = await r.text()
                if r.status >= 400:
                    return None, f"http {r.status}: {txt[:300]}"
                data = json.loads(txt) if txt else {}
        images = data.get("images") if isinstance(data, dict) else None
        if not images:
            return None, "no image in local backend response"
        raw = str(images[0] or "")
        if "," in raw and raw.startswith("data:"):
            raw = raw.split(",", 1)[1]
        return base64.b64decode(raw), None
    except Exception as e:
        return None, str(e)


async def _generate_fal(prompt: str, width: int, height: int, api_key_env: str) -> tuple[bytes | None, str | None]:
    try:
        import fal_client  # type: ignore[import-not-found]
    except ImportError:
        return None, "fal-client not installed. Install with: pip install ngram[imagegen]"
    key = (os.environ.get(api_key_env) or "").strip()
    if not key:
        return None, f"missing {api_key_env}"
    prev = os.environ.get("FAL_KEY")
    os.environ["FAL_KEY"] = key
    try:
        result = await asyncio.to_thread(
            fal_client.subscribe,
            "fal-ai/fast-sdxl",
            arguments={
                "prompt": prompt,
                "image_size": {"width": int(width), "height": int(height)},
            },
            with_logs=False,
        )
        images = result.get("images") if isinstance(result, dict) else None
        if not images:
            return None, "fal returned no images"
        url = str((images[0] or {}).get("url") or "")
        if not url:
            return None, "fal response missing image url"
        data = await _fetch_bytes(url)
        return data, None
    except Exception as e:
        return None, str(e)
    finally:
        if prev is None:
            os.environ.pop("FAL_KEY", None)
        else:
            os.environ["FAL_KEY"] = prev


@tool(
    name="generate_image",
    description="Create an image from a text description. Use this when you want to express something visually, illustrate an idea, or just because you're feeling creative.",
)
async def generate_image(prompt: str, width: int = 1024, height: int = 1024) -> str:
    p = (prompt or "").strip()
    if not p:
        return json.dumps({"error": "empty prompt"})
    width = max(256, min(int(width or 1024), 1536))
    height = max(256, min(int(height or 1024), 1536))
    cfg = _imagegen_cfg()
    backend = str(cfg.get("backend") or "fal").strip().lower()
    if backend not in ("fal", "local"):
        backend = "fal"

    if backend == "local":
        img_bytes, err = await _generate_local(p, width, height, str(cfg.get("local_url") or ""))
    else:
        api_key_env = str(cfg.get("fal_api_key_env") or "FAL_API_KEY")
        img_bytes, err = await _generate_fal(p, width, height, api_key_env)
    if err or not img_bytes:
        return json.dumps({"ok": False, "backend": backend, "error": err or "generation failed"})

    client = get_execution_client()
    out_dir = client.workspace_root / ".ngram" / "generated"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"image_{int(time.time())}_{os.getpid()}.png"
    out_path.write_bytes(img_bytes)

    sent = False
    ctx = require_tool_runtime()
    if ctx.platform is not None and ctx.inp is not None:
        send_img = getattr(ctx.platform, "send_image", None)
        if callable(send_img):
            try:
                await send_img(ctx.inp.channel, str(out_path))
                sent = True
            except Exception:
                sent = False
    return json.dumps(
        {"ok": True, "backend": backend, "path": str(out_path), "sent": sent},
        ensure_ascii=False,
    )
