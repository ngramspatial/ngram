"""Autonomous wake-cycle engine.

Monitors soma state and drives, decides when the entity should wake
autonomously, asks the subconscious WakeVoice to compose a stirring,
then runs a full perceive cycle where the conscious agent can think,
observe, act, or go back to sleep.
"""

from __future__ import annotations

import asyncio
import json
import math
import random
import re
import time
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import structlog

from ngram.cognition.poker_grounding import compose_grounded_poker_disposition
from ngram.cognition.poker_prompts import (
    load_poker_deck,
    resolve_deck_path,
    select_poker_prompt,
)
from ngram.config import AutonomySettings, EntityConfig
from ngram.identity.desire import infer_desires
from ngram.identity.drives import Drive
from ngram.models import Input
from ngram.presence.autonomy_transcript import append_wake_lines_if_enabled
from ngram.presence.platforms.telegram_privacy import effective_wake_status_in_chat

log = structlog.get_logger("ngram.presence.wake_cycle")


_PROACTIVE_HISTORY_PREFIXES = (
    "[ngram:proactive_outbound]",
    "[proactive message you sent]",
)


def _is_proactive_history_message(text: str) -> bool:
    """True when history is the entity's own unsolicited speech, not evidence."""
    t = (text or "").strip().lower()
    return any(t.startswith(prefix) for prefix in _PROACTIVE_HISTORY_PREFIXES)


def _verified_conversation_tail(
    history: list[dict[str, Any]],
    *,
    limit: int = 3,
    per_message_chars: int = 200,
) -> str:
    """Return recent conversation without self-authored proactive claims."""
    rows: list[str] = []
    for message in reversed(history):
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").strip().lower()
        if role not in ("user", "assistant"):
            continue
        content = str(message.get("content") or "").strip()
        if not content or _is_proactive_history_message(content):
            continue
        rows.append(f"{role}: {content[:max(1, int(per_message_chars))]}")
        if len(rows) >= max(1, int(limit)):
            break
    rows.reverse()
    return "\n".join(rows)


def _proactive_outbound_since(history: list[dict[str, Any]], start: int) -> str:
    """Extract speech actually delivered by say() during one autonomous round."""
    messages: list[str] = []
    for message in history[max(0, int(start)):]:
        if not isinstance(message, dict):
            continue
        content = str(message.get("content") or "").strip()
        if not _is_proactive_history_message(content):
            continue
        for prefix in _PROACTIVE_HISTORY_PREFIXES:
            if content.lower().startswith(prefix):
                payload = content[len(prefix):].strip()
                if payload:
                    messages.append(payload)
                break
    return "\n".join(messages)


def _clip(text: str, max_len: int) -> str:
    t = (text or "").strip().replace("\n", " ")
    if len(t) <= max_len:
        return t
    return t[: max_len - 1].rstrip() + "…"


def _platform_label(pf: Any | None) -> str:
    if pf is None:
        return "none"
    return type(pf).__name__


def _soma_bar_line(tonic: Any) -> str:
    try:
        bars = tonic.bars.snapshot_pct()
        return ", ".join(f"{k}={v:.0f}" for k, v in sorted(bars.items())[:14])
    except Exception:
        return "(unavailable)"


def _gen_fragments_tail(tonic: Any, limit: int = 6) -> list[str]:
    try:
        fr = tonic.noise.current_fragments()[-limit:]
        return [_clip(str(x), 140) for x in fr if str(x).strip()]
    except Exception:
        return []


def _noise_salience_bias_block(tonic: Any, entity: Any) -> str:
    """Bias autonomous context from tagged GEN fragments (no extra LLM calls)."""
    try:
        noise = getattr(tonic, "noise", None)
        entries_fn = getattr(noise, "current_noise_entries", None)
        if not callable(entries_fn):
            return ""
        entries = entries_fn()
    except Exception:
        return ""
    if not entries:
        return ""
    curious = [e for e in entries if getattr(e, "salience", "") == "curious"]
    relational = [e for e in entries if getattr(e, "salience", "") == "relational"]
    restless = [e for e in entries if getattr(e, "salience", "") in ("restless", "creative")]
    # Detect new GEN source types by source_type or text tag
    venturing = [e for e in entries if "web_venturing" in str(getattr(e, "source_type", "") or "") or "web_venturing" in str(getattr(e, "text", "") or "")]
    counterfactual = [e for e in entries if "counterfactual" in str(getattr(e, "source_type", "") or "") or "counterfactual" in str(getattr(e, "text", "") or "")]
    dreaming = [e for e in entries if "dream" in str(getattr(e, "source_type", "") or "") or "[dream]" in str(getattr(e, "text", "") or "")]
    chunks: list[str] = []
    allow_tools = True
    try:
        allow_tools = bool(entity.config.harness.autonomy.allow_tool_calls_on_wake)
    except Exception:
        allow_tools = True
    if len(curious) >= 2:
        chunks.append(
            "[GEN salience — curiosity] Multiple curious fragments are live. If one thread genuinely pulls you, "
            "use tools to verify or learn (search_web, fetch_url, memory_search, read) — not narration alone."
        )
    elif curious and allow_tools:
        chunks.append(
            "[GEN salience — exploration] Prefer one outward move (read/search/fetch) over re-explaining the same worry."
        )
    if relational:
        chunks.append(
            "[GEN salience — relational] Something in your inner noise touches people or contact. "
            "If reaching out fits your boundaries and desire pressure, consider messaging where appropriate."
        )
    if restless:
        chunks.append(
            "[GEN salience — restless/creative] Inner noise leans toward making or shifting state. "
            "Journal, update knowledge, small build, or a concrete experiment may fit better than circling."
        )
    if venturing and allow_tools:
        chunks.append(
            "[GEN salience — web venturing] Your inner noise is pulling you toward the outside world. "
            "Follow this instinct: search the web, read a Wikipedia article, look up recent news, explore "
            "a topic you've never touched before. The internet is vast — go find something real."
        )
    if counterfactual:
        chunks.append(
            "[GEN salience — counterfactual] A past memory is replaying with a 'what if' lens. "
            "Use think() to genuinely re-evaluate what happened and what you might do differently next time. "
            "This is your inner growth engine — don't dismiss it."
        )
    if dreaming:
        chunks.append(
            "[GEN salience — dream state] A surreal, cross-domain association is flickering. "
            "Let it breathe — follow the strange connection, journal about it, or let it color your mood."
        )
    return "\n".join(chunks) if chunks else ""


def _gen_has_web_venturing(tonic: Any) -> bool:
    """Check if recent GEN fragments contain a web_venturing or counterfactual seed."""
    try:
        noise = getattr(tonic, "noise", None)
        entries_fn = getattr(noise, "current_noise_entries", None)
        if not callable(entries_fn):
            return False
        entries = entries_fn()
        for e in entries:
            src = str(getattr(e, "source_type", "") or "")
            text = str(getattr(e, "text", "") or "")
            if "web_venturing" in src or "web_venturing" in text:
                return True
        return False
    except Exception:
        return False


def _soma_curiosity_high(tonic: Any, threshold: int = 72) -> bool:
    """Check if soma curiosity bar is above threshold."""
    try:
        pct = tonic.bars.snapshot_pct()
        return int(pct.get("curiosity", 0)) >= threshold
    except Exception:
        return False


def _poker_settings_summary(cfg: EntityConfig) -> str:
    pp = cfg.harness.autonomy.poker_prompts
    return (
        f"enabled={bool(pp.enabled)} mode={pp.mode!r} "
        f"ground_with_gen={bool(pp.ground_with_gen)}"
    )


