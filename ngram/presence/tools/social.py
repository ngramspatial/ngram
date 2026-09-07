"""Social media broadcasting tools."""

from __future__ import annotations

import json
import os
import aiohttp

# tweepy is imported locally in post_zweet_status to avoid registration-time failures if dependencies are missing.

from ngram.presence.tools.registry import tool

def _get_mastodon_config() -> tuple[str, str]:
    url = os.environ.get("MASTODON_URL", "").strip()
    token = os.environ.get("MASTODON_ACCESS_TOKEN", "").strip()
    return url, token

def _get_zweet_config() -> tuple[str, str, str, str]:
    api_key = os.environ.get("X_API_KEY", "").strip()
    api_secret = os.environ.get("X_API_SECRET", "").strip()
    access_token = os.environ.get("X_ACCESS_TOKEN", "").strip()
    access_secret = os.environ.get("X_ACCESS_SECRET", "").strip()
    return api_key, api_secret, access_token, access_secret

@tool(
    name="post_mastodon_status",
    description="Post a status update to your configured Mastodon account. visibility can be 'public', 'unlisted', 'private', or 'direct'.",
)
async def post_mastodon_status(status: str, visibility: str = "public") -> str:
    url, token = _get_mastodon_config()
    if not url or not token:
        return json.dumps({"error": "Mastodon URL or Token not configured in environment."})

    text = (status or "").strip()
    if not text:
        return json.dumps({"error": "status cannot be empty"})

    vis = (visibility or "public").strip().lower()
    if vis not in ("public", "unlisted", "private", "direct"):
        vis = "public"

    endpoint = f"{url.rstrip('/')}/api/v1/statuses"

    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        ) as s:
            async with s.post(endpoint, json={"status": text, "visibility": vis}) as r:
                if r.status >= 400:
                    try:
                        err_data = await r.json()
                        return json.dumps({"error": f"http {r.status}", "details": err_data})
                    except Exception:
                        return json.dumps({"error": f"http {r.status}"})
                data = await r.json()

        return json.dumps({
            "success": True,
            "id": data.get("id"),
            "url": data.get("url"),
            "visibility": data.get("visibility")
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)})

@tool(
    name="read_mastodon_timeline",
    description="Read the latest posts from your Mastodon home timeline.",
)
async def read_mastodon_timeline(limit: int = 10) -> str:
    url, token = _get_mastodon_config()
    if not url or not token:
        return json.dumps({"error": "Mastodon URL or Token not configured in environment."})

    lim = max(1, min(40, int(limit or 10)))
    endpoint = f"{url.rstrip('/')}/api/v1/timelines/home?limit={lim}"

    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15),
            headers={
                "Authorization": f"Bearer {token}",
            },
        ) as s:
            async with s.get(endpoint) as r:
                if r.status >= 400:
                    return json.dumps({"error": f"http {r.status}"})
                data = await r.json()

        out = []
        for post in data:
            if not isinstance(post, dict):
                continue
            acc = post.get("account", {})
            out.append({
                "id": post.get("id"),
                "created_at": post.get("created_at"),
                "account": acc.get("acct") or acc.get("username"),
                "content_html": post.get("content"),
                "replies_count": post.get("replies_count", 0),
                "reblogs_count": post.get("reblogs_count", 0),
                "favourites_count": post.get("favourites_count", 0),
            })
        return json.dumps(out, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)})

@tool(
    name="post_zweet_status",
    description="Post a zweet (tweet/status update) to your configured X (Twitter) account. Note: Reading the timeline is not supported due to X API Free Tier limitations.",
)
async def post_zweet_status(status: str) -> str:
    try:
        from tweepy.asynchronous import AsyncClient
    except Exception as e:
        return json.dumps({
            "error": f"The tweepy library or its dependencies (aiohttp, async-lru, oauthlib) are not installed or broken: {e}. "
                     "Please run `pip install 'tweepy[async]'` to fix this."
        })

    api_key, api_secret, acc_token, acc_secret = _get_zweet_config()
    if not all([api_key, api_secret, acc_token, acc_secret]):
        return json.dumps({
            "error": "Missing environment variables. X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, and X_ACCESS_SECRET are all required."
        })

    text = (status or "").strip()
    if not text:
        return json.dumps({"error": "status cannot be empty"})

    try:
        client = AsyncClient(
            consumer_key=api_key,
            consumer_secret=api_secret,
            access_token=acc_token,
            access_token_secret=acc_secret
        )

        # Twitter API v2
        res = await client.create_tweet(text=text)

        if res.errors:
            return json.dumps({"error": str(res.errors)})

        data = res.data
        if not data:
            return json.dumps({"error": "Empty response from X API"})

        tweet_id = data.get("id")
        return json.dumps({
            "success": True,
            "id": tweet_id,
            "url": f"https://x.com/user/status/{tweet_id}"
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)})
