"""FastAPI server for remote hands execution (Playwright browser + local tools)."""

import os
import time
import asyncio
from pathlib import Path
from typing import Dict

from fastapi import FastAPI, Header, HTTPException, Request


# We use a global registry to maintain active browser sessions across RPC calls
_browser_sessions: Dict[str, dict] = {}
_playwright = None

app = FastAPI(title="ngram Hands Server", version="1.0.0")


async def _get_browser_page(session_id: str):
    global _playwright
    from playwright.async_api import async_playwright

    if _playwright is None:
        _playwright = await async_playwright().start()

    if session_id not in _browser_sessions:
        browser = await _playwright.chromium.launch(headless=True) # Must be headless for Railway containers
        context = await browser.new_context(viewport={"width": 1366, "height": 900})
        page = await context.new_page()
        _browser_sessions[session_id] = {
            "browser": browser,
            "context": context,
            "page": page,
            "last_used": time.time(),
        }

    sess = _browser_sessions[session_id]
    sess["last_used"] = time.time()
    return sess["page"]


def _auth(authorization: str | None) -> None:
    exp = (os.environ.get("NGRAM_EXECUTION_RPC_TOKEN") or "").strip()
    if not exp:
        raise HTTPException(
            status_code=503,
            detail="server_misconfigured: NGRAM_EXECUTION_RPC_TOKEN not set on server",
        )
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing_bearer")
    if authorization[7:].strip() != exp:
        raise HTTPException(status_code=403, detail="invalid_token")


@app.post("/rpc")
async def handle_rpc(request: Request, authorization: str | None = Header(default=None)):
    _auth(authorization)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid_json")

    action = body.get("action")
    payload = body.get("payload") or {}
    session_id = str(payload.get("session_id") or "default").strip()

    if action == "browser_navigate":
        url = payload.get("url")
        if not url:
            return {"ok": False, "error": "url is required"}
        try:
            page = await _get_browser_page(session_id)
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)

            # Simple heuristic to wait for JS frameworks to render
            await asyncio.sleep(2.0)

            text = await page.evaluate("document.body.innerText")
            return {
                "ok": True,
                "session_id": session_id,
                "content": (text or "")[:24000],
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    elif action == "browser_click":
        selector = payload.get("selector")
        if not selector:
            return {"ok": False, "error": "selector is required"}
        try:
            page = await _get_browser_page(session_id)
            await page.click(selector, timeout=10000)
            await asyncio.sleep(1.0) # Let page react
            return {"ok": True, "session_id": session_id, "message": f"Clicked {selector}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    elif action == "browser_type":
        selector = payload.get("selector")
        text = payload.get("text", "")
        if not selector:
            return {"ok": False, "error": "selector is required"}
        try:
            page = await _get_browser_page(session_id)
            await page.fill(selector, text, timeout=10000)
            return {"ok": True, "session_id": session_id, "message": f"Typed text into {selector}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    elif action == "browser_screenshot":
        try:
            page = await _get_browser_page(session_id)
            import tempfile
            import base64
            # We return a base64 encoded string, but we trim it or the agent handles it locally.
            # ngram tools currently don't sync this to the platform card like remote_session,
            # but we return it in case future tools need it.
            tmp = Path(tempfile.gettempdir()) / f"ngram_shot_{int(time.time())}.jpg"
            await page.screenshot(path=str(tmp), type="jpeg", quality=75)
            b64 = base64.b64encode(tmp.read_bytes()).decode("ascii")
            tmp.unlink(missing_ok=True)
            return {
                "ok": True,
                "session_id": session_id,
                "image_base64": b64,
                "message": "Screenshot captured (base64 omitted from log to save space)",
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    else:
        # Strict security boundary: the hands server only handles browser actions.
        # No file, shell, or OS execution is permitted here.
        # NOTE: error must NOT contain 'unknown_action' or 'unknown action' — that
        # phrase triggers the worker's local-fallback logic in ExecutionRPCClient.call().
        return {"ok": False, "error": f"action_denied: {action!r} — hands server only supports browser_* actions"}


def create_hands_app() -> FastAPI:
    return app
