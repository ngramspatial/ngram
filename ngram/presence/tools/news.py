"""News search via DDGS."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from ngram.presence.tools.registry import tool


def _sync_news(query: str, max_results: int) -> list[dict[str, Any]]:
    try:
        from ddgs import DDGS

        d = DDGS(timeout=25)
        return list(d.news(query, max_results=max_results))  # type: ignore[arg-type]
    except ImportError:
        try:
            from duckduckgo_search import DDGS as LegacyDDGS

            with LegacyDDGS() as d:
                return list(d.news(query, max_results=max_results))
        except Exception:
            return []
    except Exception:
        return []


@tool(
    name="get_news",
    description="Check recent news, optionally on a specific topic",
)
async def get_news(topic: str = "") -> str:
    q = (topic or "").strip() or "top news"
    rows = await asyncio.to_thread(_sync_news, q, 10)
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "headline": str(r.get("title") or ""),
                "source": str(r.get("source") or ""),
                "date": str(r.get("date") or ""),
                "url": str(r.get("url") or ""),
                "summary": str(r.get("body") or "")[:280],
            }
        )
    return json.dumps({"topic": topic or "", "results": out}, ensure_ascii=False)
