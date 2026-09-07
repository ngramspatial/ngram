"""Orchestrates scheduled routines as full deliberate perception cycles."""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from typing import TYPE_CHECKING, Any

import structlog
from apscheduler.job import Job
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from ngram.memory import automations_repo
from ngram.models import Input
from ngram.presence.automations.models import Automation, AutomationOrigin
from ngram.presence.automations.schedule import ScheduleParseError, parse_schedule

if TYPE_CHECKING:
    from ngram.entity import Entity

log = structlog.get_logger("ngram.presence.automations.engine")


def _emotion_snapshot(state: Any) -> str:
    try:
        p = state.primary.value if hasattr(state.primary, "value") else str(state.primary)
        return json.dumps(
            {"primary": p, "intensity": float(getattr(state, "intensity", 0.5))},
            ensure_ascii=False,
        )
    except Exception:
        return ""


def _parse_deliver_to(raw: str | None) -> tuple[str | None, str | None]:
    if not raw or not str(raw).strip():
        return None, None
    s = str(raw).strip()
    if ":" not in s:
        return None, None
    plat, _, rest = s.partition(":")
    plat = plat.strip().lower()
    tgt = rest.strip()
    if plat in ("telegram", "discord", "cli") and tgt:
        return plat, tgt
    return None, None


def _summarize_text(s: str, max_len: int = 400) -> str:
    t = (s or "").strip().replace("\n", " ")
    if len(t) <= max_len:
        return t
    return t[: max_len - 1] + "…"


def _cron_next_unix(cron_expr: str, start: float | None = None) -> float | None:
    try:
        trig = CronTrigger.from_crontab(cron_expr.strip())
    except Exception:
        return None
    from datetime import datetime

    now = datetime.fromtimestamp(start or time.time())
    nxt = trig.get_next_fire_time(None, now)
    if nxt is None:
        return None
    return nxt.timestamp()


async def _yes_no_model(
    client: Any,
    model: str,
    system: str,
    user: str,
    *,
    num_ctx: int | None = None,
) -> bool:
    try:
        res = await client.chat_completion(
            model,
            [
                {"role": "system", "content": system[:6000]},
                {"role": "user", "content": user[:8000]},
            ],
            temperature=0.2,
            max_tokens=16,
            think=False,
            num_ctx=num_ctx,
        )
        t = (res.content or "").strip().upper()
        if not t:
            return False
        first = t.split()[0]
        return first.startswith("Y") or first == "YES"
    except Exception:
        return False


