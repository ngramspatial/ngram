"""Wikipedia reader using the REST API."""

from __future__ import annotations

import html
import json
import re
from html.parser import HTMLParser
from urllib.parse import quote

import aiohttp

from ngram.presence.tools.registry import tool


class _HTMLToText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[override]
        t = tag.lower()
        if t in {"script", "style", "noscript"}:
            self._skip += 1
        elif t in {"p", "br", "div", "li", "h1", "h2", "h3"}:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:  # type: ignore[override]
        t = tag.lower()
        if t in {"script", "style", "noscript"} and self._skip > 0:
            self._skip -= 1
        elif t in {"p", "div", "li", "h1", "h2", "h3"}:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:  # type: ignore[override]
        if self._skip == 0 and data:
            self._chunks.append(data)

    def text(self) -> str:
        t = html.unescape("".join(self._chunks))
        t = re.sub(r"[ \t\r\f\v]+", " ", t)
        t = re.sub(r"\n{3,}", "\n\n", t)
        return t.strip()


def _html_to_text(raw_html: str, max_chars: int = 24000) -> str:
    p = _HTMLToText()
    try:
        p.feed(raw_html)
        p.close()
        txt = p.text()
    except Exception:
        txt = re.sub(r"<[^>]+>", " ", raw_html)
    if len(txt) > max_chars:
        txt = txt[: max_chars - 100] + "\n… [truncated]"
    return txt


@tool(
    name="read_wikipedia",
    description="Read a Wikipedia article in depth when you want to really understand a topic",
)
async def read_wikipedia(topic: str, full_article: bool = False) -> str:
    t = (topic or "").strip()
    if not t:
        return json.dumps({"error": "empty topic"})
    enc = quote(t.replace(" ", "_"), safe="")
    summary_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{enc}"
    headers = {"User-Agent": "NgramBot/0.1 (+https://pypi.org/project/ngram/)"}
    try:
        async with aiohttp.ClientSession(
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=25),
        ) as s:
            async with s.get(summary_url) as r:
                if r.status >= 400:
                    return json.dumps({"error": f"http {r.status}", "topic": t})
                summary = await r.json()
            if not full_article:
                return json.dumps(
                    {
                        "title": summary.get("title", t),
                        "summary": summary.get("extract", ""),
                        "url": summary.get("content_urls", {}).get("desktop", {}).get("page", ""),
                        "type": summary.get("type", "standard"),
                    },
                    ensure_ascii=False,
                )
            title = summary.get("title") or t
            title_enc = quote(str(title).replace(" ", "_"), safe="")
            html_url = f"https://en.wikipedia.org/api/rest_v1/page/html/{title_enc}"
            async with s.get(html_url) as hr:
                if hr.status >= 400:
                    return json.dumps(
                        {
                            "title": title,
                            "summary": summary.get("extract", ""),
                            "error": f"full article http {hr.status}",
                        },
                        ensure_ascii=False,
                    )
                html_text = await hr.text()
            article_text = _html_to_text(html_text)
            return json.dumps(
                {
                    "title": title,
                    "summary": summary.get("extract", ""),
                    "article_text": article_text,
                    "url": summary.get("content_urls", {}).get("desktop", {}).get("page", ""),
                    "type": summary.get("type", "standard"),
                },
                ensure_ascii=False,
            )
    except Exception as e:
        return json.dumps({"error": str(e), "topic": t})
