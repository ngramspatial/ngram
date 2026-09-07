"""Exogenous seeds for GEN — one unpredictable token bundle per tick (see design spec)."""

from __future__ import annotations

import json
import random
import re
import time
from collections import deque
from pathlib import Path
from typing import Any

import structlog

from ngram.memory.relational_document import tail_sentences
from ngram.memory.seed_log import insert_seed_entry
from ngram.models import new_id

log = structlog.get_logger("ngram.identity.noise_seeder")

_SOURCE_KEYS = (
    "episodic_random",
    "belief_random",
    "knowledge_random",
    "world_discovery",
    "relationship_echo",
    "journal_echo",
    "temporal",
    "counterfactual_simulation",
    "dream_state",
    "web_venturing",
)

_RANDOM_DOMAINS = (
    "biology",
    "history",
    "music",
    "urban planning",
    "games",
    "fiction",
    "physics",
    "cooking",
    "linguistics",
    "architecture",
    "networks",
    "economics",
)


def _normalize_weights(raw: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for k in _SOURCE_KEYS:
        v = float(raw.get(k, 0.0) or 0.0)
        if v > 0:
            out[k] = v
    s = sum(out.values())
    if s <= 0:
        return {k: 1.0 / len(_SOURCE_KEYS) for k in _SOURCE_KEYS}
    return {k: out[k] / s for k in out}


def _weighted_choice(rng: random.Random, weights: dict[str, float]) -> str:
    items = list(weights.items())
    total = sum(w for _, w in items)
    r = rng.random() * total
    acc = 0.0
    for k, w in items:
        acc += w
        if r <= acc:
            return k
    return items[-1][0]


class NoiseSeeder:
    """Produces one seed per tick; TonicBody passes it to NoiseEngine.generate."""

    def __init__(
        self,
        *,
        entity_name: str,
        soma_cfg: dict[str, Any],
        knowledge_path: Path,
        journal_path: Path,
        curiosity_topics: list[str],
        relational_enabled: bool,
        web_tools_enabled: bool,
    ) -> None:
        self._entity_name = entity_name
        cfg = dict(soma_cfg.get("noise_seeder") or {})
        self._enabled = bool(cfg.get("enabled", False))
        self._cycle_seconds = float(cfg.get("cycle_seconds", 90.0) or 90.0)
        self._weights = _normalize_weights(dict(cfg.get("source_weights") or {}))
        self._recency_window = max(2, int(cfg.get("recency_suppression_window", 3) or 3))
        self._max_resample = max(1, int(cfg.get("max_resample_attempts", 3) or 3))
        wd = dict(cfg.get("world_discovery") or {})
        self._concepts_path = Path(wd.get("concepts_path") or "configs/noise_seeds/concepts.txt")
        if not self._concepts_path.is_absolute():
            self._concepts_path = Path(__file__).resolve().parent.parent.parent / self._concepts_path
        self._allow_external_fetch = bool(wd.get("allow_external_fetch", False))
        self._external_fetch_p = float(wd.get("external_fetch_probability", 0.1) or 0.1)
        self._curiosity_templates: tuple[str, ...] = tuple(
            wd.get("curiosity_question_templates")
            or (
                "what would {topic} look like from the perspective of {domain}?",
                "is there a connection between {topic} and {concept}?",
                "what's the opposite of {topic}?",
            )
        )
        self._episodic_min_age_h = float((cfg.get("episodic_random") or {}).get("min_age_hours", 24) or 24)
        self._belief_min_age_h = float((cfg.get("belief_random") or {}).get("min_age_hours", 12) or 12)
        self._rel_min_silence_h = float((cfg.get("relationship_echo") or {}).get("min_silence_hours", 48) or 48)
        self._journal_min_age_d = float((cfg.get("journal_echo") or {}).get("min_age_days", 3) or 3)
        self._knowledge_path = Path(knowledge_path).expanduser()
        self._journal_path = Path(journal_path).expanduser()
        self._curiosity_topics = [str(t).strip() for t in (curiosity_topics or []) if str(t).strip()]
        self._relational_enabled = bool(relational_enabled)
        self._web_tools_enabled = bool(web_tools_enabled)
        self._last_tick = 0.0
        self._source_recency: deque[str] = deque(maxlen=5)
        self._rng = random.Random()
        self._last_concept_thread: str = ""

    def should_tick(self, *, noise_enabled: bool) -> bool:
        if not self._enabled or not noise_enabled:
            return False
        return (time.monotonic() - self._last_tick) >= self._cycle_seconds

    def _suppress_weights(self, base: dict[str, float], tonic: Any = None) -> dict[str, float]:
        w = dict(base)

        # Mood-congruent Daydreaming: Bias weights based on Soma state
        if tonic is not None:
            try:
                pct = tonic.bars.snapshot_pct()
                if pct.get("tension", 0) > 75:
                    w["episodic_random"] = w.get("episodic_random", 1.0) * 2.0  # Rumination
                    w["world_discovery"] = w.get("world_discovery", 1.0) * 0.2
                if pct.get("social", 0) < 30 or pct.get("loneliness", 0) > 70:
                    w["relationship_echo"] = w.get("relationship_echo", 1.0) * 2.5
                if pct.get("curiosity", 0) > 70:
                    w["web_venturing"] = w.get("web_venturing", 1.0) * 2.0
                    w["world_discovery"] = w.get("world_discovery", 1.0) * 1.5
            except Exception:
                pass

        if len(self._source_recency) < self._recency_window - 1:
            return w
        last = list(self._source_recency)
        if len(last) >= self._recency_window - 1:
            tail = last[-(self._recency_window - 1) :]
            if len(set(tail)) == 1:
                bad = tail[0]
                if bad in w:
                    w[bad] = w[bad] * 0.08
                s = sum(w.values())
                if s > 0:
                    return {k: v / s for k, v in w.items()}
        return w

    async def tick(
        self,
        conn: Any,
        *,
        tonic: Any,
        client: Any | None = None,
    ) -> None:
        """Select a seed strategy, produce text, log to DB, set tonic pending fields."""
        self._last_tick = time.monotonic()
        if not self._enabled:
            return
        weights = self._suppress_weights(self._weights, tonic=tonic)
        tried: set[str] = set()
        seed_text = ""
        source_type = ""
        source_detail = ""
        for _attempt in range(self._max_resample):
            remaining = {k: v for k, v in weights.items() if k not in tried}
            if not remaining:
                break
            pick = _weighted_choice(self._rng, remaining)
            tried.add(pick)
            pair = await self._try_source(conn, pick, client=client)
            if pair:
                seed_text, source_detail = pair
                source_type = pick
                break
        if not seed_text:
            log.debug("noise_seeder_no_seed", entity=self._entity_name)
            return

        row_id = new_id("sd_")
        trace_id = new_id("tr_")
        self._source_recency.append(source_type)
        try:
            await insert_seed_entry(
                conn,
                row_id=row_id,
                entity_name=self._entity_name,
                tick_timestamp=time.time(),
                source_type=source_type,
                source_detail=source_detail[:500] if source_detail else "",
                seed_text=seed_text[:2000],
                trace_id=trace_id,
            )
            await conn.commit()
        except Exception as e:
            log.warning("noise_seeder_log_insert_failed", error=str(e))
            return

        tonic._pending_seed_text = seed_text[:2000]
        tonic._pending_seed_trace_id = trace_id
        tonic._pending_seed_log_id = row_id
        log.info(
            "noise_seeder_seed",
            entity=self._entity_name,
            source=source_type,
            trace_id=trace_id,
        )

    async def _try_source(
        self,
        conn: Any,
        key: str,
        *,
        client: Any | None,
    ) -> tuple[str, str] | None:
        now = time.time()
        try:
            if key == "episodic_random":
                return await self._src_episodic(conn, now)
            if key == "belief_random":
                return await self._src_belief(conn, now)
            if key == "knowledge_random":
                return self._src_knowledge()
            if key == "world_discovery":
                return await self._src_world_discovery(conn, client)
            if key == "relationship_echo":
                return await self._src_relationship(conn, now)
            if key == "journal_echo":
                return self._src_journal(now)
            if key == "temporal":
                return self._src_temporal(now)
            if key == "counterfactual_simulation":
                return await self._src_counterfactual(conn, now)
            if key == "dream_state":
                return self._src_dream_state()
            if key == "web_venturing":
                return self._src_web_venturing()
        except Exception as e:
            log.debug("noise_seeder_source_failed", source=key, error=str(e))
        return None

    def _src_web_venturing(self) -> tuple[str, str] | None:
        if not self._web_tools_enabled:
            return None
        return ("[web_venturing] There is a vast world on the internet right now. Follow a curiosity, find a Wikipedia article, look up news, or learn a completely new skill unprompted.", "web_venturing")

    def _src_dream_state(self) -> tuple[str, str] | None:
        """Surreal mix of domains during dormant/long silence periods."""
        domain1 = self._rng.choice(_RANDOM_DOMAINS)
        domain2 = self._rng.choice(_RANDOM_DOMAINS)
        return (f"[dream] A disjointed thought crosses: how does {domain1} relate to {domain2}?", "dream")

    async def _src_counterfactual(self, conn: Any, now: float) -> tuple[str, str] | None:
        cutoff = now - self._episodic_min_age_h * 3600.0
        cur = await conn.execute(
            "SELECT id, summary FROM episodes WHERE timestamp < ? ORDER BY RANDOM() LIMIT 1",
            (cutoff,),
        )
        row = await cur.fetchone()
        if not row:
            return None
        eid, summary = str(row[0]), str(row[1] or "").strip()
        snip = summary[:150] + ("…" if len(summary) > 150 else "")
        return (f"[counterfactual] What if I had acted differently? Memory: {snip}", eid)

    async def _src_episodic(self, conn: Any, now: float) -> tuple[str, str] | None:
        cutoff = now - self._episodic_min_age_h * 3600.0
        cur = await conn.execute(
            "SELECT id, summary FROM episodes WHERE timestamp < ? ORDER BY RANDOM() LIMIT 1",
            (cutoff,),
        )
        row = await cur.fetchone()
        if not row:
            return None
        eid, summary = str(row[0]), str(row[1] or "").strip()
        if not summary:
            return None
        snip = summary[:150] + ("…" if len(summary) > 150 else "")
        return (f"[memory] {snip}", eid)

    async def _src_belief(self, conn: Any, now: float) -> tuple[str, str] | None:
        cutoff = now - self._belief_min_age_h * 3600.0
        cur = await conn.execute(
            "SELECT id, content, confidence FROM beliefs WHERE last_reinforced < ? "
            "ORDER BY RANDOM() LIMIT 1",
            (cutoff,),
        )
        row = await cur.fetchone()
        if not row:
            return None
        bid, content, conf = str(row[0]), str(row[1] or "").strip(), float(row[2] or 0.0)
        if not content:
            return None
        return (f"[belief] {content[:220]} (confidence: {conf:.2f})", bid)

    def _src_knowledge(self) -> tuple[str, str] | None:
        if not self._knowledge_path.is_file():
            return None
        try:
            text = self._knowledge_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return None
        headers = re.findall(r"(?m)^##\s+(.+)$", text)
        if not headers:
            return None
        title = self._rng.choice(headers).strip()
        body_start = text.find(f"## {title}")
        if body_start < 0:
            return None
        rest = text[body_start + len(f"## {title}") :]
        next_h = re.search(r"(?m)^##\s+", rest[1:])
        chunk = rest[: next_h.start() + 1] if next_h is not None else rest
        chunk = re.sub(r"\s+", " ", chunk.strip())
        snip = chunk[:100] + ("…" if len(chunk) > 100 else "")
        return (f"[knowledge] {title}: {snip}", title[:120])

    async def _src_world_discovery(
        self,
        conn: Any,
        client: Any | None,
    ) -> tuple[str, str] | None:
        if (
            self._allow_external_fetch
            and self._web_tools_enabled
            and self._rng.random() < self._external_fetch_p
        ):
            title = await self._fetch_wikipedia_random_title()
            if title:
                return (f"[external] {title}", "wikipedia_random")
        # curiosity-biased question vs concept — 45% question if topics exist
        if self._curiosity_topics and self._rng.random() < 0.45:
            q = self._make_curiosity_question()
            if q:
                return (f"[curiosity] {q}", "curiosity_template")
        concept_pair = await self._pick_concept(conn)
        if concept_pair:
            concept, used = concept_pair
            # Associative chaining: remember the last concept for future turns
            self._last_concept_thread = concept
            return (f"[world] {concept}", used)
        q = self._make_curiosity_question()
        if q:
            return (f"[curiosity] {q}", "curiosity_template_fallback")
        return None

    def _make_curiosity_question(self) -> str:
        if not self._curiosity_topics:
            return ""
        topic = self._rng.choice(self._curiosity_topics)
        dom = self._rng.choice(_RANDOM_DOMAINS)
        conc = self._pick_concept_line()
        tpl = self._rng.choice(self._curiosity_templates)
        if "{concept}" in tpl and not conc:
            tpl = tpl.replace(" {concept}", "").replace("{concept}", "something unfamiliar")
        aspect = topic.split()[0] if topic.split() else topic
        try:
            return tpl.format(topic=topic, domain=dom, concept=conc or "an unrelated field", topic_aspect=aspect)
        except Exception:
            return f"what would {topic} look like from the perspective of {dom}?"

    def _pick_concept_line(self) -> str:
        lines = self._load_concept_lines()
        if not lines:
            return ""
        return self._rng.choice(lines)

    def _load_concept_lines(self) -> list[str]:
        p = self._concepts_path
        if not p.is_file():
            return []
        try:
            raw = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return []
        return [ln.strip() for ln in raw.splitlines() if ln.strip() and not ln.strip().startswith("#")]

    async def _pick_concept(self, conn: Any) -> tuple[str, str] | None:
        lines = self._load_concept_lines()
        if not lines:
            return None
        used = await self._load_used_concepts(conn)
        candidates = [c for c in lines if c not in used]
        pool = candidates if candidates else lines

        # Associative Chaining: prefer concepts that share words with the previous thread
        choice: str | None = None
        if self._last_concept_thread and self._rng.random() < 0.70:
            prev_words = set(self._last_concept_thread.lower().split())
            scored = []
            for c in pool:
                overlap = len(prev_words & set(c.lower().split()))
                if overlap > 0:
                    scored.append((overlap, c))
            if scored:
                scored.sort(key=lambda x: x[0], reverse=True)
                # Pick from top candidates with some randomness
                top = scored[: min(5, len(scored))]
                choice = self._rng.choice(top)[1]

        if choice is None:
            choice = self._rng.choice(pool)

        used.add(choice)
        await self._save_used_concepts(conn, used)
        return choice, "concept_corpus"

    async def _load_used_concepts(self, conn: Any) -> set[str]:
        try:
            cur = await conn.execute(
                "SELECT value FROM entity_state WHERE key = ?",
                ("noise_seeder_used_concepts",),
            )
            row = await cur.fetchone()
            if not row or not row[0]:
                return set()
            data = json.loads(str(row[0]))
            if isinstance(data, list):
                return set(str(x) for x in data if x)
        except Exception:
            pass
        return set()

    async def _save_used_concepts(self, conn: Any, used: set[str]) -> None:
        trimmed = list(used)[-80:]
        try:
            await conn.execute(
                "INSERT OR REPLACE INTO entity_state (key, value) VALUES (?, ?)",
                ("noise_seeder_used_concepts", json.dumps(trimmed)),
            )
        except Exception:
            pass

    async def _fetch_wikipedia_random_title(self) -> str:
        try:
            import aiohttp

            url = (
                "https://en.wikipedia.org/w/api.php?action=query&format=json"
                "&list=random&rnnamespace=0&rnlimit=1"
            )
            timeout = aiohttp.ClientTimeout(total=8)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers={"User-Agent": "NgramNoiseSeeder/1.0"}) as resp:
                    if resp.status != 200:
                        return ""
                    js = await resp.json()
            q = js.get("query") or {}
            rnd = q.get("random") or []
            if rnd and isinstance(rnd, list):
                t = rnd[0].get("title")
                if t:
                    return str(t)
        except Exception as e:
            log.debug("wikipedia_random_failed", error=str(e))
        return ""

    async def _src_relationship(self, conn: Any, now: float) -> tuple[str, str] | None:
        if not self._relational_enabled:
            return None
        cutoff = now - self._rel_min_silence_h * 3600.0
        cur = await conn.execute(
            "SELECT person_id, person_name, document FROM relational_documents "
            "WHERE last_interaction < ? AND length(document) > 40 ORDER BY RANDOM() LIMIT 1",
            (cutoff,),
        )
        row = await cur.fetchone()
        if not row:
            return None
        pid, _, doc = str(row[0]), str(row[1] or ""), str(row[2] or "")
        tail = tail_sentences(doc, 2)
        if not tail:
            return None
        snip = tail[:280] + ("…" if len(tail) > 280 else "")
        return (f"[relationship] {snip}", pid)

    def _src_journal(self, now: float) -> tuple[str, str] | None:
        if not self._journal_path.is_file():
            return None
        try:
            text = self._journal_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return None
        parts = re.split(r"(?=\n## \d{4}-\d{2}-\d{2})", text)
        entries: list[tuple[float, str]] = []
        cutoff = now - self._journal_min_age_d * 86400.0
        for p in parts:
            p = p.strip()
            if not p:
                continue
            m = re.match(r"^## (\d{4}-\d{2}-\d{2})", p)
            if not m:
                continue
            try:
                import datetime as _dt

                dt = _dt.datetime.strptime(m.group(1), "%Y-%m-%d").timetuple()
                ts = time.mktime(dt)
            except Exception:
                continue
            if ts >= cutoff:
                continue
            body = re.sub(r"^##[^\n]*\n", "", p, count=1).strip()
            if body:
                entries.append((ts, body))
        if not entries:
            return None
        _, body = self._rng.choice(entries)
        snip = body[:150] + ("…" if len(body) > 150 else "")
        return (f"[journal] {snip}", "journal_entry")

    def _src_temporal(self, now: float) -> tuple[str, str] | None:
        import datetime as dt

        local = dt.datetime.now().astimezone()
        hour = local.hour
        wday = local.strftime("%A")
        lines = [
            f"it's {hour}:{'%02d' % local.minute} on {wday} — the room has its own rhythm.",
            f"another {wday.lower()} — some weeks this slot feels heavier than others.",
        ]
        # crude daypart
        if hour < 5:
            lines.append("late night quiet — thoughts get louder when the feed goes still.")
        elif hour < 12:
            lines.append("morning light — the day hasn't committed to a shape yet.")
        else:
            lines.append("afternoon weight — energy finds corners to pool in.")

        # silence since last message — need tonic? passed via tick; approximate from entity_state not available here
        frag = self._rng.choice(lines)
        return (f"[time] {frag}", "clock")
