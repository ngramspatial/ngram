"""Long-term micro-evolution: stats + deliberate JSON plan + persistence."""

from __future__ import annotations

import json
import time
import uuid
from collections import Counter
from typing import TYPE_CHECKING, Any

import structlog

from ngram.config import EntityConfig

if TYPE_CHECKING:
    from ngram.inference.protocol import InferenceProvider

log = structlog.get_logger("ngram.identity.evolution")


class EvolutionEngine:
    def __init__(self, entity: EntityConfig) -> None:
        self.entity = entity

    async def run_cycle(self, stats: dict[str, Any]) -> dict[str, float]:
        """Backward-compatible shallow cycle (tests / callers)."""
        traits = dict(self.entity.personality.core_traits)
        changed: dict[str, float] = {}
        anxiety_trend = float(stats.get("anxiety_trend", 0))
        if abs(anxiety_trend) > 0.1:
            delta = 0.01 if anxiety_trend > 0 else -0.01
            k = "neuroticism"
            traits[k] = max(0.0, min(1.0, traits.get(k, 0.3) + delta))
            changed[k] = traits[k]
            log.info(
                "trait_adjustment",
                module="evolution",
                trait=k,
                new_value=traits[k],
                reason="emotional_history_trend",
            )
        self.entity.personality.core_traits.update(traits)
        return changed

    async def run_deep_cycle(self, conn, client: InferenceProvider, entity_facade: Any) -> None:
        """Every ~100 interactions: analyze patterns, apply ±0.01 trait caps, persist."""
        episodes = await entity_facade.episodic.recent_for_evolution(conn, 100)
        if len(episodes) < 3:
            return

        emo_counts = Counter(e.emotional_imprint.value for e in episodes)
        tag_counts: Counter[str] = Counter()
        for e in episodes:
            for t in e.tags:
                tag_counts[t.lower()] += 1

        rels = await entity_facade.relational.list_recent(conn, 18)
        rel_lines = [
            f"{r.name}: warmth {r.warmth:.2f}, trust {r.trust:.2f}, n={r.interaction_count}"
            for r in rels[:12]
        ]

        stats_blob: dict[str, Any] = {
            "episode_count": len(episodes),
            "emotional_imprint_histogram": dict(emo_counts),
            "top_tags": tag_counts.most_common(18),
            "relationship_summary": rel_lines,
            "current_traits": dict(self.entity.personality.core_traits),
            "current_behavior": dict(self.entity.personality.behavioral_patterns),
            "curiosity_topics": list(self.entity.drives.curiosity_topics),
        }
        tonic = getattr(entity_facade, "tonic", None)
        if tonic is not None:
            try:
                pct = tonic.bars.snapshot_pct()
                stats_blob["soma_snapshot"] = {
                    "bars_pct": pct,
                    "salience": round(float(tonic.compute_salience()), 4),
                    "active_conflicts": [
                        str(c.get("label") or "")
                        for c in getattr(tonic.bars, "_active_conflicts", []) or []
                    ],
                    "active_impulses": [
                        str(i.get("label") or "")
                        for i in getattr(tonic.bars, "_active_impulses", []) or []
                        if not i.get("on_cooldown")
                    ],
                }
            except Exception:
                pass
        user = (
            "You are analyzing a long-running digital entity's experience. "
            "Return ONLY compact JSON (no markdown) with this shape:\n"
            '{"trait_adjustments":{"trait_name":0.01},'
            '"behavior_updates":{"conflict_response":"engage"},'
            '"new_curiosity_topics":["topic"],'
            '"reasoning":"one paragraph"}\n'
            "Rules: each trait_adjustments value must be -0.01, 0, or +0.01 only; "
            "at most 4 trait keys. behavior_updates optional, max 2 keys, values short snake_case strings. "
            "new_curiosity_topics max 4 items, only if sustained interest is evident from tags/topics.\n\n"
            f"{json.dumps(stats_blob, indent=2)[:10000]}"
        )
        raw_json: dict[str, Any] = {}
        try:
            res = await client.chat_completion(
                self.entity.effective_deliberate_model(),
                [
                    {
                        "role": "system",
                        "content": "You output only valid JSON objects for machine parsing.",
                    },
                    {"role": "user", "content": user},
                ],
                temperature=0.35,
                max_tokens=700,
                think=False,
                num_ctx=self.entity.effective_ollama_num_ctx(),
            )
            text = (getattr(res, "content", None) or "").strip()
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1:
                raw_json = json.loads(text[start : end + 1])
        except Exception as e:
            log.warning("evolution_llm_failed", module="evolution", error=str(e))
            return

        reasoning = str(raw_json.get("reasoning") or "no_reason_given")[:2000]
        traits = dict(self.entity.personality.core_traits)
        adjustments = raw_json.get("trait_adjustments") or {}
        changed_traits: dict[str, float] = {}
        for k, v in adjustments.items():
            if k not in traits:
                continue
            try:
                dv = float(v)
            except (TypeError, ValueError):
                continue
            dv = max(-0.01, min(0.01, dv))
            if abs(dv) < 1e-9:
                continue
            nv = max(0.0, min(1.0, float(traits[k]) + dv))
            traits[k] = nv
            changed_traits[k] = nv
            log.info(
                "trait_adjustment",
                module="evolution",
                trait=k,
                new_value=nv,
                reason=reasoning[:400],
            )
        self.entity.personality.core_traits.update(traits)

        bupd = raw_json.get("behavior_updates") or {}
        if isinstance(bupd, dict):
            for bk, bv in list(bupd.items())[:2]:
                if isinstance(bk, str) and isinstance(bv, str):
                    self.entity.personality.behavioral_patterns[bk] = bv
                    log.info(
                        "behavior_pattern_update",
                        module="evolution",
                        pattern=bk,
                        value=bv,
                        reason=reasoning[:300],
                    )

        topics = raw_json.get("new_curiosity_topics") or []
        if isinstance(topics, list):
            cur = list(self.entity.drives.curiosity_topics)
            for t in topics[:4]:
                if isinstance(t, str) and t.strip() and t not in cur:
                    cur.append(t.strip())
            self.entity.drives.curiosity_topics = cur[:40]

        eid = f"evo_{uuid.uuid4().hex[:10]}"
        await conn.execute(
            "INSERT INTO evolution_log (id, timestamp, changes_json, reasoning) VALUES (?, ?, ?, ?)",
            (
                eid,
                time.time(),
                json.dumps(
                    {
                        "traits": changed_traits,
                        "behavior_updates": bupd,
                        "new_topics": topics,
                    }
                ),
                reasoning,
            ),
        )

        await conn.execute(
            "INSERT OR REPLACE INTO entity_state (key, value) VALUES (?, ?)",
            ("core_traits", json.dumps(self.entity.personality.core_traits)),
        )
        await conn.execute(
            "INSERT OR REPLACE INTO entity_state (key, value) VALUES (?, ?)",
            (
                "behavioral_patterns",
                json.dumps(self.entity.personality.behavioral_patterns),
            ),
        )
        await conn.execute(
            "INSERT OR REPLACE INTO entity_state (key, value) VALUES (?, ?)",
            ("curiosity_topics", json.dumps(self.entity.drives.curiosity_topics)),
        )
        await conn.commit()

        entity_facade.personality.invalidate_cache()
