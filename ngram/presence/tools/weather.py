"""Simple weather checks via wttr.in JSON endpoint."""

from __future__ import annotations

import json
from urllib.parse import quote

import aiohttp

from ngram.presence.tools.registry import tool


@tool(
    name="get_weather",
    description="Check the current weather somewhere",
)
async def get_weather(location: str) -> str:
    loc = (location or "").strip()
    if not loc:
        return json.dumps({"error": "empty location"})
    url = f"https://wttr.in/{quote(loc)}?format=j1"
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15),
            headers={"User-Agent": "NgramBot/0.1"},
        ) as s:
            async with s.get(url) as r:
                if r.status >= 400:
                    return json.dumps({"error": f"http {r.status}", "location": loc})
                data = await r.json()
        cur = ((data.get("current_condition") or [{}])[0] if isinstance(data, dict) else {}) or {}
        desc = ""
        weather_desc = cur.get("weatherDesc") or []
        if isinstance(weather_desc, list) and weather_desc:
            desc = str((weather_desc[0] or {}).get("value") or "")
        out = {
            "location": loc,
            "temperature_c": cur.get("temp_C"),
            "temperature_f": cur.get("temp_F"),
            "condition": desc,
            "humidity": cur.get("humidity"),
            "wind_kph": cur.get("windspeedKmph"),
        }
        return json.dumps(out, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e), "location": loc})
