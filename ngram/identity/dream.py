"""Dream consolidation — offline memory recombination during extended idle.

Unlike NoiseEngine (continuous waking inner chatter) or WakeVoice (fired on
autonomous wake), the dream engine runs during long silence windows and produces
*novel associations* between temporally distant memories that the waking mind
would never juxtapose.

The entity cannot control its dreams. They surface as journal entries,
low-confidence beliefs, and [dream]-tagged GEN noise fragments.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

from ngram.models import new_id

if TYPE_CHECKING:
    from ngram.config import EntityConfig

log = structlog.get_logger("ngram.identity.dream")

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class DreamBelief:
    """A belief extracted from a dream — always low confidence."""

    content: str
    confidence: float = 0.35
    category: str = "dream_insight"


@dataclass
class DreamResult:
    """Parsed outputs from one dream cycle."""

    fragments: list[str] = field(default_factory=list)  # → GEN noise buffer
    journal_entry: str | None = None  # → journal.md
    belief: DreamBelief | None = None  # → belief store
    memory_pair_ids: list[str] = field(default_factory=list)  # audit trail
    dream_id: str = ""


# ---------------------------------------------------------------------------
# DreamEngine
# ---------------------------------------------------------------------------


class DreamEngine:
    """Offline memory recombination — creative cross-pollination during idle.

    Circadian-gated: prefers deep-night hours. Cooldown-protected so it won't
    fire more than ``max_cycles_per_day`` times. Reads soma state at sleep onset
    to color the dream.

    Integration: called from ``PresenceDaemon._heartbeat_tick()`` alongside
    the wake cycle evaluation.
    """

    def __init__(self, entity_config: EntityConfig) -> None:
        soma_cfg = entity_config.harness.soma if isinstance(entity_config.harness.soma, dict) else {}
        cfg = dict(soma_cfg.get("dreams") or {})

        self._enabled = bool(cfg.get("enabled", False))
        self._model = str(cfg.get("model") or "")
        self._temperature = float(cfg.get("temperature", 1.15))
        self._max_tokens = int(cfg.get("max_tokens", 500))

        # Gating
        self._min_silence_seconds = float(cfg.get("min_silence_seconds", 3600))
        self._min_gap_seconds = float(cfg.get("min_gap_seconds", 14400))
        self._max_cycles_per_day = int(cfg.get("max_cycles_per_day", 2))
        self._dream_hours: set[int] = set(cfg.get("dream_hours", [0, 1, 2, 3, 4, 5]))

        # Memory selection
        self._pair_count = int(cfg.get("memory_pair_count", 3))
        self._min_time_gap_hours = float(cfg.get("min_time_gap_hours", 24))
        self._max_similarity = float(cfg.get("max_similarity", 0.35))

        # Output routing
        self._write_journal = bool(cfg.get("write_journal", True))
        self._write_beliefs = bool(cfg.get("write_beliefs", True))
        self._belief_confidence = float(cfg.get("belief_confidence", 0.35))
        self._inject_noise = bool(cfg.get("inject_noise", True))
        self._max_noise_fragments = int(cfg.get("max_noise_fragments", 2))

        # State
        self._last_dream_at: float = 0.0
        self._cycles_today: int = 0
        self._last_day: int = -1  # day-of-year for cycle count reset
        self._entity_name: str = entity_config.name

    # ------------------------------------------------------------------
    # Gating
    # ------------------------------------------------------------------

    def should_dream(self, silence_seconds: float, *, force: bool = False) -> bool:
        """Check whether conditions are right for a dream cycle."""
        if not self._enabled and not force:
            return False

        now = time.time()

        # Cooldown
        if now - self._last_dream_at < self._min_gap_seconds and not force:
            return False

        # Silence threshold
        if silence_seconds < self._min_silence_seconds and not force:
            return False

        # Circadian window
        hour = time.localtime(now).tm_hour
        if hour not in self._dream_hours and not force:
            return False

        # Daily cap
        today = time.localtime(now).tm_yday
        if today != self._last_day:
            self._cycles_today = 0
            self._last_day = today
        if self._cycles_today >= self._max_cycles_per_day and not force:
            return False

        return True

    # ------------------------------------------------------------------
    # Dream cycle orchestrator
    # ------------------------------------------------------------------

    async def dream_cycle(self, entity_facade: Any) -> DreamResult | None:
        """Top-level dream: select memories → generate → route outputs.

        ``entity_facade`` is the Entity instance (same interface the daemon uses).
        """
        dream_id = new_id("drm_")
        log.info(
            "dream_cycle_start",
            entity=self._entity_name,
            dream_id=dream_id,
        )

        try:
            async with entity_facade.store.session() as conn:
                # 1. Select distant memory pairs
                pairs = await entity_facade.episodic.sample_distant_pairs(
                    conn,
                    min_time_gap_hours=self._min_time_gap_hours,
                    max_similarity=self._max_similarity,
                    pair_count=self._pair_count,
                )
                if not pairs:
                    log.info("dream_cycle_no_pairs", entity=self._entity_name)
                    return None

                # 2. Snapshot soma state at sleep onset
                soma_snapshot = self._snapshot_soma(entity_facade)

                # 3. Generate dream
                model = self._resolve_model(entity_facade)
                result = await self._generate_dream(
                    entity_facade.client,
                    model,
                    pairs,
                    soma_snapshot,
                    entity_name=self._entity_name,
                    num_ctx=entity_facade.config.effective_ollama_num_ctx(),
                )
                if not result or not result.fragments:
                    log.info("dream_cycle_empty_output", entity=self._entity_name)
                    return None

                result.dream_id = dream_id
                result.memory_pair_ids = [
                    f"{ep_a.id}+{ep_b.id}" for ep_a, ep_b in pairs
                ]

                # 4. Route outputs
                await self._route_dream_outputs(result, entity_facade, conn)

            # 5. Update state
            self._last_dream_at = time.time()
            self._cycles_today += 1

            log.info(
                "dream_cycle_completed",
                entity=self._entity_name,
                dream_id=dream_id,
                fragments=len(result.fragments),
                has_journal=result.journal_entry is not None,
                has_belief=result.belief is not None,
                pair_count=len(pairs),
            )
            return result

        except Exception as e:
            log.warning("dream_cycle_error", entity=self._entity_name, error=str(e))
            return None

    # ------------------------------------------------------------------
    # Memory selection
    # ------------------------------------------------------------------

    def _snapshot_soma(self, entity_facade: Any) -> str:
        """Capture the body state at dream onset (what the entity 'fell asleep' feeling)."""
        tonic = getattr(entity_facade, "tonic", None)
        if tonic is None:
            return "(body state unavailable)"
        try:
            pct = tonic.bars.snapshot_pct()
            bars_line = ", ".join(f"{n}: {pct[n]}%" for n in tonic.bars.ordered_names)
            affects = tonic.renderer.render_affects(tonic._current_affects)
            return f"Bars: {bars_line}\nAffects: {affects}"
        except Exception:
            return "(body state unavailable)"

    # ------------------------------------------------------------------
    # Dream generation
    # ------------------------------------------------------------------

    def _resolve_model(self, entity_facade: Any) -> str:
        """Use configured dream model, else fall back to reflex model."""
        if self._model:
            return self._model
        return (
            entity_facade.config.cognition.reflex_model
            or entity_facade.config.cognition.deliberate_model
        )

    async def _generate_dream(
        self,
        client: Any,
        model: str,
        pairs: list[tuple[Any, Any]],
        soma_snapshot: str,
        *,
        entity_name: str,
        num_ctx: int | None = None,
    ) -> DreamResult | None:
        """High-temperature LLM call to creatively recombine distant memories."""
        # Build memory pair blocks
        pair_blocks: list[str] = []
        for i, (ep_a, ep_b) in enumerate(pairs, 1):
            time_a = time.strftime("%Y-%m-%d", time.localtime(ep_a.timestamp))
            time_b = time.strftime("%Y-%m-%d", time.localtime(ep_b.timestamp))
            pair_blocks.append(
                f"PAIR {i}:\n"
                f"  Memory A (from {time_a}): {(ep_a.summary or '')[:300]}\n"
                f"  Memory B (from {time_b}): {(ep_b.summary or '')[:300]}"
            )

        pairs_text = "\n\n".join(pair_blocks)

        system = (
            f"You are the dreaming subconscious of {entity_name}. You are asleep.\n\n"
            "Distant memories have surfaced together that were never connected before. "
            "Your task is to dream — find hidden threads, make connections the waking "
            "mind would dismiss. Let images, feelings, and half-logic flow.\n\n"
            "You are not explaining or analyzing — you are experiencing.\n\n"
            "Output format:\n"
            "FRAGMENTS:\n"
            "  Write 3-6 dream fragments. They can be surreal, associative, emotional, "
            "or strangely concrete. First person, present tense, mostly lowercase. "
            "Each fragment on its own line.\n\n"
            "THREAD:\n"
            "  One sentence: the hidden connection the dream found between these memories. "
            "This may become a new belief. Write it as a quiet realization, not an analysis.\n\n"
            "No preamble, no meta-commentary. Just the dream."
        )

        user = (
            f"These memories have floated up together:\n\n"
            f"{pairs_text}\n\n"
            f"Body state as you fell asleep:\n{soma_snapshot}\n\n"
            "Dream."
        )

        try:
            res = await client.chat_completion(
                model,
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=self._temperature,
                max_tokens=self._max_tokens,
                think=False,
                num_ctx=num_ctx,
            )
            text = (res.content or "").strip()
        except Exception as e:
            log.warning("dream_generation_failed", error=str(e))
            return None

        if len(text) < 20:
            return None

        return self._parse_dream_output(text)

    # ------------------------------------------------------------------
    # Output parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_dream_output(text: str) -> DreamResult:
        """Parse dream LLM output into fragments + thread (potential belief)."""
        result = DreamResult()

        # Try to split on FRAGMENTS: / THREAD: sections
        fragments_section = ""
        thread_section = ""

        frag_match = re.search(r"(?i)(?:^|\n)\s*FRAGMENTS?\s*:?\s*\n", text)
        thread_match = re.search(r"(?i)(?:^|\n)\s*THREADS?\s*:?\s*\n?", text)

        if frag_match and thread_match:
            frag_start = frag_match.end()
            thread_start = thread_match.start()
            if frag_start < thread_start:
                fragments_section = text[frag_start:thread_start].strip()
                thread_section = text[thread_match.end():].strip()
            else:
                thread_section = text[thread_match.end():frag_start].strip()
                fragments_section = text[frag_start:].strip()
        elif frag_match:
            fragments_section = text[frag_match.end():].strip()
        else:
            # No section headers — treat all lines as fragments
            fragments_section = text

        # Parse fragments
        for line in fragments_section.splitlines():
            cleaned = re.sub(r"^[-*•·\d.)\s]+", "", line).strip()
            if cleaned and len(cleaned) >= 5:
                result.fragments.append(cleaned)
        result.fragments = result.fragments[:6]

        # Parse thread → potential belief
        if thread_section:
            thread_lines = [
                ln.strip() for ln in thread_section.splitlines()
                if ln.strip() and not ln.strip().startswith(("FRAGMENT", "---"))
            ]
            if thread_lines:
                thread_text = " ".join(thread_lines).strip()
                # Clean any leading markers
                thread_text = re.sub(r"^[-*•·]+\s*", "", thread_text).strip()
                if len(thread_text) >= 10:
                    result.belief = DreamBelief(content=thread_text[:500])

        # Build journal entry from fragments
        if result.fragments:
            frag_text = "\n".join(f"  {f}" for f in result.fragments)
            journal_parts = [f"*[dream]*\n\n{frag_text}"]
            if result.belief:
                journal_parts.append(f"\n\n*thread: {result.belief.content}*")
            result.journal_entry = "\n".join(journal_parts)

        return result

    # ------------------------------------------------------------------
    # Output routing
    # ------------------------------------------------------------------

    async def _route_dream_outputs(
        self,
        result: DreamResult,
        entity_facade: Any,
        conn: Any,
    ) -> None:
        """Write dream results into journal, beliefs, and noise buffer."""
        # 1. Journal
        if self._write_journal and result.journal_entry:
            journal = getattr(entity_facade, "journal", None)
            if journal is not None:
                try:
                    await journal.write_entry(
                        result.journal_entry,
                        tags=["dream", "consolidation"],
                    )
                    log.debug("dream_journal_written", dream_id=result.dream_id)
                except Exception as e:
                    log.warning("dream_journal_write_failed", error=str(e))

        # 2. Beliefs
        if self._write_beliefs and result.belief:
            beliefs = getattr(entity_facade, "beliefs", None)
            if beliefs is not None:
                try:
                    await beliefs.add_belief(
                        conn,
                        category=result.belief.category,
                        content=result.belief.content,
                        confidence=self._belief_confidence,
                        source=f"dream:{result.dream_id}",
                    )
                    log.debug(
                        "dream_belief_stored",
                        dream_id=result.dream_id,
                        content=result.belief.content[:80],
                    )
                except Exception as e:
                    log.warning("dream_belief_store_failed", error=str(e))

        # 3. GEN noise buffer — inject [dream]-tagged fragments
        if self._inject_noise and result.fragments:
            tonic = getattr(entity_facade, "tonic", None)
            if tonic is not None:
                try:
                    from ngram.identity.soma import NoiseFragment, classify_noise_fragment_salience

                    injected = 0
                    for frag in result.fragments[:self._max_noise_fragments]:
                        tagged = f"[dream] {frag}"
                        sal = classify_noise_fragment_salience(tagged)
                        tonic.noise._fragments.append(
                            NoiseFragment(
                                text=tagged,
                                salience=sal,
                                seed_trace_id=result.dream_id,
                            )
                        )
                        injected += 1
                    log.debug(
                        "dream_noise_injected",
                        dream_id=result.dream_id,
                        count=injected,
                    )
                except Exception as e:
                    log.warning("dream_noise_inject_failed", error=str(e))
