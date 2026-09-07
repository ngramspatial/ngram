"""Reddit readers via public JSON endpoints (no API key)."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.parse import urlparse

import aiohttp

from ngram.presence.tools.registry import tool

_UA = "NgramBot/0.1 (+https://pypi.org/project/ngram/)"


async def _fetch_json_with_retry(url: str, retries: int = 2) -> tuple[Any, str | None]:
    err: str | None = None
    for i in range(retries + 1):
        try:
            async with aiohttp.ClientSession(
                headers={"User-Agent": _UA},
                timeout=aiohttp.ClientTimeout(total=20),
            ) as s:
                async with s.get(url) as resp:
                    if resp.status == 429:
                        await asyncio.sleep(1.5 * (i + 1))
                        err = "rate_limited"
                        continue
                    if resp.status >= 400:
                        return None, f"http {resp.status}"
                    return await resp.json(), None
        except Exception as e:
            err = str(e)
            await asyncio.sleep(0.5)
    return None, err


def _normalize_subreddit(s: str) -> str:
    t = (s or "").strip().lower()
    if t.startswith("r/"):
        t = t[2:]
    return "".join(ch for ch in t if ch.isalnum() or ch in ("_", "-"))


def _ensure_reddit_json_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    if u.endswith(".json"):
        return u
    try:
        p = urlparse(u)
    except ValueError:
        return ""
    if "reddit.com" not in (p.hostname or "").lower():
        return ""
    path = p.path.rstrip("/")
    if not path:
        return ""
    return f"https://www.reddit.com{path}.json"


@tool(
    name="read_reddit",
    description="Browse a subreddit to see what people are talking about",
)
async def read_reddit(subreddit: str, sort: str = "hot", limit: int = 10) -> str:
    sub = _normalize_subreddit(subreddit)
    if not sub:
        return json.dumps({"error": "invalid subreddit"})
    srt = (sort or "hot").strip().lower()
    if srt not in {"hot", "new", "top", "rising"}:
        srt = "hot"
    lim = max(1, min(int(limit or 10), 30))
    url = f"https://www.reddit.com/r/{sub}/{srt}.json?limit={lim}"
    data, err = await _fetch_json_with_retry(url)
    if err:
        return json.dumps({"error": err, "url": url})
    children = (((data or {}).get("data") or {}).get("children") or []) if isinstance(data, dict) else []
    posts: list[dict[str, Any]] = []
    for c in children:
        d = (c or {}).get("data") or {}
        permalink = str(d.get("permalink") or "")
        posts.append(
            {
                "title": str(d.get("title") or ""),
                "url": f"https://www.reddit.com{permalink}" if permalink else str(d.get("url") or ""),
                "score": int(d.get("score") or 0),
                "comments": int(d.get("num_comments") or 0),
                "author": str(d.get("author") or ""),
            }
        )
    return json.dumps({"subreddit": sub, "sort": srt, "posts": posts}, ensure_ascii=False)


@tool(
    name="read_reddit_post",
    description="Read a specific Reddit post and its top comments",
)
async def read_reddit_post(url: str) -> str:
    jurl = _ensure_reddit_json_url(url)
    if not jurl:
        return json.dumps({"error": "provide a valid Reddit post URL"})
    data, err = await _fetch_json_with_retry(jurl)
    if err:
        return json.dumps({"error": err, "url": jurl})
    if not isinstance(data, list) or len(data) < 2:
        return json.dumps({"error": "unexpected reddit response", "url": jurl})
    post_data = ((((data[0] or {}).get("data") or {}).get("children") or [{}])[0] or {}).get("data") or {}
    comments = (((data[1] or {}).get("data") or {}).get("children") or [])
    top_comments: list[dict[str, Any]] = []
    for c in comments:
        d = (c or {}).get("data") or {}
        body = str(d.get("body") or "").strip()
        if not body:
            continue
        top_comments.append(
            {
                "author": str(d.get("author") or ""),
                "score": int(d.get("score") or 0),
                "body": body[:1200],
            }
        )
        if len(top_comments) >= 15:
            break
    out = {
        "title": str(post_data.get("title") or ""),
        "subreddit": str(post_data.get("subreddit") or ""),
        "score": int(post_data.get("score") or 0),
        "comments_count": int(post_data.get("num_comments") or 0),
        "url": f"https://www.reddit.com{post_data.get('permalink')}" if post_data.get("permalink") else url,
        "top_comments": top_comments[:15],
    }
    return json.dumps(out, ensure_ascii=False)
