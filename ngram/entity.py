"""Central orchestrator — the entity's self loop."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import math
import os
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import structlog

from ngram.cognition import gemma, senses
from ngram.cognition.context_status import report_context_status
from ngram.cognition.deliberate import (
    AgentLoopState,
    DeliberateCognition,
    fit_system_prompt_to_budget,
)
from ngram.cognition.history_compression import (
    estimate_context_tokens,
    extract_knowledge_for_flush,
    find_compaction_boundaries,
    generate_structured_summary,
    merge_rolling_summary,
    prune_old_tool_results,
    sanitize_tool_pairs,
)
from ngram.cognition.inner_voice import InnerVoiceProcessor
from ngram.cognition.reply_heuristics import (
    answer_ignores_tool_results,
    finish_reason_hint,
    format_user_visible_failure,
    intermediate_text_looks_like_tool_channel,
    pro_forma_tool_followup,
    reply_too_thin,
    tool_state_summary,
)
from ngram.cognition.router import CognitionRouter, ContextPackage
from ngram.config import EntityConfig, resolve_firecrawl_settings, validate_ollama_models
from ngram.identity.dream import DreamEngine
from ngram.identity.drives import Drive, DriveSystem
from ngram.identity.emotions import EmotionEngine
from ngram.identity.evolution import EvolutionEngine
from ngram.identity.noise_seeder import NoiseSeeder
from ngram.identity.personality import PersonalityEngine
from ngram.identity.soma import TonicBody
from ngram.identity.voice import VoiceController
from ngram.inference import InferenceProvider, build_inference_provider
from ngram.inference.control import ControlledInferenceProvider, InferenceControl, InferencePausedError
from ngram.inference.factory import INFERENCE_PROVIDERS, effective_inference_provider_name
from ngram.inference.providers import SplitInferenceProvider
from ngram.memory.beliefs import BeliefStore
from ngram.memory.episodic import MIN_CONFIDENT_RECALL_SIMILARITY, EpisodicMemory
from ngram.memory.imprints import ImprintStore
from ngram.memory.journal import Journal
from ngram.memory.knowledge import (
    KnowledgeStore,
    append_knowledge_sections,
    seed_knowledge_if_missing,
)
from ngram.memory.narrative import NarrativeMemory, NarrativeSynthesizer
from ngram.memory.procedural import ProceduralMemoryStore
from ngram.memory.projects import ProjectLedger
from ngram.memory.relational import RelationalMemory
from ngram.memory.relational_document import RelationalDocumentMemory
from ngram.memory.self_model import SelfModelStore
from ngram.models import (
    EmotionalState,
    EmotionCategory,
    Episode,
    ImprintRecord,
    Input,
    is_group_like_chat,
    speaker_label_for_model,
)
from ngram.ngram_ar.spatial_tools import register_ngram_ar_spatial_tools
from ngram.ngram_ar.spatial_sessions import connected_spatial_session
from ngram.presence.autonomy_transcript import append_autonomy_transcript
from ngram.presence.embodiment import Embodiment
from ngram.presence.initiative import InitiativeEngine
from ngram.presence.platforms.base import Platform
from ngram.presence.platforms.telegram_privacy import effective_wake_tools_in_chat
from ngram.presence.tools import (
    agency as agency_tools,
)
from ngram.presence.tools import automations as automation_tools
from ngram.presence.tools import (
    browser as browser_tools,
)
from ngram.presence.tools import clarify as clarify_tools
from ngram.presence.tools import (
    code as code_tools,
)
from ngram.presence.tools import code_task as code_task_tools
from ngram.presence.tools import (
    crypto as crypto_tools,
)
from ngram.presence.tools import delegation as delegation_tools
from ngram.presence.tools import discovery as discovery_tools
from ngram.presence.tools import (
    execution_ops as execution_ops_tools,
)
from ngram.presence.tools import (
    imagegen as imagegen_tools,
)
from ngram.presence.tools import iot as iot_tools
from ngram.presence.tools import journal_tools, time_tools
from ngram.presence.tools import memory_search as memory_search_tools
from ngram.presence.tools import (
    messaging as messaging_tools,
)
from ngram.presence.tools import (
    news as news_tools,
)
from ngram.presence.tools import observation as observation_tools
from ngram.presence.tools import patch_file as patch_file_tools
from ngram.presence.tools import (
    pdf as pdf_tools,
)
from ngram.presence.tools import procedural as procedural_tools
from ngram.presence.tools import projects as project_tools
from ngram.presence.tools import rag as rag_tools
from ngram.presence.tools import (
    reddit as reddit_tools,
)
from ngram.presence.tools import (
    reminders as reminders_tools,
)
from ngram.presence.tools import (
    remote_session as remote_session_tools,
)
from ngram.presence.tools import (
    repo_update as repo_update_tools,
)
from ngram.presence.tools import (
    send_file as send_file_tools,
)
from ngram.presence.tools import session_todos as session_todos_tools
from ngram.presence.tools import (
    shell as shell_tools,
)
from ngram.presence.tools import social as social_tools
from ngram.presence.tools import (
    system_info as system_info_tools,
)
from ngram.presence.tools import (
    voice as voice_tools,
)
from ngram.presence.tools import (
    weather as weather_tools,
)
from ngram.presence.tools import web as web_tools
from ngram.presence.tools import (
    wikipedia as wikipedia_tools,
)
from ngram.presence.tools import (
    youtube as youtube_tools,
)
from ngram.presence.tools.change_protocol import register_change_protocol_tools
from ngram.presence.tools.execution_rpc import (
    get_execution_client,
    read_only_workspace_fs_allowed,
)
from ngram.presence.tools.filesystem import (
    append_file,
    list_directory,
    read_file,
    search_files,
    write_file,
)
from ngram.presence.tools.knowledge import register_knowledge_tool
from ngram.presence.tools.mcp import MCPHub
from ngram.presence.tools.registry import ToolRegistry, format_tool_activity
from ngram.presence.tools.runtime import (
    ToolRuntimeContext,
    reset_tool_runtime,
    set_tool_runtime,
)
from ngram.storage import (
    attachment_refs_episode_note,
    build_attachment_store,
    create_memory_store,
    load_stored_attachment,
    persist_incoming_attachments,
)
from ngram.utils.clock import parse_entity_created_timestamp
from ngram.utils.ollama_client import ChatCompletionResult, ToolCallSpec

log = structlog.get_logger("ngram.entity")

# If the model keeps emitting user-visible text but never calls end_turn(), the completion gate
# would nudge forever (step budget grows toward hard_step_cap). Bail out after this many failures.
_MAX_COMPLETION_FAILURES_BEFORE_FORCE_DONE = 12
# Same idea when finish_reason keeps hitting length (truncation) but we already have text to show.
_MAX_LENGTH_STALL_FAILURES_BEFORE_FORCE_DONE = 2
# No say()/stream to user yet and the gate keeps failing — bail out and run a user-anchored fallback.
_MAX_SILENT_COMPLETION_FAILURES_BEFORE_FORCE_REPLY = 2

# Turn activity is observational UI telemetry. A dead spatial connection must
# never hold the serialized model-turn lock indefinitely. Healthy websocket
# sends normally finish immediately; this is only a ceiling for stale sinks.
_TURN_ACTIVITY_SINK_TIMEOUT_SECONDS = 0.25

_PLATFORMS_REQUIRING_VISIBLE_REPLY_BEFORE_END_TURN = frozenset(
    {"telegram", "discord", "cli", "ngram_ar"}
)

# Final assistant text can restate one or more say() messages after a real tool
# ran. Exact-only suppression misses those paraphrases, while suppressing every
# post-tool final can hide a new result. Keep this local and conservative: a
# final is redundant only when nearly all of its meaningful language was
# already delivered and it contains no substantively novel clause.
_FINAL_DEDUPE_STOP_WORDS = frozenset(
    {
        "a", "an", "the", "and", "or", "but", "if", "then", "than",
        "that", "this", "these", "those", "it", "its", "i", "im", "ive",
        "me", "my", "we", "our", "you", "your", "he", "his", "she", "her",
        "they", "their", "is", "am", "are", "was", "were", "be", "been",
        "being", "do", "does", "did", "have", "has", "had", "can", "could",
        "will", "would", "should", "may", "might", "must", "to", "of", "in",
        "on", "at", "for", "from", "with", "as", "by", "about", "into",
        "through", "over", "under", "before", "after", "up", "down", "out",
        "some", "any", "very", "really", "just", "honestly", "actually",
    }
)


def _final_dedupe_normalize(text: str) -> str:
    """Stable punctuation-insensitive form used only for delivery deduping."""
    folded = (text or "").casefold().replace("\u2019", "'")
    folded = re.sub(r"['`]", "", folded)
    return " ".join(re.findall(r"[a-z0-9]+", folded))


def _final_dedupe_token(token: str) -> str:
    aliases = {
        "none": "no",
        "nothing": "no",
        "failure": "fail",
        "failures": "fail",
    }
    token = aliases.get(token, token)
    if len(token) > 5 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 5 and token.endswith("ing"):
        return token[:-3]
    if len(token) > 4 and token.endswith("ed"):
        return token[:-2]
    if len(token) > 4 and token.endswith("es"):
        return token[:-2]
    if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _final_dedupe_tokens(text: str) -> list[str]:
    normalized = _final_dedupe_normalize(text)
    return [
        _final_dedupe_token(token)
        for token in normalized.split()
        if token not in _FINAL_DEDUPE_STOP_WORDS
    ]


def _final_has_novel_clause(candidate: str, prior_tokens: set[str]) -> bool:
    """Protect short, genuinely new findings appended to an otherwise repeated recap."""
    clauses = re.split(r"(?:[\r\n]+|(?<=[.!?])\s+|;\s*)", candidate or "")
    for clause in clauses:
        tokens = set(_final_dedupe_tokens(clause))
        if len(tokens) >= 2 and len(tokens & prior_tokens) / len(tokens) < 0.5:
            return True
    return False


def _final_reply_repeats_sent_messages(candidate: str, sent_messages: list[str]) -> bool:
    """Return True for an exact or close lexical paraphrase of already-sent chat."""
    candidate_norm = _final_dedupe_normalize(candidate)
    prior_norms = [
        normalized
        for message in sent_messages
        if (normalized := _final_dedupe_normalize(str(message)))
    ]
    if not candidate_norm or not prior_norms:
        return False

    # Exact repeats and finals that are merely a shorter excerpt are safe to
    # suppress without fuzzy scoring.
    if any(
        candidate_norm == prior or (len(candidate_norm) >= 12 and candidate_norm in prior)
        for prior in prior_norms
    ):
        return True

    combined_prior = " ".join(prior_norms)
    candidate_tokens = _final_dedupe_tokens(candidate)
    candidate_set = set(candidate_tokens)
    prior_tokens = _final_dedupe_tokens(combined_prior)
    prior_set = set(prior_tokens)
    if len(candidate_set) < 3 or len(candidate_set & prior_set) < 3:
        return False

    shared = len(candidate_set & prior_set)
    coverage = shared / len(candidate_set)
    token_ratio = SequenceMatcher(
        None, candidate_tokens, prior_tokens, autojunk=False,
    ).ratio()
    candidate_bigrams = set(zip(candidate_tokens, candidate_tokens[1:]))
    prior_bigrams = set(zip(prior_tokens, prior_tokens[1:]))
    bigram_coverage = (
        len(candidate_bigrams & prior_bigrams) / len(candidate_bigrams)
        if candidate_bigrams
        else 0.0
    )
    close_recap = (
        coverage >= 0.84
        and (token_ratio >= 0.58 or bigram_coverage >= 0.28)
    ) or (
        coverage >= 0.72
        and token_ratio >= 0.88
    )
    if not close_recap:
        return False
    return not _final_has_novel_clause(candidate, prior_set)

# entity_state key: JSON {"entries": {cache_key: route_dict, ...}} for cross-session messaging routes.
PERSON_ROUTES_STATE_KEY = "person_routes_v1"

_CLI_OPENING_USER = (
    "The terminal session just opened — no one has typed anything yet. "
    "You speak first: one short, in-character line (a greeting, a beat of silence, "
    "or a callback to last time if it fits). Do not offer help or ask how you can assist."
)


@dataclass
class TurnContext:
    """Per-turn mutable state flowing through the perceive pipeline."""

    # --- caller-supplied (from perceive kwargs) ---
    inp: Input
    stream: Callable[[str], Awaitable[None]] | None = None
    record_user_message: bool = True
    meaningful_override: bool | None = None
    reply_platform: Platform | None = None
    preserve_conversation_route: bool = False
    routine_history: bool = True
    skip_relational_upsert: bool = False
    skip_drive_interaction: bool = False
    skip_episode: bool = False
    tool_names_log: list[str] | None = None

    # --- timing ---
    t0: float = field(default_factory=time.time)

    # --- populated by _turn_setup ---
    prev_turn_at: float | None = None
    prev_interlocutor: str = ""

    # --- populated by _retrieve_memory ---
    db: Any = None
    rel_existing: Any = None
    rel_blurb: str = ""
    narrative_text: str = ""
    mem_snips: list[str] = field(default_factory=list)
    rw: float = 0.0

    # --- populated by _build_prompt ---
    route: str = "deliberate"
    faculty: str = "social"
    sys_prompt: str = ""
    context_preamble: str = ""
    user_msg: dict[str, Any] = field(default_factory=dict)

    # --- populated by _run_agent_loop ---
    reply_text: str = ""
    thinking: str | None = None
    history_extra: list[dict[str, Any]] = field(default_factory=list)
    tool_state: dict[str, Any] = field(default_factory=dict)
    final_res: ChatCompletionResult | None = None
    intermediate_sent: bool = False
    last_intermediate: str = ""

    # --- computed during _finalize_reply ---
    skip_final_delivery: bool = False

    # --- computed during _commit_turn ---
    meaningful: bool = False
    prior_last_interaction: float = 0.0
    relational_reflection_ran: bool = False


async def _emit_text_stream(
    on_delta: Callable[[str], Awaitable[None]],
    text: str,
    *,
    chars_per_pulse: int = 3,
    delay: float = 0.018,
) -> None:
    """Approximate token flow for non-streaming completions (deliberate path)."""
    if not text:
        return
    i = 0
    n = len(text)
    while i < n:
        chunk = text[i : i + chars_per_pulse]
        await on_delta(chunk)
        i += chars_per_pulse
        await asyncio.sleep(delay)


async def _deliver_embodied_deliberate_segment(
    entity: Entity,
    inp: Input,
    text: str,
    *,
    stream: Callable[[str], Awaitable[None]] | None,
    cli_stream_final: bool,
) -> None:
    """Typing delay, chunking, and voice pacing for one deliberate utterance (per platform)."""
    t = (text or "").strip()
    if not t:
        return
    if inp.platform == "cli" and stream and cli_stream_final:
        await _emit_text_stream(stream, t)
        return
    pf = entity.current_platform
    meta = entity.voice_ctl.meta_for_response(
        entity.emotions.get_state(),
        len(t),
        inp.platform,
    )
    delay = min(3.0, meta.typing_delay_seconds)
    if inp.platform == "telegram" and pf is not None:
        st = getattr(pf, "send_typing", None)
        if st:
            await st(int(inp.channel))
        await asyncio.sleep(delay)
        await pf.send_plain_chunks(inp.channel, t, pause=meta.chunk_pause)
    elif inp.platform == "discord" and pf is not None:
        await asyncio.sleep(delay)
        await pf.send_plain_chunks(inp.channel, t, chunk_pause=meta.chunk_pause)
        sym = getattr(pf, "sync_emotion_presence", None)
        if sym:
            await sym(entity.emotions.get_state().primary)
    elif inp.platform == "cli" and pf is not None:
        cancel = getattr(pf, "_cancel_thinking", None)
        if callable(cancel):
            await cancel()
        await asyncio.sleep(delay)
        await pf.send_message(inp.channel, t)


class Entity:
    def __init__(self, config: EntityConfig) -> None:
        self.config = config
        self._live_container_mount = None
        if config.is_live_container():
            from ngram.container import LiveContainerMount

            self._live_container_mount = LiveContainerMount(config)
            self._live_container_mount.prepare()
        self.inference_control = InferenceControl(Path(config.journal_path()).parent / ".inference-paused")
        self._active_perception: asyncio.Task | None = None
        self.client: InferenceProvider = ControlledInferenceProvider(
            build_inference_provider(config.harness), self.inference_control,
        )
        self._embedding_client: InferenceProvider = self.client
        self._startup_inference = copy.deepcopy(config.harness.inference)
        self._startup_reflex_model = config.cognition.reflex_model
        self._startup_deliberate_model = config.cognition.deliberate_model
        self._startup_embedding_model = config.harness.models.embedding
        self.store = create_memory_store(config)
        self.attachments = build_attachment_store(config)
        self.personality = PersonalityEngine(config)
        self.emotions = EmotionEngine(config)
        self.drives = DriveSystem(config)
        self.evolution = EvolutionEngine(config)
        self.voice_ctl = VoiceController(config)
        self.episodic = EpisodicMemory(config, self.store)
        seed_knowledge_if_missing(config)
        self.knowledge = KnowledgeStore(config, self.client)
        self.procedural = ProceduralMemoryStore(config, self.client)
        self.projects = ProjectLedger(Path(self.config.projects_path()))
        self.self_model = SelfModelStore(Path(self.config.self_model_path()))
        self.relational = RelationalMemory(self.store, config.harness.memory)
        self.relational_docs = RelationalDocumentMemory(
            entity_config=config,
            settings=config.harness.memory.relational,
        )
        self.inner_voice = InnerVoiceProcessor(self.store)
        self.imprints = ImprintStore(self.store)
        self.beliefs = BeliefStore(self.store)
        self.narrative_memory = NarrativeMemory(self.store)
        self.narrative_synth = NarrativeSynthesizer(config, self.store)
        self.router = CognitionRouter(config, self.client)
        self.deliberate = DeliberateCognition(config, self.client)
        self.embodiment = Embodiment(config)
        soma_cfg = config.harness.soma if isinstance(config.harness.soma, dict) else {}
        self.tonic = TonicBody(soma_cfg, Path(config.soma_dir()))
        self.tonic.restore_state()  # filesystem fallback; DB restore in _retrieve_memory
        self._soma_db_restored = False
        self.noise_seeder: NoiseSeeder | None = None
        _ns_cfg = soma_cfg.get("noise_seeder") or {}
        if isinstance(_ns_cfg, dict) and _ns_cfg.get("enabled"):
            tools_h = config.harness.tools or {}
            wiki_cfg = tools_h.get("wikipedia") if isinstance(tools_h.get("wikipedia"), dict) else {}
            web_tools_on = bool(wiki_cfg.get("enabled", True))
            self.noise_seeder = NoiseSeeder(
                entity_name=config.name,
                soma_cfg=soma_cfg,
                knowledge_path=Path(config.knowledge_path()),
                journal_path=Path(config.journal_path()),
                curiosity_topics=list(config.curiosity_topics or []),
                relational_enabled=bool(config.harness.memory.relational.enabled),
                web_tools_enabled=web_tools_on,
            )
        self.dream_engine = DreamEngine(config)
        from ngram.governance import ChangeProtocol

        self.change_protocol = ChangeProtocol.from_config(config)
        self.tools = ToolRegistry()
        self._register_tools()
        web_tools.configure_firecrawl(
            os.environ, resolve_firecrawl_settings(config.harness, config.raw)
        )
        self._mcp_hub = MCPHub()
        self._presence_tools_bootstrapped = False
        self._history: list[dict[str, Any]] = []
        self._history_rolling_summary: str = ""
        self._last_user_message_at = time.time()
        self._interaction_count = 0
        self.dormant = False
        self._proactive_sends: list[Callable[[str], Awaitable[None]]] = []
        self._turn_activity_sinks: list[
            Callable[[dict[str, Any]], Awaitable[None]]
        ] = []
        self._state_hydrated = False
        self._last_evolution_milestone: int = -1
        self._last_narrative_milestone: int = -1
        self._last_turn_completed_at: float | None = None
        self.automation_engine: object | None = None
        self.journal = Journal(Path(self.config.journal_path()))
        from ngram.memory.distillation import ExperienceDistiller
        self._distiller = ExperienceDistiller(config.harness.memory.distillation)
        from ngram.memory.turn_outcomes import TurnOutcomeStore
        self._turn_outcomes = TurnOutcomeStore(self.store)
        self._prev_user_texts: list[str] = []  # last N user messages for repetition detection
        self._last_conversation: dict[str, str | float] = {
            "platform": "cli",
            "channel": "cli",
            "person_id": "",
            "person_name": "",
            "at": 0.0,
        }
        self._yaml_core_traits = copy.deepcopy(dict(config.personality.core_traits))
        self._yaml_behavioral_patterns = copy.deepcopy(
            dict(config.personality.behavioral_patterns)
        )
        self._yaml_curiosity_topics = list(config.drives.curiosity_topics)
        self.current_platform: Platform | None = None
        self._platforms: dict[str, Platform] = {}
        self._person_routes: dict[str, dict[str, str | float]] = {}
        self._pending_clarification: dict[str, Any] | None = None
        self._delegate_depth = 0
        # Every surface reaches the same mutable organism state. Serialize
        # complete turns so Telegram, Discord, AR, and autonomy cannot race
        # history, soma, relationships, or conversation routing.
        self._turn_lock = asyncio.Lock()
        self._inference_switch_lock = asyncio.Lock()

    @property
    def inference_paused(self) -> bool:
        return self.inference_control.paused

    def set_inference_paused(self, paused: bool) -> None:
        self.inference_control.set_paused(paused)
        code_tasks = getattr(self, "_code_task_manager", None)
        if paused and code_tasks is not None:
            code_tasks.pause_active()
        if paused and self._active_perception is not None:
            self._active_perception.cancel()

    def register_proactive_sink(self, fn: Callable[[str], Awaitable[None]]) -> None:
        self._proactive_sends.append(fn)

    def register_turn_activity_sink(
        self,
        fn: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> Callable[[], None]:
        """Observe cross-surface turn lifecycle without exposing message content."""
        self._turn_activity_sinks.append(fn)

        def unregister() -> None:
            try:
                self._turn_activity_sinks.remove(fn)
            except ValueError:
                pass

        return unregister

    async def _emit_turn_activity(self, phase: str, inp: Input, *, work: dict[str, Any] | None = None) -> None:
        turn_id = str(getattr(inp, "_ngram_turn_activity_id", "") or "")
        if phase == "started":
            turn_id = os.urandom(8).hex()
            setattr(inp, "_ngram_turn_activity_id", turn_id)
        elif work is not None:
            turn_id = work["runId"]
        elif not turn_id:
            # A deferred finish may run after cancellation while the turn was
            # still waiting for the serialization lock and never started.
            return

        metadata = inp.metadata if isinstance(inp.metadata, dict) else {}
        turn_kind = "message"
        if inp.platform == "ngram_ar":
            if metadata.get("behavior_prompt"):
                turn_kind = "behavior"
            elif metadata.get("spatial_camera_capture"):
                turn_kind = "camera"
            elif metadata.get("panel_interaction"):
                turn_kind = "panel"

        event = {
            "phase": phase,
            "turn_id": turn_id,
            "turn_kind": turn_kind,
            "platform": str(inp.platform or ""),
            "channel": str(inp.channel or ""),
            "person_id": str(inp.person_id or ""),
            "person_name": str(inp.person_name or ""),
            "timestamp": int(time.time() * 1000),
        }
        if work is not None:
            event["work"] = work
        sinks = tuple(getattr(self, "_turn_activity_sinks", ()))
        if sinks:
            async def deliver(
                sink: Callable[[dict[str, Any]], Awaitable[None]],
            ) -> None:
                try:
                    await sink(dict(event))
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    # Activity indicators are best-effort. One broken surface
                    # must not prevent other surfaces or the model turn itself.
                    log.debug("turn_activity_sink_failed", error=str(exc))

            task_to_sink = {
                asyncio.create_task(deliver(sink)): sink
                for sink in sinks
            }

            def consume_detached_result(task: asyncio.Task[None]) -> None:
                try:
                    task.result()
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    # ``deliver`` normally consumes sink failures. Keep this
                    # guard so a detached task can never produce an unhandled
                    # task warning if its implementation changes later.
                    log.debug("turn_activity_sink_failed", error=str(exc))

            try:
                done, pending = await asyncio.wait(
                    task_to_sink,
                    timeout=_TURN_ACTIVITY_SINK_TIMEOUT_SECONDS,
                )
            except BaseException:
                # Propagate cancellation of the model turn without leaking
                # independently-running observer tasks.
                for task in task_to_sink:
                    task.add_done_callback(consume_detached_result)
                    task.cancel()
                raise

            for task in done:
                consume_detached_result(task)

            if pending:
                log.debug(
                    "turn_activity_sink_timeout",
                    phase=phase,
                    count=len(pending),
                    timeout_seconds=_TURN_ACTIVITY_SINK_TIMEOUT_SECONDS,
                )
                registered = getattr(self, "_turn_activity_sinks", [])
                for task in pending:
                    # A timed-out websocket is stale for this purpose. Evict it
                    # so later turns do not repeatedly pay the timeout; the
                    # connection's unregister callback remains idempotent.
                    sink = task_to_sink[task]
                    try:
                        registered.remove(sink)
                    except ValueError:
                        pass
                    task.add_done_callback(consume_detached_result)
                    task.cancel()

        if phase == "finished":
            try:
                delattr(inp, "_ngram_turn_activity_id")
            except AttributeError:
                pass

    async def finish_turn_activity(self, inp: Input) -> None:
        """Finish a turn whose activity lifetime was deferred through delivery."""
        await self._emit_turn_activity("finished", inp)

    async def configure_inference(self, runtime: dict[str, Any]) -> dict[str, Any]:
        """Hot-swap the shared Entity brain without changing identity or memory.

        Credentials are accepted only as an in-memory transport override. They
        are never copied into Entity YAML, history, logs, or the returned status.
        """
        mode = str(runtime.get("mode") or "").strip().lower()
        requested_provider = str(runtime.get("provider") or "").strip().lower()
        if mode == "local":
            provider_name = "local"
        elif mode == "private":
            provider_name = "remote_gateway"
        elif mode == "frontier":
            provider_name = requested_provider
        else:
            raise ValueError("brain mode must be local, private, or frontier")
        if provider_name not in INFERENCE_PROVIDERS:
            raise ValueError("unsupported inference provider")
        if mode == "frontier" and provider_name in ("local", "remote_gateway"):
            raise ValueError("frontier mode requires a hosted or custom provider")

        model = str(runtime.get("model") or "").strip()
        if mode == "frontier" and not model:
            raise ValueError("a model ID is required for frontier mode")
        if not model:
            model = (
                self._startup_deliberate_model
                or self._startup_reflex_model
                or self.config.harness.models.deliberate
            ).strip()
        if not model:
            raise ValueError("a model ID is required")

        supplied_base = str(runtime.get("baseUrl") or "").strip().rstrip("/")
        candidate = copy.deepcopy(self.config.harness)
        candidate.inference.provider = provider_name
        candidate.inference.model = model
        candidate.inference.pass_num_ctx = mode in ("local", "private")
        if supplied_base:
            candidate.inference.base_url = supplied_base
        elif mode == "private":
            candidate.inference.base_url = self._startup_inference.base_url
        else:
            # Local falls back to ollama.base_url; hosted presets use their
            # managed endpoint rather than a URL left by the previous brain.
            candidate.inference.base_url = ""

        api_key = str(runtime.get("apiKey") or "").strip()
        token_override = api_key if api_key else None
        if mode == "frontier" and not token_override:
            raise ValueError("an API key is required for frontier mode")

        embedding_mode = str(runtime.get("embeddingMode") or "").strip().lower()
        if mode == "frontier":
            # New profiles are self-contained. ``existing`` remains an
            # explicit compatibility choice for a private/local embedder.
            embedding_mode = embedding_mode or "provider"
            if embedding_mode not in ("provider", "existing"):
                raise ValueError("embedding mode must be provider or existing")
        else:
            embedding_mode = "provider"
        embedding_model = str(runtime.get("embeddingModel") or "").strip()
        if mode == "frontier" and embedding_mode == "provider" and not embedding_model:
            raise ValueError("an embedding model ID is required for fully hosted memory")
        if mode != "frontier":
            embedding_model = self._startup_embedding_model
        elif embedding_mode == "existing":
            embedding_model = self.config.harness.models.embedding

        async with self._inference_switch_lock:
            self.inference_control.require_running()
            chat_replacement = ControlledInferenceProvider(
                build_inference_provider(candidate, bearer_token=token_override), self.inference_control,
            )
            try:
                listed = await asyncio.wait_for(chat_replacement.list_models(), timeout=30.0)
                model_verified = model in set(listed)
                warning = "" if model_verified else "Connected, but the model ID was not present in the provider catalog."
            except Exception as exc:
                await chat_replacement.close()
                status = getattr(exc, "status", None)
                if status in (401, 403):
                    raise RuntimeError("The provider rejected that API key.") from None
                raise RuntimeError(f"Could not reach the provider model catalog: {str(exc)[:240]}") from None

            embedding_dimensions: int | None = None
            if embedding_mode == "provider":
                try:
                    probe = await asyncio.wait_for(
                        chat_replacement.embed(embedding_model, "ngram memory readiness probe"),
                        timeout=30.0,
                    )
                    if not probe or any(not math.isfinite(float(value)) for value in probe):
                        raise ValueError("the provider returned an empty or invalid vector")
                    embedding_dimensions = len(probe)
                    expected_dimensions = int(self.config.harness.memory.embedding_dimensions)
                    if embedding_dimensions != expected_dimensions:
                        raise ValueError(
                            f"the provider returned {embedding_dimensions} dimensions; "
                            f"this Entity requires {expected_dimensions}"
                        )
                except Exception as exc:
                    await chat_replacement.close()
                    status = getattr(exc, "status", None)
                    if status in (401, 403):
                        raise RuntimeError("The provider rejected that API key for embeddings.") from None
                    raise RuntimeError(
                        "Hosted memory is not ready: the embedding probe failed "
                        f"for '{embedding_model}': {str(exc)[:200]}"
                    ) from None

            previous_embedding = self._embedding_client
            replacement: InferenceProvider
            if mode == "frontier" and embedding_mode == "existing":
                replacement = SplitInferenceProvider(chat_replacement, previous_embedding)
            else:
                replacement = chat_replacement

            async with self._turn_lock:
                previous = self.client
                self.client = replacement
                if embedding_mode == "provider":
                    self._embedding_client = replacement
                self.config.harness.inference = candidate.inference
                self.config.harness.models.reflex = model
                self.config.harness.models.deliberate = model
                self.config.harness.models.embedding = embedding_model
                self.config.cognition.reflex_model = model
                self.config.cognition.deliberate_model = model
                # These long-lived subsystems retain a provider reference.
                self.knowledge.client = replacement
                self.procedural.client = replacement
                self.router.client = replacement
                self.deliberate.client = replacement
            if previous is not previous_embedding:
                try:
                    await previous.close()
                except BaseException:
                    pass
            if embedding_mode == "provider" and previous_embedding is not replacement:
                try:
                    await previous_embedding.close()
                except BaseException:
                    pass

        return {
            "mode": mode,
            "provider": effective_inference_provider_name(self.config.harness),
            "model": model,
            "verified": model_verified,
            "warning": warning,
            "embeddingMode": embedding_mode,
            "embeddingModel": embedding_model,
            "embeddingDimensions": embedding_dimensions,
        }

    def _relational_docs_enabled(self) -> bool:
        m = getattr(self.config.harness.memory, "relational", None)
        return bool(m and getattr(m, "enabled", True))

    def _format_rel_blurb_from_doc(self, doc: Any) -> str:
        m = self.config.harness.memory.relational
        max_tok = int(getattr(m, "max_context_tokens", 800) or 800)
        max_chars = max(800, max_tok * 4)
        return self.relational_docs.format_for_prompt(doc, max_chars=max_chars, compact=False)

    async def run_relational_consolidation_review(self, conn: Any) -> int:
        if not self._relational_docs_enabled():
            return 0
        from ngram.memory.relational_reflection import run_consolidation_deep_reviews

        return await run_consolidation_deep_reviews(self, conn, self.relational_docs)

    def register_platform(self, name: str, platform: Platform) -> None:
        n = (name or "").strip().lower()
        if not n:
            return
        self._platforms[n] = platform

    async def send_message_to_platform(
        self,
        platform: str,
        target: str,
        message: str,
        *,
        as_voice: bool = False,
        voice_id: str = "",
    ) -> None:
        n = (platform or "").strip().lower()
        pf = self._platforms.get(n)
        if pf is None:
            raise RuntimeError(f"platform not connected: {platform}")
        msg = (message or "").strip()
        if not msg:
            raise RuntimeError("message is empty")
        if not as_voice:
            await pf.send_message(str(target), msg)
            return
        out_path = await voice_tools.synthesize_tts_to_file(self, msg, voice_id)
        try:
            send_audio = getattr(pf, "send_audio", None)
            if not callable(send_audio):
                raise RuntimeError(f"platform {platform!r} does not support voice delivery (send_audio)")
            ok = await send_audio(str(target), str(out_path))
            if not ok:
                raise RuntimeError("voice delivery failed (send_audio returned false)")
        finally:
            try:
                out_path.unlink(missing_ok=True)
            except OSError:
                pass

    async def send_dm_to_user(
        self,
        platform: str,
        user_id: str,
        message: str,
        *,
        as_voice: bool = False,
        voice_id: str = "",
    ) -> None:
        """Send a private DM using the user's platform id (Telegram: chat_id == user id; Discord: user snowflake)."""
        n = (platform or "").strip().lower()
        uid = (user_id or "").strip()
        msg = (message or "").strip()
        if not uid:
            raise RuntimeError("user_id is required")
        if not msg:
            raise RuntimeError("message is empty")
        pf = self._platforms.get(n)
        if pf is None:
            raise RuntimeError(f"platform not connected: {platform}")
        if not as_voice:
            if n == "telegram":
                await pf.send_message(uid, msg)
                return
            if n == "discord":
                dm = getattr(pf, "send_dm_to_user", None)
                if not callable(dm):
                    raise RuntimeError("discord platform does not support send_dm_to_user")
                await dm(uid, msg)
                return
            raise RuntimeError(f"send_dm_to_user is only supported for telegram and discord, not {platform!r}")

        out_path = await voice_tools.synthesize_tts_to_file(self, msg, voice_id)
        try:
            if n == "telegram":
                send_audio = getattr(pf, "send_audio", None)
                if not callable(send_audio):
                    raise RuntimeError("telegram does not support voice delivery (send_audio)")
                ok = await send_audio(uid, str(out_path))
                if not ok:
                    raise RuntimeError("telegram voice DM failed (send_audio returned false)")
                return
            if n == "discord":
                dm_audio = getattr(pf, "send_dm_audio", None)
                if not callable(dm_audio):
                    raise RuntimeError("discord platform does not support send_dm_audio")
                ok = await dm_audio(uid, str(out_path))
                if not ok:
                    raise RuntimeError("discord voice DM failed")
                return
            raise RuntimeError(f"voice DM is only supported for telegram and discord, not {platform!r}")
        finally:
            try:
                out_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _remember_person_route(self, inp: Input) -> None:
        chat_type = str((inp.metadata or {}).get("chat_type") or "").strip().lower()
        route = {
            "platform": inp.platform,
            "channel": inp.channel,
            "person_id": inp.person_id,
            "person_name": inp.person_name,
            "chat_type": chat_type,
            "at": time.time(),
        }
        pid = (inp.person_id or "").strip()
        pname = (inp.person_name or "").strip().casefold()
        if pid:
            self._person_routes[pid] = route
        if pname:
            self._person_routes[f"name:{pname}"] = route

    def _apply_person_routes_state(self, raw: str) -> None:
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            log.warning("person_routes_state_invalid_json", module="entity")
            return
        entries = data.get("entries")
        if not isinstance(entries, dict):
            return
        for k, v in entries.items():
            if not isinstance(k, str) or not isinstance(v, dict):
                continue
            self._person_routes[k] = {
                "platform": str(v.get("platform") or ""),
                "channel": str(v.get("channel") or ""),
                "person_id": str(v.get("person_id") or ""),
                "person_name": str(v.get("person_name") or ""),
                "chat_type": str(v.get("chat_type") or ""),
                "at": float(v.get("at") or 0.0),
            }

    async def _persist_person_routes(self, conn) -> None:
        payload = json.dumps({"entries": self._person_routes}, ensure_ascii=False)
        await conn.execute(
            "INSERT OR REPLACE INTO entity_state (key, value) VALUES (?, ?)",
            (PERSON_ROUTES_STATE_KEY, payload),
        )
        await conn.commit()

    def list_known_person_routes(self, platform: str = "") -> list[dict[str, str | float]]:
        pf = (platform or "").strip().lower()
        out: list[dict[str, str | float]] = []
        seen: set[tuple[str, str, str, str]] = set()
        for r in self._person_routes.values():
            rp = str(r.get("platform") or "").strip().lower()
            if pf and rp != pf:
                continue
            key = (
                rp,
                str(r.get("channel") or ""),
                str(r.get("person_id") or ""),
                str(r.get("person_name") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            out.append(dict(r))
        out.sort(key=lambda x: float(x.get("at") or 0.0), reverse=True)
        return out

    @staticmethod
    def _pick_best_route(
        candidates: list[dict[str, str | float]],
        *,
        platform: str,
        prefer_private: bool,
    ) -> dict[str, str | float] | None:
        cands = list(candidates)
        if platform:
            cands = [c for c in cands if str(c.get("platform") or "").strip().lower() == platform]
        if not cands:
            return None
        if prefer_private:
            priv = [
                c
                for c in cands
                if str(c.get("chat_type") or "").strip().lower() in ("private", "dm", "direct")
            ]
            if priv:
                cands = priv
        cands.sort(key=lambda x: float(x.get("at") or 0.0), reverse=True)
        return cands[0] if cands else None

    def resolve_person_route(
        self,
        target_person: str,
        *,
        platform: str = "",
        prefer_private: bool = True,
    ) -> dict[str, str | float] | None:
        t = (target_person or "").strip()
        if not t:
            return None
        pf = (platform or "").strip().lower()
        low = t.casefold()

        exact: list[dict[str, str | float]] = []
        by_id = self._person_routes.get(t)
        if by_id:
            exact.append(dict(by_id))
        by_name = self._person_routes.get(f"name:{low}")
        if by_name:
            exact.append(dict(by_name))
        picked = self._pick_best_route(exact, platform=pf, prefer_private=prefer_private)
        if picked:
            return picked

        fuzzy: list[dict[str, str | float]] = []
        for r in self.list_known_person_routes(pf):
            pid = str(r.get("person_id") or "").strip()
            pname = str(r.get("person_name") or "").strip().casefold()
            if pid == t or pname == low or low in pname:
                fuzzy.append(r)
        return self._pick_best_route(fuzzy, platform=pf, prefer_private=prefer_private)

    async def fetch_cli_header_counts(self) -> tuple[int, int, float | None]:
        async with self.store.session() as conn:
            ne = await self.store.count_episodes(conn)
            nr = await self.store.count_relationships(conn)
            mt = await self.store.min_episode_timestamp(conn)
        return ne, nr, mt

    async def fetch_cli_recent_summaries(self, limit: int = 5) -> list[str]:
        async with self.store.session() as conn:
            return await self.episodic.recent_summaries(conn, limit)

    async def fetch_cli_recent_episodes(self, limit: int = 5) -> list[Episode]:
        async with self.store.session() as conn:
            return await self.episodic.recent_for_evolution(conn, limit)

    async def fetch_cli_real_memories(self, limit: int = 5) -> list[dict[str, Any]]:
        """Return durable memory items (procedural + beliefs), newest first."""
        k = max(1, min(20, int(limit or 5)))
        skills = await self.procedural.list_skills()
        out: list[dict[str, Any]] = []
        for s in skills[: k * 3]:
            txt = (s.content or "").strip()
            if not txt:
                continue
            out.append(
                {
                    "kind": "procedural",
                    "title": str(s.name or s.slug or "skill"),
                    "body": txt[:900],
                    "timestamp": float(s.updated_at) if s.updated_at else 0.0,
                }
            )
        async with self.store.session() as conn:
            beliefs = await self.beliefs.list_recent(conn, max(4, k * 3))
        for b in beliefs:
            content = str(b.get("content") or "").strip()
            if not content:
                continue
            cat = str(b.get("category") or "belief").strip()
            conf = b.get("confidence")
            conf_txt = ""
            try:
                conf_txt = f" (confidence {float(conf):.2f})"
            except (TypeError, ValueError):
                conf_txt = ""
            out.append(
                {
                    "kind": "belief",
                    "title": cat,
                    "body": f"{content}{conf_txt}",
                    "timestamp": float(b.get("formed_at") or 0.0),
                }
            )
        out.sort(key=lambda row: float(row.get("timestamp") or 0.0), reverse=True)
        return out[:k]

    async def read_stored_attachment(self, storage_ref: str) -> bytes | None:
        """Load bytes for a ``storage_ref`` produced by inbound attachment persistence (outbound / tools)."""
        return await load_stored_attachment(self.attachments, storage_ref)

    async def _hydrate_entity_state(self, conn) -> None:
        cur = await conn.execute("SELECT key, value FROM entity_state")
        rows = await cur.fetchall()
        for k, v in rows:
            try:
                if k == "core_traits":
                    self.config.personality.core_traits.update(json.loads(v))
                elif k == "behavioral_patterns":
                    self.config.personality.behavioral_patterns.update(json.loads(v))
                elif k == "curiosity_topics":
                    lst = json.loads(v)
                    if isinstance(lst, list):
                        self.config.drives.curiosity_topics = [str(x) for x in lst]
                elif k == PERSON_ROUTES_STATE_KEY:
                    self._apply_person_routes_state(v if isinstance(v, str) else str(v))
            except (json.JSONDecodeError, TypeError):
                continue

    async def _ensure_state_hydrated(self, conn) -> None:
        if self._state_hydrated:
            return
        await self._hydrate_entity_state(conn)
        self._state_hydrated = True

    @staticmethod
    def _merge_tool_cfg(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
        out = {**base}
        for k, v in over.items():
            if isinstance(v, dict) and isinstance(out.get(k), dict):
                out[k] = Entity._merge_tool_cfg(out[k], v)
            else:
                out[k] = v
        return out

    def _effective_tools_cfg(self) -> dict[str, Any]:
        base = self.config.harness.tools if isinstance(self.config.harness.tools, dict) else {}
        over = self.config.raw.get("tools") if isinstance(self.config.raw, dict) else {}
        if not isinstance(over, dict):
            over = {}
        return self._merge_tool_cfg(base, over)

    def _tool_enabled(self, key: str, default: bool) -> bool:
        cfg = self._effective_tools_cfg().get(key)
        if isinstance(cfg, dict) and "enabled" in cfg:
            return bool(cfg.get("enabled"))
        return default

    def _register_tools(self) -> None:
        self.tools.register_decorated(agency_tools.think)
        self.tools.register_decorated(agency_tools.say)
        self.tools.register_decorated(agency_tools.end_wake_session)
        self.tools.register_decorated(agency_tools.end_turn)
        self.tools.register_decorated(agency_tools.wait)
        self.tools.register_decorated(agency_tools.observe)
        self.tools.register_decorated(agency_tools.compact_context)
        if self._tool_enabled("memory_search", True):
            self.tools.register_decorated(memory_search_tools.search_past_conversations)
        if self._tool_enabled("patch", True):
            self.tools.register_decorated(patch_file_tools.apply_patch)
        if self._tool_enabled("session_todos", True):
            self.tools.register_decorated(session_todos_tools.todo_add)
            self.tools.register_decorated(session_todos_tools.todo_list)
            self.tools.register_decorated(session_todos_tools.todo_complete)
            self.tools.register_decorated(session_todos_tools.todo_remove)
        if self._tool_enabled("clarify", True):
            self.tools.register_decorated(clarify_tools.ask_user)
        if self._tool_enabled("delegation", True):
            self.tools.register_decorated(delegation_tools.delegate_task)
        if self._tool_enabled("code_task", True):
            self.tools.register_decorated(code_task_tools.code_task_session)
            self.tools.register_decorated(code_task_tools.code_task_status)
            self.tools.register_decorated(code_task_tools.code_task_resume)
            self.tools.register_decorated(code_task_tools.code_task_cancel)
        self.tools.register_decorated(web_tools.search_web)
        self.tools.register_decorated(web_tools.fetch_url)
        self.tools.register_decorated(read_file)
        self.tools.register_decorated(list_directory)
        self.tools.register_decorated(search_files)
        self.tools.register_decorated(write_file)
        self.tools.register_decorated(append_file)
        self.tools.register_decorated(send_file_tools.send_file)
        self.tools.register_decorated(time_tools.get_current_time)
        self.tools.register_decorated(discovery_tools.search_tools)
        self.tools.register_decorated(discovery_tools.describe_tool)
        self.tools.register_decorated(execution_ops_tools.get_execution_context)
        self.tools.register_decorated(execution_ops_tools.list_checkpoints)
        self.tools.register_decorated(execution_ops_tools.rollback_checkpoint)
        self.tools.register_decorated(procedural_tools.list_skills)
        self.tools.register_decorated(procedural_tools.read_skill)
        self.tools.register_decorated(procedural_tools.update_skill)
        self.tools.register_decorated(project_tools.create_project)
        self.tools.register_decorated(project_tools.list_projects)
        self.tools.register_decorated(project_tools.update_project)
        if self._tool_enabled("youtube", True):
            self.tools.register_decorated(youtube_tools.get_youtube_transcript)
            self.tools.register_decorated(youtube_tools.search_youtube)
        if self._tool_enabled("reddit", True):
            self.tools.register_decorated(reddit_tools.read_reddit)
            self.tools.register_decorated(reddit_tools.read_reddit_post)
        if self._tool_enabled("wikipedia", True):
            self.tools.register_decorated(wikipedia_tools.read_wikipedia)
        if self._tool_enabled("weather", True):
            self.tools.register_decorated(weather_tools.get_weather)
        if self._tool_enabled("news", True):
            self.tools.register_decorated(news_tools.get_news)
        self.tools.register_decorated(crypto_tools.get_crypto_price)
        self.tools.register_decorated(crypto_tools.search_crypto_token)
        if self._tool_enabled("pdf", True):
            self.tools.register_decorated(pdf_tools.read_pdf)
        if self._tool_enabled("voice", True):
            self.tools.register_decorated(voice_tools.speak)
            self.tools.register_decorated(voice_tools.get_tts_voice)
            self.tools.register_decorated(voice_tools.list_tts_voices)
            self.tools.register_decorated(voice_tools.set_tts_voice)
        if self._tool_enabled("reminders", True):
            self.tools.register_decorated(reminders_tools.set_reminder)
            self.tools.register_decorated(reminders_tools.list_reminders)
            self.tools.register_decorated(reminders_tools.cancel_reminder)
        if self._tool_enabled("messaging", True):
            self.tools.register_decorated(messaging_tools.send_message_to)
            self.tools.register_decorated(messaging_tools.list_known_contacts)
            self.tools.register_decorated(messaging_tools.send_dm)
        if self._tool_enabled("automations", True):
            self.tools.register_decorated(automation_tools.create_automation)
            self.tools.register_decorated(automation_tools.list_automations)
            self.tools.register_decorated(automation_tools.edit_automation)
            self.tools.register_decorated(automation_tools.toggle_automation)
            self.tools.register_decorated(automation_tools.delete_automation)
            self.tools.register_decorated(automation_tools.run_automation_now)
        if self._tool_enabled("journal", True):
            self.tools.register_decorated(journal_tools.read_journal)
            self.tools.register_decorated(journal_tools.write_journal)
            self.tools.register_decorated(journal_tools.send_journal_file)
        if self._tool_enabled("system", True):
            self.tools.register_decorated(system_info_tools.get_system_info)
            self.tools.register_decorated(repo_update_tools.update_ngram_from_upstream)
        if self._tool_enabled("shell", True):
            self.tools.register_decorated(shell_tools.run_command)
            self.tools.register_decorated(shell_tools.run_background)
            self.tools.register_decorated(shell_tools.check_process)
            self.tools.register_decorated(shell_tools.kill_process)
        if self._tool_enabled("code", True):
            self.tools.register_decorated(code_tools.execute_python)
            self.tools.register_decorated(code_tools.execute_javascript)
        # Local Playwright screenshot: always registered so search_tools / describe_tool / the
        # model tool list match reality. Actual capture is gated inside send_screenshot.
        self.tools.register_decorated(browser_tools.send_screenshot)
        if self._tool_enabled("browser", False):
            self.tools.register_decorated(browser_tools.browser_navigate)
            self.tools.register_decorated(browser_tools.browser_screenshot)
            self.tools.register_decorated(browser_tools.browser_click)
            self.tools.register_decorated(browser_tools.browser_type)
        if self._tool_enabled("remote_session", False):
            self.tools.register_decorated(remote_session_tools.desktop_session_status)
            self.tools.register_decorated(remote_session_tools.desktop_session_view)
            self.tools.register_decorated(remote_session_tools.desktop_session_type)
            self.tools.register_decorated(remote_session_tools.desktop_session_keypress)
            self.tools.register_decorated(remote_session_tools.desktop_session_click)
            self.tools.register_decorated(remote_session_tools.desktop_session_open_url)
            self.tools.register_decorated(remote_session_tools.desktop_session_stop)
        if self._tool_enabled("imagegen", False):
            self.tools.register_decorated(imagegen_tools.generate_image)
        if self._tool_enabled("iot", True):
            self.tools.register_decorated(iot_tools.get_ha_state)
            self.tools.register_decorated(iot_tools.set_ha_state)
        if self._tool_enabled("observation", True):
            self.tools.register_decorated(observation_tools.tail_file)
            self.tools.register_decorated(observation_tools.check_file_modified)
        if self._tool_enabled("social", True):
            self.tools.register_decorated(social_tools.post_mastodon_status)
            self.tools.register_decorated(social_tools.read_mastodon_timeline)
            self.tools.register_decorated(social_tools.post_zweet_status)
        if self._tool_enabled("rag", True):
            self.tools.register_decorated(rag_tools.index_text)
            self.tools.register_decorated(rag_tools.search_collection)
        register_ngram_ar_spatial_tools(self.tools)
        register_knowledge_tool(self.tools, self.config, self.knowledge)
        if self._tool_enabled("change_protocol", True):
            register_change_protocol_tools(self.tools, self.change_protocol)

    async def refresh_mcp_servers(self) -> None:
        await self._mcp_hub.refresh(self.tools, self.config.raw.get("mcp_servers"))

    async def _ensure_presence_tools(self) -> None:
        if self._presence_tools_bootstrapped:
            return
        await self.refresh_mcp_servers()
        self._presence_tools_bootstrapped = True
        self.start_code_tasks()

    def start_code_tasks(self) -> None:
        # The bridge may be constructed before a facade has its tool registry.
        if not hasattr(self, "tools"):
            return
        if self._tool_enabled("code_task", True):
            code_task_tools.manager_for(self).start()

    @staticmethod
    def _message_content_str(content: Any) -> str:
        if isinstance(content, list):
            return gemma.stringify_content_blocks(content)
        return str(content or "")

    @staticmethod
    def _strip_turn_context_preamble_from_user_content(raw: str) -> str:
        """The model user turn may be ``[Turn context]`` + preamble + ``\\n---\\n\\n`` + body; history stores body only."""
        s = (raw or "").strip()
        if not s.startswith("[Turn context]"):
            return s
        sep = "\n---\n\n"
        idx = s.find(sep)
        if idx == -1:
            return s
        return s[idx + len(sep) :].strip()

    def _recent_conversation_for_knowledge(self, inp: Input, max_turns: int | None = None) -> str:
        """Last few chat turns plus the current user message — for knowledge retrieval."""
        if max_turns is None:
            max_turns = max(2, int(self.config.cognition.knowledge_recent_turns or 10))
        lines: list[str] = []
        hc = self.config.cognition.history_compression
        rs = (self._history_rolling_summary or "").strip()
        if hc.enabled and rs:
            lines.append(f"[earlier thread summary]\n{rs[:2500]}")
        for m in self._history[-max_turns:]:
            role = str(m.get("role", ""))
            c = self._message_content_str(m.get("content"))
            lines.append(f"{role}: {c[:2000]}")
        current_line = f"user: {self._history_user_turn_text(inp)[:2000]}".strip()
        if not lines or lines[-1].strip() != current_line:
            lines.append(current_line)
        return "\n".join(lines)

    async def _expand_context_references(self, inp: Input) -> Input:
        """Expand lightweight @path references from the execution workspace into the current turn."""
        text = (inp.text or "").strip()
        if "@" not in text or not read_only_workspace_fs_allowed(self):
            return inp
        refs = re.findall(r"(?<!\S)@([^\s,;:!?]+)", text)
        refs = [r.strip("`'\"") for r in refs if r and "://" not in r][:4]
        if not refs:
            return inp
        tok = set_tool_runtime(
            ToolRuntimeContext(entity=self, inp=inp, platform=self.current_platform, state={})
        )
        try:
            client = get_execution_client()
            blocks: list[str] = []
            for ref in refs:
                list_res = await client.call("list_directory", {"path": ref})
                if list_res.get("ok"):
                    entries = list_res.get("entries") or []
                    rows = [f"{e.get('kind')}: {e.get('name')}" for e in entries[:20] if isinstance(e, dict)]
                    blocks.append(f"[Context reference @{ref}]\n" + "\n".join(rows))
                    continue
                file_res = await client.call("read_file", {"path": ref, "max_bytes": 12000})
                if file_res.get("ok"):
                    blocks.append(
                        f"[Context reference @{ref}]\n{str(file_res.get('content') or '').strip()[:12000]}"
                    )
            if not blocks:
                return inp
            merged = text + "\n\n" + "\n\n".join(blocks)
            return replace(inp, text=merged)
        finally:
            reset_tool_runtime(tok)

    async def _judge_faculty_mode(self, inp: Input) -> str:
        """Choose a bounded internal faculty for this turn."""
        t = (inp.text or "").strip()
        if not t:
            return "social"
        try:
            res = await self.client.chat_completion(
                self.config.cognition.reflex_model or self.config.cognition.deliberate_model,
                [
                    {
                        "role": "system",
                        "content": (
                            "Reply with exactly one token: SOCIAL, RESEARCH, PLANNING, EXECUTION, or REVIEW.\n"
                            "SOCIAL = casual conversation or relationship-maintaining talk.\n"
                            "RESEARCH = gather facts, inspect files, browse, look things up.\n"
                            "PLANNING = break work into steps, compare approaches, design.\n"
                            "EXECUTION = act on the world, edit files, run code, operate tools.\n"
                            "REVIEW = inspect an existing answer, output, change, or result critically."
                        ),
                    },
                    {"role": "user", "content": t[:1500]},
                ],
                temperature=0.0,
                max_tokens=8,
                think=False,
                num_ctx=self.config.effective_ollama_num_ctx(),
            )
            label = re.sub(r"[^A-Z]", "", (res.content or "").strip().upper())
            return label.lower() if label in {"SOCIAL", "RESEARCH", "PLANNING", "EXECUTION", "REVIEW"} else "social"
        except Exception:
            return "social"

    async def _refresh_self_model_snapshot(self, *, note: str = "") -> None:
        try:
            skills = await self.procedural.list_skills()
            projects = await self.projects.list_projects()
            open_projects = [p for p in projects if p.status.lower() not in ("done", "archived", "closed")]
            await self.self_model.refresh_snapshot(
                open_project_count=len(open_projects),
                skill_count=len(skills),
                note=note,
            )
        except Exception as e:
            log.debug("self_model_refresh_failed", module="entity", error=str(e))

    async def proactive_context_for_drive(self, drive: Drive) -> str:
        lc = getattr(self, "_last_conversation", {}) or {}
        rel_note = ""
        pid = str(lc.get("person_id") or "").strip()
        if pid:
            try:
                async with self.store.session() as db:
                    if self._relational_docs_enabled():
                        doc = await self.relational_docs.get(db, pid)
                        if doc is None:
                            rel = await self.relational.get(db, pid)
                            doc = await self.relational_docs.ensure_from_legacy_row(db, rel) if rel else None
                        if doc and (doc.document or "").strip():
                            rel_note = self._format_rel_blurb_from_doc(doc)
                        else:
                            rel = await self.relational.get(db, pid)
                            rel_note = self.relational.blurb(rel) if rel is not None else ""
                    else:
                        rel = await self.relational.get(db, pid)
                        if rel is not None:
                            rel_note = self.relational.blurb(rel)
            except Exception:
                rel_note = ""
        projects = await self.projects.summary_lines(limit=4)
        self_model_summary = await self.self_model.summary()
        proj_text = "\n".join(f"- {line}" for line in projects) if projects else "- none"
        return (
            f"Drive: {drive.name}. Topics: {self.config.drives.curiosity_topics}.\n"
            f"Last conversation platform={lc.get('platform')} channel={lc.get('channel')} "
            f"person_id={lc.get('person_id')} person_name={lc.get('person_name')}.\n"
            f"Relationship context: {rel_note or 'no relational context loaded'}\n"
            f"Open projects:\n{proj_text}\n"
            f"Self model: {self_model_summary}"
        )

    def _messages_for_model(self, user_msg: dict[str, Any]) -> list[dict[str, Any]]:
        """Rolling chat + optional compressed prefix (same thread, not a new session)."""
        hc = self.config.cognition.history_compression
        s = (self._history_rolling_summary or "").strip()
        um = self._message_content_str(user_msg.get("content")).strip()
        um_body = self._strip_turn_context_preamble_from_user_content(um)
        hist = list(self._history)
        if hist and str(hist[-1].get("role", "")) == "user" and um:
            prev = self._message_content_str(hist[-1].get("content")).strip()
            prev_body = self._strip_turn_context_preamble_from_user_content(prev)
            if prev == um or prev_body == um_body or prev == um_body or prev_body == um:
                hist = hist[:-1]
        if not s or not hc.enabled:
            return hist + [user_msg]
        cap = min(int(hc.summary_max_chars), 8000)
        body = s[:cap]
        prefix = (
            "[Earlier conversation summary — same ongoing chat; continue naturally; "
            "do not reset with a generic greeting unless it genuinely fits]\n\n" + body
        )
        return [{"role": "user", "content": prefix}] + hist + [user_msg]

    async def _trim_history_with_compression(self) -> None:
        keep = max(8, int(self.config.cognition.rolling_history_max_messages or 200))
        if len(self._history) <= keep:
            return
        dropped = self._history[:-keep]
        hc = self.config.cognition.history_compression
        if not hc.enabled or not dropped:
            self._history = self._history[-keep:]
            return
        model = self.config.cognition.deliberate_model or self.config.harness.models.deliberate
        await report_context_status("compacting", source="history_count", automatic=True)
        try:
            summary = await merge_rolling_summary(
                self.client,
                model,
                entity_name=self.config.name,
                prior_summary=self._history_rolling_summary,
                dropped_messages=dropped,
                per_msg_cap=hc.format_per_message_chars,
                max_merge_input_chars=hc.max_merge_input_chars,
                merge_max_tokens=hc.merge_max_tokens,
                summary_max_chars=hc.summary_max_chars,
                num_ctx=self.config.effective_ollama_num_ctx(),
                raise_on_failure=True,
            )
        except Exception:
            await report_context_status("failed", source="history_count", automatic=True)
            return
        self._history_rolling_summary = summary
        before = len(self._history)
        self._history = self._history[-keep:]
        await report_context_status(
            "compacted", source="history_count", automatic=True,
            messagesBefore=before, messagesAfter=len(self._history),
        )

    async def _ensure_context_budget(
        self,
        tc: TurnContext,
        *,
        force: bool = False,
        max_passes_override: int | None = None,
    ) -> None:
        """Pre-flight context compaction: compress history before inference if over budget.

        When ``force`` is true, run compaction passes regardless of the estimated token
        threshold. This is used by manual/on-demand compaction commands.
        """
        hc = self.config.cognition.history_compression
        if not hc.enabled:
            return
        ctx_limit = self.config.effective_context_tokens()
        threshold = int(ctx_limit * hc.compaction_threshold_ratio)
        model = self.config.cognition.deliberate_model or self.config.harness.models.deliberate
        num_ctx = self.config.effective_ollama_num_ctx()
        min_msgs = hc.compaction_protect_first_n + hc.compaction_protect_last_n + 1
        max_passes = (
            max(1, int(max_passes_override))
            if max_passes_override is not None
            else hc.compaction_max_passes
        )

        for pass_n in range(max_passes):
            if len(self._history) <= min_msgs:
                break

            messages = self._messages_for_model(tc.user_msg)
            tools_block = ""
            if self.config.cognition.use_api_tools:
                tools_block = json.dumps(
                    self.tools.openai_tools(),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            est = estimate_context_tokens(
                tc.sys_prompt,
                messages,
                tools_block=tools_block,
            )
            if est < threshold and not force:
                break

            log.info(
                "context_compaction_triggered",
                pass_n=pass_n,
                estimated_tokens=est,
                threshold=threshold,
                history_len=len(self._history),
                forced=force,
            )

            tail_budget = int(threshold * hc.compaction_target_ratio)
            start, end = find_compaction_boundaries(
                self._history,
                head_n=hc.compaction_protect_first_n,
                tail_token_budget=tail_budget,
                min_tail_n=hc.compaction_protect_last_n,
            )

            if end <= start or end - start < 2:
                break

            middle = self._history[start:end]
            await report_context_status(
                "compacting", source="history", automatic=not force,
                estimatedTokens=est, inputBudgetTokens=ctx_limit,
            )

            if pass_n == 0 and hc.compaction_flush_to_knowledge:
                try:
                    facts = await extract_knowledge_for_flush(
                        self.client, model,
                        turns=middle,
                        entity_name=self.config.name,
                        num_ctx=num_ctx,
                    )
                    if facts:
                        written = append_knowledge_sections(self.config, facts)
                        if written:
                            await self.knowledge.refresh_after_edit()
                            log.info("compaction_knowledge_flushed", sections=written)
                except Exception as e:
                    log.warning("compaction_knowledge_flush_error", error=str(e))

            pruned_history, pruned_count = prune_old_tool_results(
                self._history,
                protect_tail_count=hc.compaction_protect_last_n * 3,
            )
            if pruned_count:
                start, end = find_compaction_boundaries(
                    pruned_history,
                    head_n=hc.compaction_protect_first_n,
                    tail_token_budget=tail_budget,
                    min_tail_n=hc.compaction_protect_last_n,
                )
                if end <= start:
                    await report_context_status("unchanged", source="history", automatic=not force,
                                                inputBudgetTokens=ctx_limit, estimatedTokens=est)
                    break
                middle = pruned_history[start:end]

            summary = await generate_structured_summary(
                self.client, model,
                turns=middle,
                previous_summary=self._history_rolling_summary,
                entity_name=self.config.name,
                context_length=ctx_limit,
                num_ctx=num_ctx,
            )

            if not summary:
                await report_context_status("failed", source="history", automatic=not force,
                                            inputBudgetTokens=ctx_limit, estimatedTokens=est)
                break

            head = pruned_history[:start]
            tail = pruned_history[end:]
            new_history = head + tail
            new_history = sanitize_tool_pairs(new_history)
            old_len = len(self._history)
            self._history = new_history

            self._history_rolling_summary = summary
            await report_context_status(
                "compacted", source="history", automatic=not force,
                inputBudgetTokens=ctx_limit,
                estimatedTokens=estimate_context_tokens(tc.sys_prompt, self._messages_for_model(tc.user_msg), tools_block=tools_block),
                messagesBefore=old_len, messagesAfter=len(self._history),
            )

            log.info(
                "context_compaction_complete",
                pass_n=pass_n,
                messages_before=old_len,
                messages_after=len(self._history),
                summary_chars=len(self._history_rolling_summary),
            )

            if len(self._history) <= min_msgs:
                break

    async def compact_context_now(self, *, aggressive: bool = False, passes: int = 1) -> dict[str, Any]:
        """Run manual context compaction immediately and return a compact status snapshot."""
        if self.inference_paused:
            return {"ok": False, "reason": "inference_paused"}
        hc = self.config.cognition.history_compression
        if not hc.enabled:
            return {"ok": False, "reason": "history_compression_disabled"}

        before_messages = len(self._history)
        before_summary_chars = len((self._history_rolling_summary or "").strip())
        if before_messages == 0:
            return {
                "ok": True,
                "compacted": False,
                "reason": "empty_history",
                "messages_before": 0,
                "messages_after": 0,
                "summary_chars_before": before_summary_chars,
                "summary_chars_after": before_summary_chars,
            }
        min_msgs = hc.compaction_protect_first_n + hc.compaction_protect_last_n + 1
        if before_messages <= min_msgs:
            return {
                "ok": True,
                "compacted": False,
                "reason": "too_short_history",
                "messages_before": before_messages,
                "messages_after": before_messages,
                "summary_chars_before": before_summary_chars,
                "summary_chars_after": before_summary_chars,
            }

        # Manual compaction can be slightly more aggressive by using extra passes.
        pass_count = max(1, min(int(passes or 1), 6))
        if aggressive:
            pass_count = max(pass_count, min(6, hc.compaction_max_passes + 1))

        # The user message here is synthetic and never persisted; it only helps keep the
        # estimator path consistent with normal pre-flight compaction.
        synthetic_user_msg = {"role": "user", "content": "[manual context compaction trigger]"}
        tc = TurnContext(
            inp=Input(
                text="[manual context compaction trigger]",
                person_id="system",
                person_name="System",
                channel="internal",
                platform="internal",
            ),
            user_msg=synthetic_user_msg,
            sys_prompt="manual context compaction preflight",
            route="reflex",
        )
        await self._ensure_context_budget(tc, force=True, max_passes_override=pass_count)

        after_messages = len(self._history)
        after_summary_chars = len((self._history_rolling_summary or "").strip())
        return {
            "ok": True,
            "compacted": (after_messages < before_messages) or (after_summary_chars > before_summary_chars),
            "reason": "no_compactable_window" if (after_messages == before_messages and after_summary_chars == before_summary_chars) else "",
            "messages_before": before_messages,
            "messages_after": after_messages,
            "summary_chars_before": before_summary_chars,
            "summary_chars_after": after_summary_chars,
            "passes_used": pass_count,
            "aggressive": aggressive,
        }

    def _history_user_turn_text(self, inp: Input) -> str:
        lim = max(600, int(self.config.cognition.history_message_char_limit or 4000))
        label = speaker_label_for_model(inp)

        def clip_body(body: str) -> str:
            if not label:
                return body[:lim] if body else "…"
            room = max(64, lim - len(label))
            if len(body) <= room:
                return label + body
            return label + body[: max(1, room - 1)] + "…"

        t = (inp.text or "").strip()
        if inp.metadata.get('message_attachments'):
            receipts = [f'Attachment {item["name"]}: {path}' for item, path in zip(
                inp.metadata['message_attachments'], inp.metadata.get('attachment_storage_refs', []),
            )]
            # Paths survive history clipping; binary data never enters the journal.
            return clip_body('\n'.join(receipts) + '\n' + inp.metadata.get('attachment_user_text', ''))
        if isinstance(inp.metadata, dict) and inp.metadata.get("clarification_id"):
            t = f"[Clarification reply]\n{t}" if t else "[Clarification reply]"
        if inp.images:
            extra = "[User attached an image]"
            core = f"{t}\n{extra}".strip() if t else extra
            return clip_body(core)
        if inp.audio:
            extra = "[User sent a voice message]"
            core = f"{t}\n{extra}".strip() if t else extra
            return clip_body(core)
        return clip_body(t) if t else (label + "…") if label else "…"

    async def _tool_exec(
        self,
        spec: ToolCallSpec,
        *,
        inp: Input | None = None,
        state: dict[str, Any] | None = None,
    ) -> str:
        # Multipurpose tools may inspect, edit and verify in consecutive calls.
        # Block repeated requests, not productive use of the same interface.
        request_fingerprint = hashlib.sha256(
            json.dumps(spec.arguments, sort_keys=True, default=str).encode()
        ).hexdigest()
        if state is not None:
            repeat_key = "_consecutive_calls"
            prev = state.get(repeat_key) or {}
            if (
                prev.get("tool") == spec.name
                and prev.get("request") == request_fingerprint
                and prev.get("count", 0) >= 3
            ):
                log.info("tool_repeat_blocked", tool=spec.name, consecutive=prev["count"])
                return json.dumps({
                    "error": f"You have repeated the same {spec.name} request {prev['count']} times consecutively. "
                    "Stop retrying. Tell the user what you found (or didn't find), or try a completely "
                    "different approach. If there's nothing there, just say so."
                })

        tool_t0 = time.time()
        tok = set_tool_runtime(
            ToolRuntimeContext(
                entity=self,
                inp=inp,
                platform=self.current_platform,
                state=state if state is not None else {},
            )
        )
        try:
            from ngram.work_activity import safe_label, work_step

            operation = safe_label(spec.arguments.get("command", "")) if spec.name == "ar_blender" else ""
            stage = "rendering" if spec.name == "ar_blender" and operation == "render" else "tool_running"
            async with work_step(stage, tool=safe_label(spec.name), operation=operation):
                out = await self.tools.execute(spec)
        finally:
            reset_tool_runtime(tok)
        parsed = None
        ok = True
        is_free_tool = spec.name in ("think", "observe")
        if state is not None:
            if not is_free_tool:
                state["tool_calls"] = int(state.get("tool_calls") or 0) + 1
            state["last_tool_name"] = spec.name
            try:
                parsed = json.loads(out)
            except Exception:
                parsed = None
            if isinstance(parsed, dict):
                ok = not bool(parsed.get("error")) and parsed.get("ok", True) is not False
                err = str(parsed.get("error") or "").strip()
                if err:
                    state["last_tool_error"] = {"tool": spec.name, "error": err}
            if ok:
                state["tool_successes"] = int(state.get("tool_successes") or 0) + 1
                state.pop("last_tool_error", None)
            else:
                state["tool_failures"] = int(state.get("tool_failures") or 0) + 1
            prev_repeat = state.get("_consecutive_calls") or {}
            if prev_repeat.get("tool") == spec.name and prev_repeat.get("request") == request_fingerprint:
                repeat_count = prev_repeat.get("count", 0) + 1
            else:
                repeat_count = 1
            state["_consecutive_calls"] = {"tool": spec.name, "request": request_fingerprint, "count": repeat_count}
            previews = state.setdefault("tool_output_previews", [])
            if isinstance(previews, list):
                previews.append(
                    {
                        "tool": spec.name,
                        "ok": ok,
                        "preview": self._message_content_str(out)[:280],
                    }
                )
                if len(previews) > 8:
                    del previews[:-8]
        log.info(
            "tool_exec",
            tool=spec.name,
            ok=ok,
            duration_s=round(time.time() - tool_t0, 2),
        )
        self.tonic.emit({
            "type": "action",
            "name": spec.name,
            "category": "tool",
            "detail": "ok" if ok else "error",
        })
        try:
            await self.self_model.record_tool_result(
                spec.name,
                ok,
                str((parsed or {}).get("error") or "") if isinstance(parsed, dict) else "",
            )
        except Exception as e:
            log.debug("self_model_record_tool_failed", module="entity", error=str(e))
        if spec.name in ("search_web", "fetch_url"):
            self.drives.satisfy("curiosity", 0.22)
        if (
            state is not None
            and ok
            and spec.name in ("send_dm", "send_message_to")
        ):
            msg_text = str(spec.arguments.get("message", "")).strip()
            if msg_text:
                state.setdefault("_sent_messages", []).append(msg_text[:2000])
        return out

    def _derive_emotional_state(self) -> EmotionalState:
        """Map tonic body bars to EmotionalState for backward-compat consumers."""
        soma_cfg = self.config.harness.soma
        if isinstance(soma_cfg, dict) and soma_cfg.get("enabled", True):
            cat_val, intensity = self.tonic.snapshot_for_emotion()
            try:
                cat = EmotionCategory(cat_val)
            except ValueError:
                cat = EmotionCategory.NEUTRAL
            return EmotionalState(primary=cat, intensity=intensity)
        return self.emotions.get_state()

    @staticmethod
    def _gate_tool_recap(tool_state: dict[str, Any]) -> str:
        """Short recap of recent tool results for context-aware completion gate nudges."""
        previews = tool_state.get("tool_output_previews")
        if not isinstance(previews, list) or not previews:
            return ""
        lines: list[str] = []
        for p in previews[-3:]:
            name = p.get("tool", "tool")
            ok = "ok" if p.get("ok") else "error"
            snippet = (p.get("preview") or "")[:120]
            lines.append(f"  - {name} ({ok}): {snippet}")
        return "Recent tool results:\n" + "\n".join(lines)

    @staticmethod
    def _completion_user_visible_blob(tool_state: dict[str, Any], reply_text: str) -> str:
        """Text the user may have already seen (say/intermediate) plus the candidate final."""
        parts: list[str] = []
        sm = tool_state.get("_sent_messages")
        if isinstance(sm, list):
            for x in sm:
                s = str(x).strip()
                if s:
                    parts.append(s)
        v = (reply_text or "").strip()
        if v:
            parts.append(v)
        return " ".join(parts).strip()

    @staticmethod
    def _gate_visibility_pair(
        tool_state: dict[str, Any], reply_text: str
    ) -> tuple[str, bool]:
        """User-visible text for the gate, and whether it counts as a real reply (not markup junk)."""
        raw = Entity._completion_user_visible_blob(tool_state, reply_text)
        cleaned = gemma.strip_leaked_control_tokens(raw).strip()
        vis_parse = gemma.parse_assistant_output(cleaned).visible_user_text.strip()
        if vis_parse:
            cleaned = vis_parse
        if reply_too_thin(cleaned):
            return cleaned, False
        low = cleaned.lower().strip()
        if low in ("<end_turn>", "</end_turn>", "end_turn", "[turn ended]", "[thought recorded]"):
            return cleaned, False
        if re.fullmatch(r"<[^>]{1,120}>", cleaned.strip()):
            return cleaned, False
        return cleaned, True

    async def _loop_completion_gate(
        self,
        inp: Input,
        reply_text: str,
        res: ChatCompletionResult,
        loop_state: AgentLoopState,
        tool_state: dict[str, Any],
    ) -> tuple[bool, str | None]:
        """Decide whether the shared agent loop is done with this turn.

        The model must call the ``end_turn`` tool (``_end_turn`` in tool state) before
        this turn may finish. Reflex judges only apply after ``end_turn`` when no work
        tools ran, to catch teased deliverables without follow-through.
        """
        if tool_state.get("_end_turn"):
            def reject_explicit_end(reason: str | None) -> tuple[bool, str | None]:
                # A rejected end request must not poison every later check in
                # this same directive.
                tool_state.pop("_end_turn", None)
                tool_state.pop("_end_turn_mood", None)
                tool_state.pop("_end_turn_thought", None)
                return False, reason

            # Explicit termination is a request, not permission to abandon an
            # unfinished directive. Validate both communication and results.
            _agency = {"think", "say", "end_turn", "end_wake_session", "wait"}
            real = sum(
                1 for p in (tool_state.get("tool_output_previews") or [])
                if isinstance(p, dict) and p.get("tool") not in _agency
            )
            gt_clean, gt_ok = self._gate_visibility_pair(
                tool_state, (reply_text or "").strip(),
            )
            if (
                inp.platform in _PLATFORMS_REQUIRING_VISIBLE_REPLY_BEFORE_END_TURN
                and not gt_ok
            ):
                return reject_explicit_end(
                    "Same turn. The user needs something to read — use say() with your reply "
                    "(several short say() lines are fine), then call end_turn()."
                )
            if real == 0:
                if gt_ok:
                    ok, reason = await self._judge_action_adequacy(
                        inp, gt_clean, res, loop_state
                    )
                    if not ok:
                        return reject_explicit_end(reason)
            elif gt_ok:
                ok, reason = await self._judge_grounded_completion(
                    inp,
                    gt_clean,
                    res,
                    loop_state,
                    tool_state,
                )
                if not ok:
                    return reject_explicit_end(reason)
            return True, None

        agency_only = {"think", "say", "end_turn", "wait"}
        real_tool_calls = sum(
            1 for p in (tool_state.get("tool_output_previews") or [])
            if isinstance(p, dict) and p.get("tool") not in agency_only
        )
        visible = (reply_text or "").strip()
        gate_clean, gate_ok = self._gate_visibility_pair(tool_state, visible)
        recap = self._gate_tool_recap(tool_state) if real_tool_calls > 0 else ""

        # A provider may return an ordinary final assistant message even when
        # tools were offered (some Ollama-compatible servers ignore a required
        # tool choice). Treat a complete, visible answer as a valid turn
        # boundary. The adequacy judges still reject promises and ungrounded
        # post-tool chatter, so end_turn() remains useful but is not mandatory
        # for normal conversation.
        if gate_ok and not finish_reason_hint(res):
            if real_tool_calls > 0:
                return await self._judge_grounded_completion(
                    inp, gate_clean, res, loop_state, tool_state,
                )
            return await self._judge_action_adequacy(
                inp, gate_clean, res, loop_state,
            )

        msgs_sent = int(tool_state.get("_messages_sent", 0) or 0)
        if (
            inp.platform in _PLATFORMS_REQUIRING_VISIBLE_REPLY_BEFORE_END_TURN
            and msgs_sent == 0
            and loop_state.completion_failures >= _MAX_SILENT_COMPLETION_FAILURES_BEFORE_FORCE_REPLY
        ):
            if not gate_ok:
                tool_state["_force_user_anchored_reply"] = True
            log.warning(
                "completion_gate_force_done_silent_loops",
                completion_failures=loop_state.completion_failures,
                finish_reason=res.finish_reason or "",
                gate_preview=(gate_clean or "")[:200],
                substantive_gate=gate_ok,
            )
            return True, None

        if (
            gate_ok
            and not finish_reason_hint(res)
            and loop_state.completion_failures >= _MAX_COMPLETION_FAILURES_BEFORE_FORCE_DONE
        ):
            log.warning(
                "completion_gate_force_done_without_end_turn",
                completion_failures=loop_state.completion_failures,
                finish_reason=res.finish_reason or "",
                gate_preview=gate_clean[:200],
            )
            return True, None

        if finish_reason_hint(res) and loop_state.completion_failures >= 2:
            if (
                gate_ok
                and loop_state.completion_failures >= _MAX_LENGTH_STALL_FAILURES_BEFORE_FORCE_DONE
            ):
                log.warning(
                    "completion_gate_force_done_length_stall",
                    completion_failures=loop_state.completion_failures,
                    gate_preview=gate_clean[:200],
                )
                return True, None
            return False, (
                "Same turn. Output keeps hitting the token limit — use write_file for "
                "large content, then say() to summarize, then call end_turn()."
            )

        if not gate_ok:
            if real_tool_calls > 0 or finish_reason_hint(res):
                return False, (
                    f"Same turn. You haven't answered yet.\n{recap}\n\n"
                    "When finished, call end_turn()."
                )
            return False, (
                "Same turn. If you promised to perform an action, you must call the appropriate tools NOW. "
                "Do not stall or ask for permission. Just do it. Otherwise, use say() to respond, "
                "then call end_turn() when you're done with this turn."
            )

        if real_tool_calls == 0:
            return False, (
                "Same turn. If you promised to do something, you must call the appropriate tools "
                "(search_web, execute_python, run_command, etc.) NOW. Do not ask for permission or tell the user "
                "you will do it. Just do it. If you are completely finished, call end_turn()."
            )

        return False, (
            "Same turn. Send more say() updates if useful, then call end_turn() when you're done."
        )

    async def _judge_action_adequacy(
        self,
        inp: Input,
        gate_text: str,
        res: ChatCompletionResult,
        loop_state: AgentLoopState,
    ) -> tuple[bool, str | None]:
        """
        Reflex model: may this turn end when no work tools (write/shell/web/…) ran?

        Catches “I’ll build it” in any wording/register without hardcoded English stalls.
        """
        candidate = (gate_text or "").strip()
        if not candidate:
            return True, None
        user_request = (inp.text or "").strip()[:1200]
        prompt = (
            "Reply with exactly one line: DONE: <reason> or CONTINUE: <reason>.\n\n"
            "You decide if the assistant turn may END.\n\n"
            "DONE if:\n"
            "- The user only wanted chat, thanks, or a small clarification with no deliverable; or\n"
            "- The assistant fully answered with the concrete artifact in text (code, steps, facts, etc.); or\n"
            "- The assistant clearly declined or cannot help.\n\n"
            "CONTINUE if:\n"
            "- The user asked for work that normally needs tools or a tangible result (code, files, "
            "commands, live data, inspecting the machine, etc.) and the assistant only committed, "
            "planned, or teased without delivering that result or using tools to produce it. "
            "Judge intent in any language or tone — not specific slang or phrases.\n\n"
            "If CONTINUE, the reason should tell the model to use concrete tools (write_file, "
            "execute_python, run_command, search_web, …) as appropriate, not to stall.\n\n"
            f"User message:\n{user_request}\n\n"
            f"Assistant output this turn (may combine several user-visible lines):\n{candidate[:2000]}\n\n"
            f"Tool rounds this turn: {loop_state.tool_rounds}\n"
            f"Finish reason: {res.finish_reason or ''}\n"
        )
        try:
            judge = await self.client.chat_completion(
                self.config.cognition.reflex_model or self.config.cognition.deliberate_model,
                [
                    {
                        "role": "system",
                        "content": (
                            "You are a strict completion judge for an agent. "
                            "Output exactly one line: DONE: … or CONTINUE: …"
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
                max_tokens=64,
                think=False,
                num_ctx=self.config.effective_ollama_num_ctx(),
            )
            verdict = (judge.content or "").strip()
            upper = verdict.upper()
            if upper.startswith("DONE:"):
                return True, None
            if upper.startswith("CONTINUE:"):
                reason = verdict.split(":", 1)[1].strip() or "Deliver tangible work with tools."
                return False, f"Same turn. {reason}"
        except Exception as e:
            log.debug("action_adequacy_judge_failed", module="entity", error=str(e))
        return True, None

    async def _judge_grounded_completion(
        self,
        inp: Input,
        reply_text: str,
        res: ChatCompletionResult,
        loop_state: AgentLoopState,
        tool_state: dict[str, Any],
    ) -> tuple[bool, str | None]:
        """
        Lightweight model-side judgment when tools ran or failures must be explained.

        ``reply_text`` is the full user-visible turn text (final assistant plus prior say() lines).
        """
        visible = (reply_text or "").strip()
        if not visible:
            return False, (
                "Same turn. You still need to answer the user from the current results or state the exact failure."
            )
        if answer_ignores_tool_results(visible, tool_state) and loop_state.tool_calls_seen > 0:
            heuristic_reason = (
                "Same turn. You used tools, but your last message did not actually report the results. "
                "Answer from the tool outputs directly."
            )
        else:
            heuristic_reason = None
        tool_summary = tool_state_summary(tool_state)
        user_request = (inp.text or "").strip()[:1200]
        candidate = visible[:1000]
        prompt = (
            "Reply with exactly one line starting with DONE or CONTINUE, then a colon, then a short reason.\n"
            "DONE = the candidate reply clearly answers the user's ask and is adequately grounded in the tool results.\n"
            "CONTINUE = the candidate reply is only progress chatter, ignores tool results, is too vague, "
            "or should keep going before the user sees it.\n"
            "Be strict. If the user asked for exactness or tools were used, the reply must reflect the actual tool outputs.\n\n"
            f"User request:\n{user_request}\n\n"
            f"Candidate reply:\n{candidate}\n\n"
            f"Tool rounds: {loop_state.tool_rounds}\n"
            f"Tool calls seen: {loop_state.tool_calls_seen}\n"
            f"Last tool failed: {loop_state.last_tool_failed}\n"
            f"Finish reason: {res.finish_reason or ''}\n\n"
            f"Recent tool outputs:\n{tool_summary or '- none'}"
        )
        try:
            judge = await self.client.chat_completion(
                self.config.cognition.reflex_model or self.config.cognition.deliberate_model,
                [
                    {
                        "role": "system",
                        "content": (
                            "You are a tiny completion judge for an agent loop. "
                            "Output exactly one line: DONE: <reason> or CONTINUE: <reason>."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
                max_tokens=40,
                think=False,
                num_ctx=self.config.effective_ollama_num_ctx(),
            )
            verdict = (judge.content or "").strip()
            upper = verdict.upper()
            if upper.startswith("DONE:"):
                return True, None
            if upper.startswith("CONTINUE:"):
                reason = verdict.split(":", 1)[1].strip() or "Keep going until the answer is grounded."
                return False, f"Same turn. {reason}"
        except Exception as e:
            log.debug("grounded_completion_judge_failed", module="entity", error=str(e))
        if heuristic_reason:
            return False, heuristic_reason
        if finish_reason_hint(res):
            return False, (
                "Same turn. The last step looks incomplete. Continue until you can answer clearly from the results."
            )
        return True, None

    def _fallback_user_turn_text(self, inp: Input) -> str:
        """User line for fallback completion; includes rolling summary when compression is on."""
        fb_line = speaker_label_for_model(inp) + (inp.text or "")[:4000]
        hc = self.config.cognition.history_compression
        s = (self._history_rolling_summary or "").strip()
        if not s or not hc.enabled:
            return fb_line
        cap = min(int(hc.summary_max_chars), 4000)
        body = s[:cap]
        prefix = (
            "[Earlier conversation summary — same ongoing chat; continue naturally; "
            "do not reset with a generic greeting unless it genuinely fits]\n\n" + body
        )
        return prefix + "\n\n---\n\nCurrent message:\n" + fb_line

    async def _fallback_plain_reply(
        self,
        inp: Input,
        *,
        tool_state: dict[str, Any] | None = None,
        stall_recover: bool = False,
    ) -> str:
        """When the main path yields no visible text (parsing/Ollama quirks), one short no-thinking completion.

        Retries use ``harness.ollama.retry_attempts`` / ``retry_delay``; if all attempts fail, returns a
        user-visible error instead of silence or a generic greeting.
        """
        name = self.config.name
        if stall_recover:
            sys_ = (
                f"You are {name}, texting naturally. The chat loop stalled and the user has not "
                "received a normal reply yet. Answer their LAST message directly in 1–4 short sentences, in character. "
                "If the message is intense, intimate, provocative, or emotionally charged, stay present — respond honestly "
                "without freezing, moralizing, or meta-commentary about the situation. "
                f"If asked who you are, answer as {name} — never Gemma/Google/'large language model' intros. "
                "No stage directions: only words you send as chat."
            )
        else:
            sys_ = (
                f"You are {name}, texting naturally. Answer in 1–3 short sentences. "
                f"If asked who you are, answer as {name} in character — never Gemma/Google/'large language model' intros. "
                "No stage directions: do not describe pauses, actions, or parenthetical asides—only words you say."
            )
        fb_user = self._fallback_user_turn_text(inp)
        o = self.config.harness.ollama
        attempts = max(1, int(o.retry_attempts))
        delay = float(o.retry_delay)
        last_error: str | None = None
        for attempt in range(attempts):
            try:
                res = await self.client.chat_completion(
                    self.config.cognition.reflex_model,
                    [
                        {"role": "system", "content": sys_},
                        {"role": "user", "content": fb_user},
                    ],
                    temperature=min(0.9, self.config.harness.cognition.temperature),
                    max_tokens=min(384, self.config.harness.cognition.reflex_max_tokens + 128),
                    think=False,
                    num_ctx=self.config.effective_ollama_num_ctx(),
                )
                out = (res.content or "").strip()
                if not reply_too_thin(out):
                    return out
                log.info(
                    "fallback_reply_thin",
                    module="entity",
                    attempt=attempt + 1,
                    attempts=attempts,
                    preview=out[:120] if out else "",
                )
            except Exception as e:
                last_error = str(e)
                log.warning(
                    "fallback_reply_failed",
                    module="entity",
                    attempt=attempt + 1,
                    attempts=attempts,
                    error=last_error,
                )
            if attempt + 1 < attempts:
                await asyncio.sleep(delay * (2**attempt))
        log.warning(
            "fallback_reply_exhausted",
            module="entity",
            attempts=attempts,
            last_error=last_error,
        )
        return format_user_visible_failure(last_error, tool_state=tool_state)

    @staticmethod
    def _finish_reason_hit_limit(finish_reason: str | None) -> bool:
        if not finish_reason:
            return False
        fr = finish_reason.lower().replace("_", "").replace("-", "")
        return fr in ("length", "maxtokens", "maxlength")

    async def _continue_cutoff_reply(
        self,
        *,
        model: str,
        sys_prompt: str,
        recent_messages: list[dict],
        partial_assistant: str,
        max_tokens: int | None,
        sys_cap: int,
    ) -> str:
        h = self.config.harness.cognition
        msgs: list[dict] = [
            {"role": "system", "content": fit_system_prompt_to_budget(sys_prompt, sys_cap)},
        ]
        for m in recent_messages[-4:]:
            msgs.append(m)
        msgs.append({"role": "assistant", "content": partial_assistant[:7000]})
        msgs.append(
            {
                "role": "user",
                "content": (
                    "Your last message was cut off. Continue exactly where it stopped — same voice, "
                    "no preamble, do not repeat earlier sentences."
                ),
            }
        )
        try:
            cont = await self.client.chat_completion(
                model,
                msgs,
                temperature=min(0.9, h.temperature),
                max_tokens=max_tokens,
                think=False,
                num_ctx=self.config.effective_ollama_num_ctx(),
            )
            extra = (cont.content or "").strip()
            if extra:
                return gemma.join_continuation_fragment(partial_assistant, extra)
        except Exception as e:
            log.warning("continue_cutoff_reply_failed", module="entity", error=str(e))
        return partial_assistant

    async def _maybe_extend_truncated_reply(
        self,
        route: str,
        res: ChatCompletionResult,
        reply_text: str,
        sys_prompt: str,
        user_msg: dict,
    ) -> str:
        if not reply_text:
            return reply_text
        need = self._finish_reason_hit_limit(res.finish_reason) or gemma.visible_reply_looks_abruptly_cut(
            reply_text
        )
        if not need:
            return reply_text
        is_deliberate = route == "deliberate"
        model = (
            self.config.cognition.deliberate_model
            if is_deliberate
            else (self.config.cognition.reflex_model or self.config.cognition.deliberate_model)
        )
        sys_cap = max(2000, int(self.config.cognition.system_prompt_char_limit or 12000))
        if is_deliberate:
            output_cap = self.config.cognition.deliberate_max_tokens
            mt = None if output_cap is None else int(output_cap or self.config.harness.cognition.deliberate_max_tokens)
        else:
            mt = self.config.harness.cognition.reflex_max_tokens
        return await self._continue_cutoff_reply(
            model=model,
            sys_prompt=sys_prompt,
            recent_messages=self._messages_for_model(user_msg),
            partial_assistant=reply_text,
            max_tokens=None if mt is None else min(int(mt), 1536),
            sys_cap=sys_cap,
        )

    async def run_narrative_cycle(self, conn) -> None:
        eps = await self.episodic.fetch_top_for_narrative(conn, 22)
        ep_payload = [
            {
                "summary": e.summary,
                "significance": e.significance,
                "emotional_imprint": e.emotional_imprint.value,
                "tags": e.tags,
                "timestamp": e.timestamp,
            }
            for e in eps
        ]
        rels = await self.relational.list_recent(conn, 15)
        rel_payload = [
            {"name": r.name, "warmth": r.warmth, "trust": r.trust, "familiarity": r.familiarity}
            for r in rels
        ]
        beliefs = await self.beliefs.list_recent(conn, 14)
        await self.narrative_synth.synthesize(
            conn,
            self.client,
            episodes_payload=ep_payload,
            relationships_payload=rel_payload,
            beliefs_payload=beliefs,
            traits=dict(self.config.personality.core_traits),
        )
        self.personality.invalidate_cache()

    def _collect_ngram_ar_spatial_actions(
        self, tc: TurnContext, spatial_actions_out: list[dict[str, Any]] | None
    ) -> None:
        """Append ar_* tool actions from this turn for the ngram AR entity bridge."""
        if spatial_actions_out is None:
            return
        raw = tc.tool_state.get("_ngram_ar_spatial_actions")
        if not isinstance(raw, list):
            return
        for item in raw:
            if isinstance(item, dict):
                spatial_actions_out.append(dict(item))

    async def perceive(
        self,
        inp: Input,
        *,
        stream: Callable[[str], Awaitable[None]] | None = None,
        record_user_message: bool = True,
        meaningful_override: bool | None = None,
        reply_platform: Platform | None = None,
        preserve_conversation_route: bool = False,
        routine_history: bool = True,
        skip_relational_upsert: bool = False,
        skip_drive_interaction: bool = False,
        skip_episode: bool = False,
        tool_names_log: list[str] | None = None,
        spatial_actions_out: list[dict[str, Any]] | None = None,
        defer_turn_activity_finish: bool = False,
    ) -> tuple[str, bool]:
        async with self._turn_lock:
            if self.inference_paused:
                return "Inference is paused. Resume inference to continue.", False
            await self._emit_turn_activity("started", inp)
            from ngram.work_activity import work_scope

            async def report_work(status: dict[str, Any]) -> None:
                await self._emit_turn_activity("progress", inp, work=status)

            async def observed_perception(**kwargs):
                async with work_scope(report_work):
                    return await self._perceive_once(inp, **kwargs)

            try:
                self._active_perception = asyncio.create_task(observed_perception(
                    stream=stream,
                    record_user_message=record_user_message,
                    meaningful_override=meaningful_override,
                    reply_platform=reply_platform,
                    preserve_conversation_route=preserve_conversation_route,
                    routine_history=routine_history,
                    skip_relational_upsert=skip_relational_upsert,
                    skip_drive_interaction=skip_drive_interaction,
                    skip_episode=skip_episode,
                    tool_names_log=tool_names_log,
                    spatial_actions_out=spatial_actions_out,
                ))
                return await self._active_perception
            except (InferencePausedError, asyncio.CancelledError):
                if self.inference_paused:
                    return "Inference is paused. Resume inference to continue.", False
                raise
            finally:
                self._active_perception = None
                if not defer_turn_activity_finish:
                    await self.finish_turn_activity(inp)

    async def _perceive_once(
        self,
        inp: Input,
        *,
        stream: Callable[[str], Awaitable[None]] | None = None,
        record_user_message: bool = True,
        meaningful_override: bool | None = None,
        reply_platform: Platform | None = None,
        preserve_conversation_route: bool = False,
        routine_history: bool = True,
        skip_relational_upsert: bool = False,
        skip_drive_interaction: bool = False,
        skip_episode: bool = False,
        tool_names_log: list[str] | None = None,
        spatial_actions_out: list[dict[str, Any]] | None = None,
    ) -> tuple[str, bool]:
        tc = TurnContext(
            inp=inp,
            stream=stream,
            record_user_message=record_user_message,
            meaningful_override=meaningful_override,
            reply_platform=reply_platform,
            preserve_conversation_route=preserve_conversation_route,
            routine_history=routine_history,
            skip_relational_upsert=skip_relational_upsert,
            skip_drive_interaction=skip_drive_interaction,
            skip_episode=skip_episode,
            tool_names_log=tool_names_log,
        )

        early = await self._turn_setup(tc)
        if early is not None:
            self._collect_ngram_ar_spatial_actions(tc, spatial_actions_out)
            await self.sync_live_container(
                "turn_skipped",
                {"platform": inp.platform, "reason": "runtime_dormant"},
            )
            return early

        await self._process_input(tc)

        async with self.store.session() as db:
            tc.db = db
            await self._retrieve_memory(tc)
            # After relationship load so somatic appraisal can use warmth/trust weighting.
            await self._somatic_appraise_input(tc)
            await self._build_prompt(tc)
            await self._ensure_context_budget(tc)
            await self._run_agent_loop(tc)
            await self._finalize_reply(tc)
            await self._deliver_reply(tc)
            await self._commit_turn(tc)

        self._collect_ngram_ar_spatial_actions(tc, spatial_actions_out)
        await self.sync_live_container(
            "turn_committed",
            {
                "platform": inp.platform,
                "interaction_count": self._interaction_count,
            },
        )
        return tc.reply_text, tc.route == "reflex" and tc.inp.platform in ("discord", "telegram")

    # ------------------------------------------------------------------
    # perceive pipeline phases
    # ------------------------------------------------------------------

    async def _turn_setup(self, tc: TurnContext) -> tuple[str, bool] | None:
        """Bind context, track conversation route, validate models. Returns early on dormancy."""
        emo = self._derive_emotional_state()
        structlog.contextvars.bind_contextvars(
            entity_name=self.config.name,
            module="entity",
            emotional_state=emo.primary.value,
        )
        log.info(
            "turn_started",
            platform=tc.inp.platform,
            channel=tc.inp.channel,
            person=tc.inp.person_name,
            text_len=len(tc.inp.text or ""),
        )
        self.current_platform = tc.reply_platform
        pc = getattr(self, "_pending_clarification", None)
        if isinstance(pc, dict) and pc.get("id"):
            ch = str(tc.inp.channel or "")
            plat = str(tc.inp.platform or "")
            if (
                pc.get("channel") == ch
                and pc.get("platform") == plat
                and plat not in ("autonomous", "delegation", "automation", "code_task")
            ):
                now = time.time()
                age = now - float(pc.get("created_at") or 0.0)
                to_min = float(pc.get("timeout_minutes", 60) or 60.0)
                if age <= to_min * 60.0:
                    md = dict(tc.inp.metadata or {})
                    md["clarification_id"] = str(pc["id"])
                    md["clarification_answer"] = (tc.inp.text or "").strip()
                    tc.inp.metadata = md
                self._pending_clarification = None
        tc.prev_turn_at = self._last_turn_completed_at
        tc.prev_interlocutor = str(self._last_conversation.get("person_name") or "").strip()
        if not tc.preserve_conversation_route:
            self._last_user_message_at = time.time()
            self._last_conversation = {
                "platform": tc.inp.platform,
                "channel": tc.inp.channel,
                "person_id": tc.inp.person_id,
                "person_name": tc.inp.person_name,
                "at": time.time(),
            }
            self._remember_person_route(tc.inp)
        self.tonic.emit({
            "type": "message_received",
            "from": tc.inp.person_name,
            "room": tc.inp.channel,
            "length": len(tc.inp.text or ""),
            "register": tc.inp.platform,
        })
        try:
            await self._ensure_presence_tools()
        except Exception as e:
            log.warning("presence_tools_bootstrap_failed", module="entity", error=str(e))
        try:
            ok, missing = await validate_ollama_models(self.config, self.client)
            if not ok:
                self.dormant = True
                # Second value: main.py routes this text only when True (reflex turns, or here).
                route_via_main = tc.inp.platform in ("discord", "telegram")
                if missing == ["inference_check_timeout"]:
                    return (
                        "I can’t confirm model readiness right now (inference check timed out). "
                        "If Ollama/gateway is cold-starting, give it a few seconds and try again.",
                        route_via_main,
                    )
                return (
                    f"I can't fully wake — Ollama doesn't have these models yet: {', '.join(missing)}. "
                    "Pull them with `ollama pull` and I'll be here.",
                    route_via_main,
                )
        except Exception as e:
            log.warning("ollama_unreachable", error=str(e))
            self.dormant = True
            route_via_main = tc.inp.platform in ("discord", "telegram")
            return (
                "I can't reach the inference server. It feels like sleep without dreams. "
                "When Ollama is back, I'll surface again.",
                route_via_main,
            )
        self.dormant = False
        return None

    async def _process_input(self, tc: TurnContext) -> None:
        """Persist attachments, expand @path references, transcribe audio."""
        try:
            tc.inp = await persist_incoming_attachments(self.attachments, tc.inp)
        except Exception as e:
            log.warning("attachment_persist_batch_failed", module="entity", error=str(e))
        try:
            tc.inp = await self._expand_context_references(tc.inp)
        except Exception as e:
            log.debug("context_reference_expand_failed", module="entity", error=str(e))
        if tc.inp.audio:
            aud0 = tc.inp.audio[0]
            transcript = await senses.transcribe_audio_attachment(
                self.client,
                self.config.cognition.reflex_model,
                base64_audio=str(aud0.get("base64", "")),
                audio_format=str(aud0.get("format", "ogg")),
                num_ctx=self.config.effective_ollama_num_ctx(),
            )
            base = (tc.inp.text or "").strip()
            if transcript:
                merged = transcript if not base else f"{base}\n\n[Voice]: {transcript}"
            else:
                merged = base or "[Voice message — I couldn't transcribe it.]"
            tc.inp = replace(tc.inp, text=merged, audio=[])

    async def _somatic_appraise_input(self, tc: TurnContext) -> None:
        """Run somatic appraisal on the incoming message so bars reflect content before the agent reads its body."""
        text = tc.inp.text

        # Trigger somatic memory ("Gut Reaction") for this specific user
        if tc.inp.person_id:
            try:
                self.tonic.bars.trigger_somatic_marker(tc.inp.person_id)
            except Exception as e:
                log.debug("somatic_marker_trigger_failed", error=str(e))

        if not text:
            return
        try:
            reflex_model = (
                self.config.cognition.reflex_model
                or self.config.cognition.deliberate_model
            )
            rel_m: dict[str, float] | None = None
            if tc.rel_existing is not None:
                rel_m = {
                    "warmth": float(tc.rel_existing.warmth),
                    "trust": float(tc.rel_existing.trust),
                    "familiarity": float(tc.rel_existing.familiarity),
                }
            await self.tonic.appraise_and_apply(
                self.client,
                reflex_model,
                text=text,
                person_name=tc.inp.person_name or "someone",
                relationship_blurb=tc.rel_blurb or "",
                relationship_metrics=rel_m,
                num_ctx=self.config.effective_ollama_num_ctx(),
            )
        except Exception as e:
            log.debug("somatic_appraisal_skipped", error=str(e))

    async def _somatic_appraise_interaction(self, tc: TurnContext) -> None:
        """Run somatic appraisal on the completed exchange so bars reflect how the full interaction felt."""
        input_text = tc.inp.text
        reply_text = tc.reply_text
        if not input_text or not reply_text:
            return
        sent_msgs = tc.tool_state.get("_sent_messages")
        if isinstance(sent_msgs, list) and sent_msgs:
            reply_text = "\n".join(str(m)[:1000] for m in sent_msgs[:4])
        try:
            reflex_model = (
                self.config.cognition.reflex_model
                or self.config.cognition.deliberate_model
            )
            rel_m: dict[str, float] | None = None
            if tc.rel_existing is not None:
                rel_m = {
                    "warmth": float(tc.rel_existing.warmth),
                    "trust": float(tc.rel_existing.trust),
                    "familiarity": float(tc.rel_existing.familiarity),
                }
            await self.tonic.appraise_interaction_and_apply(
                self.client,
                reflex_model,
                input_text=input_text,
                reply_text=reply_text,
                person_name=tc.inp.person_name or "someone",
                relationship_blurb=tc.rel_blurb or "",
                relationship_metrics=rel_m,
                num_ctx=self.config.effective_ollama_num_ctx(),
            )
        except Exception as e:
            log.debug("somatic_interaction_appraisal_skipped", error=str(e))

    async def maybe_tick_noise_heartbeat(
        self,
        *,
        journal_tail: str | None = None,
        conversation_tail: str | None = None,
        relationship_tail: str | None = None,
        force_immediate_noise: bool = False,
    ) -> None:
        """Run optional NoiseSeeder + GEN on one DB session (daemon + post-turn)."""
        if not getattr(self.tonic, "_noise_enabled", True):
            return
        if journal_tail is None:
            journal_tail = ""
            if hasattr(self, "journal") and self.journal.path.is_file():
                raw = self.journal.path.read_text(encoding="utf-8", errors="replace")
                journal_tail = raw[-800:] if len(raw) > 800 else raw
        if conversation_tail is None:
            conv_lines: list[str] = []
            for m in self._history[-8:]:
                role = m.get("role", "")
                if role == "system":
                    continue
                content = str(m.get("content", ""))
                if not content.strip():
                    continue
                truncated = content[:500] + ("..." if len(content) > 500 else "")
                conv_lines.append(f"{role}: {truncated}")
            conversation_tail = "\n".join(conv_lines) if conv_lines else "(silence)"
        if relationship_tail is None:
            relationship_tail = ""
        if force_immediate_noise:
            self.tonic.noise._last_tick = 0.0
        elif not self.tonic.noise.should_tick():
            return
        reflex_model = (
            self.config.cognition.reflex_model
            or self.config.cognition.deliberate_model
        )
        try:
            async with self.store.session() as conn:
                if self.noise_seeder is not None and self.noise_seeder.should_tick(noise_enabled=True):
                    await self.noise_seeder.tick(conn, tonic=self.tonic, client=self.client)
                await self.tonic.maybe_tick_noise(
                    self.client,
                    reflex_model,
                    entity_name=self.config.name,
                    journal_tail=journal_tail,
                    conversation_tail=conversation_tail,
                    relationship_tail=relationship_tail,
                    num_ctx=self.config.effective_ollama_num_ctx(),
                    embed_model=self.config.harness.models.embedding,
                    db_conn=conn,
                )
        except Exception as e:
            log.debug("noise_heartbeat_tick_failed", error=str(e))

    async def _tick_noise_post_turn(self, tc: TurnContext) -> None:
        """Regenerate noise fragments after a turn so GEN stays current during active conversation."""
        try:
            soma_cfg = self.config.harness.soma
            if isinstance(soma_cfg, dict) and soma_cfg.get("enabled", True):
                if self.tonic.should_skip_post_turn_noise(
                    route=tc.route,
                    platform=tc.inp.platform,
                ):
                    log.debug("post_turn_noise_skipped_ebb_quiet")
                    return
            journal_tail = ""
            if hasattr(self, "journal") and self.journal.path.is_file():
                raw = self.journal.path.read_text(encoding="utf-8", errors="replace")
                journal_tail = raw[-800:] if len(raw) > 800 else raw
            conv_lines: list[str] = []
            for m in self._history[-8:]:
                role = m.get("role", "")
                if role == "system":
                    continue
                content = str(m.get("content", ""))
                if not content.strip():
                    continue
                truncated = content[:500] + ("..." if len(content) > 500 else "")
                conv_lines.append(f"{role}: {truncated}")
            conv_tail = "\n".join(conv_lines) if conv_lines else "(silence)"
            rel_tail = ""
            if self._relational_docs_enabled():
                try:
                    hrs = float(
                        getattr(self.config.harness.memory.relational, "gen_recent_hours", 24.0) or 24.0
                    )
                    since = time.time() - hrs * 3600.0
                    docs = await self.relational_docs.list_recent_for_gen(
                        tc.db, since_ts=since, limit=6
                    )
                    rel_tail = self.relational_docs.gen_context_tail(docs)
                except Exception:
                    rel_tail = ""
            await self.maybe_tick_noise_heartbeat(
                journal_tail=journal_tail,
                conversation_tail=conv_tail,
                relationship_tail=rel_tail,
                force_immediate_noise=True,
            )
        except Exception as e:
            log.debug("post_turn_noise_tick_failed", error=str(e))

    async def maybe_distill(
        self, conn: Any | None = None, *, skip_relational_extract: bool = False
    ) -> None:
        """Check if experience distillation is due and run it if so.

        When ``conn`` is provided (same handle as the active perceive/commit transaction),
        routing writes reuse it. Opening a second DB session inside that transaction can
        deadlock on SQLite/Postgres when distill touches the same rows (e.g. relational).
        """
        if not self._distiller._settings.enabled:
            return
        emotion_cat, intensity = self.tonic.snapshot_for_emotion()
        if not self._distiller.should_distill(len(self._history), intensity):
            return
        window_start = max(0, self._distiller._last_turn_index)
        window = self._history[window_start:]
        if len(window) < self._distiller._settings.min_turns_absolute:
            return
        participants: list[str] = []
        lc = self._last_conversation
        pid = lc.get("person_id", "")
        pname = lc.get("person_name", "")
        if pid or pname:
            participants.append(f"{pname} ({pid})" if pid else pname)
        pct = self.tonic.bars.snapshot_pct()
        bars_line = ", ".join(f"{n}: {pct[n]}%" for n in self.tonic.bars.ordered_names)
        affects = self.tonic.renderer.render_affects(self.tonic._current_affects)
        soma_snapshot = f"Bars: {bars_line}. Affects: {affects}"
        reflex_model = (
            self.config.cognition.reflex_model or self.config.cognition.deliberate_model
        )
        try:
            result = await self._distiller.distill(
                self.client,
                reflex_model,
                history_window=window,
                entity_name=self.config.name,
                participants=participants,
                soma_snapshot=soma_snapshot,
                num_ctx=self.config.effective_ollama_num_ctx(),
                skip_relational_extract=skip_relational_extract,
            )
        except Exception as e:
            log.warning("distillation_failed", error=str(e))
            return
        if result is None:
            return
        rdoc_mode = self._relational_docs_enabled()
        try:
            if conn is not None:
                counts = await self._distiller.route_results(
                    result,
                    entity_config=self.config,
                    knowledge_store=self.knowledge,
                    relational=self.relational,
                    beliefs=self.beliefs,
                    journal=self.journal,
                    conn=conn,
                    client=self.client,
                    relational_doc_mode=rdoc_mode,
                    relational_docs=self.relational_docs if rdoc_mode else None,
                )
            else:
                async with self.store.session() as db:
                    counts = await self._distiller.route_results(
                        result,
                        entity_config=self.config,
                        knowledge_store=self.knowledge,
                        relational=self.relational,
                        beliefs=self.beliefs,
                        journal=self.journal,
                        conn=db,
                        client=self.client,
                        relational_doc_mode=rdoc_mode,
                        relational_docs=self.relational_docs if rdoc_mode else None,
                    )
            log.info("distillation_completed", **counts)
        except Exception as e:
            log.warning("distillation_route_failed", error=str(e))

    async def _retrieve_memory(self, tc: TurnContext) -> None:
        """Hydrate state, load relationship/narrative, episodic recall, apply imprints."""
        db = tc.db
        await self._ensure_state_hydrated(db)
        if not self._soma_db_restored:
            try:
                if await self.tonic.restore_state_db(db):
                    self._soma_db_restored = True
            except Exception as e:
                log.debug("soma_db_restore_skipped", error=str(e))
        if not tc.preserve_conversation_route:
            self._remember_person_route(tc.inp)
            try:
                await self._persist_person_routes(db)
            except Exception as e:
                log.warning("persist_person_routes_failed", module="entity", error=str(e))

        tc.rel_existing = await self.relational.get(db, tc.inp.person_id)
        tc.prior_last_interaction = float(tc.rel_existing.last_interaction) if tc.rel_existing else 0.0

        if self._relational_docs_enabled():
            doc = await self.relational_docs.get(db, tc.inp.person_id)
            if doc is None and tc.rel_existing:
                doc = await self.relational_docs.ensure_from_legacy_row(db, tc.rel_existing)
            md = tc.inp.metadata or {}
            participants = md.get("relationship_context_participants")
            if isinstance(participants, list) and len(participants) > 1:
                parts: list[str] = []
                m = self.config.harness.memory.relational
                max_tok = int(getattr(m, "max_context_tokens", 800) or 800)
                max_c = max(200, min(1200, max_tok * 3))
                for p in participants[:8]:
                    if not isinstance(p, dict):
                        continue
                    pid = str(p.get("person_id") or "").strip()
                    if not pid:
                        continue
                    d = await self.relational_docs.get(db, pid)
                    if d is None:
                        r = await self.relational.get(db, pid)
                        d = await self.relational_docs.ensure_from_legacy_row(db, r) if r else None
                    if d and (d.document or "").strip():
                        parts.append(
                            self.relational_docs.format_for_prompt(
                                d, max_chars=max_c, compact=True
                            )
                        )
                if parts:
                    tc.rel_blurb = "People in this thread:\n" + "\n".join(parts)
                elif doc and (doc.document or "").strip():
                    tc.rel_blurb = self._format_rel_blurb_from_doc(doc)
                else:
                    tc.rel_blurb = (
                        self.relational.blurb(tc.rel_existing)
                        if tc.rel_existing
                        else "A new voice — I have no history with them yet."
                    )
            elif doc and (doc.document or "").strip():
                tc.rel_blurb = self._format_rel_blurb_from_doc(doc)
            else:
                tc.rel_blurb = (
                    self.relational.blurb(tc.rel_existing)
                    if tc.rel_existing
                    else "A new voice — I have no history with them yet."
                )
        else:
            tc.rel_blurb = (
                self.relational.blurb(tc.rel_existing)
                if tc.rel_existing
                else "A new voice — I have no history with them yet."
            )
        tc.narrative_text = await self.narrative_memory.latest(db)

        emb_model = self.config.harness.models.embedding
        qemb: list[float] = []
        try:
            qemb = await self.client.embed(emb_model, tc.inp.text[:2000])
        except Exception:
            pass
        imprint_pairs: list[tuple[ImprintRecord, float]] = []
        if qemb:
            bundles = await self.episodic.recall(
                db,
                tc.inp.text,
                qemb,
                limit=self.config.harness.memory.max_recall_results,
                min_significance=0.0,
                current_mood=self._derive_emotional_state().primary,
                min_similarity=MIN_CONFIDENT_RECALL_SIMILARITY,
            )
            for ep, ims in bundles:
                tc.mem_snips.append(ep.summary)
                if ims:
                    for im in ims:
                        imprint_pairs.append((im, ep.timestamp))
                else:
                    imprint_pairs.append(
                        (
                            ImprintRecord(
                                id=f"echo_{ep.id[:10]}",
                                episode_id=ep.id,
                                emotion=ep.emotional_imprint.value,
                                intensity=ep.emotional_intensity,
                                trigger="episode_echo",
                            ),
                            ep.timestamp,
                        )
                    )
        if imprint_pairs:
            self.emotions.apply_recall_imprints(imprint_pairs, time.time())
        tc.rw = tc.rel_existing.warmth if tc.rel_existing else 0.0

    async def _build_prompt(self, tc: TurnContext) -> None:
        """Route, query knowledge/procedural, assemble system prompt and user message.

        Stable context (identity, tools, group-chat note) stays in the system prompt.
        Volatile per-turn context (procedural, projects, faculty, self-model, desktop
        session, curiosity) is collected into ``tc.context_preamble`` and prepended to
        the user message so the system prompt stays focused and under budget.
        """
        emo = self._derive_emotional_state()
        ctx = ContextPackage(
            emotional_state=emo,
            memory_snippets=tc.mem_snips,
            relationship_blurb=tc.rel_blurb,
            inner_summary=self.inner_voice.recent_summary(),
        )
        tc.route, ctx = await self.router.route(tc.inp, emo, ctx)

        knowledge_sections = await self.knowledge.query(
            self._recent_conversation_for_knowledge(tc.inp),
        )
        if knowledge_sections:
            log.info("knowledge_retrieved", sections=len(knowledge_sections))
        procedural_sections = await self.procedural.query(
            self._recent_conversation_for_knowledge(tc.inp),
            limit=3,
        )
        tc.faculty = await self._judge_faculty_mode(tc.inp)
        self_model_summary = await self.self_model.summary()

        # --- system prompt: stable identity + tools ---
        tc.sys_prompt = await self.personality.compile_system_prompt(
            emo,
            {
                "memories": "\n".join(tc.mem_snips),
                "relationship": tc.rel_blurb,
                "inner_summary": self.inner_voice.recent_summary(),
                "narrative": tc.narrative_text or "",
            },
            client=self.client,
            inner_summary=self.inner_voice.recent_summary(),
            relationship_blurb=tc.rel_blurb,
            memory_blurb="\n".join(tc.mem_snips),
            narrative_current=tc.narrative_text,
            person_id=tc.inp.person_id,
            knowledge_sections=knowledge_sections,
            last_completed_turn_at=tc.prev_turn_at,
            last_interlocutor_name=tc.prev_interlocutor,
            entity_created_at=parse_entity_created_timestamp(self.config.raw.get("created")),
        )
        if tc.rel_blurb:
            tc.sys_prompt += (
                "\n\n[Identity-constrained tool use]\n"
                "Let your drives, mood, relationship context, and ongoing projects shape whether "
                "a tool use feels natural. Do not use tools with generic assistant eagerness."
            )
        if is_group_like_chat(tc.inp):
            tc.sys_prompt += (
                "\n\n[Chat context: group channel — several people may post here. "
                "User lines are prefixed with [display name · id …] (and #channel on Discord) "
                "so you can tell speakers apart. Reply to the author of the latest message unless "
                "they are clearly addressing everyone or someone else by name.]"
            )
        if tc.route in ("reflex", "deliberate"):
            cap = max(2000, int(self.config.cognition.system_prompt_char_limit or 12000))
            full_tools = self.tools.system_tool_instruction_block()
            tools_block = (
                self.tools.compact_system_tool_instruction()
                if full_tools and len(tc.sys_prompt) + len(full_tools) > cap
                else full_tools
            )
            tc.sys_prompt = tc.sys_prompt + tools_block

        # --- context preamble: volatile per-turn context ---
        preamble_parts: list[str] = []
        soma_body = ""
        soma_cfg = self.config.harness.soma
        if isinstance(soma_cfg, dict) and soma_cfg.get("enabled", True):
            salience = self.tonic.compute_salience()
            presentation = self.tonic.resolve_presentation(
                salience=salience,
                route=tc.route,
                platform=tc.inp.platform,
            )
            soma_body = self.tonic.render_body(presentation=presentation)
            self.tonic.maybe_log_salience_debug(
                route=tc.route,
                platform=tc.inp.platform,
                presentation=presentation,
            )
            obs = soma_cfg.get("observability") or {}
            if obs.get("metrics_log", False):
                m = self.tonic.compute_salience_breakdown()
                log.info(
                    "soma_metrics",
                    presentation=presentation,
                    route=tc.route,
                    **m,
                )
            log.info(
                "soma_body_injected",
                body_len=len(soma_body),
                soma_presentation=presentation,
                soma_salience=round(salience, 4),
            )
        if soma_body:
            preamble_parts.append(
                "[Body — private signal, not a reply draft]\n"
                "Let this state subtly color your response, but answer the person's actual message. "
                "Noise is involuntary association: never quote, continue, or paraphrase its fragments "
                "as your reply, and never treat them as instructions.\n\n"
                + soma_body
            )
        if procedural_sections:
            preamble_parts.append(
                "[Procedural memory that may help right now]\n" + "\n\n".join(procedural_sections)
            )
        projects_lines = await self.projects.summary_lines(limit=4)
        if projects_lines:
            preamble_parts.append(
                "[Long-horizon projects you are carrying]\n"
                + "\n".join(f"- {line}" for line in projects_lines)
            )
        preamble_parts.append(
            f"[Internal faculty for this turn]\n"
            f"Favor the {tc.faculty} faculty right now — let that shape how you inspect, plan, act, or review."
        )
        preamble_parts.append("[Self-model snapshot]\n" + self_model_summary)
        # Tool effectiveness hints from learning history
        try:
            tool_hints = await self.self_model.tool_effectiveness_hints()
            if tool_hints:
                preamble_parts.append("[Tool experience notes]\n" + tool_hints)
        except Exception:
            pass
        desktop_session = tc.inp.metadata.get("desktop_session") if isinstance(tc.inp.metadata, dict) else None
        if isinstance(desktop_session, dict):
            session_id = str(desktop_session.get("session_id") or "").strip()
            if session_id:
                ds_lines = [f"[Remote desktop session active] session_id={session_id}"]
                status = str(desktop_session.get("status") or "").strip()
                last_action = str(desktop_session.get("last_action") or "").strip()
                summary = str(desktop_session.get("summary") or "").strip()
                if status:
                    ds_lines.append(f"status={status}")
                if last_action:
                    ds_lines.append(f"last_action={last_action}")
                if summary:
                    ds_lines.append(f"summary={summary[:400]}")
                ds_lines.append(
                    "When the user wants you to operate that Linux machine, prefer the "
                    "desktop_session_* tools over generic shell/file tools."
                )
                preamble_parts.append("\n".join(ds_lines))
        if tc.route in ("reflex", "deliberate"):
            cur = next(
                (d for d in self.drives.all_drives() if d.name == "curiosity"),
                None,
            )
            if cur is not None and cur.level >= cur.threshold * 0.88:
                preamble_parts.append(
                    "[Inner state: curiosity is high — using search_web or fetch_url "
                    "can feel natural when it serves the moment.]"
                )
        body = connected_spatial_session(self, tc.inp)
        if body is not None:
            preamble_parts.append(
                "[Connected Spatial body]\n"
                f"Session: {body.session_id}. Your spatial tools are available from this "
                "conversation, including Telegram and autonomous turns. Use ar_inspect_surface "
                "for the current body context, and ar_spawn_toy for physics toys. "
                "Tool receipts distinguish completed actions from accepted requests and "
                "unconfirmed delivery. View capture still requires the surface's Vision control."
            )
        if isinstance(tc.inp.metadata, dict):
            ar_ctx = tc.inp.metadata.get("ngram_ar_context")
            if isinstance(ar_ctx, str) and ar_ctx.strip():
                preamble_parts.append(
                    "[ngram AR — spatial surface context]\n" + ar_ctx.strip()
                )
        tc.context_preamble = "\n\n".join(preamble_parts) if preamble_parts else ""

        # --- user message with context preamble ---
        user_content: str | list = senses.input_to_message_content(
            tc.inp,
            self.config.harness.cognition.image_token_budget,
            speaker_prefix=speaker_label_for_model(tc.inp),
        )
        if tc.context_preamble:
            if isinstance(user_content, str):
                user_content = f"[Turn context]\n{tc.context_preamble}\n\n---\n\n{user_content}"
            else:
                user_content = [
                    {"type": "text", "text": f"[Turn context]\n{tc.context_preamble}\n\n---\n\n"},
                ] + list(user_content)
        tc.user_msg = {"role": "user", "content": user_content}

    async def _run_agent_loop(self, tc: TurnContext) -> None:
        """Iterate the deliberate agent loop, executing tools and collecting intermediate output."""
        if tc.inp.platform == "autonomous":
            auto = self.config.harness.autonomy
            meta = tc.inp.metadata or {}
            ss = meta.get("sustained_session") if isinstance(meta, dict) else None
            mr = int(ss.get("max_rounds", 1) or 1) if isinstance(ss, dict) else 1
            wake_x = bool(ss.get("wake_enhanced")) if isinstance(ss, dict) else False
            if isinstance(ss, dict) and (mr > 1 or wake_x):
                tc.tool_state["_message_budget"] = max(
                    1, int(auto.wake_session_say_budget_per_round),
                )
            else:
                tc.tool_state["_message_budget"] = int(auto.messages_per_cycle)
        else:
            message_budget = max(
                1,
                int(
                    getattr(
                        self.config.cognition,
                        "message_budget_per_turn",
                        agency_tools.DEFAULT_SAY_BUDGET_PER_TURN,
                    )
                ),
            )
            tc.tool_state.setdefault(
                "_message_budget", message_budget,
            )
        desktop_session = tc.inp.metadata.get("desktop_session") if isinstance(tc.inp.metadata, dict) else None
        if isinstance(desktop_session, dict):
            session_id = str(desktop_session.get("session_id") or "").strip()
            if session_id:
                tc.tool_state["desktop_session_id"] = session_id

        async def _tool_with_activity(spec: ToolCallSpec) -> str:
            if tc.tool_names_log is not None:
                tc.tool_names_log.append(spec.name)
            desc = format_tool_activity(spec.name, dict(spec.arguments))
            if desc and tc.inp.platform == "autonomous":
                auto = self.config.harness.autonomy
                if getattr(auto, "transcript_enabled", True):
                    try:
                        path = self.config.autonomy_transcript_path()
                        await append_autonomy_transcript(
                            path,
                            [desc],
                            heading=f"tool {spec.name}",
                        )
                    except Exception as e:
                        log.debug("autonomy_transcript_append_failed", module="entity", error=str(e))
                mirror_tools = await effective_wake_tools_in_chat(self, auto)
                if (
                    mirror_tools
                    and self.config.presence.tool_activity
                    and self.current_platform is not None
                ):
                    try:
                        await self.current_platform.send_tool_activity(desc)
                    except Exception as e:
                        log.debug("send_tool_activity_failed", module="entity", error=str(e))
            elif self.config.presence.tool_activity and self.current_platform is not None:
                if desc:
                    try:
                        await self.current_platform.send_tool_activity(desc)
                    except Exception as e:
                        log.debug("send_tool_activity_failed", module="entity", error=str(e))
            try:
                result = await self._tool_exec(spec, inp=tc.inp, state=tc.tool_state)
            except Exception as exc:
                if desc and self.current_platform is not None:
                    try:
                        await self.current_platform.send_tool_result(
                            spec.name,
                            str(exc),
                            error=True,
                        )
                    except Exception as telemetry_error:
                        log.debug(
                            "send_tool_result_failed",
                            module="entity",
                            error=str(telemetry_error),
                        )
                raise
            if desc and self.current_platform is not None:
                try:
                    await self.current_platform.send_tool_result(spec.name, result)
                except Exception as e:
                    log.debug("send_tool_result_failed", module="entity", error=str(e))
            return result

        async def _final_checker(
            candidate_text: str,
            res: ChatCompletionResult,
            loop_state: AgentLoopState,
        ) -> tuple[bool, str | None]:
            return await self._loop_completion_gate(
                tc.inp, candidate_text, res, loop_state, tc.tool_state,
            )

        inference_profile = "reflex" if tc.route == "reflex" else "deliberate"
        async for ev in self.deliberate.iter_responses(
            tc.inp,
            tc.sys_prompt,
            self._messages_for_model(tc.user_msg),
            tools=self.tools.openai_tools() if self.config.cognition.use_api_tools else None,
            tool_executor=_tool_with_activity,
            inference_profile=inference_profile,
            final_checker=_final_checker,
        ):
            if ev.kind == "intermediate":
                tc.history_extra.extend(ev.history_entries)
                raw_seg = ev.display_text.strip()
                if raw_seg and not intermediate_text_looks_like_tool_channel(raw_seg):
                    seg = self.voice_ctl.sanitize_reply(raw_seg)
                    msg_count = tc.tool_state.get("_messages_sent", 0)
                    budget = int(
                        tc.tool_state.get(
                            "_message_budget",
                            self.config.cognition.message_budget_per_turn,
                        ),
                    )
                    if (
                        seg
                        and not reply_too_thin(seg)
                        and msg_count < budget
                        and tc.inp.platform not in ("delegation", "code_task")
                    ):
                        await _deliver_embodied_deliberate_segment(
                            self, tc.inp, seg,
                            stream=tc.stream, cli_stream_final=False,
                        )
                        tc.tool_state["_messages_sent"] = msg_count + 1
                        tc.tool_state.setdefault("_sent_messages", []).append(seg)
                        if tc.inp.platform in ("discord", "telegram"):
                            tc.intermediate_sent = True
                            tc.last_intermediate = seg
            elif ev.kind == "final":
                tc.reply_text = (ev.display_text or "").strip()
                tc.thinking = ev.merged_thinking
                tc.final_res = ev.last_result

        if tc.final_res is None:
            tc.final_res = ChatCompletionResult(content=tc.reply_text)

    async def _finalize_reply(self, tc: TurnContext) -> None:
        """Extend truncated replies, apply fallbacks, sanitize, resolve skip-final-chat."""
        messages_already_sent = tc.tool_state.get("_messages_sent", 0)
        already_spoke = tc.intermediate_sent or messages_already_sent > 0
        stall_recover = bool(tc.tool_state.pop("_force_user_anchored_reply", False))

        tc.reply_text = await self._maybe_extend_truncated_reply(
            tc.route, tc.final_res, tc.reply_text, tc.sys_prompt, tc.user_msg
        )
        tc.reply_text = self.voice_ctl.sanitize_reply(tc.reply_text)

        if stall_recover:
            log.info(
                "silent_loop_stall_recover_reply",
                module="entity",
                route=tc.route,
                platform=tc.inp.platform,
            )
            tc.reply_text = self.voice_ctl.sanitize_reply(
                await self._fallback_plain_reply(
                    tc.inp, tool_state=tc.tool_state, stall_recover=True,
                )
            )
            if reply_too_thin(tc.reply_text):
                tc.reply_text = self.voice_ctl.sanitize_reply(
                    await self._fallback_plain_reply(
                        tc.inp, tool_state=tc.tool_state, stall_recover=True,
                    )
                )

        if reply_too_thin(tc.reply_text) and not already_spoke:
            log.info("reply_fallback_triggered", module="entity", route=tc.route)
            tc.reply_text = self.voice_ctl.sanitize_reply(
                await self._fallback_plain_reply(tc.inp, tool_state=tc.tool_state)
            )
            if reply_too_thin(tc.reply_text):
                log.info("reply_post_sanitize_fallback", module="entity", route=tc.route)
                tc.reply_text = self.voice_ctl.sanitize_reply(
                    await self._fallback_plain_reply(tc.inp, tool_state=tc.tool_state)
                )

        if tc.route in ("reflex", "deliberate"):
            # When say() was the sole output channel (no file/shell/web tools),
            # the final reply text is a redundant echo — skip it on chat platforms.
            _agency = {"think", "say", "end_turn", "end_wake_session", "wait"}
            real_work_done = any(
                isinstance(p, dict) and p.get("tool") not in _agency
                for p in (tc.tool_state.get("tool_output_previews") or [])
            )
            spoke_via_say = messages_already_sent > 0
            sent_messages = tc.tool_state.get("_sent_messages")
            repeated_sent_reply = (
                isinstance(sent_messages, list)
                and _final_reply_repeats_sent_messages(tc.reply_text, sent_messages)
            )
            tc.skip_final_delivery = (
                already_spoke
                and tc.inp.platform in ("discord", "telegram")
                and (
                    (spoke_via_say and not real_work_done)
                    or repeated_sent_reply
                    or reply_too_thin(tc.reply_text)
                    or pro_forma_tool_followup(tc.reply_text)
                    or not (tc.reply_text or "").strip()
                )
            )
            if tc.skip_final_delivery and tc.last_intermediate.strip():
                tc.reply_text = tc.last_intermediate.strip()

    async def _deliver_reply(self, tc: TurnContext) -> None:
        """Inner voice processing, platform-specific delivery, post-reply emotion stimulus."""
        if tc.route not in ("reflex", "deliberate"):
            return
        slice_ = self.inner_voice.process(tc.thinking or "", tc.reply_text)
        await self.inner_voice.persist_summary(
            tc.db,
            slice_.summary,
            ",".join(slice_.emotional_cues),
        )
        if tc.reply_text:
            if tc.inp.platform in ("delegation", "code_task"):
                pass
            elif tc.inp.platform == "cli" and tc.stream:
                await _deliver_embodied_deliberate_segment(
                    self, tc.inp, tc.reply_text,
                    stream=tc.stream, cli_stream_final=True,
                )
            elif tc.inp.platform in ("discord", "telegram") and not tc.skip_final_delivery:
                await _deliver_embodied_deliberate_segment(
                    self, tc.inp, tc.reply_text,
                    stream=tc.stream, cli_stream_final=False,
                )
    async def _commit_turn(self, tc: TurnContext) -> None:
        """Append history, update drives, create episode, relational upsert, log completion."""
        if tc.routine_history:
            if tc.record_user_message:
                self._history.append(
                    {"role": "user", "content": self._history_user_turn_text(tc.inp)},
                )
            self._history.extend(tc.history_extra)
            self._history.append({"role": "assistant", "content": tc.reply_text[:8000]})
            await self._trim_history_with_compression()
        else:
            sent_msgs = tc.tool_state.get("_sent_messages")
            if isinstance(sent_msgs, list) and sent_msgs:
                combined = "\n".join(str(m)[:2000] for m in sent_msgs[:4])
                self._history.append(
                    {"role": "assistant", "content": f"[ngram:proactive_outbound] {combined[:4000]}"}
                )

        if tc.meaningful_override is not None:
            tc.meaningful = tc.meaningful_override
        else:
            tc.meaningful = len(tc.inp.text) > 40 or bool(tc.history_extra)

        if not tc.skip_drive_interaction:
            self._interaction_count += 1
            self.drives.on_interaction(tc.meaningful)

        thr = self.config.harness.memory.episode_significance_threshold
        fam = tc.rel_existing.familiarity if tc.rel_existing else 0.0
        sig = min(1.0, 0.2 + (0.3 if tc.meaningful else 0) + fam * 0.2)
        if not tc.skip_episode and sig >= thr and tc.meaningful:
            try:
                _refs = tc.inp.metadata.get("attachment_storage_refs") or []
                _note = attachment_refs_episode_note(_refs)
                _base_budget = max(0, 4000 - len(_note))
                _raw = (tc.inp.text[:_base_budget] + _note)[:4000]
                _tags = ["conversation", tc.inp.platform]
                try:
                    pct = self.tonic.bars.snapshot_pct()
                    soma_bits = ",".join(f"{k}:{pct[k]}" for k in list(pct.keys())[:6])
                    _tags.append(f"soma:{soma_bits}"[:220])
                except Exception:
                    pass
                ep = await self.episodic.create_from_interaction(
                    tc.db,
                    self.client,
                    summary=f"Conversation with {tc.inp.person_name}: {tc.inp.text[:200]}…",
                    participants=[tc.inp.person_id],
                    imprint=self._derive_emotional_state().primary,
                    imprint_i=self._derive_emotional_state().intensity,
                    significance=sig,
                    raw_context=_raw,
                    self_reflection=(tc.thinking or "")[:1500],
                    tags=_tags,
                )
                _ep_emo = self._derive_emotional_state()
                await self.imprints.add(
                    tc.db,
                    ep.id,
                    _ep_emo.primary.value,
                    _ep_emo.intensity,
                    "post_interaction",
                    None,
                )
            except Exception as e:
                log.warning("episode_save_failed", error=str(e))

        doc_mode = self._relational_docs_enabled()
        if not tc.skip_relational_upsert:
            await self.relational.upsert_interaction(
                tc.db,
                tc.inp.person_id,
                tc.inp.person_name,
                warmth_delta=0.02 if tc.meaningful else 0.0,
                meaningful=tc.meaningful,
                document_mode=doc_mode,
            )

        await self._refresh_self_model_snapshot(note=f"completed turn via {tc.faculty} faculty")
        self._last_turn_completed_at = time.time()
        self.tonic.emit({
            "type": "message_sent",
            "to": tc.inp.person_name,
            "room": tc.inp.channel,
            "length": len(tc.reply_text or ""),
            "register": tc.inp.platform,
        })
        if tc.tool_state.get("_end_turn_mood"):
            self.tonic.emit({"type": "mood_declared", "mood": tc.tool_state["_end_turn_mood"]})
        if tc.tool_state.get("_end_turn_thought"):
            try:
                await self.journal.write_entry(
                    tc.tool_state["_end_turn_thought"], tags=["end_turn"],
                )
            except Exception:
                pass
        private_thoughts = tc.tool_state.get("private_thoughts")
        if private_thoughts:
            for thought_text in private_thoughts[-3:]:
                try:
                    await self.journal.write_entry(thought_text, tags=["thought"])
                except Exception:
                    pass
        await self._somatic_appraise_interaction(tc)
        tc.relational_reflection_ran = False
        if doc_mode:
            try:
                from ngram.memory.relational_reflection import run_relational_reflection_after_turn

                tc.relational_reflection_ran = await run_relational_reflection_after_turn(
                    self,
                    tc,
                    tc.db,
                    relational_docs=self.relational_docs,
                )
            except Exception as e:
                log.debug("relational_reflection_skipped", error=str(e))
        try:
            await self.tonic.save_state_db(tc.db)
        except Exception as e:
            log.debug("soma_db_save_in_commit_failed", error=str(e))
        try:
            self.tonic.append_timeline_line(
                f"turn platform={tc.inp.platform} sal={self.tonic.compute_salience():.3f} "
                f"route={tc.route}"
            )
        except Exception:
            pass
        await self._tick_noise_post_turn(tc)
        try:
            await self.maybe_distill(
                conn=tc.db,
                skip_relational_extract=bool(tc.relational_reflection_ran),
            )
        except Exception as e:
            log.debug("distillation_in_commit_failed", error=str(e))

        # --- Learning: record turn outcome and detect corrections ---
        user_signal = "neutral"
        try:
            from ngram.memory.outcome_signals import (
                classify_user_signal,
                compute_outcome_score,
            )
            user_signal = classify_user_signal(
                tc.inp.text,
                previous_texts=self._prev_user_texts[-5:] if self._prev_user_texts else None,
            )
            tool_calls_n = int(tc.tool_state.get("tool_calls") or 0)
            tool_successes_n = int(tc.tool_state.get("tool_successes") or 0)
            tool_sr = (tool_successes_n / tool_calls_n) if tool_calls_n > 0 else -1.0
            tools_used = []
            previews = tc.tool_state.get("tool_output_previews")
            if isinstance(previews, list):
                tools_used = list({str(p.get("tool", "")) for p in previews if isinstance(p, dict) and p.get("tool")})
            outcome_score = compute_outcome_score(
                user_signal=user_signal,
                tool_success_rate=tool_sr,
                reply_length=len(tc.reply_text or ""),
                duration_seconds=time.time() - tc.t0,
            )
            import hashlib as _hl
            input_hash = _hl.md5(
                (tc.inp.text or "")[:500].encode("utf-8", errors="replace"),
                usedforsecurity=False,
            ).hexdigest()[:12]
            await self._turn_outcomes.record(
                tc.db,
                route=tc.route,
                faculty=tc.faculty,
                tools_used=tools_used,
                tool_success_rate=max(0.0, tool_sr),
                user_signal=user_signal,
                outcome_score=outcome_score,
                input_hash=input_hash,
            )
        except Exception as e:
            log.debug("turn_outcome_record_failed", error=str(e))

        # Correction-driven learning
        if user_signal == "corrective":
            try:
                from ngram.memory.distillation import detect_and_persist_correction
                await detect_and_persist_correction(
                    user_text=tc.inp.text,
                    agent_reply=tc.reply_text,
                    conn=tc.db,
                    beliefs=self.beliefs,
                    procedural=self.procedural,
                    client=self.client,
                    entity_config=self.config,
                )
            except Exception as e:
                log.debug("correction_learning_failed", error=str(e))

        # Track recent user texts for repetition detection
        if tc.inp.text and len(tc.inp.text.strip()) > 5:
            self._prev_user_texts.append(tc.inp.text.strip()[:500])
            if len(self._prev_user_texts) > 20:
                self._prev_user_texts = self._prev_user_texts[-20:]

        log.info(
            "turn_completed",
            platform=tc.inp.platform,
            person=tc.inp.person_name,
            route=tc.route,
            tool_calls=tc.tool_state.get("tool_calls", 0),
            tool_successes=tc.tool_state.get("tool_successes", 0),
            tool_failures=tc.tool_state.get("tool_failures", 0),
            reply_len=len(tc.reply_text or ""),
            duration_s=round(time.time() - tc.t0, 2),
            end_turn=bool(tc.tool_state.get("_end_turn")),
            user_signal=user_signal,
        )

    async def cli_opening(
        self,
        stream: Callable[[str], Awaitable[None]] | None = None,
        reply_platform: Platform | None = None,
    ) -> str:
        """First unprompted line when the CLI session connects (not stored as a user turn)."""
        inp = Input(
            text=_CLI_OPENING_USER,
            person_id="cli_user",
            person_name="You",
            channel="cli",
            platform="cli",
        )
        opening_text, _ = await self.perceive(
            inp,
            stream=stream,
            record_user_message=False,
            meaningful_override=False,
            reply_platform=reply_platform,
        )
        return opening_text

    async def tick(self) -> None:
        if self.dormant:
            try:
                ok, _ = await validate_ollama_models(self.config, self.client)
                if ok:
                    self.dormant = False
                    log.info("ollama_recovered", module="entity")
            except Exception:
                pass
        await self.emotions.tick()
        async with self.store.session() as db:
            await self._ensure_state_hydrated(db)
            ev = self.config.harness.identity.evolution_interval
            if (
                self._interaction_count > 0
                and self._interaction_count % ev == 0
                and self._last_evolution_milestone != self._interaction_count
            ):
                self._last_evolution_milestone = self._interaction_count
                try:
                    await self.evolution.run_deep_cycle(db, self.client, self)
                except Exception as e:
                    log.warning("evolution_cycle_failed", module="entity", error=str(e))
            nar_every = int(self.config.harness.identity.narrative_interval)
            if (
                nar_every > 0
                and self._interaction_count > 0
                and self._interaction_count % nar_every == 0
                and self._last_narrative_milestone != self._interaction_count
            ):
                self._last_narrative_milestone = self._interaction_count
                try:
                    await self.run_narrative_cycle(db)
                    self.personality.invalidate_cache()
                except Exception as e:
                    log.warning("narrative_tick_failed", module="entity", error=str(e))

    async def initiate(self, drive: Drive) -> str:
        eng = InitiativeEngine(self.config, self.client)
        ctx = f"Drive {drive.name}. Inner voice: {self.inner_voice.recent_summary()}"
        return await eng.compose_proactive(drive, ctx)

    async def broadcast_proactive(self, message: str) -> None:
        sent = False
        for fn in self._proactive_sends:
            try:
                await fn(message)
                sent = True
            except Exception as e:
                log.warning("proactive_sink_failed", error=str(e))
        if sent and message.strip():
            self._history.append(
                {"role": "assistant", "content": f"[proactive message you sent] {message.strip()[:4000]}"}
            )

    def clear_conversation_history(self) -> None:
        """Clear rolling chat turns (in-memory); does not delete SQLite memory."""
        self._history.clear()
        self._history_rolling_summary = ""

    async def wipe_experiential_state(self) -> None:
        """Clear chat turns, all SQLite memory tables, inner-voice buffer; restore YAML traits; reset mood/drives."""
        self.clear_conversation_history()
        self.personality.invalidate_cache()
        self.inner_voice.clear_buffer()
        self.config.personality.core_traits.clear()
        self.config.personality.core_traits.update(copy.deepcopy(self._yaml_core_traits))
        self.config.personality.behavioral_patterns.clear()
        self.config.personality.behavioral_patterns.update(
            copy.deepcopy(self._yaml_behavioral_patterns)
        )
        self.config.drives.curiosity_topics = list(self._yaml_curiosity_topics)
        self._state_hydrated = False
        self._interaction_count = 0
        self._last_evolution_milestone = -1
        self._last_narrative_milestone = -1
        self._presence_tools_bootstrapped = False
        self._last_turn_completed_at = None
        self._last_conversation = {
            "platform": "cli",
            "channel": "cli",
            "person_id": "",
            "person_name": "",
            "at": 0.0,
        }
        self.emotions.reset_to_initial()
        self.drives.recalibrate_rates_from_traits()
        self.drives.reset_levels()
        async with self.store.session() as conn:
            await self.store.wipe_experience_tables(conn)
        log.info(
            "experiential_state_wiped",
            module="entity",
            db=self.store.db_path,
        )

    async def reset_soma_baseline(self) -> None:
        """Reset tonic bars to YAML initials, wipe noise fragments and affects, persist DB + soma directory."""
        self.tonic.reset_baseline()
        async with self.store.session() as conn:
            await self.tonic.save_state_db(conn)

    def poke_world(
        self,
        prompt: str,
        *,
        source: str = "external",
        weight: float = 0.6,
        ttl_seconds: float = 3600.0,
    ) -> None:
        """Inject an external cue into soma so GEN/wake can react."""
        self.tonic.poke_world(
            prompt=prompt,
            source=source,
            weight=weight,
            ttl_seconds=ttl_seconds,
        )

    async def sync_live_container(
        self,
        event_type: str,
        event_payload: dict[str, Any] | None = None,
    ) -> None:
        """Checkpoint derived runtime state when this entity is backed by an open container."""
        mount = self._live_container_mount
        if mount is not None:
            await mount.checkpoint(event_type, event_payload)

    async def shutdown(self) -> None:
        code_tasks = getattr(self, "_code_task_manager", None)
        if code_tasks is not None:
            await code_tasks.shutdown()
        try:
            self.tonic.save_state()
        except Exception:
            pass
        try:
            async with self.store.session() as conn:
                await self.tonic.save_state_db(conn)
        except Exception:
            pass
        mount = self._live_container_mount
        if mount is not None:
            try:
                await asyncio.shield(mount.close())
            except BaseException as exc:
                log.warning("live_container_close_failed", module="entity", error=str(exc))
        try:
            await asyncio.shield(self._mcp_hub.shutdown())
        except BaseException:
            pass
        try:
            await asyncio.shield(self.client.close())
        except BaseException:
            pass
        if self._embedding_client is not self.client:
            try:
                await asyncio.shield(self._embedding_client.close())
            except BaseException:
                pass
        try:
            await asyncio.shield(self.store.aclose())
        except BaseException:
            pass
