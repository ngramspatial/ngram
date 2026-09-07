"""Browser tools: RPC controls plus local Playwright screenshot capture."""

from __future__ import annotations

import json
import time
from pathlib import Path

from ngram.presence.tools.execution_rpc import get_execution_client
from ngram.presence.tools.registry import tool
from ngram.presence.tools.runtime import require_tool_runtime


def _session_id() -> str:
    ctx = require_tool_runtime()
    return str(ctx.state.get("browser_session_id") or "").strip()


def _set_session_id(v: str) -> None:
    ctx = require_tool_runtime()
    if v:
        ctx.state["browser_session_id"] = v


def _json(res: dict) -> str:
    return json.dumps(res, ensure_ascii=False)


@tool(
    name="browser_navigate",
    description="Open a URL in a real browser and get the rendered page content. Unlike fetch_url, this runs JavaScript and handles SPAs.",
)
async def browser_navigate(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return json.dumps({"error": "empty url"})
    client = get_execution_client()
    payload = {"url": u}
    sid = _session_id()
    if sid:
        payload["session_id"] = sid
    res = await client.call("browser_navigate", payload)
    if isinstance(res, dict) and res.get("session_id"):
        _set_session_id(str(res["session_id"]))
    return _json(res if isinstance(res, dict) else {"ok": False, "error": "invalid result"})


@tool(
    name="browser_screenshot",
    description="Take a screenshot of a web page so you can see it",
)
async def browser_screenshot(url: str = "") -> str:
    client = get_execution_client()
    payload: dict[str, str] = {}
    u = (url or "").strip()
    if u:
        payload["url"] = u
    sid = _session_id()
    if sid:
        payload["session_id"] = sid
    res = await client.call("browser_screenshot", payload)
    if isinstance(res, dict) and res.get("session_id"):
        _set_session_id(str(res["session_id"]))
    return _json(res if isinstance(res, dict) else {"ok": False, "error": "invalid result"})


@tool(
    name="browser_click",
    description="Click an element on the current browser page",
)
async def browser_click(selector: str) -> str:
    sel = (selector or "").strip()
    if not sel:
        return json.dumps({"error": "empty selector"})
    client = get_execution_client()
    payload = {"selector": sel}
    sid = _session_id()
    if sid:
        payload["session_id"] = sid
    res = await client.call("browser_click", payload)
    if isinstance(res, dict) and res.get("session_id"):
        _set_session_id(str(res["session_id"]))
    return _json(res if isinstance(res, dict) else {"ok": False, "error": "invalid result"})


@tool(
    name="browser_type",
    description="Type text into a form field on the current browser page",
)
async def browser_type(selector: str, text: str) -> str:
    sel = (selector or "").strip()
    if not sel:
        return json.dumps({"error": "empty selector"})
    client = get_execution_client()
    payload = {"selector": sel, "text": text or ""}
    sid = _session_id()
    if sid:
        payload["session_id"] = sid
    res = await client.call("browser_type", payload)
    if isinstance(res, dict) and res.get("session_id"):
        _set_session_id(str(res["session_id"]))
    return _json(res if isinstance(res, dict) else {"ok": False, "error": "invalid result"})


@tool(
    name="send_screenshot",
    description=(
        "Open a URL with local Playwright, take a screenshot, and send it to the current chat as an attachment. "
        "Requires tools.browser.enabled: true in entity YAML plus pip install 'ngram[browser]' and "
        "playwright install chromium on the worker. Use when someone asks for a webpage screenshot."
    ),
)
async def send_screenshot(
    url: str,
    full_page: bool = True,
    wait_seconds: float = 1.5,
    width: int = 1366,
    height: int = 900,
) -> str:
    u = (url or "").strip()
    if not u:
        return json.dumps({"ok": False, "error": "url is required"})
    if not (u.startswith("http://") or u.startswith("https://")):
        return json.dumps({"ok": False, "error": "url must start with http:// or https://"})

    ctx = require_tool_runtime()
    entity = ctx.entity
    if not entity._tool_enabled("browser", False):
        return json.dumps(
            {
                "ok": False,
                "error": (
                    "send_screenshot is disabled: set tools.browser.enabled: true under your entity YAML "
                    "(e.g. configs/entities/canary.yaml) and restart the worker. "
                    "The tool stays visible in your kit so you can see this message instead of false "
                    "'tool missing' replies."
                ),
            },
            ensure_ascii=False,
        )

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return json.dumps(
            {
                "ok": False,
                "error": (
                    "playwright not installed. Install extras: pip install 'ngram[browser]' "
                    "and run: playwright install chromium"
                ),
            }
        )

    data_dir = Path(entity.config.db_path()).expanduser().resolve().parent
    out_dir = data_dir / "screenshots"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"shot_{int(time.time())}.jpg"

    w = max(640, min(int(width), 2560))
    h = max(480, min(int(height), 1600))
    ws = max(0.0, min(float(wait_seconds), 15.0))
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(viewport={"width": w, "height": h})
            page = await context.new_page()
            await page.goto(u, wait_until="networkidle", timeout=60000)
            if ws > 0:
                await page.wait_for_timeout(int(ws * 1000))
            await page.screenshot(
                path=str(out_path),
                full_page=bool(full_page),
                type="jpeg",
                quality=78,
            )
            await context.close()
            await browser.close()
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)[:500], "url": u})

    if ctx.platform is None or ctx.inp is None:
        return json.dumps(
            {
                "ok": False,
                "error": "no active chat context to send image",
                "url": u,
                "path": str(out_path),
            },
            ensure_ascii=False,
        )
    send_attach = getattr(ctx.platform, "send_attachment_bytes", None)
    send_img = getattr(ctx.platform, "send_image", None)
    if not callable(send_attach) and not callable(send_img):
        return json.dumps(
            {
                "ok": False,
                "error": "current platform cannot send attachments/images",
                "url": u,
                "path": str(out_path),
            },
            ensure_ascii=False,
        )
    try:
        if callable(send_attach):
            data = out_path.read_bytes()
            await send_attach(
                ctx.inp.channel,
                data,
                content_type="application/octet-stream",
                filename=f"screenshot_{int(time.time())}.jpg",
            )
        else:
            # Fallback for platforms without attachment bytes API.
            await send_img(ctx.inp.channel, str(out_path))
    except Exception as e:
        return json.dumps(
            {
                "ok": False,
                "error": f"screenshot delivery failed: {str(e)[:300]}",
                "url": u,
                "path": str(out_path),
            },
            ensure_ascii=False,
        )

    return json.dumps(
        {
            "ok": True,
            "url": u,
            "path": str(out_path),
            "sent": True,
            "full_page": bool(full_page),
            "viewport": {"width": w, "height": h},
        },
        ensure_ascii=False,
    )
