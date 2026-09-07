"""Web search (DDGS) and bounded URL fetch with HTML-to-text / extract fallback."""

from __future__ import annotations

import asyncio
import html as html_module
import importlib.util
import json
import re
import warnings
from collections.abc import Mapping
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote, urlparse

import aiohttp

from ngram.config import FirecrawlSettings
from ngram.presence.tools.registry import tool

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class _FirecrawlRuntime:
    api_key: str | None
    base_url: str
    prefer_for_fetch: bool
    prefer_for_search: bool


_FC: _FirecrawlRuntime | None = None


def configure_firecrawl(environ: Mapping[str, str], settings: FirecrawlSettings) -> None:
    """Call once per entity process; reads API key from ``environ[settings.api_key_env]``."""
    global _FC
    key = (environ.get(settings.api_key_env) or "").strip()
    base = settings.base_url.rstrip("/")
    _FC = _FirecrawlRuntime(
        api_key=key or None,
        base_url=base,
        prefer_for_fetch=bool(key) and settings.prefer_for_fetch,
        prefer_for_search=bool(key) and settings.prefer_for_search,
    )


async def _firecrawl_post(
    subpath: str,
    body: dict[str, Any],
    *,
    http_timeout: float,
) -> tuple[dict[str, Any] | None, int, str]:
    global _FC
    if not _FC or not _FC.api_key:
        return None, 0, "firecrawl not configured"
    url = f"{_FC.base_url}{subpath}"
    headers = {
        "Authorization": f"Bearer {_FC.api_key}",
        "Content-Type": "application/json",
    }
    try:
        to = max(float(http_timeout), 15.0)
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=to + 5.0),
        ) as session:
            async with session.post(url, json=body, headers=headers) as resp:
                raw_txt = await resp.text()
                try:
                    payload: dict[str, Any] = json.loads(raw_txt) if raw_txt else {}
                except json.JSONDecodeError:
                    return None, resp.status, raw_txt[:400]
                return payload, resp.status, ""
    except Exception as e:
        return None, 0, str(e)


class _HTMLToText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        if t in ("script", "style", "noscript", "template"):
            self._skip += 1
        elif t in ("br", "p", "div", "tr", "li", "h1", "h2", "h3", "h4"):
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in ("script", "style", "noscript", "template") and self._skip > 0:
            self._skip -= 1
        elif t in ("p", "div", "tr", "li", "h1", "h2", "h3", "h4"):
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip == 0 and data:
            self._chunks.append(data)

    def text(self) -> str:
        raw = "".join(self._chunks)
        raw = html_module.unescape(raw)
        raw = re.sub(r"[ \t\r\f\v]+", " ", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()


def _html_to_text(html: str, max_chars: int = 12000) -> str:
    p = _HTMLToText()
    try:
        p.feed(html)
        p.close()
    except Exception:
        stripped = re.sub(r"(?is)<script.*?>.*?</script>", " ", html)
        stripped = re.sub(r"(?is)<style.*?>.*?</style>", " ", stripped)
        stripped = re.sub(r"<[^>]+>", " ", stripped)
        return html_module.unescape(stripped)[:max_chars]
    t = p.text()
    return t[:max_chars]


def _sync_ddgs_text_search(query: str, max_results: int) -> list[dict[str, Any]]:
    """Prefer ``ddgs`` (successor to duckduckgo-search); avoid Bing-only redirect hrefs when possible.

    DuckDuckGo-native backend is tried first (cleaner destination URLs). On empty
    results *or* backend exceptions (common on some home ISPs / rate limits), fall
    through ``auto`` / ``brave`` / ``bing`` instead of returning [].
    """
    try:
        from ddgs import DDGS

        d = DDGS(timeout=25)
        # duckduckgo first for real hrefs; auto/bing often surface bing.com/ck junk
        # (filtered later). Exceptions must not abort the whole fallback chain.
        for backend in ("duckduckgo", "auto", "brave", "bing"):
            try:
                rows = list(d.text(query, max_results=max_results, backend=backend) or [])
            except Exception:
                rows = []
            if rows:
                return rows
        return []
    except ImportError:
        pass
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=".*renamed to.*ddgs.*",
                category=RuntimeWarning,
            )
            from duckduckgo_search import DDGS as LegacyDDGS

            with LegacyDDGS() as leg:
                return list(leg.text(query, max_results=max_results))
    except ImportError:
        return []
    except Exception:
        return []