class AutomationEngine:
    def __init__(self, entity: Entity, scheduler: AsyncIOScheduler) -> None:
        self.entity = entity
        self.scheduler = scheduler
        self.store = entity.store
        self._active_jobs: dict[str, Job] = {}
        self._run_lock = asyncio.Lock()

    def _job_id(self, auto_id: str) -> str:
        return f"ngram_auto:{self.entity.config.name}:{auto_id}"

    async def _ensure_genesis_seeded(self) -> None:
        cfg = self.entity.config.automations
        if not cfg.enabled:
            return
        async with self.store.session() as conn:
            cur = await conn.execute(
                "SELECT value FROM entity_state WHERE key = ?",
                ("automations_genesis_v1",),
            )
            if await cur.fetchone():
                return
            n = await automations_repo.count_automations(conn)
            if n == 0:
                for a in automations_repo.new_seed_automations():
                    await automations_repo.save_automation(conn, a)
            await conn.execute(
                "INSERT OR REPLACE INTO entity_state (key, value) VALUES (?, ?)",
                ("automations_genesis_v1", "1"),
            )
            await conn.commit()

    async def startup(self) -> None:
        if not self.entity.config.automations.enabled:
            return
        await self._ensure_genesis_seeded()
        autos = await self.store.list_automations(enabled_only=True)
        for a in autos:
            await self._schedule_job(a)
        log.info(
            "automation_engine_started",
            module="presence",
            count=len(autos),
        )

    async def shutdown(self) -> None:
        for jid in list(self._active_jobs.keys()):
            try:
                self.scheduler.remove_job(jid)
            except Exception:
                pass
        self._active_jobs.clear()

    async def _schedule_job(self, auto: Automation) -> None:
        if not auto.enabled or not self.entity.config.automations.enabled:
            return
        jid = self._job_id(auto.id)
        try:
            self.scheduler.remove_job(jid)
        except Exception:
            pass
        try:
            trig = CronTrigger.from_crontab(auto.cron_expression.strip())
        except Exception as e:
            log.warning("automation_cron_invalid", id=auto.id, error=str(e))
            return
        job = self.scheduler.add_job(
            self._job_wrapper,
            trigger=trig,
            id=jid,
            args=(auto.id,),
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        self._active_jobs[auto.id] = job
        nxt = _cron_next_unix(auto.cron_expression)
        if nxt is not None:
            await self.store.update_automation(auto.id, next_run=nxt)

    async def _job_wrapper(self, automation_id: str) -> None:
        async with self._run_lock:
            try:
                await self.execute(automation_id)
            except Exception:
                log.exception("automation_job_failed", automation_id=automation_id)

    async def create(self, automation: Automation) -> Automation:
        cfg = self.entity.config.automations
        n = await self.store.count_automations()
        if n >= cfg.max_automations:
            raise RuntimeError(f"max automations ({cfg.max_automations}) reached")
        automation.max_failures = cfg.max_failures
        await self.store.save_automation(automation)
        await self._schedule_job(automation)
        return automation

    async def reschedule(self, auto_id: str) -> None:
        a = await self.store.get_automation(auto_id)
        if a and a.enabled:
            await self._schedule_job(a)
        else:
            await self.unregister(auto_id)

    async def unregister(self, auto_id: str) -> None:
        jid = self._job_id(auto_id)
        try:
            self.scheduler.remove_job(jid)
        except Exception:
            pass
        self._active_jobs.pop(auto_id, None)

    async def execute(self, automation_id: str) -> dict[str, Any]:
        if getattr(self.entity, "inference_paused", False) is True:
            return {"ok": False, "skipped": True, "reason": "inference_paused"}
        cfg = self.entity.config.automations
        started = time.time()
        auto = await self.store.get_automation(automation_id)
        if not auto or not auto.enabled:
            return {"ok": False, "skipped": True}
        if not cfg.enabled:
            return {"ok": False, "skipped": True}

        emo_before = _emotion_snapshot(self.entity.emotions.get_state())
        tool_names: list[str] = []
        reply_text = ""
        success = False
        delivered_to: str | None = None
        self_modified = False
        modification_description: str | None = None
        self_destructed = False

        synthetic = (
            f'You have a scheduled routine called "{auto.name}".\n\n'
            f"What to do: {auto.description}\n\n"
            f"Context for why this exists: {auto.context or '(none)'}\n\n"
            f"This is run #{auto.run_count + 1}. Last time you ran this: "
            f'{auto.last_result_summary or "never run before"}.\n\n'
            "Do this now. Use whatever tools you need. If you realize this routine is no longer "
            "relevant or useful, say so — you can modify or disable it."
        )
        inp = Input(
            text=synthetic,
            person_id=f"routine:{auto.id}",
            person_name="Scheduled routine",
            channel=auto.id,
            platform="automation",
            metadata={"automation_id": auto.id},
        )
        try:
            reply_text, _ = await self.entity.perceive(
                inp,
                stream=None,
                record_user_message=False,
                meaningful_override=True,
                reply_platform=None,
                preserve_conversation_route=True,
                routine_history=False,
                skip_relational_upsert=True,
                skip_drive_interaction=False,
                skip_episode=False,
                tool_names_log=tool_names,
            )
            success = True
        except Exception as e:
            log.exception("automation_perceive_failed", automation_id=automation_id)
            reply_text = f"(routine failed: {e})"
            success = False

        summary = _summarize_text(reply_text, 500)

        plat, tgt = _parse_deliver_to(auto.deliver_to)
        if (not plat or not tgt) and auto.deliver_platform and auto.deliver_to:
            plat = (auto.deliver_platform or "").strip().lower()
            raw = str(auto.deliver_to)
            tgt = raw.split(":", 1)[-1].strip() if ":" in raw else raw.strip()
        if (not plat or not tgt) and auto.origin in (
            AutomationOrigin.SELF,
            AutomationOrigin.USER,
        ):
            lc = getattr(self.entity, "_last_conversation", None) or {}
            p = str(lc.get("platform") or "").strip().lower()
            ch = str(lc.get("channel") or "").strip()
            if p in ("telegram", "discord") and ch:
                plat, tgt = p, ch
        if success and plat and tgt:
            try:
                sender = getattr(self.entity, "send_message_to_platform", None)
                if callable(sender):
                    await sender(plat, tgt, reply_text.strip() or summary)
                    delivered_to = f"{plat}:{tgt}"
            except Exception as e:
                log.warning("automation_deliver_failed", error=str(e))
                success = False
                reply_text = f"{reply_text}\n(delivery failed: {e})"

        if success and not delivered_to and cfg.journal.enabled:
            tags = ["automation", auto.name[:40]]
            try:
                await self.entity.journal.write_entry(
                    f"**{auto.name}** (run {auto.run_count + 1})\n\n{reply_text.strip() or summary}",
                    tags=tags,
                )
            except Exception as e:
                log.warning("automation_journal_failed", error=str(e))

        if success:
            try:
                sm, sdesc = await self.self_modify_check(auto, summary)
                if sm:
                    self_modified = True
                    modification_description = sdesc
            except Exception as e:
                log.warning("automation_self_modify_failed", error=str(e))

            if (auto.self_destruct_condition or "").strip():
                try:
                    if await self.evaluate_self_destruct(auto, summary):
                        self_destructed = True
                        summary = f"{summary} [routine removed — condition met]"
                except Exception as e:
                    log.warning("automation_self_destruct_eval_failed", error=str(e))

        emo_after = _emotion_snapshot(self.entity.emotions.get_state())
        fail_count = auto.consecutive_failures
        if success:
            fail_count = 0
        else:
            fail_count = fail_count + 1

        nxt = None if self_destructed else _cron_next_unix(auto.cron_expression)

        if not self_destructed:
            await self.store.update_automation(
                auto.id,
                last_run=time.time(),
                run_count=auto.run_count + 1,
                last_result_summary=_summarize_text(summary, 500),
                next_run=nxt,
                consecutive_failures=fail_count,
                enabled=False if fail_count >= auto.max_failures else auto.enabled,
            )
        if fail_count >= auto.max_failures and not success:
            try:
                await self.entity.broadcast_proactive(
                    f"hey, my routine '{auto.name}' has been failing for a while so i turned it off. "
                    "might want to check what's going on"
                )
            except Exception:
                pass
            jid = self._job_id(auto.id)
            try:
                self.scheduler.remove_job(jid)
            except Exception:
                pass
            self._active_jobs.pop(auto.id, None)

        if self_destructed:
            jid = self._job_id(auto.id)
            try:
                self.scheduler.remove_job(jid)
            except Exception:
                pass
            self._active_jobs.pop(auto.id, None)
            await self.store.delete_automation(auto.id)

        await self.store.save_automation_run(
            {
                "id": f"arun_{uuid.uuid4().hex[:12]}",
                "automation_id": auto.id,
                "started_at": started,
                "completed_at": time.time(),
                "success": success,
                "result_summary": summary,
                "emotional_state_before": emo_before,
                "emotional_state_after": emo_after,
                "tools_used": tool_names,
                "delivered_to": delivered_to,
                "self_modified": self_modified,
                "modification_description": modification_description,
            }
        )
        return {
            "ok": success,
            "summary": summary,
            "delivered_to": delivered_to,
            "self_destructed": self_destructed,
        }

    async def evaluate_self_destruct(self, automation: Automation, result: str) -> bool:
        cond = (automation.self_destruct_condition or "").strip()
        if not cond:
            return False
        model = self.entity.config.cognition.deliberate_model or self.entity.config.harness.models.deliberate
        user = (
            f"The condition for removing this routine was: {cond!r}.\n"
            f"Routine output summary: {result!r}.\n"
            "Based on what just happened, is this condition met? Reply with exactly YES or NO."
        )
        return await _yes_no_model(
            self.entity.client,
            model,
            "You answer only YES or NO.",
            user,
            num_ctx=self.entity.config.effective_ollama_num_ctx(),
        )

    async def self_modify_check(self, automation: Automation, result: str) -> tuple[bool, str | None]:
        model = self.entity.config.cognition.deliberate_model or self.entity.config.harness.models.deliberate
        user = (
            f"You just ran your routine {automation.name!r}. The result was: {result!r}.\n"
            "Should this routine change? More or less often? Disabled? Different description? "
            "If no changes, reply exactly: NO_CHANGES\n"
            "Otherwise reply one line starting with CHANGE: then brief instructions "
            "(e.g. CHANGE: disable | CHANGE: schedule every day at 9am | CHANGE: description ...)."
        )
        try:
            res = await self.entity.client.chat_completion(
                model,
                [
                    {"role": "system", "content": "Follow the user's format exactly."},
                    {"role": "user", "content": user[:8000]},
                ],
                temperature=0.3,
                max_tokens=120,
                think=False,
                num_ctx=self.entity.config.effective_ollama_num_ctx(),
            )
            line = (res.content or "").strip()
        except Exception:
            return False, None
        if not line or "NO_CHANGES" in line.upper():
            return False, None
        m = re.match(r"CHANGE:\s*(.+)", line, re.I | re.DOTALL)
        if not m:
            return False, None
        instr = m.group(1).strip().lower()
        aid = automation.id
        if "disable" in instr or "turn off" in instr or "stop" in instr:
            await self.store.update_automation(aid, enabled=False)
            await self.reschedule(aid)
            return True, "disabled routine"
        if "every" in instr or "daily" in instr or "hour" in instr:
            try:
                cron = parse_schedule(instr)
                await self.store.update_automation(
                    aid, cron_expression=cron, schedule_natural=instr[:200]
                )
                a2 = await self.store.get_automation(aid)
                if a2:
                    await self._schedule_job(a2)
                return True, f"reschedule: {instr[:120]}"
            except ScheduleParseError:
                return False, None
        return True, instr[:200]

    async def suggest_automations(self) -> list[dict[str, Any]]:
        from ngram.presence.automations.emergence import AutomationEmergence

        em = AutomationEmergence()
        return await em.analyze_and_suggest(self.entity)


def build_automation_from_tool_args(
    *,
    name: str,
    description: str,
    schedule: str,
    deliver_to: str,
    priority: str,
    context: str,
    self_destruct_condition: str,
    origin: AutomationOrigin,
    created_by: str,
) -> Automation:
    cron = parse_schedule(schedule)
    plat, tgt = _parse_deliver_to(deliver_to.strip() or None)
    dt = deliver_to.strip() or None
    if plat and tgt:
        deliver_str = f"{plat}:{tgt}"
    else:
        deliver_str = dt if dt else None
    pr = priority.strip().lower()
    if pr not in ("low", "normal", "high"):
        pr = "normal"
    from ngram.presence.automations.models import AutomationPriority

    prio = AutomationPriority(pr)
    return Automation(
        name=name.strip(),
        description=description.strip(),
        schedule_natural=schedule.strip(),
        cron_expression=cron,
        origin=origin,
        created_by=created_by,
        deliver_to=deliver_str,
        deliver_platform=plat,
        context=(context or "").strip(),
        self_destruct_condition=(self_destruct_condition or "").strip() or None,
        priority=prio,
    )