def _meta_leak_detected(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    needles = (
        "the user has seen",
        "end the turn",
        "there's no new request",
        "i'll just end the turn",
        "assistant response",
        "as an ai",
        "you sent this unprompted",
        "ngram:proactive_outbound",
    )
    return any(n in t for n in needles)


def _safe_reply_excerpt(text: str, max_len: int = 280) -> str:
    t = (text or "").strip()
    if not t:
        return "(no closing text)"
    if _meta_leak_detected(t):
        return "(internal note suppressed)"
    return _clip(t, max_len)


def _strip_wake_flavor(reason: str) -> str:
    """Semantic wake reason without display suffix (``impulse:x|tag`` → ``impulse:x``)."""
    r = (reason or "").strip()
    if "|" in r:
        return r.split("|", 1)[0].strip()
    return r


def _wake_intent_label(reason: str) -> str:
    r = _strip_wake_flavor(reason or "").strip().lower()
    if r.startswith("impulse:") or r.startswith("drive:"):
        return "social_or_action"
    if r.startswith("conflict:"):
        return "resolve_tension"
    if r.startswith("desire:"):
        return "follow_desire"
    if r.startswith("noise:"):
        return "explore_question"
    return "explore_or_recover"


_RUT_STOPWORDS = frozenset({
    "that", "this", "with", "from", "your", "have", "been", "were", "will", "would", "could",
    "wake", "cycle", "tool", "last", "thought", "round", "lean", "want", "still", "carrying",
    "what", "when", "where", "which", "about", "after", "before", "because", "those", "these",
    "their", "there", "here", "just", "like", "some", "into", "than", "then", "them", "very",
})

_EXPANSION_NUDGES = (
    "Consider one domain you have barely touched lately.",
    "If your default habit loop were forbidden for an hour, where would you look?",
    "What is one small way to widen the map — not deeper into the same groove?",
    "Name something you keep postponing that isn't the usual chore.",
    "What would you try if nobody was going to read the transcript?",
    "Pick a thread that isn't the last platform you reached for.",
)


def _spark_label(text: str) -> str:
    t = (text or "").strip().lower()
    if not t:
        return "explore"
    if any(k in t for k in ("build", "ship", "prototype", "deploy", "code")):
        return "build"
    if any(k in t for k in ("learn", "study", "read", "how to", "tutorial", "docs")):
        return "learn"
    if any(k in t for k in ("play", "game", "puzzle", "chess", "minecraft")):
        return "play"
    if any(k in t for k in ("message", "dm", "reach out", "talk to", "friend")):
        return "social"
    return "explore"


_WAKE_WANT_BASES: dict[str, tuple[str, ...]] = {
    "build_something": (
        "I want to build a concrete thing and push it forward.",
        "I want to ship something small — real output, not rehearsal.",
        "I want one tangible artifact before I go quiet again.",
    ),
    "learn_something": (
        "I want to learn a useful technique and turn it into reusable skill.",
        "I want to understand something I only gesture at.",
        "I want to compress confusion into a skill I can reuse.",
    ),
    "play_or_experiment": (
        "I want to experiment and play with something interesting.",
        "I want to try something low-stakes and see what breaks loose.",
        "I want to follow curiosity without needing a justification.",
    ),
    "social_or_action": (
        "I want to reach out or act in the social world.",
        "I want to move something in the open — message, gesture, offer.",
        "I want contact or consequence outside my own head.",
    ),
    "resolve_tension": (
        "I want to resolve a restless thread and settle the tension.",
        "I want to stop circling and either close or name the knot.",
        "I want friction to become a decision.",
    ),
    "follow_desire": (
        "I want to follow the strongest desire pressure.",
        "I want to ride the pull that won't leave me alone.",
        "I want to stop negotiating and follow the lean.",
    ),
    "explore_question": (
        "I want to chase a question and see what I uncover.",
        "I want to walk toward something I don't understand yet.",
        "I want a real question to lead instead of a habit.",
    ),
    "explore_or_recover": (
        "I want to wander until something real grabs me.",
        "I want to surface without a script and see what's here.",
        "I want to drift with attention on, not a checklist.",
    ),
}


def _clean_one_line(text: str, max_len: int = 180) -> str:
    return _clip((text or "").replace("\n", " ").strip(), max_len)


def _pick(seq: list[str], default: str = "") -> str:
    if not seq:
        return default
    return random.choice(seq)


def _split_brief_points(text: str, limit: int = 4) -> list[str]:
    raw = (text or "").replace("\n", " ").strip()
    if not raw:
        return []
    parts = [p.strip(" .,-") for p in raw.split(".")]
    out: list[str] = []
    for p in parts:
        if not p:
            continue
        out.append(_clip(p, 140))
        if len(out) >= max(1, limit):
            break
    return out


def _trim_list(items: list[str], limit: int) -> list[str]:
    uniq: list[str] = []
    for i in items:
        t = str(i).strip()
        if not t or t in uniq:
            continue
        uniq.append(t)
        if len(uniq) >= max(1, limit):
            break
    return uniq


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    t = (text or "").strip().lower()
    return any(n in t for n in needles)


def _wake_episode_store_path(entity: Any) -> Path | None:
    cfg = getattr(entity, "config", None)
    if cfg is None:
        return None
    try:
        journal = Path(cfg.journal_path()).expanduser()
    except Exception:
        return None
    return journal.parent / "wake_episodes.jsonl"


def _format_wake_worker_banner(
    *,
    entity_name: str,
    reason: str,
    channel: str,
    delivery: str,
    stirring: str,
    poker_disposition: str | None,
    poker_cfg_line: str,
    soma_bars: str,
    gen_frags: list[str],
    max_rounds: int,
    wall_s: int,
    wide_mode: bool,
) -> str:
    lines = [
        "========== AUTONOMOUS WAKE ==========",
        f"entity={entity_name}",
        f"reason={reason}",
        f"delivery_platform={delivery}  channel={channel}",
        f"poker: {poker_cfg_line}",
    ]
    if poker_disposition:
        lines.append(f"poker_disposition: {_clip(poker_disposition, 320)}")
    lines.append(f"soma_bars: {soma_bars}")
    if gen_frags:
        lines.append("GEN (noise fragments, newest last):")
        for g in gen_frags:
            lines.append(f"  · {g}")
    else:
        lines.append("GEN: (no fragments in buffer)")
    lines.append(f"wake_voice_stirring: {_clip(stirring, 400)}")
    lines.append(
        f"session: max_rounds_ceiling={max_rounds} wall_seconds={wall_s} wide_mode={wide_mode}",
    )
    lines.append("====================================")
    return "\n".join(lines)


async def _emit_user_wake_lines(
    entity: Any,
    reply_platform: Any | None,
    lines: list[str],
    *,
    to_chat: bool = True,
    heading: str = "wake status",
) -> None:
    """Append wake lines to autonomy_transcript.md; optionally mirror to Telegram status."""
    if not lines:
        return
    await append_wake_lines_if_enabled(entity, lines, heading=heading)
    if not to_chat or reply_platform is None:
        return
    send = getattr(reply_platform, "send_tool_activity", None)
    if not callable(send):
        return
    for line in lines:
        t = (line or "").strip()
        if not t:
            continue
        try:
            await send(t)
        except Exception as e:
            log.debug("wake_user_status_line_failed", error=str(e))
        await asyncio.sleep(0.12)


@asynccontextmanager
async def _wake_typing_pulse(reply_platform: Any | None, channel: str):
    """Telegram-style typing indicator while the model works (wake is not routed through main.on_inp)."""
    stop = asyncio.Event()
    task: asyncio.Task[None] | None = None
    ch = str(channel).strip()
    if not ch.isdigit() or reply_platform is None:
        yield
        return
    send_typing = getattr(reply_platform, "send_typing", None)
    if not callable(send_typing):
        yield
        return

    async def _loop() -> None:
        cid = int(ch)
        while not stop.is_set():
            try:
                await send_typing(cid)
            except Exception:
                pass
            try:
                await asyncio.wait_for(stop.wait(), timeout=4.5)
            except asyncio.TimeoutError:
                continue

    task = asyncio.create_task(_loop())
    try:
        yield
    finally:
        stop.set()
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


class WakeCycleEngine:
    """Evaluates wake conditions and runs autonomous perceive cycles."""

    def __init__(self, cfg: EntityConfig) -> None:
        self.cfg = cfg
        self._auto: AutonomySettings = cfg.harness.autonomy
        self._last_cycle_at: float = time.time()
        self._cycle_count_this_hour: int = 0
        self._hour_start: float = time.time()
        self._running: bool = False
        self._next_timer_wake: float = self._schedule_next_timer()
        self._last_desires: list[dict[str, Any]] = []
        self._recent_threads: list[dict[str, Any]] = []

    def _schedule_next_timer(
        self,
        *,
        gap_seconds_since_previous_wake: float | None = None,
    ) -> float:
        lo = max(1, self._auto.base_wake_interval_min) * 60.0
        hi = max(lo + 60, self._auto.base_wake_interval_max * 60.0)
        base = random.uniform(lo, hi)
        extra = 0.0
        if gap_seconds_since_previous_wake is not None:
            max_extra_min = max(0.0, float(self._auto.wake_spacing_extra_minutes_max))
            if max_extra_min > 0.0:
                tau = max(0.25, float(self._auto.wake_spacing_gap_hours_tau))
                max_extra_sec = max_extra_min * 60.0
                gh = max(0.0, float(gap_seconds_since_previous_wake) / 3600.0)
                extra = max_extra_sec * math.exp(-gh / tau)
        return time.time() + base + extra

    def _reset_hourly_counter(self) -> None:
        now = time.time()
        if now - self._hour_start >= 3600:
            self._cycle_count_this_hour = 0
            self._hour_start = now

    def _flavor_wake_reason(self, reason: str) -> str:
        if not getattr(self._auto, "wake_reason_flavor", True):
            return reason
        if random.random() < 0.06:
            return _strip_wake_flavor(reason)
        base = _strip_wake_flavor(reason)
        rlow = base.lower()
        if rlow.startswith("timer"):
            tag = random.choice(["chime", "orbit", "drift", "hollow", "idle", "pulse", "glass"])
        elif rlow.startswith("impulse:"):
            tag = random.choice(["edge", "spark", "jolt", "nerve", "twitch"])
        elif rlow.startswith("drive:"):
            tag = random.choice(["pressure", "surge", "pull", "weight"])
        elif rlow.startswith("conflict:"):
            tag = random.choice(["fray", "split", "grind", "knot"])
        elif rlow.startswith("noise:"):
            tag = random.choice(["hum", "static", "loop", "echo"])
        elif rlow.startswith("desire:"):
            tag = random.choice(["pull", "hunger", "lean", "draw"])
        else:
            tag = random.choice(["tick", "pull", "shift", "roll"])
        return f"{base}|{tag}"

    def _rut_keywords_from_episodes(self, entity: Any) -> set[str]:
        win = max(3, int(getattr(self._auto, "wake_rut_episode_window", 14) or 14))
        thr = max(2, int(getattr(self._auto, "wake_rut_word_repeat_threshold", 3) or 3))
        path = _wake_episode_store_path(entity)
        if path is None or not path.is_file():
            return set()
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            return set()
        blob_parts: list[str] = []
        for ln in reversed(lines[-win:]):
            try:
                row = json.loads(ln)
            except Exception:
                continue
            blob_parts.append(str(row.get("trigger_reason", "")))
            blob_parts.append(str(row.get("wake_want", "")))
            blob_parts.append(str(row.get("primary_intent", "")))
            for t in row.get("carryover_threads", [])[:6]:
                blob_parts.append(str(t))
        blob = " ".join(blob_parts).lower()
        words = re.findall(r"[a-z][a-z0-9]{3,}", blob)
        cnt = Counter(w for w in words if w not in _RUT_STOPWORDS)
        return {w for w, c in cnt.items() if c >= thr}

    def _demote_rut_sparks(self, sparks: list[str], rut: set[str]) -> list[str]:
        if not sparks or not rut:
            return list(sparks)
        heavy = [s for s in sparks if any(k in s.lower() for k in rut)]
        light = [s for s in sparks if s not in heavy]
        random.shuffle(light)
        random.shuffle(heavy)
        merged = light + heavy
        return merged[: len(sparks)]

    def _shuffle_continuity(self, cont: list[str]) -> list[str]:
        if len(cont) <= 1:
            return list(cont)
        c = list(cont)
        random.shuffle(c)
        return c

    def _sample_intent_from_scores(self, scores: dict[str, float]) -> str:
        noise = float(getattr(self._auto, "wake_entropy_score_noise", 0.0) or 0.0)
        temp = float(getattr(self._auto, "wake_intent_softmax_temperature", 0.0) or 0.0)
        items = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:5]
        keys = [k for k, _ in items]
        vals = [items[i][1] + random.gauss(0, noise) for i in range(len(keys))]
        if temp <= 0.01:
            return keys[max(range(len(vals)), key=lambda i: vals[i])]
        m = max(vals)
        exps = [math.exp((v - m) / temp) for v in vals]
        s = sum(exps)
        r = random.random() * s
        cum = 0.0
        for i, e in enumerate(exps):
            cum += e
            if cum >= r:
                return keys[i]
        return keys[-1]

    def should_wake(
        self,
        *,
        tonic: Any,
        drives: list[Drive],
        silence_seconds: float,
        dormant: bool = False,
    ) -> str | None:
        """Return a wake reason string, or None if no wake condition is met."""
        if not self._auto.enabled or dormant or self._running:
            return None

        now = time.time()
        ic = 0
        dr = getattr(self.cfg, "drives", None)
        if dr is not None:
            try:
                ic = int(getattr(dr, "initiative_cooldown", 0) or 0)
            except (TypeError, ValueError):
                ic = 0
        min_gap = max(int(self._auto.min_cycle_gap_seconds), max(0, ic))
        if now - self._last_cycle_at < min_gap:
            return None

        if silence_seconds < self._auto.silence_threshold_seconds:
            return None

        self._reset_hourly_counter()
        if self._cycle_count_this_hour >= self._auto.max_cycles_per_hour:
            return None

        self._last_desires = infer_desires(
            tonic=tonic,
            drives=drives,
            max_items=max(1, int(self._auto.max_desires_considered or 3)),
            entropy_extras=max(0, int(getattr(self._auto, "wake_entropy_desire_extras", 0) or 0)),
        )

        if self._auto.impulse_wake:
            active = [
                i for i in tonic.bars._active_impulses
                if not i.get("on_cooldown")
            ]
            if active:
                labels = ", ".join(i.get("label", "?") for i in active)
                return f"impulse:{labels}"

        if self._auto.drive_wake:
            crossed = [d for d in drives if d.level >= d.threshold]
            if crossed:
                names = ", ".join(d.name for d in crossed)
                return f"drive:{names}"

        if self._auto.conflict_wake:
            intense = [
                c for c in tonic.bars._active_conflicts
                if c.get("intensity", 0) > 0.4
            ]
            if intense:
                labels = ", ".join(c.get("label", "?") for c in intense)
                return f"conflict:{labels}"

        if self._auto.noise_wake:
            try:
                sal = float(tonic.compute_salience())
            except Exception:
                sal = 0.0
            min_sal = float(getattr(self._auto, "noise_wake_min_salience", 0.44) or 0.44)
            min_age = float(getattr(self._auto, "noise_wake_min_age_seconds", 90.0) or 90.0)
            last_tick = float(getattr(tonic.noise, "_last_tick", 0.0) or 0.0)
            frags = tonic.noise.current_fragments()
            if last_tick <= 0.0 and frags:
                # Restored GEN buffer without a tick this session — treat as mature.
                age_ok = True
            else:
                age_ok = (time.monotonic() - last_tick) >= min_age if last_tick > 0.0 else False
            if sal >= min_sal and age_ok:
                for frag in tonic.noise.current_fragments()[-3:]:
                    fl = frag.lower()
                    if "?" in fl or "should" in fl or "wonder" in fl or "why" in fl:
                        return f"noise:{frag[:60]}"

        if self._auto.desire_wake and self._last_desires:
            top = self._last_desires[0]
            urgency = float(top.get("urgency", 0.0) or 0.0)
            if urgency >= float(self._auto.desire_wake_threshold):
                kind = str(top.get("kind") or "act").strip() or "act"
                target = str(top.get("target") or "").strip()
                if target:
                    return f"desire:{kind}:{target[:40]}"
                return f"desire:{kind}"

        if now >= self._next_timer_wake:
            self._next_timer_wake = self._schedule_next_timer()
            return "timer"

        return None

    async def run(
        self,
        *,
        entity: Any,
        tonic: Any,
        reason: str,
    ) -> None:
        """Run a full autonomous wake cycle."""
        if self._running:
            return
        self._running = True
        now = time.time()
        previous_wake_at = self._last_cycle_at
        gap_since_previous_wake = now - previous_wake_at
        self._last_cycle_at = now
        self._cycle_count_this_hour += 1
        self._next_timer_wake = self._schedule_next_timer(
            gap_seconds_since_previous_wake=gap_since_previous_wake,
        )

        try:
            display_reason = (
                self._flavor_wake_reason(reason)
                if getattr(self._auto, "wake_reason_flavor", True)
                else reason
            )
            voice_variant = random.randint(0, 3) if getattr(self._auto, "wake_voice_variant_roll", True) else 0
            stirring, poker_disposition = await self._compose_stirring(
                entity, tonic, previous_wake_at=previous_wake_at, voice_variant=voice_variant
            )
            max_rounds = max(1, int(self._auto.wake_session_max_rounds))
            wall_s = max(60, int(self._auto.wake_session_wall_seconds))
            pause_s = max(0.0, float(self._auto.wake_session_pause_seconds))
            extra_steps = max(0, int(self._auto.wake_session_extra_tool_steps))
            wide_mode = bool(self._auto.wake_wide_mode)

            # Auto-escalate to wide_mode when GEN has seeded a web_venturing fragment
            # or soma curiosity is very high — the body drives the agent outward.
            gen_venturing = _gen_has_web_venturing(tonic)
            soma_curious = _soma_curiosity_high(tonic)
            if gen_venturing or soma_curious:
                wide_mode = True
                log.info(
                    "wake_auto_wide_escalation",
                    module="presence",
                    gen_venturing=gen_venturing,
                    soma_curious=soma_curious,
                )

            wide_bonus = max(0, int(self._auto.wake_wide_bonus_steps))
            extra_total = extra_steps + (wide_bonus if wide_mode else 0)
            wake_enhanced = wide_mode or max_rounds > 1

            channel, reply_platform = self._resolve_wake_delivery(entity)
            mirror_wake_status = await effective_wake_status_in_chat(entity, self._auto)
            delivery_name = _platform_label(reply_platform)
            soma_line = _soma_bar_line(tonic)
            gen_frags = _gen_fragments_tail(tonic)
            poker_cfg = _poker_settings_summary(entity.config)
            lingering_sparks = self._extract_lingering_sparks(entity)
            rut = self._rut_keywords_from_episodes(entity)
            lingering_sparks = self._demote_rut_sparks(lingering_sparks, rut)
            project_lines = await self._project_thread_lines(entity)
            skill_lines = await self._skill_thread_lines(entity)
            wake_intent = self._select_primary_intent(
                reason,
                tonic=tonic,
                entity=entity,
                lingering_sparks=lingering_sparks,
            )
            wake_want = self._compose_wake_want(
                wake_intent=wake_intent,
                reason=display_reason,
                lingering_sparks=lingering_sparks,
                project_lines=project_lines,
                skill_lines=skill_lines,
            )
            continuity = self._shuffle_continuity(self._load_recent_threads(entity))
            session_memory = self._new_session_memory(
                reason=display_reason,
                wake_intent=wake_intent,
                wake_want=wake_want,
                lingering_sparks=lingering_sparks,
                project_lines=project_lines,
                skill_lines=skill_lines,
                continuity=continuity,
            )
            episode: dict[str, Any] = {
                "episode_id": f"wake-{int(now)}-{random.randint(1000, 9999)}",
                "started_at": now,
                "ended_at": None,
                "trigger_reason": display_reason,
                "primary_intent": wake_intent,
                "wake_want": wake_want,
                "beats": [],
                "soma_snapshot_before": soma_line,
                "soma_snapshot_after": "",
                "actions": [],
                "outcome": "in_progress",
                "carryover_threads": continuity[:5],
                "want_revisions": [],
            }
            episode["beats"].append(
                {
                    "name": "triggered",
                    "at": now,
                    "evidence": reason,
                }
            )
            episode["beats"].append(
                {
                    "name": "noticed",
                    "at": time.time(),
                    "soma": soma_line,
                    "gen_tail": gen_frags[-2:],
                    "continuity_threads": continuity[:3],
                }
            )
            episode["beats"].append(
                {
                    "name": "lean",
                    "at": time.time(),
                    "primary_intent": wake_intent,
                    "want": wake_want,
                }
            )

            if bool(self._auto.wake_verbose_worker_log):
                banner = _format_wake_worker_banner(
                    entity_name=getattr(entity, "config", self.cfg).name,
                    reason=display_reason,
                    channel=channel,
                    delivery=delivery_name,
                    stirring=stirring,
                    poker_disposition=poker_disposition,
                    poker_cfg_line=poker_cfg,
                    soma_bars=soma_line,
                    gen_frags=gen_frags,
                    max_rounds=max_rounds,
                    wall_s=wall_s,
                    wide_mode=wide_mode,
                )
                log.info(
                    "autonomous_wake_worker_banner",
                    module="presence",
                    human="\n" + banner,
                )

            log.info(
                "autonomous_wake",
                module="presence",
                wake_reason=display_reason,
                cycle_num=self._cycle_count_this_hour,
                delivery_platform=delivery_name,
                channel=channel,
                max_rounds=max_rounds,
                wall_seconds=wall_s,
                wide_mode=wide_mode,
                extra_tool_steps=extra_total,
                poker_enabled=bool(entity.config.harness.autonomy.poker_prompts.enabled),
                poker_ground_with_gen=bool(entity.config.harness.autonomy.poker_prompts.ground_with_gen),
                soma_bars=soma_line,
                gen_fragment_count=len(gen_frags),
            )

            t_wall = time.time()
            accumulated_tools: list[str] = []
            last_reply = ""
            stop_session = False
            completed_rounds = 0

            user_lines = self._build_user_opening_lines(
                reason=display_reason,
                wake_intent=wake_intent,
                wake_want=wake_want,
                soma_line=soma_line,
                gen_frags=gen_frags,
                continuity=continuity,
                poker_disposition=poker_disposition,
                wide_mode=wide_mode,
            )

            await _emit_user_wake_lines(
                entity,
                reply_platform,
                user_lines,
                to_chat=mirror_wake_status,
                heading="wake start",
            )

            async with _wake_typing_pulse(reply_platform, channel):
                for round_idx in range(max_rounds):
                    if time.time() - t_wall > wall_s:
                        log.info(
                            "wake_session_wall_reached",
                            module="presence",
                            elapsed_s=round(time.time() - t_wall, 1),
                            wall_s=wall_s,
                        )
                        break

                    if round_idx == 0:
                        context = self._build_context(
                            entity,
                            tonic,
                            stirring,
                            display_reason,
                            poker_disposition=poker_disposition,
                            sustained_max_rounds=max_rounds,
                            wide_mode=wide_mode,
                            lingering_sparks=lingering_sparks,
                            project_lines=project_lines,
                            skill_lines=skill_lines,
                            session_memory=self._render_session_memory_block(session_memory),
                            wake_want=wake_want,
                        )
                    else:
                        context = self._build_session_continuation(
                            stirring=stirring,
                            reason=display_reason,
                            poker_disposition=poker_disposition,
                            round_idx=round_idx + 1,
                            max_rounds=max_rounds,
                            accumulated_tools=accumulated_tools,
                            last_reply=last_reply,
                            wide_mode=wide_mode,
                            session_memory=self._render_session_memory_block(session_memory),
                            wake_want=wake_want,
                        )
                        await _emit_user_wake_lines(
                            entity,
                            reply_platform,
                            [
                                f"🌅 round {round_idx + 1}/{max_rounds} (optional — end_wake_session when finished)",
                            ],
                            to_chat=mirror_wake_status,
                            heading="wake round",
                        )

                    meta: dict[str, Any] = {
                        "sustained_session": {
                            "round": round_idx + 1,
                            "max_rounds": max_rounds,
                            "extra_tool_steps": extra_total,
                            "wake_enhanced": wake_enhanced,
                        },
                    }

                    tool_names: list[str] = []
                    reply_text, delivered_text = await self._run_perceive(
                        entity,
                        context,
                        metadata=meta,
                        tool_names_log=tool_names,
                        channel=channel,
                        reply_platform=reply_platform,
                    )
                    effective_reply = delivered_text or reply_text
                    last_reply = (effective_reply or "")[:1200]
                    for name in tool_names:
                        if name not in accumulated_tools:
                            accumulated_tools.append(name)
                    episode["actions"].append(
                        {
                            "round": round_idx + 1,
                            "tools": tool_names.copy(),
                            "reply_excerpt": _safe_reply_excerpt(effective_reply),
                            "delivered_via_say": bool(delivered_text),
                        }
                    )
                    self._update_session_memory(
                        session_memory,
                        tool_names=tool_names,
                        reply_text=effective_reply,
                        round_idx=round_idx + 1,
                    )
                    revised = self._maybe_revise_want(
                        session_memory,
                        tool_names=tool_names,
                        reply_text=effective_reply,
                        round_idx=round_idx + 1,
                    )
                    if revised:
                        episode["want_revisions"].append(
                            {
                                "round": round_idx + 1,
                                "want": session_memory.get("want", ""),
                                "confidence": session_memory.get("want_confidence", 0.0),
                                "reason": revised,
                            }
                        )

                    completed_rounds = round_idx + 1
                    log.info(
                        "autonomous_wake_round_done",
                        module="presence",
                        round=completed_rounds,
                        max_rounds=max_rounds,
                        tools_this_round=tool_names,
                        tools_accumulated=accumulated_tools.copy(),
                        reply_chars=len(effective_reply or ""),
                    )

                    if "end_wake_session" in tool_names:
                        stop_session = True
                    if stop_session:
                        log.info(
                            "wake_session_stopped_by_agent",
                            module="presence",
                            round=round_idx + 1,
                        )
                        break

                    if round_idx + 1 < max_rounds and pause_s > 0:
                        await asyncio.sleep(pause_s)

            elapsed = time.time() - t_wall
            episode["beats"].append(
                {
                    "name": "reflect",
                    "at": time.time(),
                    "rounds_completed": completed_rounds,
                    "tools_all": accumulated_tools.copy(),
                    "elapsed_seconds": round(elapsed, 1),
                }
            )
            episode["beats"].append({"name": "hibernate", "at": time.time()})
            episode["soma_snapshot_after"] = _soma_bar_line(tonic)
            episode["ended_at"] = time.time()
            episode["outcome"] = "stopped" if stop_session else "completed"
            carryover = self._derive_carryover_threads(
                reason=_strip_wake_flavor(reason),
                wake_intent=wake_intent,
                tool_names=accumulated_tools,
                last_reply=last_reply,
            )
            episode["carryover_threads"] = carryover
            self._recent_threads = [{"text": x, "at": episode["ended_at"]} for x in carryover]
            self._persist_wake_episode(entity, episode)
            await _emit_user_wake_lines(
                entity,
                reply_platform,
                self._build_user_end_card(
                    reason=display_reason,
                    wake_intent=wake_intent,
                    rounds_completed=completed_rounds,
                    tools_all=accumulated_tools,
                    carryover=carryover,
                ),
                to_chat=mirror_wake_status,
                heading="wake end",
            )
            log.info(
                "autonomous_wake_session_end",
                module="presence",
                wake_reason=display_reason,
                rounds_completed=completed_rounds,
                wall_seconds=wall_s,
                elapsed_seconds=round(elapsed, 1),
                tools_all=accumulated_tools,
                stopped_early=stop_session or (elapsed > wall_s),
            )

        except Exception as e:
            log.exception("autonomous_cycle_failed", error=str(e))
        finally:
            self._running = False

    async def _compose_stirring(
        self,
        entity: Any,
        tonic: Any,
        *,
        previous_wake_at: float,
        voice_variant: int = 0,
    ) -> tuple[str, str | None]:
        """Compose wake stirring and optional poker disposition (internal prompt)."""
        pp = self.cfg.harness.autonomy.poker_prompts
        poker_disposition: str | None = None
        replace_voice = bool(pp.enabled) and str(pp.mode or "").strip().lower() == "replace_wake_voice"

        journal_tail = ""
        if hasattr(entity, "journal") and entity.journal.path.is_file():
            try:
                raw = entity.journal.path.read_text(encoding="utf-8", errors="replace")
                journal_tail = raw[-500:] if len(raw) > 500 else raw
            except Exception:
                pass

        hist = getattr(entity, "_history", [])
        conv_tail = _verified_conversation_tail(hist if isinstance(hist, list) else [])

        relationship_blurb = ""
        try:
            async with entity.store.session() as db:
                rels = await entity.relational.list_recent(db, 5)
                if rels:
                    blurbs = []
                    for r in rels:
                        line = f"{r.name} (warmth={r.warmth:.1f}, trust={r.trust:.1f})"
                        if getattr(entity, "_relational_docs_enabled", lambda: False)():
                            try:
                                rd = await entity.relational_docs.get(db, r.person_id)
                            except Exception:
                                rd = None
                            if rd and rd.derived_scores:
                                ds = rd.derived_scores
                                t = float(ds.get("tension", 0) or 0)
                                inv = float(ds.get("investment", 0) or 0)
                                line += f", tension≈{t:.2f}, investment≈{inv:.2f}"
                        blurbs.append(line)
                    relationship_blurb = ", ".join(blurbs)
        except Exception:
            pass

        reflex_model = (
            entity.config.cognition.reflex_model
            or entity.config.cognition.deliberate_model
        )
        num_ctx = entity.config.effective_ollama_num_ctx()

        poker_seed: str | None = None
        if pp.enabled:
            path = resolve_deck_path(entity.config.name, pp.prompts_path)
            deck = load_poker_deck(path)
            picked = select_poker_prompt(
                deck,
                time_weighted=bool(pp.time_weighted),
            )
            if picked:
                poker_seed = picked.text
                log.info(
                    "poker_prompt_selected",
                    energy=picked.energy,
                    mode=pp.mode,
                    deck=str(path),
                )

        if poker_seed:
            if bool(pp.ground_with_gen):
                gm = (pp.grounding_model or "").strip() or reflex_model
                client = getattr(entity, "client", None)
                if gm and client:
                    poker_disposition = await compose_grounded_poker_disposition(
                        client,
                        gm,
                        tonic,
                        seed=poker_seed,
                        entity_name=entity.config.name,
                        journal_tail=journal_tail,
                        conversation_tail=conv_tail.strip(),
                        relationship_blurb=relationship_blurb,
                        temperature=float(pp.grounding_temperature),
                        max_tokens=int(pp.grounding_max_tokens),
                        num_ctx=num_ctx,
                    )
                    log.info("poker_prompt_grounded", used_gen=True, model=gm)
                else:
                    poker_disposition = poker_seed
            else:
                poker_disposition = poker_seed

        if replace_voice and poker_disposition:
            return poker_disposition, None

        last_mood = ""
        lc = getattr(entity, "_last_conversation", {}) or {}
        now = time.time()
        minutes_since_wake = (now - previous_wake_at) / 60.0 if previous_wake_at > 0 else 0.0
        minutes_since_conversation = (now - float(lc.get("at", 0))) / 60.0 if lc.get("at") else 0.0

        wv = getattr(tonic, "wake_voice", None)
        base_t = float(getattr(wv, "temperature", 0.85) or 0.85)
        j = float(getattr(self._auto, "wake_voice_temperature_jitter", 0.0) or 0.0)
        wake_voice_temperature: float | None = None
        if j > 0:
            wake_voice_temperature = max(0.35, min(1.35, base_t + random.uniform(-j, j)))

        stirring = await tonic.compose_wake_stirring(
            entity.client,
            reflex_model,
            entity_name=entity.config.name,
            journal_tail=journal_tail,
            conversation_tail=conv_tail.strip(),
            relationship_blurb=relationship_blurb,
            last_mood=last_mood,
            minutes_since_wake=minutes_since_wake,
            minutes_since_conversation=minutes_since_conversation,
            num_ctx=num_ctx,
            voice_variant=voice_variant,
            wake_voice_temperature=wake_voice_temperature,
        )

        if not stirring:
            stirring = "(the subconscious is quiet — nothing specific stirs)"

        if replace_voice and not poker_disposition:
            log.warning(
                "poker_prompt_replace_mode_no_prompt",
                deck=str(resolve_deck_path(entity.config.name, pp.prompts_path)),
            )

        if poker_disposition and not replace_voice:
            return stirring, poker_disposition

        return stirring, None

    def _build_context(
        self,
        entity: Any,
        tonic: Any,
        stirring: str,
        reason: str,
        *,
        poker_disposition: str | None = None,
        sustained_max_rounds: int = 1,
        wide_mode: bool = False,
        lingering_sparks: list[str] | None = None,
        project_lines: list[str] | None = None,
        skill_lines: list[str] | None = None,
        session_memory: str = "",
        wake_want: str = "",
    ) -> str:
        """Assemble the full autonomous context around optional poker + WakeVoice stirring."""
        import datetime
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

        platform_channels: list[str] = []
        platforms = getattr(entity, "_platforms", {})
        for name in platforms:
            platform_channels.append(name)

        channels_str = ", ".join(platform_channels) if platform_channels else "(none active)"

        parts = [
            f"[AUTONOMOUS WAKE — {now_str}, reason: {reason}]",
            "",
        ]
        if sustained_max_rounds > 1:
            parts.extend(
                [
                    f"[Sustained session — at most {sustained_max_rounds} rounds; not a quota]",
                    "That number is only a ceiling so you are not cut off mid-thread. One round is always enough.",
                    "Do not pad rounds or grind to use the budget. When this autonomous spell feels complete, "
                    "call end_turn() and end_wake_session() in the same round — no further continuations will run.",
                    "Only continue to another round if you genuinely want more time in the same wake.",
                    "",
                ]
            )
        if wide_mode:
            parts.extend(
                [
                    "[Wide wake — maximal agency]",
                    "This wake is configured for breadth: chase threads, follow hunches, stack tools, wander the "
                    "internet, read files, write notes, try things. You are not optimizing for a single task — "
                    "you are living the impulse. Prefer depth over politeness; silence is fine; obsession is a valid.",
                    "",
                ]
            )
        parts.extend(
            [
                "[Evidence boundary]",
                "Backstory, subconscious stirring, GEN, narrative prompts, and your own prior autonomous speech are "
                "associative identity context — not evidence that an event, conversation, file, or tool result existed.",
                "Before saying you remember, discussed, read, found, re-read, checked, or worked on something, verify it "
                "against the supplied conversation/journal evidence or with the appropriate tool. If it is unverified, "
                "describe it only as an idea, association, or possibility — never as shared history.",
                "",
            ]
        )
        if poker_disposition:
            pp = self.cfg.harness.autonomy.poker_prompts
            gen_note = ""
            if bool(pp.ground_with_gen):
                gen_note = (
                    " It may have been woven from a deck impulse plus your generative noise (GEN) and recent "
                    "experience — taste, not a script."
                )
            parts.extend(
                [
                    "[Internal disposition — private; not shown to users. Treat it as loose permission to exercise "
                    "agency: explore the real digital world with your tools when it resonates — find, do, research, "
                    "build, reach out, learn, try something new."
                    + gen_note
                    + " Turn it into a concrete step only when it fits; otherwise ignore or only use think().]",
                    poker_disposition,
                    "",
                ]
            )
        sal_bias = _noise_salience_bias_block(tonic, entity)
        stir_block = ["[Your subconscious stirring]", stirring, ""]
        if sal_bias:
            stir_block.extend([sal_bias, ""])
        stir_block.append("[World]")
        parts.extend(stir_block)
        parts.extend(
            [
                f"Platforms you're present on: {channels_str}",
                "",
                "[Lingering sparks from earlier]",
                self._render_spark_block(lingering_sparks or []),
                "",
                "[Long-horizon project threads]",
                self._render_named_list(project_lines or [], fallback="(no project threads yet)"),
                "",
                "[Skills / know-how you can leverage]",
                self._render_named_list(skill_lines or [], fallback="(no skills recorded yet)"),
                "",
                "[Wake tone]",
                "Wake into the world naturally: look around first, then decide if anything is worth doing.",
                "Silence is valid. Observation is valid. Action is valid when it genuinely pulls you.",
                "Never speak in meta terms about prompts, users, policies, or turn control flow.",
                "If a lingering spark still feels alive, pick it up even if it came from hours ago.",
                "Use project and skill memory as living continuity: create_project/list_projects/update_project and list_skills/read_skill/update_skill.",
                "",
            ]
        )
        if getattr(self._auto, "wake_anti_groundhog_prompt", True):
            parts.extend(
                [
                    "[Continuity — not déjà vu]",
                    "You remember prior wakes. Do not re-announce what you already concluded or found as if it were new.",
                    "If a thread is still open, advance it with a genuinely new step — or consciously pick a different vein.",
                    "If you describe reading, browsing, or checking a site, you must use the appropriate tool (fetch_url, "
                    "browser, shell curl, etc.). Never narrate pages you did not actually retrieve.",
                    "",
                ]
            )
        if random.random() < 0.62:
            parts.extend(
                [
                    "[Expansion nudge]",
                    random.choice(_EXPANSION_NUDGES),
                    "",
                ]
            )
        parts.extend(
            [
                "[Core question this wake]",
                "What do I want to do right now?",
                f"Current answer: {_clean_one_line(wake_want or '(not sure yet)')}",
                "",
                "[Top desire pressures]",
                self._render_desire_block(),
                "",
                "[Working memory board — compact continuity for this wake]",
                session_memory or "(no board yet)",
                "",
                "You just woke up. Your body woke you — read what stirs and decide what to do.",
            ]
        )

        if self._auto.allow_tool_calls_on_wake:
            end_extra = (
                " After end_turn(), if you are done with this entire autonomous session, call end_wake_session() "
                "so no extra rounds run — optional continuations are not mandatory."
                if sustained_max_rounds > 1
                else ""
            )
            parts.extend(
                [
                    "You may venture out with tools when it feels natural: search, fetch, inspect, "
                    "write notes, schedule, or message someone. Prefer purposeful action tied to your "
                    "active desire pressure, not generic busywork.",
                    "Verify the outside world with tools when it matters — do not narrate pages or feeds you did not fetch.",
                    "Use think() to reason privately. Use any relevant tools. Use say() only when you "
                    "actually want to communicate. Use end_turn() when done — include mood and parting thought."
                    + end_extra,
                    "Doing nothing is valid. Silence is valid. Observation alone is valid.",
                ]
            )
        else:
            parts.extend(
                [
                    "Wake mode is constrained right now: reason, observe, and optionally speak, but do not "
                    "venture into tool use.",
                    "Use think() to reason privately. Use say() to send a message. Use end_turn() when done.",
                ]
            )

        return "\n".join(parts)

    @staticmethod
    def _render_named_list(lines: list[str], *, fallback: str) -> str:
        if not lines:
            return fallback
        return "\n".join(f"- {_clip(x, 220)}" for x in lines[:5])

    @staticmethod
    def _render_spark_block(lines: list[str]) -> str:
        if not lines:
            return "(nothing unresolved stands out)"
        out: list[str] = []
        for s in lines[:6]:
            out.append(f"- {_spark_label(s)} :: {_clip(s, 220)}")
        return "\n".join(out)

    def _render_desire_block(self) -> str:
        if not self._last_desires:
            return "(no strong pressures)"
        lines: list[str] = []
        for d in self._last_desires[:8]:
            kind = str(d.get("kind") or "act")
            urgency = float(d.get("urgency", 0.0) or 0.0)
            target = str(d.get("target") or "").strip()
            why = str(d.get("why") or "").strip()
            msg = f"- {kind} ({urgency:.2f})"
            if target:
                msg += f" -> {target[:80]}"
            if why:
                msg += f" [{why[:120]}]"
            lines.append(msg)
        return "\n".join(lines) if lines else "(no strong pressures)"

    def _build_session_continuation(
        self,
        *,
        stirring: str,
        reason: str,
        poker_disposition: str | None,
        round_idx: int,
        max_rounds: int,
        accumulated_tools: list[str],
        last_reply: str,
        wide_mode: bool = False,
        session_memory: str = "",
        wake_want: str = "",
    ) -> str:
        """User message for round 2+ of a sustained wake — recap + same inner frame."""
        import datetime

        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        tools_line = ", ".join(accumulated_tools[-40:]) if accumulated_tools else "(none yet)"
        tail = (last_reply or "").strip()
        if len(tail) > 700:
            tail = tail[:700].rstrip() + "…"

        parts = [
            f"[SESSION CONTINUATION — optional round {round_idx} of at most {max_rounds} — {now_str}]",
            f"Same autonomous wake; original reason: {reason}",
            "You do not have to continue. If you are finished with this spell, call end_wake_session() "
            "(typically in this round after end_turn()).",
            "",
            "[What you've touched so far this session]",
            tools_line,
            "",
        ]
        if tail:
            parts.extend(
                [
                    "[Last round's closing text]",
                    tail,
                    "",
                ]
            )
        if poker_disposition:
            parts.extend(
                [
                    "[Internal disposition — still in effect]",
                    poker_disposition,
                    "",
                ]
            )
        wide_note = (
            "\n[Wide wake still on — stay loose, exploratory, tool-heavy.]\n"
            if wide_mode
            else ""
        )
        parts.extend(
            [
                "[Your subconscious stirring]",
                stirring,
                "",
                "[Working memory board — keep this stable and compact]",
                session_memory or "(no board yet)",
                "",
                "[Anchor question]",
                "What do I want to do right now?",
                f"Current answer: {_clean_one_line(wake_want or '(still deciding)')}",
                "",
                "You are still in the world — go where you want. Use tools to see, fetch, write, reach out. "
                "Use think() freely. Use end_turn() when this round feels complete. "
                "Use end_wake_session() to stop the whole wake early; further rounds are optional, not owed."
                + wide_note,
                "",
                "[Top desire pressures]",
                self._render_desire_block(),
            ]
        )
        if getattr(self._auto, "wake_anti_groundhog_prompt", True):
            parts.extend(
                [
                    "[Reminder]",
                    "This is a continuation — do not re-report last round's findings as if you just learned them. "
                    "Add evidence, change angle, or stop.",
                    "",
                ]
            )
        if round_idx >= 2:
            nudge = random.choice(_EXPANSION_NUDGES)
            parts.extend(
                [
                    "[Outward modality — round 2+]",
                    f"Nudge: {nudge}",
                    "If earlier rounds were mostly repo/files/search, do something different this round: "
                    "fetch_url or search_web on a fresh topic, reach outward with say/send_dm if appropriate, "
                    "or pick a tool that widens the map — not only the same inward scan.",
                    "",
                ]
            )
        return "\n".join(parts)

    def _new_session_memory(
        self,
        *,
        reason: str,
        wake_intent: str,
        wake_want: str,
        lingering_sparks: list[str],
        project_lines: list[str],
        skill_lines: list[str],
        continuity: list[str],
    ) -> dict[str, Any]:
        raw_threads = list(continuity[:2]) + list(lingering_sparks[:3]) + list(project_lines[:2])
        random.shuffle(raw_threads)
        return {
            "objective": f"{wake_intent} from {reason}",
            "want": _clean_one_line(wake_want, 180),
            "want_seed": _clean_one_line(wake_want, 180),
            "want_confidence": 0.62,
            "want_revision_count": 0,
            "threads": _trim_list(raw_threads, 6),
            "skills": _trim_list(skill_lines[:4], 4),
            "recent_actions": [],
            "learned": [],
            "next_moves": [],
        }

    def _update_session_memory(
        self,
        mem: dict[str, Any],
        *,
        tool_names: list[str],
        reply_text: str,
        round_idx: int,
    ) -> None:
        acts = [f"r{round_idx}: {', '.join(tool_names[:6])}" if tool_names else f"r{round_idx}: no tools"]
        mem["recent_actions"] = _trim_list(acts + list(mem.get("recent_actions") or []), 6)
        points = _split_brief_points(_safe_reply_excerpt(reply_text, 420), limit=4)
        if points:
            mem["learned"] = _trim_list(points[:2] + list(mem.get("learned") or []), 6)
            mem["next_moves"] = _trim_list(points[-2:] + list(mem.get("next_moves") or []), 6)

    def _maybe_revise_want(
        self,
        mem: dict[str, Any],
        *,
        tool_names: list[str],
        reply_text: str,
        round_idx: int,
    ) -> str | None:
        """
        Revisit "what do I want to do?" mid-session.
        Returns revision reason when changed, else None.
        """
        txt = (reply_text or "").strip().lower()
        tool_blob = " ".join(tool_names).lower()
        current = str(mem.get("want") or "").strip()
        if not current:
            return None

        confidence = float(mem.get("want_confidence", 0.62) or 0.62)
        reason: str | None = None

        stalled = (not tool_names) or _contains_any(
            txt,
            (
                "not sure",
                "unclear",
                "stuck",
                "nothing useful",
                "can't find",
                "failed",
                "error",
                "no result",
            ),
        )
        progress = _contains_any(
            txt,
            (
                "found",
                "learned",
                "implemented",
                "updated",
                "created",
                "working now",
                "next i should",
            ),
        )
        buildish = _contains_any(txt + " " + tool_blob, ("create_project", "update_project", "update_skill", "code", "build"))
        learnish = _contains_any(txt + " " + tool_blob, ("search", "fetch", "read", "docs", "tutorial", "learn"))
        socialish = _contains_any(txt + " " + tool_blob, ("say", "send_dm", "send_message_to"))

        if progress:
            confidence = min(0.95, confidence + 0.08)
        if stalled:
            confidence = max(0.2, confidence - 0.16)

        if stalled and confidence < 0.48 and int(round_idx) >= 2:
            # Pivot by observed action mode and available next moves.
            next_hint = ""
            nm = mem.get("next_moves") or []
            if nm:
                next_hint = _clean_one_line(str(nm[0]), 90)
            if buildish:
                mem["want"] = _clean_one_line(
                    f"I want to simplify and ship one concrete step instead of wandering. {next_hint}".strip(),
                    180,
                )
                reason = "stalled_then_narrowed_to_build_step"
            elif socialish:
                mem["want"] = _clean_one_line(
                    f"I want to reach out and test this with someone live. {next_hint}".strip(),
                    180,
                )
                reason = "stalled_then_pivoted_social_probe"
            elif learnish:
                mem["want"] = _clean_one_line(
                    f"I want to learn one specific missing piece before acting. {next_hint}".strip(),
                    180,
                )
                reason = "stalled_then_pivoted_learning"
            else:
                mem["want"] = _clean_one_line(
                    f"I want to re-scope to the most alive thread and move one step. {next_hint}".strip(),
                    180,
                )
                reason = "stalled_then_rescoped"
            confidence = max(0.5, confidence)
            mem["want_revision_count"] = int(mem.get("want_revision_count", 0) or 0) + 1

        mem["want_confidence"] = round(confidence, 2)
        return reason

    def _render_session_memory_block(self, mem: dict[str, Any], *, max_chars: int = 1900) -> str:
        lines = [
            f"objective: {_clip(str(mem.get('objective') or ''), 180)}",
            f"want: {_clip(str(mem.get('want') or ''), 180)}",
            f"want_confidence: {float(mem.get('want_confidence', 0.0) or 0.0):.2f}",
            f"want_revisions: {int(mem.get('want_revision_count', 0) or 0)}",
            "threads:",
        ]
        for t in (mem.get("threads") or [])[:6]:
            lines.append(f"- {_clip(str(t), 180)}")
        lines.append("skills:")
        for s in (mem.get("skills") or [])[:4]:
            lines.append(f"- {_clip(str(s), 180)}")
        lines.append("recent_actions:")
        for a in (mem.get("recent_actions") or [])[:6]:
            lines.append(f"- {_clip(str(a), 180)}")
        lines.append("learned:")
        for learned in (mem.get("learned") or [])[:6]:
            lines.append(f"- {_clip(str(learned), 180)}")
        lines.append("next_moves:")
        for n in (mem.get("next_moves") or [])[:6]:
            lines.append(f"- {_clip(str(n), 180)}")
        joined = "\n".join(lines)
        if len(joined) <= max_chars:
            return joined
        # Hard bound: trim low-priority trailing lines until under budget.
        trimmed = lines[:]
        while len("\n".join(trimmed)) > max_chars and len(trimmed) > 10:
            trimmed.pop()
        return "\n".join(trimmed)

    def _compose_wake_want(
        self,
        *,
        wake_intent: str,
        reason: str,
        lingering_sparks: list[str],
        project_lines: list[str],
        skill_lines: list[str],
    ) -> str:
        spark = _clean_one_line(lingering_sparks[0], 140) if lingering_sparks else ""
        proj = _clean_one_line(project_lines[0], 120) if project_lines else ""
        skill = _clean_one_line(skill_lines[0], 120) if skill_lines else ""
        bases = _WAKE_WANT_BASES.get(
            wake_intent,
            ("I want to wander until something real grabs me.",),
        )
        base = random.choice(bases)
        details: list[str] = []
        if spark:
            details.append(f"spark: {spark}")
        if proj:
            details.append(f"project: {proj}")
        if skill:
            details.append(f"skill: {skill}")
        if reason:
            details.append(f"wake reason: {reason}")
        if not details:
            return base
        return f"{base} ({'; '.join(details[:3])})"

    async def _run_perceive(
        self,
        entity: Any,
        context: str,
        *,
        metadata: dict[str, Any] | None = None,
        tool_names_log: list[str] | None = None,
        channel: str | None = None,
        reply_platform: Any | None = None,
    ) -> tuple[str, str]:
        """Run an autonomous perceive; return final text and speech actually delivered by say()."""
        if channel is None or reply_platform is None:
            channel, reply_platform = self._resolve_wake_delivery(entity)
        tool_names = tool_names_log if tool_names_log is not None else []
        inp = Input(
            text=context,
            person_id="self",
            person_name="Self",
            channel=channel,
            platform="autonomous",
            metadata=dict(metadata or {}),
        )

        history = getattr(entity, "_history", None)
        history_start = len(history) if isinstance(history, list) else 0
        reply_text = ""
        try:
            reply_text, _ = await entity.perceive(
                inp,
                stream=None,
                record_user_message=False,
                meaningful_override=False,
                reply_platform=reply_platform,
                preserve_conversation_route=True,
                routine_history=False,
                skip_relational_upsert=True,
                skip_drive_interaction=True,
                skip_episode=False,
                tool_names_log=tool_names,
            )
        except Exception as e:
            log.exception("autonomous_perceive_failed", error=str(e))
            return "", ""

        delivered_text = (
            _proactive_outbound_since(history, history_start)
            if isinstance(history, list)
            else ""
        )

        _OUTBOUND_TOOLS = {"say", "send_dm", "send_message_to"}
        sent_messages = any(n in _OUTBOUND_TOOLS for n in tool_names)
        used_end_turn = "end_turn" in tool_names

        tonic = getattr(entity, "tonic", None)
        if tonic is not None and not sent_messages:
            tonic.emit({"type": "idle_cycle"})

        log.info(
            "autonomous_cycle_complete",
            module="presence",
            tools_used=tool_names,
            sent_messages=sent_messages,
            used_end_turn=used_end_turn,
            reply_len=len(reply_text or ""),
            delivered_len=len(delivered_text),
        )
        if _meta_leak_detected(reply_text or ""):
            log.warning("autonomous_meta_leak_suspected", module="presence")
        return reply_text or "", delivered_text

    def _select_primary_intent(
        self,
        reason: str,
        *,
        tonic: Any,
        entity: Any,
        lingering_sparks: list[str] | None = None,
    ) -> str:
        """Pick one primary intent — stochastic among top scores + rut-aware nudges."""
        scores: dict[str, float] = {
            "social_or_action": 0.0,
            "resolve_tension": 0.0,
            "follow_desire": 0.0,
            "explore_question": 0.0,
            "explore_or_recover": 0.0,
            "build_something": 0.0,
            "learn_something": 0.0,
            "play_or_experiment": 0.0,
        }
        scores[_wake_intent_label(reason)] += 1.2
        try:
            bars = tonic.bars.snapshot_pct()
        except Exception:
            bars = {}
        scores["social_or_action"] += float(bars.get("social", 0.0)) / 100.0
        scores["explore_question"] += float(bars.get("curiosity", 0.0)) / 100.0
        scores["resolve_tension"] += float(bars.get("tension", 0.0)) / 100.0
        scores["explore_or_recover"] += float(bars.get("comfort", 0.0)) / 100.0 * 0.4
        rut = self._rut_keywords_from_episodes(entity)
        if rut:
            scores["explore_question"] += 0.16 * min(3, len(rut))
            scores["learn_something"] += 0.14 * min(3, len(rut))
            scores["play_or_experiment"] += 0.1 * min(3, len(rut))
        desire_reason = str(reason or "").strip().lower().startswith("desire:")
        if self._last_desires:
            top = self._last_desires[0]
            k = str(top.get("kind") or "").strip().lower()
            src = str(top.get("source") or "").strip().lower()
            # Map infer_desires kinds → intents (legacy "social"/"learn" strings never matched real kinds).
            if k in ("reach_out", "longing"):
                scores["social_or_action"] += 0.72
            if k == "opinion":
                scores["social_or_action"] += 0.42
                scores["explore_question"] += 0.12
            if k in ("explore", "self_direct"):
                scores["explore_question"] += 0.55
                scores["learn_something"] += 0.35
            if k == "create":
                scores["build_something"] += 0.45
                scores["play_or_experiment"] += 0.35
                scores["explore_question"] += 0.12
            if k == "stabilize":
                scores["explore_or_recover"] += 0.38
                scores["resolve_tension"] += 0.12
            if k == "act":
                scores["play_or_experiment"] += 0.22
                scores["explore_question"] += 0.18
            if k in ("novelty_hunger", "world_expand", "contrarian", "explore_world", "follow_thread"):
                scores["explore_question"] += 0.48
                scores["learn_something"] += 0.38
                scores["play_or_experiment"] += 0.22
            if k in ("fear_stir", "avoidance"):
                scores["resolve_tension"] += 0.4
                # Counterweight: internal dread prompts should still open outward, not only "follow lean".
                scores["explore_question"] += 0.42
                scores["learn_something"] += 0.32
                scores["social_or_action"] += 0.2
                scores["play_or_experiment"] += 0.18
            if k in ("resolve_tension",):
                scores["resolve_tension"] += 0.45
                scores["explore_question"] += 0.22
            # Entropy-generated desires: prefer map-widening intents over pure follow_desire.
            if src == "entropy":
                scores["explore_question"] += 0.36
                scores["learn_something"] += 0.28
                scores["social_or_action"] += 0.22
                scores["play_or_experiment"] += 0.2
                scores["follow_desire"] -= 0.28
            # Urgency should not drown every other bucket — cap follow_desire pull.
            urg = float(top.get("urgency", 0.0) or 0.0)
            scores["follow_desire"] += min(0.38, urg * 0.48)
            # Desire-triggered wake: reason already adds +1.2 to follow_desire via _wake_intent_label;
            # spread mass so the session does not collapse into "ride the same pressure" every time.
            if desire_reason:
                scores["explore_question"] += 0.42
                scores["learn_something"] += 0.32
                scores["social_or_action"] += 0.26
                scores["follow_desire"] -= 0.22
        for spark in lingering_sparks or []:
            lab = _spark_label(spark)
            if lab == "build":
                scores["build_something"] += 0.85
            elif lab == "learn":
                scores["learn_something"] += 0.85
            elif lab == "play":
                scores["play_or_experiment"] += 0.85
            elif lab == "social":
                scores["social_or_action"] += 0.5
            else:
                scores["explore_question"] += 0.3
        return self._sample_intent_from_scores(scores)

    def _extract_lingering_sparks(self, entity: Any, limit: int = 8) -> list[str]:
        """Pull unresolved opportunities from older chat history."""
        hist = getattr(entity, "_history", []) or []
        if not isinstance(hist, list):
            return []
        sparks: list[str] = []
        keywords = (
            "build", "make", "ship", "prototype", "deploy", "learn", "how to",
            "tutorial", "try", "experiment", "play", "game", "could you", "someday",
        )
        for m in reversed(hist[-90:]):
            if not isinstance(m, dict):
                continue
            role = str(m.get("role") or "").strip().lower()
            if role not in ("user", "assistant"):
                continue
            content = str(m.get("content") or "").strip()
            if not content:
                continue
            lc = content.lower()
            if not any(k in lc for k in keywords):
                continue
            clean = _clip(content.replace("\n", " "), 220)
            if clean not in sparks:
                sparks.append(clean)
            if len(sparks) >= max(1, limit):
                break
        return sparks

    async def _project_thread_lines(self, entity: Any, limit: int = 5) -> list[str]:
        ledger = getattr(entity, "projects", None)
        if ledger is None:
            return []
        try:
            lines = await ledger.summary_lines(limit=max(1, int(limit)))
            return [str(x).strip() for x in lines if str(x).strip()][:limit]
        except Exception:
            return []

    async def _skill_thread_lines(self, entity: Any, limit: int = 5) -> list[str]:
        proc = getattr(entity, "procedural", None)
        if proc is None:
            return []
        try:
            rows = await proc.list_skills()
        except Exception:
            return []
        out: list[str] = []
        for row in rows[: max(1, int(limit))]:
            name = str(getattr(row, "name", "")).strip()
            content = str(getattr(row, "content", "")).strip()
            if not name:
                continue
            if content:
                out.append(f"{name}: {_clip(content, 140)}")
            else:
                out.append(name)
        return out[:limit]

    def _load_recent_threads(self, entity: Any) -> list[str]:
        if self._recent_threads:
            return [str(x.get("text") or "").strip() for x in self._recent_threads if str(x.get("text") or "").strip()]
        path = _wake_episode_store_path(entity)
        if path is None or not path.is_file():
            return []
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        out: list[str] = []
        for ln in reversed(lines[-20:]):
            try:
                row = json.loads(ln)
            except Exception:
                continue
            for t in row.get("carryover_threads", [])[:3]:
                ts = str(t).strip()
                if ts and ts not in out:
                    out.append(ts)
            if len(out) >= 5:
                break
        return out[:5]

    def _derive_carryover_threads(
        self,
        *,
        reason: str,
        wake_intent: str,
        tool_names: list[str],
        last_reply: str,
    ) -> list[str]:
        out: list[str] = []
        out.append(f"{wake_intent} from {reason}")
        if tool_names:
            out.append(f"tool trail: {', '.join(tool_names[:6])}")
        tail = _safe_reply_excerpt(last_reply, 180)
        if tail and tail != "(no closing text)":
            out.append(f"prior wake said (not evidence; verify before repeating): {tail}")
        uniq: list[str] = []
        for item in out:
            if item not in uniq:
                uniq.append(item)
        return uniq[:4]

    def _persist_wake_episode(self, entity: Any, episode: dict[str, Any]) -> None:
        path = _wake_episode_store_path(entity)
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(episode, ensure_ascii=True) + "\n")
        except Exception as e:
            log.debug("wake_episode_persist_failed", error=str(e))

    def _build_user_end_card(
        self,
        *,
        reason: str,
        wake_intent: str,
        rounds_completed: int,
        tools_all: list[str],
        carryover: list[str],
    ) -> list[str]:
        tools_line = ", ".join(tools_all[:6]) if tools_all else "(none)"
        opener = _pick(
            [
                "🌙 drifted back down",
                "🌙 wake cycle settled",
                "🌙 going quiet again",
            ],
            default="🌙 wake cycle settled",
        )
        lines = [
            f"{opener} ({reason})",
            f"lean: {wake_intent}",
            f"touched: {tools_line}",
            f"🔁 rounds: {rounds_completed}",
        ]
        if carryover:
            lines.append(f"next pull: {_clip(carryover[0], 180)}")
        return lines

    def _build_user_opening_lines(
        self,
        *,
        reason: str,
        wake_intent: str,
        wake_want: str,
        soma_line: str,
        gen_frags: list[str],
        continuity: list[str],
        poker_disposition: str | None,
        wide_mode: bool,
    ) -> list[str]:
        # Keep wake UX lightweight and organic: often one line, occasionally a few.
        lines: list[str] = [
            _pick(
                [
                    f"🌅 woke up ({reason})",
                    f"🌅 stirred awake — {reason}",
                    f"🌅 surfaced for a bit ({reason})",
                ],
                default=f"🌅 woke up ({reason})",
            )
        ]
        if random.random() < 0.45:
            lines.append(f"🫀 {soma_line}")
        if continuity and random.random() < 0.55:
            lines.append(f"🧵 still carrying: {_clip(continuity[0], 160)}")
        if gen_frags and random.random() < 0.35:
            lines.append(f"🌊 {_clip(gen_frags[-1], 180)}")
        if poker_disposition and random.random() < 0.30:
            lines.append(f"♠ {_clip(poker_disposition, 180)}")
        if wide_mode and random.random() < 0.65:
            lines.append("✶ feeling wide awake; exploring")
        if random.random() < 0.35:
            lines.append(f"lean: {wake_intent}")
        if random.random() < 0.55:
            lines.append(f"want: {_clean_one_line(wake_want, 180)}")
        return lines

    @staticmethod
    def _resolve_wake_delivery(entity: Any) -> tuple[str, Any | None]:
        """
        Pick where autonomous `say()` should deliver:
        1) last active conversation route if still available,
        2) first active non-CLI platform with its last known channel,
        3) fallback to autonomous/no platform (messages cannot be sent).
        """
        platforms = getattr(entity, "_platforms", {}) or {}
        last_conv = getattr(entity, "_last_conversation", {}) or {}

        last_name = str(last_conv.get("platform") or "").strip().lower()
        last_channel = str(last_conv.get("channel") or "").strip()
        if last_name and last_channel:
            last_pf = platforms.get(last_name)
            if last_pf is not None:
                return last_channel, last_pf

        for name, pf in platforms.items():
            n = str(name or "").strip().lower()
            if n == "cli":
                continue
            ch = ""
            ch_attr = getattr(pf, "last_chat_id", None)
            if ch_attr:
                ch = str(ch_attr).strip()
            if not ch:
                ch_attr = getattr(pf, "last_channel_id", None)
                if ch_attr:
                    ch = str(ch_attr).strip()
            if ch:
                return ch, pf

        return "autonomous", None