def _sync_ddgs_extract(url: str) -> tuple[str | None, str | None]:
    """Returns (plain_text_or_none, error_or_none)."""
    try:
        from ddgs import DDGS

        out = DDGS(timeout=25).extract(url, fmt="text_plain")
        raw = out.get("content")
        if raw is None:
            return None, "empty extract"
        if isinstance(raw, bytes):
            text = raw.decode("utf-8", errors="replace")
        else:
            text = str(raw)
        text = text.strip()
        return (text if text else None), None
    except ImportError:
        return None, None
    except Exception as e:
        return None, str(e)


def _web_search_packages_available() -> bool:
    return importlib.util.find_spec("ddgs") is not None or importlib.util.find_spec(
        "duckduckgo_search",
    ) is not None


def _is_bing_junk_href(href: str) -> bool:
    h = (href or "").lower()
    return "bing.com/ck/" in h or h.startswith("https://www.bing.com/search?")


@tool(
    name="search_web",
    description=(
        "Search the web. Uses Firecrawl when configured (FIRECRAWL_API_KEY); otherwise local ddgs. "
        "Use when curious, when a topic comes up, or when someone asks you to look something up."
    ),
)
async def search_web(query: str) -> str:
    q = (query or "").strip()
    if not q:
        return json.dumps({"error": "empty query"})
    fc_timeout = 42.0
    if _FC and _FC.prefer_for_search:
        t_ms = int(min(max(fc_timeout * 1000, 8000), 120_000))
        fc_body: dict[str, Any] = {
            "query": q,
            "limit": 8,
            "ignoreInvalidURLs": True,
            "timeout": t_ms,
        }
        data, status, _ = await _firecrawl_post("/search", fc_body, http_timeout=fc_timeout + 8)
        if data and data.get("success") is True and isinstance(data.get("data"), list):
            slim_fc: list[dict[str, Any]] = []
            for item in data["data"]:
                if not isinstance(item, dict):
                    continue
                href = str(item.get("url") or "")
                title = str(item.get("title") or "")
                body_snip = str(item.get("description") or item.get("markdown") or "")[:400]
                slim_fc.append({"title": title, "href": href, "body": body_snip})
            if slim_fc:
                return json.dumps(
                    {"query": q, "results": slim_fc, "via": "firecrawl"},
                    ensure_ascii=False,
                )
        err_msg = ""
        if isinstance(data, dict):
            err_msg = str(data.get("error") or data.get("message") or "")
        if status and status != 200:
            err_msg = err_msg or f"http {status}"
        if err_msg and not _web_search_packages_available():
            return json.dumps({"error": f"firecrawl: {err_msg}", "query": q})
    if not _web_search_packages_available():
        return json.dumps(
            {
                "error": "install ddgs (pip install ddgs) or set FIRECRAWL_API_KEY for Firecrawl",
                "query": q,
            },
        )
    results = await asyncio.to_thread(_sync_ddgs_text_search, q, 8)
    if not results:
        return json.dumps(
            {
                "error": "no search results (network, block, or missing ddgs). pip install ddgs",
                "query": q,
            },
        )
    slim: list[dict[str, Any]] = []
    for r in results:
        href = r.get("href", "") or r.get("url", "")
        if _is_bing_junk_href(href):
            continue
        slim.append(
            {
                "title": r.get("title", ""),
                "href": href,
                "body": (r.get("body", "") or "")[:400],
            }
        )
    if not slim:
        for r in results:
            slim.append(
                {
                    "title": r.get("title", ""),
                    "href": r.get("href", "") or r.get("url", ""),
                    "body": (r.get("body", "") or "")[:400],
                }
            )
    return json.dumps({"query": q, "results": slim}, ensure_ascii=False)


