"""Always-on scheduling: heartbeat, drives, autonomy, MCP refresh, consolidation."""

from __future__ import annotations

import time

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from ngram.config import EntityConfig
from ngram.memory.consolidation import ConsolidationJob
from ngram.models import Input
from ngram.presence.automations.engine import AutomationEngine
from ngram.presence.initiative import InitiativeEngine
from ngram.presence.wake_cycle import WakeCycleEngine

log = structlog.get_logger("ngram.presence.daemon")


def _build_conversation_tail(history: list[dict], max_messages: int = 8, max_chars: int = 500) -> str:
    """Extract recent conversation for noise/wake context with enough substance to riff on."""
    if not history:
        return "(silence)"
    lines: list[str] = []
    budget = 0
    for m in history[-max_messages:]:
        role = m.get("role", "")
        if role == "system":
            continue
        content = str(m.get("content", ""))
        if not content.strip():
            continue
        truncated = content[:max_chars] + ("..." if len(content) > max_chars else "")
        lines.append(f"{role}: {truncated}")
        budget += len(truncated)
    return "\n".join(lines) if lines else "(silence)"


class PresenceDaemon:
    def __init__(self, entity_facade) -> None:
        self.entity = entity_facade
        self.cfg: EntityConfig = entity_facade.config
        self._scheduler: AsyncIOScheduler | None = None
        self._consolidation = ConsolidationJob(self.cfg)
        self._initiative = InitiativeEngine(self.cfg, entity_facade.client)
        self._wake_cycle = WakeCycleEngine(self.cfg)
        self._last_consolidation: float = time.time()
        self._last_emergence: float = 0.0

    def _daemon_dict(self) -> dict:
        return dict(self.cfg.presence.daemon or {})

    def _heartbeat_seconds(self) -> int:
        d = self._daemon_dict()
        return int(d.get("heartbeat_interval", self.cfg.harness.presence.heartbeat_interval))

    def _consolidation_seconds(self) -> int:
        d = self._daemon_dict()
        return int(d.get("memory_consolidation", self.cfg.harness.memory.consolidation_interval))

    async def start(self) -> None:
        if self._scheduler and self._scheduler.running:
            return
        self._scheduler = AsyncIOScheduler()
        self.entity.automation_engine = AutomationEngine(self.entity, self._scheduler)
        hb = max(5, self._heartbeat_seconds())
        cons = max(30, self._consolidation_seconds())
        self._scheduler.add_job(
            self._heartbeat_tick,
            "interval",
            seconds=hb,
            id="ngram_heartbeat",
            replace_existing=True,
        )
        self._scheduler.add_job(
            self._consolidation_tick,
            "interval",
            seconds=cons,
            id="ngram_consolidation",
            replace_existing=True,
        )
        self._scheduler.start()
        if self.cfg.automations.enabled:
            try:
                await self.entity.automation_engine.startup()
            except Exception as e:
                log.warning("automation_engine_startup_failed", module="presence", error=str(e))
        log.info(
            "daemon_scheduler_started",
            module="presence",
            platform="daemon",
            heartbeat_seconds=hb,
            consolidation_seconds=cons,
        )

    async def stop(self) -> None:
        eng = getattr(self.entity, "automation_engine", None)
        if eng is not None:
            try:
                await eng.shutdown()
            except Exception as e:
                log.warning("automation_engine_shutdown_failed", module="presence", error=str(e))
            self.entity.automation_engine = None
        if self._scheduler:
            try:
                self._scheduler.shutdown(wait=False)
            except Exception as e:
                log.warning("scheduler_shutdown", module="presence", error=str(e))
            self._scheduler = None

    async def _heartbeat_tick(self) -> None:
        structlog.contextvars.bind_contextvars(
            entity_name=self.cfg.name,
            module="presence",
            platform="daemon",
        )
        try:
            await self.entity.tick()
            now = time.time()
            silence = now - getattr(self.entity, "_last_user_message_at", now)
            drives_crossed = self.entity.drives.tick(silence_seconds=silence)

            # --- Legacy initiative (when autonomy is disabled) ---
            if not self.cfg.harness.autonomy.enabled:
                cooldown = float(self.cfg.drives.initiative_cooldown)
                if drives_crossed and self.entity.drives.can_initiate(now, cooldown):
                    d = drives_crossed[0]
                    ctx = await self.entity.proactive_context_for_drive(d)
                    msg = await self._initiative.compose_proactive(d, ctx)
                    if msg:
                        self.entity.drives.register_initiative_time(now)
                        self.entity.drives.satisfy(d.name, 0.5)
                        await self.entity.broadcast_proactive(msg)
                        log.info(
                            "initiative_sent",
                            module="presence",
                            platform="daemon",
                            drive=d.name,
                        )

            try:
                await self.entity.refresh_mcp_servers()
            except Exception as e:
                log.warning("mcp_heartbeat_refresh_failed", module="presence", error=str(e))

            # --- Tonic body: tick bars, emit idle, refresh affects, noise ---
            tonic = getattr(self.entity, "tonic", None)
            if tonic is not None:
                hb_sec = max(5, self._heartbeat_seconds())
                dt_hours = hb_sec / 3600.0
                if silence > 60:
                    tonic.emit({"type": "idle", "duration_minutes": silence / 60.0})
                await tonic.tick_bars(dt_hours)
                bars_pct = tonic.bars.snapshot_pct()
                log.info(
                    "soma_tick",
                    module="presence",
                    platform="daemon",
                    bars={k: v for k, v in bars_pct.items()},
                    affects=len(tonic._current_affects),
                    noise_fragments=len(tonic.noise.current_fragments()),
                )
                reflex_model = (
                    self.entity.config.cognition.reflex_model
                    or self.entity.config.cognition.deliberate_model
                )
                try:
                    await tonic.maybe_tick_affects(
                        self.entity.client,
                        reflex_model,
                        num_ctx=self.entity.config.effective_ollama_num_ctx(),
                    )
                except Exception as e:
                    log.debug("soma_affect_tick_failed", module="presence", error=str(e))
                try:
                    await self.entity.maybe_tick_noise_heartbeat()
                except Exception as e:
                    log.debug("soma_noise_tick_failed", module="presence", error=str(e))

                # --- Experience distillation (time-based trigger) ---
                try:
                    await self.entity.maybe_distill()
                except Exception as e:
                    log.debug("distillation_heartbeat_failed", module="presence", error=str(e))

                # --- Autonomous wake cycle evaluation ---
                if self.cfg.harness.autonomy.enabled:
                    all_drives = self.entity.drives.all_drives()
                    dormant = getattr(self.entity, "dormant", False)
                    reason = self._wake_cycle.should_wake(
                        tonic=tonic,
                        drives=all_drives,
                        silence_seconds=silence,
                        dormant=dormant,
                    )
                    if reason:
                        await self._wake_cycle.run(
                            entity=self.entity,
                            tonic=tonic,
                            reason=reason,
                        )

                    # --- Dream cycle evaluation (long idle, circadian-gated) ---
                    dream_engine = getattr(self.entity, "dream_engine", None)
                    if dream_engine is not None and dream_engine.should_dream(silence):
                        try:
                            await dream_engine.dream_cycle(self.entity)
                        except Exception as e:
                            log.warning(
                                "dream_cycle_failed",
                                module="presence",
                                error=str(e),
                            )

        except Exception as e:
            log.exception("daemon_heartbeat_error", module="presence", error=str(e))
        finally:
            try:
                await self.entity.sync_live_container(
                    "heartbeat_completed",
                    {"completed_at": time.time()},
                )
            except Exception as e:
                log.exception("heartbeat_checkpoint_failed", module="presence", error=str(e))

    async def _consolidation_tick(self) -> None:
        structlog.contextvars.bind_contextvars(
            entity_name=self.cfg.name,
            module="presence",
            platform="daemon",
        )
        try:
            await self._consolidation.run_for_daemon(self.entity)
            self._last_consolidation = time.time()
            await self._maybe_automation_emergence()
        except Exception as e:
            log.exception("daemon_consolidation_error", module="presence", error=str(e))
        finally:
            try:
                await self.entity.sync_live_container(
                    "consolidation_completed",
                    {"completed_at": time.time()},
                )
            except Exception as e:
                log.exception("consolidation_checkpoint_failed", module="presence", error=str(e))

    async def _maybe_automation_emergence(self) -> None:
        cfg = self.cfg.automations
        if not cfg.enabled or not cfg.emergence.enabled:
            return
        eng = getattr(self.entity, "automation_engine", None)
        if eng is None:
            return
        now = time.time()
        if now - self._last_emergence < float(cfg.emergence.analysis_interval):
            return
        self._last_emergence = now
        try:
            sug = await eng.suggest_automations()
        except Exception as e:
            log.warning("automation_emergence_failed", module="presence", error=str(e))
            return
        if not sug:
            return
        lines = "\n".join(f"{i + 1}. {s['title']} — {s['why']}" for i, s in enumerate(sug))
        body = (
            "Based on your recent experiences and current drives, here are some routines "
            "you might want to create:\n\n"
            f"{lines}\n\n"
            "Do you want to create any of these? Call create_automation for each one you want, "
            "or ignore this if none feel right."
        )
        inp = Input(
            text=body,
            person_id="harness_emergence",
            person_name="Harness",
            channel="emergence",
            platform="automation",
            metadata={"emergence": True},
        )
        try:
            await self.entity.perceive(
                inp,
                stream=None,
                record_user_message=False,
                meaningful_override=False,
                reply_platform=None,
                preserve_conversation_route=True,
                routine_history=False,
                skip_relational_upsert=True,
                skip_drive_interaction=True,
                skip_episode=True,
            )
        except Exception as e:
            log.warning("automation_emergence_perceive_failed", module="presence", error=str(e))
