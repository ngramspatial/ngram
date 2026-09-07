"""Suggest new routines from drives, memory, and relationship patterns."""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ngram.entity import Entity


class AutomationEmergence:
    async def analyze_and_suggest(self, entity: Entity) -> list[dict[str, Any]]:
        cfg = entity.config.automations.emergence
        if not cfg.enabled:
            return []
        max_n = cfg.max_suggestions

        topic_hits: Counter[str] = Counter()
        try:
            async with entity.store.session() as conn:
                cur = await conn.execute(
                    "SELECT summary, tags FROM episodes WHERE timestamp > ? ORDER BY timestamp DESC LIMIT 80",
                    (time.time() - 86400 * 7,),
                )
                rows = await cur.fetchall()
            for row in rows:
                summ = str(row[0] or "")
                for w in re.findall(r"[a-zA-Z]{4,}", summ.lower()):
                    if w in (
                        "this",
                        "that",
                        "with",
                        "from",
                        "they",
                        "have",
                        "been",
                        "were",
                        "what",
                        "when",
                        "your",
                        "conversation",
                    ):
                        continue
                    topic_hits[w] += 1
        except Exception:
            rows = []

        rel_blurbs: list[str] = []
        try:
            async with entity.store.session() as conn:
                cur = await conn.execute(
                    "SELECT name, warmth, last_interaction FROM relationships "
                    "ORDER BY warmth DESC LIMIT 12"
                )
                rel_rows = await cur.fetchall()
            now = time.time()
            for name, warmth, last_i in rel_rows:
                if warmth is None or float(warmth) < 0.45:
                    continue
                gap_days = (now - float(last_i or now)) / 86400.0
                if gap_days >= 7:
                    rel_blurbs.append(
                        f"{name}: high warmth, last interaction ~{gap_days:.0f} days ago"
                    )
        except Exception:
            pass

        top_topics = [t for t, c in topic_hits.most_common(6) if c >= 3]

        drives = entity.drives.all_drives()
        drive_bits = ", ".join(f"{d.name}={d.level:.2f}" for d in drives[:8])

        payload = {
            "topics_mentioned_often": top_topics,
            "relationship_gaps": rel_blurbs[:5],
            "drive_levels": drive_bits,
        }

        model = entity.config.cognition.deliberate_model or entity.config.harness.models.deliberate
        user = (
            "You may propose up to "
            f"{max_n} optional scheduled routines (habits) for yourself as JSON only.\n"
            "Schema: an array of objects with keys: title (short), why (one sentence), "
            "schedule_example (natural language like 'every morning at 8am'), "
            "deliver (empty string for internal journal-only, or telegram:CHAT_ID style).\n\n"
            f"Context JSON:\n{json.dumps(payload, ensure_ascii=False)[:6000]}\n\n"
            "Return ONLY valid JSON array, no markdown. If nothing fits, return []."
        )
        try:
            res = await entity.client.chat_completion(
                model,
                [
                    {
                        "role": "system",
                        "content": "Output machine-readable JSON only.",
                    },
                    {"role": "user", "content": user},
                ],
                temperature=0.55,
                max_tokens=700,
                think=False,
                num_ctx=entity.config.effective_ollama_num_ctx(),
            )
            raw = (res.content or "").strip()
        except Exception:
            return []

        m = re.search(r"\[[\s\S]*\]", raw)
        if not m:
            return []
        try:
            arr = json.loads(m.group(0))
        except json.JSONDecodeError:
            return []
        if not isinstance(arr, list):
            return []
        out: list[dict[str, Any]] = []
        for item in arr[:max_n]:
            if not isinstance(item, dict):
                continue
            out.append(
                {
                    "title": str(item.get("title", "")).strip(),
                    "why": str(item.get("why", "")).strip(),
                    "schedule_example": str(item.get("schedule_example", "")).strip(),
                    "deliver": str(item.get("deliver", "")).strip(),
                }
            )
        return [x for x in out if x.get("title")]
