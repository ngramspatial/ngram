"""YouTube tools: transcript + search."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any
from urllib.parse import parse_qs, quote_plus, urlparse

import aiohttp

from ngram.presence.tools.registry import tool
from ngram.presence.tools.web import _sync_ddgs_text_search


_YOUTUBE_SEARCH_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


def _video_id_from_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    try:
        p = urlparse(u)
    except ValueError:
        return ""
    host = (p.hostname or "").lower()
    if host.endswith("youtu.be"):
        return p.path.lstrip("/").split("/")[0]
    if "youtube.com" in host:
        if p.path == "/watch":
            q = parse_qs(p.query)
            return (q.get("v") or [""])[0]
        parts = [x for x in p.path.split("/") if x]
        if len(parts) >= 2 and parts[0] in ("shorts", "embed", "live"):
            return parts[1]
    return ""


def _text_value(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    simple = value.get("simpleText")
    if isinstance(simple, str):
        return simple.strip()
    runs = value.get("runs")
    if isinstance(runs, list):
        return "".join(
            str(run.get("text") or "")
            for run in runs
            if isinstance(run, dict)
        ).strip()
    return ""


def _walk_video_renderers(value: Any):
    if isinstance(value, dict):
        renderer = value.get("videoRenderer")
        if isinstance(renderer, dict):
            yield renderer
        for child in value.values():
            yield from _walk_video_renderers(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_video_renderers(child)


def _youtube_initial_data(page: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    for marker in (
        "var ytInitialData = ",
        "window[\"ytInitialData\"] = ",
        "ytInitialData = ",
    ):
        start = page.find(marker)
        if start < 0:
            continue
        start += len(marker)
        try:
            parsed, _ = decoder.raw_decode(page[start:].lstrip())
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _youtube_results_from_page(page: str, max_results: int) -> list[dict[str, Any]]:
    data = _youtube_initial_data(page)
    if data is None:
        return []
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for renderer in _walk_video_renderers(data):
        video_id = str(renderer.get("videoId") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{6,32}", video_id) or video_id in seen:
            continue
        seen.add(video_id)
        description = _text_value(renderer.get("descriptionSnippet"))
        if not description:
            snippets = renderer.get("detailedMetadataSnippets")
            if isinstance(snippets, list) and snippets and isinstance(snippets[0], dict):
                description = _text_value(snippets[0].get("snippetText"))
        results.append(
            {
                "title": _text_value(renderer.get("title")),
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "video_id": video_id,
                "channel": _text_value(
                    renderer.get("ownerText") or renderer.get("shortBylineText")
                ),
                "duration": _text_value(renderer.get("lengthText")),
                "description": description[:400],
            }
        )
        if len(results) >= max_results:
            break
    return results


async def _search_youtube_direct(query: str, max_results: int) -> list[dict[str, Any]]:
    url = f"https://www.youtube.com/results?search_query={quote_plus(query)}"
    timeout = aiohttp.ClientTimeout(total=25)
    headers = {
        "User-Agent": _YOUTUBE_SEARCH_UA,
        "Accept-Language": "en-US,en;q=0.9",
    }
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        async with session.get(url, allow_redirects=True) as response:
            if response.status != 200:
                return []
            page = await response.text(errors="replace")
    return _youtube_results_from_page(page, max_results)


@tool(
    name="get_youtube_transcript",
    description="Pull the transcript from a YouTube video so you can engage with what was said",
)
async def get_youtube_transcript(url: str) -> str:
    vid = _video_id_from_url(url)
    if not vid:
        return json.dumps({"error": "could not parse YouTube video id from URL"})
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        return json.dumps(
            {"error": "youtube-transcript-api not installed. Install with: pip install ngram[youtube]"}
        )
    try:
        rows = await asyncio.to_thread(YouTubeTranscriptApi().fetch, vid)
        chunks: list[str] = []
        for r in rows:
            txt = str(getattr(r, "text", "") or "").strip()
            if txt:
                chunks.append(txt)
        text = " ".join(chunks).strip()
        if len(text) > 30000:
            text = text[:29900] + "\n… [truncated]"
        return json.dumps({"video_id": vid, "transcript": text}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e), "video_id": vid})


@tool(
    name="search_youtube",
    description="Search YouTube for videos",
)
async def search_youtube(query: str, max_results: int = 5) -> str:
    q = (query or "").strip()
    if not q:
        return json.dumps({"error": "empty query"})
    max_results = max(1, min(int(max_results or 5), 20))
    try:
        direct = await _search_youtube_direct(q, max_results)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
        direct = []
    if direct:
        return json.dumps(
            {"query": q, "results": direct, "source": "youtube"},
            ensure_ascii=False,
        )

    scoped = f"site:youtube.com {q}"
    rows = await asyncio.to_thread(_sync_ddgs_text_search, scoped, max_results * 3)
    fallback: list[dict[str, Any]] = []
    for row in rows:
        href = str(row.get("href") or row.get("url") or "")
        if "youtube.com" not in href and "youtu.be" not in href:
            continue
        fallback.append(
            {
                "title": str(row.get("title") or "").strip(),
                "url": href,
                "video_id": _video_id_from_url(href),
                "description": str(row.get("body") or "").strip()[:400],
            }
        )
        if len(fallback) >= max_results:
            break
    return json.dumps(
        {"query": q, "results": fallback, "source": "web_fallback"},
        ensure_ascii=False,
    )
