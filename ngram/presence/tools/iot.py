"""Home Assistant IoT control tools."""

from __future__ import annotations

import json
import os
import aiohttp

from ngram.presence.tools.registry import tool

def _get_ha_config() -> tuple[str, str]:
    url = os.environ.get("HOME_ASSISTANT_URL", "").strip()
    token = os.environ.get("HOME_ASSISTANT_TOKEN", "").strip()
    return url, token

@tool(
    name="get_ha_state",
    description="Check the current state of a Home Assistant entity (e.g. light.living_room, sensor.temperature)",
)
async def get_ha_state(entity_id: str) -> str:
    url, token = _get_ha_config()
    if not url or not token:
        return json.dumps({"error": "Missing environment variables: HOME_ASSISTANT_URL and HOME_ASSISTANT_TOKEN are required."})

    eid = (entity_id or "").strip()
    if not eid:
        return json.dumps({"error": "empty entity_id"})

    endpoint = f"{url.rstrip('/')}/api/states/{eid}"
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        ) as s:
            async with s.get(endpoint) as r:
                if r.status >= 400:
                    return json.dumps({"error": f"http {r.status}", "entity_id": eid})
                data = await r.json()

        out = {
            "entity_id": data.get("entity_id"),
            "state": data.get("state"),
            "attributes": data.get("attributes", {}),
            "last_changed": data.get("last_changed"),
        }
        return json.dumps(out, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e), "entity_id": eid})

@tool(
    name="set_ha_state",
    description="Call a Home Assistant service to change state (e.g. domain='light', service='turn_on', entity_id='light.living_room'). service_data is an optional JSON string of extra attributes.",
)
async def set_ha_state(domain: str, service: str, entity_id: str, service_data: str = "") -> str:
    url, token = _get_ha_config()
    if not url or not token:
        return json.dumps({"error": "Missing environment variables: HOME_ASSISTANT_URL and HOME_ASSISTANT_TOKEN are required."})

    dom = (domain or "").strip()
    srv = (service or "").strip()
    eid = (entity_id or "").strip()

    if not dom or not srv:
        return json.dumps({"error": "domain and service are required"})

    endpoint = f"{url.rstrip('/')}/api/services/{dom}/{srv}"

    payload = {}
    if eid:
        payload["entity_id"] = eid

    s_data = (service_data or "").strip()
    if s_data:
        try:
            extra = json.loads(s_data)
            if isinstance(extra, dict):
                payload.update(extra)
        except json.JSONDecodeError:
            return json.dumps({"error": "service_data must be valid JSON"})

    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        ) as s:
            async with s.post(endpoint, json=payload) as r:
                if r.status >= 400:
                    return json.dumps({"error": f"http {r.status}", "domain": dom, "service": srv})
                data = await r.json()

        # Home Assistant typically returns a list of state objects that changed
        return json.dumps({"success": True, "changed_entities": data}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)})
