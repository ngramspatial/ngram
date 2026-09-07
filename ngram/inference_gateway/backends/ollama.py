"""Proxy to an OpenAI-compatible endpoint (Ollama /v1, llama.cpp server, etc.)."""

from __future__ import annotations

import json
from typing import Any

import aiohttp


class OllamaCompatibleBackend:
    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 120.0,
        max_body_bytes: int = 8_000_000,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api = f"{self.base_url}/v1"
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.max_body_bytes = max_body_bytes
        self._session: aiohttp.ClientSession | None = None

    async def _sess(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self.timeout)
        return self._session

    async def aclose(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def get_models(self) -> dict[str, Any]:
        s = await self._sess()
        async with s.get(f"{self.api}/models") as resp:
            text = await resp.text()
            if resp.status >= 400:
                return {"data": [], "error": text[:500], "status": resp.status}
            try:
                return json.loads(text) if text else {"data": []}
            except json.JSONDecodeError:
                return {"data": [], "error": "invalid_json"}

    async def post_chat_completions(self, payload: dict[str, Any]) -> tuple[int, Any]:
        s = await self._sess()
        async with s.post(f"{self.api}/chat/completions", json=payload) as resp:
            text = await resp.text()
            try:
                return resp.status, json.loads(text) if text else {}
            except json.JSONDecodeError:
                return resp.status, {"error": "invalid_json", "raw": text[:500]}

    async def open_chat_completions_stream(self, payload: dict[str, Any]) -> aiohttp.ClientResponse:
        """Caller must read the body and ``release()`` the response."""
        s = await self._sess()
        payload = {**payload, "stream": True}
        return await s.post(f"{self.api}/chat/completions", json=payload)

    async def post_embeddings(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        s = await self._sess()
        async with s.post(f"{self.api}/embeddings", json=payload) as resp:
            text = await resp.text()
            try:
                return resp.status, json.loads(text) if text else {}
            except json.JSONDecodeError:
                return resp.status, {"error": "invalid_json", "raw": text[:500]}