async def _youtube_oembed_summary(url: str, timeout: float) -> dict[str, Any] | None:
    api = f"https://www.youtube.com/oembed?url={quote(url, safe='')}&format=json"
    try:
        async with aiohttp.ClientSession(
            headers={"User-Agent": _DEFAULT_UA},
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as session:
            async with session.get(api) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
        return {
            "note": "youtube_oembed",
            "title": data.get("title", ""),
            "author_name": data.get("author_name", ""),
            "hint": "Watch page HTML is bot-blocked; use title + search_web if you need more context.",
        }
    except Exception:
        return None


@tool(
    name="fetch_url",
    description=(
        "Fetch a URL and return readable text. Prefers Firecrawl scrape when FIRECRAWL_API_KEY is set; "
        "otherwise ddgs extract / direct HTTP. YouTube: oembed title/uploader when the watch page is blocked."
    ),
)
async def fetch_url(url: str, timeout: float = 22.0) -> str:
    u = (url or "").strip()
    if not u:
        return json.dumps({"error": "empty url"})
    parsed = urlparse(u)
    if parsed.scheme not in ("http", "https"):
        return json.dumps({"error": "only http/https URLs are allowed"})
    host = (parsed.hostname or "").lower()
    fc_err_hint: str | None = None
    if host in ("youtu.be",) or host.endswith(".youtube.com") or host == "youtube.com":
        meta = await _youtube_oembed_summary(u, min(timeout, 12.0))
        if meta:
            return json.dumps({"url": u, **meta}, ensure_ascii=False)

    if _FC and _FC.prefer_for_fetch and _FC.api_key:
        fc_to = max(float(timeout), 18.0)
        scrape_body: dict[str, Any] = {"url": u, "formats": ["markdown"]}
        fc_data, fc_status, _ = await _firecrawl_post(
            "/scrape",
            scrape_body,
            http_timeout=fc_to + 10,
        )
        if fc_data and fc_data.get("success") is True:
            block = fc_data.get("data")
            if isinstance(block, dict):
                text_fc = (block.get("markdown") or "").strip()
                if not text_fc and block.get("html"):
                    text_fc = _html_to_text(str(block.get("html")))
                if text_fc and len(text_fc) > 40:
                    if len(text_fc) > 14000:
                        text_fc = text_fc[:13900] + "\n… [truncated]"
                    return json.dumps(
                        {"url": u, "status": 200, "text": text_fc, "via": "firecrawl"},
                        ensure_ascii=False,
                    )
        fc_err_hint = ""
        if isinstance(fc_data, dict):
            fc_err_hint = str(fc_data.get("error") or fc_data.get("message") or "")
        if fc_status and fc_status != 200:
            fc_err_hint = (fc_err_hint or "") + (f" http {fc_status}" if fc_err_hint else f"http {fc_status}")

    extracted, ex_err = await asyncio.to_thread(_sync_ddgs_extract, u)
    if extracted and len(extracted) > 80:
        text = extracted
        if len(text) > 14000:
            text = text[:13900] + "\n… [truncated]"
        return json.dumps({"url": u, "status": 200, "text": text, "via": "ddgs.extract"}, ensure_ascii=False)

    try:
        async with aiohttp.ClientSession(
            headers={
                "User-Agent": _DEFAULT_UA,
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as session:
            async with session.get(u, allow_redirects=True) as resp:
                status = resp.status
                ctype = (resp.headers.get("Content-Type") or "").lower()
                raw = await resp.text(errors="replace")
        if "html" in ctype or raw.lstrip().lower().startswith("<!doctype") or "<html" in raw[:500].lower():
            text = _html_to_text(raw)
        else:
            text = raw
        if len(text) < 120 and host.endswith("youtube.com"):
            meta = await _youtube_oembed_summary(u, min(timeout, 12.0))
            if meta:
                return json.dumps({"url": u, **meta}, ensure_ascii=False)
        if len(text) > 14000:
            text = text[:13900] + "\n… [truncated]"
        hint: dict[str, Any] = {}
        if ex_err:
            hint["extract_attempt"] = ex_err
        if fc_err_hint:
            hint["firecrawl_attempt"] = fc_err_hint.strip()
        return json.dumps(
            {"url": u, "status": status, "text": text, "via": "aiohttp", **hint},
            ensure_ascii=False,
        )
    except Exception as e:
        err_body: dict[str, Any] = {"error": str(e), "url": u}
        if ex_err:
            err_body["extract_attempt"] = ex_err
        if fc_err_hint:
            err_body["firecrawl_attempt"] = fc_err_hint.strip()
        if host.endswith("youtube.com") or host == "youtu.be":
            meta = await _youtube_oembed_summary(u, min(timeout, 12.0))
            if meta:
                return json.dumps({"url": u, **meta}, ensure_ascii=False)
        return json.dumps(err_body)
