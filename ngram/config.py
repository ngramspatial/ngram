"""Load and validate harness + entity YAML into typed config objects."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any

import yaml

from ngram.inference.factory import effective_inference_provider_name, inference_bearer_key_env


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def project_configs_dir() -> Path:
    """Single source: `<repo>/configs` (same directory that contains `default.yaml`)."""
    return _repo_root() / "configs"


def harness_default_path() -> Path:
    return project_configs_dir() / "default.yaml"


def _expand(path: str, entity_name: str) -> str:
    return str(Path(path.replace("{entity_name}", entity_name)).expanduser())


@dataclass
class DeploymentSettings:
    """Where the body runs: local workstation vs split Railway + home inference."""

    mode: str = "local"  # local | hybrid_railway


@dataclass
class InferenceSettings:
    """Brain endpoint selection (tunnel should terminate only at the inference gateway)."""

    provider: str = (
        ""  # local | remote_gateway | hosted preset | custom; empty derives from deployment.mode
    )
    base_url: str = ""  # remote gateway or OpenAI-compatible root; empty uses ollama.base_url
    api_key_env: str = "NGRAM_INFERENCE_GATEWAY_TOKEN"
    model: str = ""  # optional documentation default / operator hint
    timeout: float = 120.0
    # When True, chat requests include Ollama-style ``options.num_ctx`` from entity cognition.
    # Set False if your /v1/chat/completions server rejects unknown top-level fields.
    pass_num_ctx: bool = True


@dataclass
class AttachmentStorageSettings:
    """Blob storage for durable attachments (hybrid must not rely on container disk)."""

    backend: str = "local_disk"  # local_disk | object_s3_compat
    local_dir: str = "~/.ngram/entities/{entity_name}/attachments"
    # S3-compatible (optional)
    endpoint_url_env: str = "NGRAM_S3_ENDPOINT_URL"
    bucket_env: str = "NGRAM_S3_BUCKET"
    access_key_env: str = "NGRAM_S3_ACCESS_KEY"
    secret_key_env: str = "NGRAM_S3_SECRET_KEY"
    prefix: str = "ngram"


@dataclass
class OllamaSettings:
    base_url: str = "http://localhost:11434"
    timeout: float = 120.0
    retry_attempts: int = 3
    retry_delay: float = 2.0


@dataclass
class ModelSettings:
    reflex: str = "gemma4:26b"
    deliberate: str = "gemma4:26b"
    embedding: str = "nomic-embed-text"


@dataclass
class CognitionSettings:
    thinking_mode: bool = True
    temperature: float = 0.75
    reflex_max_tokens: int = 1024
    deliberate_max_tokens: int = 16384
    thinking_budget: int = 2048
    max_context_tokens: int = 16384
    escalation_threshold: float = 0.4
    image_token_budget: int = 280
    # After tool results, nudge the model up to this many times when it still sounds mid-task
    # but returned text instead of tool calls (0 disables).
    tool_continuation_rounds: int = 4


@dataclass
class IdentityHarnessSettings:
    emotion_decay_rate: float = 0.001
    drive_tick_interval: int = 120
    evolution_interval: int = 100
    narrative_interval: int = 500


@dataclass
class DistillationSettings:
    """Automatic experience extraction from conversations."""

    enabled: bool = True
    cycle_seconds: float = 300.0
    min_turns: int = 6
    min_turns_absolute: int = 4
    soma_urgency_divisor: float = 1.5
    max_extract_tokens: int = 800
    temperature: float = 0.15
    context_char_budget: int = 6000


@dataclass
class RelationalDocumentSettings:
    """LLM-maintained prose portraits per person; derived scalars sync to ``relationships``."""

    enabled: bool = True
    reflection_temperature: float = 0.4
    reflection_max_tokens: int = 800
    reflection_min_gap_seconds: float = 30.0
    score_derivation: bool = True
    flush_to_disk: bool = True
    max_context_tokens: int = 800
    deep_review_on_consolidation: bool = True
    amendment_mode_threshold: float = 0.3
    deep_review_min_new_interactions: int = 3
    deep_review_max_tokens: int = 1200
    gen_recent_hours: float = 24.0
    gen_tail_sentences: int = 3


@dataclass
class MemoryHarnessSettings:
    database_path: str = "~/.ngram/entities/{entity_name}/memory.db"
    """When set (e.g. postgresql://...), use Postgres instead of SQLite file path."""
    database_url: str = ""
    episode_significance_threshold: float = 0.3
    consolidation_interval: int = 7200
    memory_decay_rate: float = 0.0001
    max_recall_results: int = 10
    embedding_dimensions: int = 768
    narrative_every_n_consolidations: int = 3
    imprint_decay_half_life_seconds: float = 86400.0 * 30
    imprint_recall_weight: float = 0.35
    # Familiarity: decays toward floor between interactions (half-life in hours); bumps each turn.
    familiarity_decay_half_life_hours: float = 336.0
    familiarity_floor: float = 0.08
    familiarity_bump_meaningful: float = 0.017
    familiarity_bump_light: float = 0.007
    distillation: DistillationSettings = field(default_factory=DistillationSettings)
    relational: RelationalDocumentSettings = field(default_factory=RelationalDocumentSettings)


@dataclass
class PresenceHarnessSettings:
    heartbeat_interval: int = 120
    initiative_cooldown: int = 1800
    typing_speed_base: float = 30.0
    typing_speed_variance: float = 0.3
    message_chunk_max: int = 400
    chunk_delay: float = 2.0


@dataclass
class LoggingSettings:
    level: str = "INFO"
    file: str = "~/.ngram/entities/{entity_name}/ngram.log"
    format: str = "json"


@dataclass
class FirecrawlSettings:
    """Optional Firecrawl API (Bearer key in env). When set, preferred for fetch_url / search_web."""

    api_key_env: str = "FIRECRAWL_API_KEY"
    base_url: str = "https://api.firecrawl.dev/v1"
    prefer_for_fetch: bool = True
    prefer_for_search: bool = True


def default_soma_config() -> dict[str, Any]:
    return {
        "enabled": True,
        "bars": {
            "variables": [
                # decay_rate is now homeostatic: % of distance to resting point
                # restored per hour.  -40 means 40% of the gap closes each hour.
                {"name": "social", "initial": 45, "decay_rate": -40, "floor": 0, "ceiling": 100},
                {
                    "name": "curiosity",
                    "initial": 45,
                    "decay_rate": -22,
                    "decay_time_scale": 0.88,
                    "floor": 0,
                    "ceiling": 100,
                },
                {"name": "creative", "initial": 35, "decay_rate": -25, "floor": 0, "ceiling": 100},
                {"name": "tension", "initial": 10, "decay_rate": -50, "floor": 0, "ceiling": 100},
                {"name": "comfort", "initial": 60, "decay_rate": -15, "floor": 0, "ceiling": 100},
            ],
            "momentum_window": 6,
        },
        "coupling": [
            {"when": "social > 80", "effect": "curiosity.decay_rate *= 1.5"},
            {"when": "tension > 70", "effect": "comfort.decay_rate *= 2.0"},
            {"when": "curiosity > 82", "effect": "curiosity.decay_rate *= 1.25"},
        ],
        "event_effects": {
            # Values are scaled by headroom automatically (full-range saturation).
            # At resting point (~50%), a +6 gives roughly +3 effective.
            "message_received": {"social": 6},
            "message_sent": {"social": 4, "creative": 3},
            "action": {"curiosity": 1},
            "idle": {"social": -0.02, "curiosity": -0.008},
            "idle_cycle": {"comfort": 4, "tension": -3},
            "mood_declared": {"comfort": 2},
        },
        "impulses": [
            {
                "drive": "social",
                "threshold": 80,
                "type": "reach_out",
                "label": "reach_out",
                "cooldown_minutes": 30,
                "relief": {"social": -25},
            },
            {
                "drive": "curiosity",
                "threshold": 88,
                "type": "explore",
                "label": "explore_something",
                "cooldown_minutes": 20,
                "relief": {"curiosity": -22},
            },
        ],
        "conflicts": [
            {
                "drives": ["curiosity", "comfort"],
                "threshold": 70,
                "label": "restless comfort",
                "tension_per_tick": 0.5,
                "comfort_per_tick": -0.3,
            },
        ],
        "appraisal": {
            "enabled": True,
            "temperature": 0.3,
            "max_tokens": 160,
            # json: single JSON object (preferred); lines: legacy line format.
            "output_format": "json",
            "calibration_log": False,
        },
        "affect_cycle_seconds": 240,
        "noise": {
            "enabled": True,
            "model": "",
            "cycle_seconds": 90,
            "temperature": 0.88,
            "max_tokens": 240,
            "max_fragments": 8,
            # coherent GEN ticks in a row that keep thematic continuity (0 = off).
            "thematic_streak_max": 3,
            "semantic_dedup": False,
            "semantic_similarity_threshold": 0.82,
            "semantic_dedup_retries": 2,
        },
        "noise_seeder": {
            "enabled": False,
            "cycle_seconds": 90,
            "source_weights": {
                "episodic_random": 0.25,
                "belief_random": 0.10,
                "knowledge_random": 0.10,
                "world_discovery": 0.25,
                "relationship_echo": 0.10,
                "journal_echo": 0.10,
                "temporal": 0.10,
            },
            "world_discovery": {
                "concepts_path": "configs/noise_seeds/concepts.txt",
                "allow_external_fetch": False,
                "external_fetch_probability": 0.1,
            },
            "recency_suppression_window": 3,
            "max_resample_attempts": 3,
            "episodic_random": {"min_age_hours": 24},
            "belief_random": {"min_age_hours": 12},
            "relationship_echo": {"min_silence_hours": 48},
            "journal_echo": {"min_age_days": 3},
        },
        "wake_voice": {
            "enabled": True,
            "model": "",
            "temperature": 0.8,
            "max_tokens": 300,
        },
        "dreams": {
            "enabled": False,
            "model": "",
            "temperature": 1.15,
            "max_tokens": 500,
            "min_silence_seconds": 3600,
            "min_gap_seconds": 14400,
            "max_cycles_per_day": 2,
            "dream_hours": [0, 1, 2, 3, 4, 5],
            "memory_pair_count": 3,
            "min_time_gap_hours": 24,
            "max_similarity": 0.35,
            "write_journal": True,
            "write_beliefs": True,
            "belief_confidence": 0.35,
            "inject_noise": True,
            "max_noise_fragments": 2,
        },
        "ebb": {
            "enabled": True,
            "weights": {
                "bar_deviation": 0.38,
                "conflict": 0.22,
                "impulse": 0.18,
                "affect_load": 0.12,
                "noise_fill": 0.10,
            },
            "quiet_below": 0.30,
            "high_above": 0.58,
            "reflex_salience_scale": 0.75,
            "autonomous_minimum": "normal",
            "quiet_max_noise_lines": 1,
            "normal_max_noise_lines": 3,
            "high_max_noise_lines": 4,
            "skip_post_turn_noise_when_quiet": True,
            # Optional: calm | reactive | expressive — preset ebb weights/thresholds (overridable).
            "personality": "",
            "debug_salience": False,
        },
        "observability": {
            "metrics_log": False,
            "timeline_enabled": False,
            "timeline_filename": "soma_timeline.md",
            "timeline_max_bytes": 65536,
        },
    }


@dataclass
class SummonSettings:
    enabled: bool = True
    timeout_seconds: int = 30


@dataclass
class PokerPromptSettings:
    """Optional internal disposition prompts for autonomous cycles (not user-facing)."""

    enabled: bool = False
    time_weighted: bool = True
    # blend: time-weighted disposition + WakeVoice stirring; replace_wake_voice: disposition only (no LLM stirring)
    mode: str = "blend"
    prompts_path: str = ""  # empty = bundled configs/poker_prompts/default.yaml; else path under configs/ or absolute
    # Fuse deck seed with GEN (noise) fragments + soma context via a light LLM pass — emergent, not prescribed.
    ground_with_gen: bool = True
    grounding_model: str = ""  # empty uses reflex model
    grounding_temperature: float = 0.72
    grounding_max_tokens: int = 300


@dataclass
class AutonomySettings:
    """Autonomous wake cycle configuration."""

    enabled: bool = False
    min_cycle_gap_seconds: int = 600
    max_cycles_per_hour: int = 4
    messages_per_cycle: int = 2
    base_wake_interval_min: int = 20
    base_wake_interval_max: int = 45
    # Timer wakes only: after any wake, add up to this many extra minutes before the next timer
    # fire, scaled by how recently the previous wake started (small gap → more extra). Decays with
    # gap; see wake_spacing_gap_hours_tau. 0 disables.
    wake_spacing_extra_minutes_max: float = 45.0
    # E-folding time in hours for that extra spacing (larger = extra stays high longer as gaps grow).
    wake_spacing_gap_hours_tau: float = 3.0
    # Intent selection: Gaussian noise on per-bucket scores (0 = deterministic argmax).
    wake_entropy_score_noise: float = 0.28
    # Among top intents, softmax-sample with this temperature (0 = pick top only).
    wake_intent_softmax_temperature: float = 0.52
    # Rut avoidance: scan last N wake episodes for over-repeated words; demote sparks & boost novelty.
    wake_rut_episode_window: int = 14
    wake_rut_word_repeat_threshold: int = 3
    # Extra infer_desires() candidates (fears, novelty, contrarian pulls) for richer pressures.
    wake_entropy_desire_extras: int = 2
    # Random WakeVoice system-prompt style + pass variant into stirring generation.
    wake_voice_variant_roll: bool = True
    wake_voice_temperature_jitter: float = 0.12
    # Log/display only: append |flavor to reason for variety (semantic prefix unchanged before |).
    wake_reason_flavor: bool = True
    # Prompt blocks that discourage repeating last wake as new and narrating browsing without tools.
    wake_anti_groundhog_prompt: bool = True
    silence_threshold_seconds: int = 120
    impulse_wake: bool = True
    drive_wake: bool = True
    conflict_wake: bool = True
    noise_wake: bool = False
    # When noise_wake is on: require body salience and mature GEN (not fresh noise).
    noise_wake_min_salience: float = 0.44
    noise_wake_min_age_seconds: float = 90.0
    desire_wake: bool = True
    desire_wake_threshold: float = 0.72
    max_desires_considered: int = 3
    allow_tool_calls_on_wake: bool = True
    # Sustained wake: chain multiple full perceive() runs per trigger (multi-round “keep working”).
    # wake_session_max_rounds=1 preserves legacy single-pass behavior.
    wake_session_max_rounds: int = 1
    wake_session_wall_seconds: int = 1200
    wake_session_say_budget_per_round: int = 6
    wake_session_pause_seconds: float = 2.0
    # Extra agent-loop steps per round when wake_session_max_rounds > 1 (capped in deliberate.py).
    wake_session_extra_tool_steps: int = 10
    # Wider tool budget + prompt nudge — use for long exploratory wakes.
    wake_wide_mode: bool = False
    wake_wide_bonus_steps: int = 16
    # Italic status lines + typing indicator on supported platforms (Telegram, CLI whisper, etc.).
    wake_user_visible_status: bool = True
    # Multi-line human banner in worker logs (reason, soma, GEN, poker, delivery).
    wake_verbose_worker_log: bool = True
    # On-disk markdown transcript for autonomous sessions (see EntityConfig.autonomy_transcript_path).
    transcript_enabled: bool = True
    # Relative to workspace / journal dir when transcript_path is empty; see autonomy_transcript_path().
    transcript_filename: str = "autonomy_transcript.md"
    # Optional override: absolute path, or relative to workspace / ~/.ngram/entities/<name>/.
    transcript_path: str = ""
    # When False, tool activity during autonomous perceive goes to the transcript file only (not Telegram).
    wake_chat_tool_activity: bool = True
    summon: SummonSettings = field(default_factory=SummonSettings)
    poker_prompts: PokerPromptSettings = field(default_factory=PokerPromptSettings)


def default_tools_config() -> dict[str, Any]:
    return {
        "shell": {
            "enabled": True,
            "deny": ["rm -rf /", "sudo rm", "shutdown", "reboot", "mkfs", "dd if="],
            "timeout": 30,
        },
        "browser": {"enabled": False},
        "code": {"enabled": True, "timeout": 30},
        "imagegen": {
            "enabled": False,
            "backend": "fal",
            "fal_api_key_env": "FAL_API_KEY",
            "local_url": "",
        },
        "voice": {"enabled": True, "voice_id": "en-US-GuyNeural"},
        "youtube": {"enabled": True},
        "reddit": {"enabled": True},
        "pdf": {"enabled": True},
        "reminders": {"enabled": True},
        "messaging": {"enabled": True},
        "automations": {"enabled": True},
        "journal": {"enabled": True},
        "wikipedia": {"enabled": True},
        "weather": {"enabled": True},
        "news": {"enabled": True},
        "memory_search": {"enabled": True},
        "patch": {"enabled": True},
        "session_todos": {"enabled": True},
        "clarify": {"enabled": True},
        "delegation": {"enabled": True},
        "code_task": {"enabled": True},
        "system": {"enabled": True},
        # Shared dangerous-tool execution settings (RPC or container-local fallback when safe).
        "execution": {
            "base_url": "",
            "token_env": "NGRAM_EXECUTION_RPC_TOKEN",
            "timeout": 45,
            "allow_local": False,
            "require_railway": False,
            "workspace_dir": "",
            "rpc_path": "/rpc",
        },
    }


@dataclass
class HarnessConfig:
    deployment: DeploymentSettings = field(default_factory=DeploymentSettings)
    inference: InferenceSettings = field(default_factory=InferenceSettings)
    ollama: OllamaSettings = field(default_factory=OllamaSettings)
    models: ModelSettings = field(default_factory=ModelSettings)
    cognition: CognitionSettings = field(default_factory=CognitionSettings)
    identity: IdentityHarnessSettings = field(default_factory=IdentityHarnessSettings)
    memory: MemoryHarnessSettings = field(default_factory=MemoryHarnessSettings)
    presence: PresenceHarnessSettings = field(default_factory=PresenceHarnessSettings)
    logging: LoggingSettings = field(default_factory=LoggingSettings)
    firecrawl: FirecrawlSettings = field(default_factory=FirecrawlSettings)
    attachments: AttachmentStorageSettings = field(default_factory=AttachmentStorageSettings)
    tools: dict[str, Any] = field(default_factory=default_tools_config)
    soma: dict[str, Any] = field(default_factory=default_soma_config)
    autonomy: AutonomySettings = field(default_factory=AutonomySettings)


@dataclass
class EntityPersonality:
    core_traits: dict[str, float] = field(default_factory=dict)
    behavioral_patterns: dict[str, str] = field(default_factory=dict)
    voice: dict[str, Any] = field(default_factory=dict)
    backstory: str = ""


@dataclass
class EntityDrives:
    curiosity_topics: list[str] = field(default_factory=list)
    attachment_threshold: int = 5
    # Seconds of silence after which curiosity/connection tick harder (see DriveSystem.tick).
    restlessness_decay: int = 3600
    # Legacy: min seconds between proactive daemon messages when autonomy is off. When autonomy
    # is on, also floors the gap between autonomous wake cycles (with min_cycle_gap_seconds).
    initiative_cooldown: int = 1800


@dataclass
class HistoryCompressionSettings:
    """When rolling chat history exceeds ``rolling_history_max_messages``, oldest turns are dropped.
    If ``enabled``, those turns are merged into an in-memory summary prepended to model context.

    The ``compaction_*`` fields control the proactive pre-flight compactor that fires *before*
    inference when the estimated token count approaches ``max_context_tokens``.  The older
    message-count trim (``rolling_history_max_messages``) remains as a safety net.
    """

    enabled: bool = True
    summary_max_chars: int = 4500
    max_merge_input_chars: int = 12000
    merge_max_tokens: int = 900
    format_per_message_chars: int = 2200

    # --- proactive context compaction ---
    compaction_threshold_ratio: float = 0.6
    compaction_target_ratio: float = 0.08
    compaction_protect_last_n: int = 12
    compaction_protect_first_n: int = 2
    compaction_max_passes: int = 3
    compaction_flush_to_knowledge: bool = True


@dataclass
class EntityCognition:
    reflex_model: str = ""
    deliberate_model: str = ""
    always_deliberate: bool = False
    fast_deliberate_mode: bool = False
    deliberate_max_tokens: int = 0  # 0 => use harness.cognition.deliberate_max_tokens
    system_prompt_char_limit: int = 12000
    rolling_history_max_messages: int = 200
    knowledge_recent_turns: int = 10
    history_message_char_limit: int = 4000
    thinking_mode: bool = True
    temperature: float = 0.75
    max_context_tokens: int = 16384
    thinking_budget: int = 4096
    history_compression: HistoryCompressionSettings = field(
        default_factory=HistoryCompressionSettings
    )
    # Visible mid-turn say() calls allowed for one user directive.
    message_budget_per_turn: int = 12
    # 0 = use harness.cognition.tool_continuation_rounds
    tool_continuation_rounds: int = 0
    # When False, omit OpenAI-style ``tools`` from chat/completions (non-tool-capable local models).
    use_api_tools: bool = True

    def ollama_num_ctx(self) -> int | None:
        n = int(self.max_context_tokens or 0)
        return n if n > 0 else None


@dataclass
class AutomationsEmergenceSettings:
    enabled: bool = True
    analysis_interval: int = 7200
    max_suggestions: int = 3


@dataclass
class AutomationsJournalSettings:
    enabled: bool = True
    max_entries: int = 1000


@dataclass
class EntityAutomationsSettings:
    enabled: bool = True
    max_automations: int = 50
    max_failures: int = 5
    emergence: AutomationsEmergenceSettings = field(default_factory=AutomationsEmergenceSettings)
    journal: AutomationsJournalSettings = field(default_factory=AutomationsJournalSettings)


@dataclass
class EntityPresence:
    platforms: list[dict[str, Any]] = field(default_factory=list)
    daemon: dict[str, int] = field(default_factory=dict)
    tool_activity: bool = True


@dataclass
class EntityConfig:
    name: str
    harness: HarnessConfig
    personality: EntityPersonality
    drives: EntityDrives
    cognition: EntityCognition
    presence: EntityPresence
    automations: EntityAutomationsSettings = field(default_factory=EntityAutomationsSettings)
    raw: dict[str, Any] = field(default_factory=dict)
    container_root: str = ""
    runtime_cache_dir: str = ""

    def is_live_container(self) -> bool:
        return bool(self.container_root and self.runtime_cache_dir)

    def _runtime_cache_path(self, name: str) -> str:
        if not self.runtime_cache_dir:
            return ""
        return str(Path(self.runtime_cache_dir).expanduser() / name)

    def _merged_tools(self) -> dict[str, Any]:
        """Harness ``tools`` merged with entity YAML ``tools`` (same rules as execution tools)."""
        base = dict(self.harness.tools) if self.harness.tools else {}
        over = self.raw.get("tools")
        if not isinstance(over, dict) or not over:
            return base
        return _merge_dict(base, over)

    def execution_workspace_dir(self) -> str:
        """
        Root directory for ``read_file`` / ``send_file`` / workspace tools — must match
        ``knowledge_path`` / ``journal_path`` / ``soma_dir`` when a persistent volume is used.

        Order matches ``execution_rpc._configured_workspace_root``: env first, then
        merged ``tools.execution.workspace_dir`` (from ``apply_harness_env_overrides`` + YAML).
        """
        ws = (os.environ.get("NGRAM_EXECUTION_WORKSPACE_DIR") or "").strip()
        if ws:
            return ws
        merged = self._merged_tools()
        ex = merged.get("execution") if isinstance(merged.get("execution"), dict) else {}
        return str(ex.get("workspace_dir") or "").strip()

    def db_path(self) -> str:
        if self.runtime_cache_dir:
            return self._runtime_cache_path("memory.db")
        return _expand(self.harness.memory.database_path, self.name)

    def database_url(self) -> str:
        """Effective DB URL: DATABASE_URL env, then harness memory.database_url, else empty (SQLite file)."""
        if self.is_live_container():
            return ""
        env_url = (os.environ.get("DATABASE_URL") or "").strip()
        if env_url:
            return env_url
        return (self.harness.memory.database_url or "").strip()

    def knowledge_path(self) -> str:
        """Curated knowledge markdown: persistent volume when available, else beside DB file."""
        if self.runtime_cache_dir:
            return self._runtime_cache_path("knowledge.md")
        ws = self.execution_workspace_dir().strip()
        if ws:
            return str(Path(ws) / "knowledge.md")
        if self.database_url():
            return _expand("~/.ngram/entities/{entity_name}/knowledge.md", self.name)
        return str(Path(self.db_path()).expanduser().parent / "knowledge.md")

    def log_path(self) -> str:
        if self.runtime_cache_dir:
            return self._runtime_cache_path("ngram.log")
        return _expand(self.harness.logging.file, self.name)

    def effective_ollama_num_ctx(self) -> int | None:
        """Ollama OpenAI-compat ``options.num_ctx`` when enabled and entity sets a positive window."""
        if not self.harness.inference.pass_num_ctx:
            return None
        return self.cognition.ollama_num_ctx()

    def effective_context_tokens(self) -> int:
        """Working budget follows the active brain, without resizing local KV caches."""
        from ngram.inference.factory import effective_inference_provider_name

        model = self.effective_deliberate_model().lower()
        if effective_inference_provider_name(self.harness) == "openai" and (
            model == "gpt-6-astra" or model.startswith("gpt-6-astra-")
        ):
            # Deliberately below Astra's 272K long-context pricing threshold.
            # This is an application working budget, not the model's 1.05M limit.
            return 256_000
        return max(2048, int(self.cognition.max_context_tokens or 16384))

    def effective_deliberate_model(self) -> str:
        """Deliberate chat model: entity ``cognition.deliberate_model``, else harness default."""
        d = (self.cognition.deliberate_model or "").strip()
        return d or (self.harness.models.deliberate or "").strip()

    def journal_path(self) -> str:
        if self.runtime_cache_dir:
            return self._runtime_cache_path("journal.md")
        ws = self.execution_workspace_dir().strip()
        if ws:
            return str(Path(ws) / "journal.md")
        return _expand("~/.ngram/entities/{entity_name}/journal.md", self.name)

    def autonomy_transcript_path(self) -> str:
        """Markdown file for full autonomous wake/tool detail (keeps Telegram quiet when configured)."""
        if self.runtime_cache_dir:
            return self._runtime_cache_path("autonomy_transcript.md")
        auto = self.harness.autonomy
        custom = (getattr(auto, "transcript_path", None) or "").strip()
        fn = (getattr(auto, "transcript_filename", None) or "autonomy_transcript.md").strip()
        if not fn:
            fn = "autonomy_transcript.md"
        if custom:
            p = Path(custom)
            if p.is_absolute():
                return str(p.expanduser())
            ws = self.execution_workspace_dir().strip()
            if ws:
                return str(Path(ws) / custom)
            return _expand(f"~/.ngram/entities/{{entity_name}}/{custom}", self.name)
        ws = self.execution_workspace_dir().strip()
        if ws:
            return str(Path(ws) / fn)
        return str(Path(self.journal_path()).expanduser().parent / fn)

    def skills_dir(self) -> str:
        if self.runtime_cache_dir:
            return self._runtime_cache_path("skills")
        return _expand("~/.ngram/entities/{entity_name}/skills", self.name)

    def projects_path(self) -> str:
        if self.runtime_cache_dir:
            return self._runtime_cache_path("projects.json")
        return _expand("~/.ngram/entities/{entity_name}/projects.json", self.name)

    def session_todos_path(self) -> str:
        """Short-horizon session checklist JSON (distinct from projects.json)."""
        if self.runtime_cache_dir:
            return self._runtime_cache_path("session_todos.json")
        ws = self.execution_workspace_dir().strip()
        if ws:
            return str(Path(ws) / "session_todos.json")
        return _expand("~/.ngram/entities/{entity_name}/session_todos.json", self.name)

    def self_model_path(self) -> str:
        if self.runtime_cache_dir:
            return self._runtime_cache_path("self_model.json")
        return _expand("~/.ngram/entities/{entity_name}/self_model.json", self.name)

    def soma_dir(self) -> str:
        if self.runtime_cache_dir:
            return self._runtime_cache_path("soma")
        ws = self.execution_workspace_dir().strip()
        if ws:
            return str(Path(ws) / "soma")
        return _expand("~/.ngram/entities/{entity_name}/soma", self.name)

    def relationships_dir(self) -> str:
        """Filesystem mirror for per-person relationship documents (markdown)."""
        if self.runtime_cache_dir:
            return self._runtime_cache_path("relationships")
        ws = self.execution_workspace_dir().strip()
        if ws:
            return str(Path(ws) / "relationships")
        return _expand("~/.ngram/entities/{entity_name}/relationships", self.name)


def _merge_dict(base: dict, override: dict) -> dict:
    out = {**base}
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge_dict(out[k], v)
        else:
            out[k] = v
    return out


def _merge_autonomy_from_entity_yaml(
    base: AutonomySettings, over: dict[str, Any]
) -> AutonomySettings:
    """Merge entity YAML ``autonomy:`` (top-level) onto harness defaults."""
    d = dict(over)
    summon_raw = d.pop("summon", None) or {}
    poker_raw = d.pop("poker_prompts", None) or {}
    kw: dict[str, Any] = {}
    for f in fields(AutonomySettings):
        name = f.name
        if name == "summon":
            kw[name] = SummonSettings(**{**base.summon.__dict__, **summon_raw})
        elif name == "poker_prompts":
            kw[name] = PokerPromptSettings(**{**base.poker_prompts.__dict__, **poker_raw})
        elif name in d:
            kw[name] = d[name]
        else:
            kw[name] = getattr(base, name)
    return AutonomySettings(**kw)


def _dict_to_harness(d: dict[str, Any]) -> HarnessConfig:
    fc_raw = {**FirecrawlSettings().__dict__, **(d.get("firecrawl") or {})}
    for bkey in ("prefer_for_fetch", "prefer_for_search"):
        if bkey in fc_raw:
            fc_raw[bkey] = bool(fc_raw[bkey])
    att_raw = {**AttachmentStorageSettings().__dict__, **(d.get("attachments") or {})}
    mem_raw = {**MemoryHarnessSettings().__dict__, **(d.get("memory") or {})}
    dist_sub = mem_raw.pop("distillation", None)
    if isinstance(dist_sub, dict):
        mem_raw["distillation"] = DistillationSettings(
            **{**DistillationSettings().__dict__, **dist_sub}
        )
    elif not isinstance(dist_sub, DistillationSettings):
        mem_raw["distillation"] = DistillationSettings()
    rel_sub = mem_raw.pop("relational", None)
    if isinstance(rel_sub, dict):
        mem_raw["relational"] = RelationalDocumentSettings(
            **{**RelationalDocumentSettings().__dict__, **rel_sub}
        )
    elif not isinstance(rel_sub, RelationalDocumentSettings):
        mem_raw["relational"] = RelationalDocumentSettings()
    tools_raw = _merge_dict(default_tools_config(), d.get("tools") or {})
    soma_raw = _merge_dict(default_soma_config(), d.get("soma") or {})
    auto_raw = dict(d.get("autonomy") or {})
    summon_raw = auto_raw.pop("summon", None) or {}
    poker_raw = auto_raw.pop("poker_prompts", None) or {}
    autonomy = AutonomySettings(
        **{
            **AutonomySettings().__dict__,
            "summon": SummonSettings(**{**SummonSettings().__dict__, **summon_raw}),
            "poker_prompts": PokerPromptSettings(**{**PokerPromptSettings().__dict__, **poker_raw}),
            **{k: v for k, v in auto_raw.items() if k not in ("summon", "poker_prompts")},
        },
    )
    return HarnessConfig(
        deployment=DeploymentSettings(
            **{**DeploymentSettings().__dict__, **(d.get("deployment") or {})}
        ),
        inference=InferenceSettings(
            **{**InferenceSettings().__dict__, **(d.get("inference") or {})}
        ),
        ollama=OllamaSettings(**{**OllamaSettings().__dict__, **(d.get("ollama") or {})}),
        models=ModelSettings(**{**ModelSettings().__dict__, **(d.get("models") or {})}),
        cognition=CognitionSettings(
            **{**CognitionSettings().__dict__, **(d.get("cognition") or {})}
        ),
        identity=IdentityHarnessSettings(
            **{**IdentityHarnessSettings().__dict__, **(d.get("identity") or {})}
        ),
        memory=MemoryHarnessSettings(**mem_raw),
        presence=PresenceHarnessSettings(
            **{**PresenceHarnessSettings().__dict__, **(d.get("presence") or {})}
        ),
        logging=LoggingSettings(**{**LoggingSettings().__dict__, **(d.get("logging") or {})}),
        firecrawl=FirecrawlSettings(**fc_raw),
        attachments=AttachmentStorageSettings(**att_raw),
        tools=tools_raw if isinstance(tools_raw, dict) else default_tools_config(),
        soma=soma_raw if isinstance(soma_raw, dict) else default_soma_config(),
        autonomy=autonomy,
    )


def apply_harness_env_overrides(h: HarnessConfig) -> None:
    """Railway-friendly env overrides (see .env.example)."""
    dm = (os.environ.get("NGRAM_DEPLOYMENT_MODE") or "").strip().lower()
    if dm in ("local", "hybrid_railway"):
        h.deployment.mode = dm
    ip = (os.environ.get("NGRAM_INFERENCE_PROVIDER") or "").strip().lower()
    from ngram.inference.factory import INFERENCE_PROVIDERS

    if ip in INFERENCE_PROVIDERS:
        h.inference.provider = ip
    ib = (os.environ.get("NGRAM_INFERENCE_BASE_URL") or "").strip()
    if ib:
        h.inference.base_url = ib.rstrip("/")
    oh = (os.environ.get("OLLAMA_HOST") or "").strip()
    if oh and not ib:
        h.ollama.base_url = oh.rstrip("/")
    ike = (os.environ.get("NGRAM_INFERENCE_API_KEY_ENV") or "").strip()
    if ike:
        h.inference.api_key_env = ike
    ito = (os.environ.get("NGRAM_INFERENCE_TIMEOUT") or "").strip()
    if ito:
        try:
            h.inference.timeout = float(ito)
        except ValueError:
            pass
    inpnc = (os.environ.get("NGRAM_INFERENCE_PASS_NUM_CTX") or "").strip().lower()
    if inpnc in ("0", "false", "no", "off"):
        h.inference.pass_num_ctx = False
    elif inpnc in ("1", "true", "yes", "on"):
        h.inference.pass_num_ctx = True
    ilm = (os.environ.get("NGRAM_INFERENCE_MODEL") or "").strip()
    if ilm:
        h.inference.model = ilm
        h.models.reflex = ilm
        h.models.deliberate = ilm
    iem = (os.environ.get("NGRAM_EMBEDDING_MODEL") or "").strip()
    if iem:
        h.models.embedding = iem
    elif ip == "openai" and h.models.embedding == ModelSettings().embedding:
        # A fully hosted OpenAI profile must not silently ask its API for the
        # local Ollama embedding model.
        from ngram.inference.factory import default_embedding_model

        h.models.embedding = default_embedding_model(ip)
    ied = (os.environ.get("NGRAM_EMBEDDING_DIMENSIONS") or "").strip()
    if ied:
        try:
            dimensions = int(ied)
        except ValueError:
            pass
        else:
            if dimensions > 0:
                h.memory.embedding_dimensions = dimensions
    att = (os.environ.get("NGRAM_ATTACHMENTS_BACKEND") or "").strip().lower()
    if att in ("local_disk", "object_s3_compat"):
        h.attachments.backend = att
    ewd = (os.environ.get("NGRAM_EXECUTION_WORKSPACE_DIR") or "").strip()
    if ewd:
        exec_cfg = h.tools.get("execution")
        if isinstance(exec_cfg, dict):
            exec_cfg["workspace_dir"] = ewd


def load_harness_config(path: Path | None = None) -> HarnessConfig:
    p = path if path is not None else harness_default_path()
    if not p.exists():
        h = HarnessConfig()
        apply_harness_env_overrides(h)
        return h
    with open(p, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    h = _dict_to_harness(data)
    apply_harness_env_overrides(h)
    return h


def load_entity_yaml(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def resolve_firecrawl_settings(
    harness: HarnessConfig,
    entity_raw: dict[str, Any],
) -> FirecrawlSettings:
    """Merge harness defaults with optional top-level ``firecrawl:`` on the entity YAML."""
    fc = harness.firecrawl
    o = entity_raw.get("firecrawl") or {}
    if not o:
        return fc
    merged = {
        "api_key_env": str(o.get("api_key_env", fc.api_key_env)),
        "base_url": str(o.get("base_url", fc.base_url)).rstrip("/"),
        "prefer_for_fetch": bool(o.get("prefer_for_fetch", fc.prefer_for_fetch)),
        "prefer_for_search": bool(o.get("prefer_for_search", fc.prefer_for_search)),
    }
    return FirecrawlSettings(**merged)


def validate_traits(traits: dict[str, float]) -> None:
    for k, v in traits.items():
        if not isinstance(v, (int, float)) or not (0.0 <= float(v) <= 1.0):
            raise ValueError(f"Trait {k} must be in [0,1], got {v}")


def entity_from_dict(harness: HarnessConfig, data: dict[str, Any]) -> EntityConfig:
    name = data.get("name")
    if not name or not isinstance(name, str):
        raise ValueError("Entity requires string 'name'")
    pers = data.get("personality") or {}
    traits = (pers.get("core_traits") or {}) if isinstance(pers, dict) else {}
    validate_traits(traits)
    personality = EntityPersonality(
        core_traits=traits,
        behavioral_patterns=dict(pers.get("behavioral_patterns") or {}),
        voice=dict(pers.get("voice") or {}),
        backstory=str(pers.get("backstory") or ""),
    )
    dr = data.get("drives") or {}
    drives = EntityDrives(
        curiosity_topics=list(dr.get("curiosity_topics") or []),
        attachment_threshold=int(dr.get("attachment_threshold", 5)),
        restlessness_decay=int(dr.get("restlessness_decay", 3600)),
        initiative_cooldown=int(dr.get("initiative_cooldown", 1800)),
    )
    cog = data.get("cognition") or {}
    hc_raw = cog.get("history_compression")
    if isinstance(hc_raw, dict):
        history_compression = HistoryCompressionSettings(
            enabled=bool(hc_raw.get("enabled", True)),
            summary_max_chars=max(500, int(hc_raw.get("summary_max_chars", 4500) or 4500)),
            max_merge_input_chars=max(
                2000, int(hc_raw.get("max_merge_input_chars", 12000) or 12000)
            ),
            merge_max_tokens=max(200, int(hc_raw.get("merge_max_tokens", 900) or 900)),
            format_per_message_chars=max(
                400, int(hc_raw.get("format_per_message_chars", 2200) or 2200)
            ),
            compaction_threshold_ratio=max(
                0.10, min(0.95, float(hc_raw.get("compaction_threshold_ratio", 0.6) or 0.6))
            ),
            compaction_target_ratio=max(
                0.05, min(0.80, float(hc_raw.get("compaction_target_ratio", 0.08) or 0.08))
            ),
            compaction_protect_last_n=max(
                4, int(hc_raw.get("compaction_protect_last_n", 12) or 12)
            ),
            compaction_protect_first_n=max(
                0, int(hc_raw.get("compaction_protect_first_n", 2) or 2)
            ),
            compaction_max_passes=max(1, min(5, int(hc_raw.get("compaction_max_passes", 3) or 3))),
            compaction_flush_to_knowledge=bool(hc_raw.get("compaction_flush_to_knowledge", True)),
        )
    else:
        history_compression = HistoryCompressionSettings()
    runtime_model = (os.environ.get("NGRAM_INFERENCE_MODEL") or "").strip()
    ec = EntityCognition(
        reflex_model=str(runtime_model or cog.get("reflex_model") or harness.models.reflex),
        deliberate_model=str(
            runtime_model or cog.get("deliberate_model") or harness.models.deliberate
        ),
        always_deliberate=bool(cog.get("always_deliberate", False)),
        fast_deliberate_mode=bool(cog.get("fast_deliberate_mode", False)),
        deliberate_max_tokens=max(0, int(cog.get("deliberate_max_tokens", 0) or 0)),
        system_prompt_char_limit=max(
            2000, int(cog.get("system_prompt_char_limit", 12000) or 12000)
        ),
        rolling_history_max_messages=max(
            8, int(cog.get("rolling_history_max_messages", 200) or 200)
        ),
        knowledge_recent_turns=max(2, int(cog.get("knowledge_recent_turns", 10) or 10)),
        history_message_char_limit=max(
            600, int(cog.get("history_message_char_limit", 4000) or 4000)
        ),
        thinking_mode=bool(cog.get("thinking_mode", harness.cognition.thinking_mode)),
        temperature=float(cog.get("temperature", harness.cognition.temperature)),
        max_context_tokens=int(cog.get("max_context_tokens", harness.cognition.max_context_tokens)),
        thinking_budget=int(cog.get("thinking_budget", harness.cognition.thinking_budget)),
        history_compression=history_compression,
        message_budget_per_turn=max(1, min(128, int(cog.get("message_budget_per_turn", 12) or 12))),
        tool_continuation_rounds=max(0, min(64, int(cog.get("tool_continuation_rounds", 0) or 0))),
        use_api_tools=bool(cog.get("use_api_tools", True)),
    )
    pr = data.get("presence") or {}
    presence = EntityPresence(
        platforms=list(pr.get("platforms") or [{"type": "cli"}]),
        daemon=dict(pr.get("daemon") or {}),
        tool_activity=bool(pr.get("tool_activity", True)),
    )
    au = data.get("automations") or {}
    em = au.get("emergence") or {}
    jr = au.get("journal") or {}
    automations = EntityAutomationsSettings(
        enabled=bool(au.get("enabled", True)),
        max_automations=max(1, int(au.get("max_automations", 50) or 50)),
        max_failures=max(1, int(au.get("max_failures", 5) or 5)),
        emergence=AutomationsEmergenceSettings(
            enabled=bool(em.get("enabled", True)),
            analysis_interval=max(60, int(em.get("analysis_interval", 7200) or 7200)),
            max_suggestions=max(1, min(10, int(em.get("max_suggestions", 3) or 3))),
        ),
        journal=AutomationsJournalSettings(
            enabled=bool(jr.get("enabled", True)),
            max_entries=max(10, int(jr.get("max_entries", 1000) or 1000)),
        ),
    )
    harness_eff = harness
    auto_over = data.get("autonomy")
    if isinstance(auto_over, dict) and auto_over:
        harness_eff = replace(
            harness,
            autonomy=_merge_autonomy_from_entity_yaml(harness.autonomy, auto_over),
        )
    return EntityConfig(
        name=name,
        harness=harness_eff,
        personality=personality,
        drives=drives,
        cognition=ec,
        presence=presence,
        automations=automations,
        raw=data,
    )


def load_live_container_config(
    container_path: str | Path,
    harness: HarnessConfig | None = None,
    *,
    verify: bool = True,
) -> EntityConfig:
    """Build runtime config for an open container, optionally during explicit recovery."""
    from ngram.container import (
        ContainerError,
        load_manifest,
        runtime_config_from_container,
        verify_container,
    )

    h = harness or load_harness_config()
    root = Path(str(container_path)).expanduser().resolve()
    if not root.is_dir() or not (root / "manifest.json").is_file():
        raise ContainerError(f"open ngram container not found: {root}")
    if verify:
        report = verify_container(root)
        if not report.ok:
            raise ContainerError(
                "live container failed verification; run 'ngram recover <path>' only if this "
                "followed an interrupted local session: " + "; ".join(report.errors)
            )
    manifest = load_manifest(root)
    data = runtime_config_from_container(root, manifest)
    config = entity_from_dict(h, data)
    cache_base = Path(
        (os.environ.get("NGRAM_RUNTIME_CACHE_DIR") or "~/.ngram/runtime").strip()
    ).expanduser()
    entity_id = str(manifest.get("entity_id") or "").replace(":", "-")
    if not entity_id:
        raise ContainerError("live container manifest has no entity_id")
    config.container_root = str(root)
    config.runtime_cache_dir = str(cache_base / entity_id / "state")
    return config


def _resolve_named_entity_path(entity_name: str | Path) -> Path:
    entities_dir = project_configs_dir() / "entities"
    requested_filename = f"{entity_name}.yaml"
    if entities_dir.is_dir():
        matches = [
            path
            for path in entities_dir.iterdir()
            if path.is_file() and path.name.casefold() == requested_filename.casefold()
        ]
        if len(matches) == 1:
            return matches[0]
        elif len(matches) > 1:
            exact_matches = [path for path in matches if path.name == requested_filename]
            if len(exact_matches) == 1:
                return exact_matches[0]
            raise ValueError(
                f"Entity name is ambiguous on this filesystem: {entity_name}"
            )

    ent_path = entities_dir / requested_filename
    raise FileNotFoundError(f"Entity file not found: {ent_path}")


def load_entity_config(
    entity_name: str | Path, harness: HarnessConfig | None = None
) -> EntityConfig:
    h = harness or load_harness_config()
    candidate = Path(str(entity_name)).expanduser()
    if candidate.is_dir() and (candidate / "manifest.json").is_file():
        return load_live_container_config(candidate, h)
    if candidate.is_file() and candidate.suffix.lower() == ".ngram":
        raise ValueError(
            "runtime commands require an open .ngram directory; run 'ngram open' first"
        )
    ent_path = _resolve_named_entity_path(entity_name)
    merged = load_entity_yaml(ent_path)
    return entity_from_dict(h, merged)


def validate_entity_env(entity: EntityConfig) -> list[str]:
    """Return list of warnings if platform env vars missing."""
    warnings: list[str] = []
    mode = (entity.harness.deployment.mode or "local").strip().lower()
    eff = effective_inference_provider_name(entity.harness)
    if eff != "local":
        envk = inference_bearer_key_env(entity.harness)
        if not (os.environ.get(envk) or "").strip():
            hint = "home inference gateway" if eff == "remote_gateway" else f"{eff} API"
            warnings.append(f"Remote inference ({eff}): set {envk} (bearer token for the {hint})")
        if eff == "remote_gateway" and mode == "hybrid_railway":
            if not (entity.harness.inference.base_url or "").strip():
                warnings.append(
                    "Hybrid Railway: set inference.base_url or NGRAM_INFERENCE_BASE_URL to the tunneled gateway URL"
                )
    if mode == "hybrid_railway" and not entity.database_url():
        warnings.append(
            "Hybrid Railway: set DATABASE_URL (Postgres) — container-local SQLite is not durable"
        )
    if mode == "hybrid_railway" and entity.harness.attachments.backend == "local_disk":
        warnings.append(
            "Hybrid Railway: prefer attachments.backend: object_s3_compat with S3 env vars — local_disk is not durable on Railway disks"
        )
    for pl in entity.presence.platforms:
        t = (pl.get("type") or "").lower()
        if t == "discord":
            envk = pl.get("token_env", "DISCORD_TOKEN")
            if not os.environ.get(envk):
                warnings.append(f"Discord platform: env {envk} not set")
        if t == "telegram":
            envk = pl.get("token_env", "TELEGRAM_TOKEN")
            if not os.environ.get(envk):
                warnings.append(f"Telegram platform: env {envk} not set")
    fc = resolve_firecrawl_settings(entity.harness, entity.raw)
    if (fc.prefer_for_fetch or fc.prefer_for_search) and not (
        os.environ.get(fc.api_key_env) or ""
    ).strip():
        warnings.append(
            f"Firecrawl: env {fc.api_key_env} not set — fetch_url/search_web fall back to ddgs/aiohttp"
        )
    return warnings


async def validate_ollama_models(entity: EntityConfig, provider: Any) -> tuple[bool, list[str]]:
    """Require chat models on the inference backend. Embedding is optional."""
    fn = getattr(provider, "ensure_models", None)
    if not callable(fn):
        return True, []
    # Keep turn startup responsive when inference is down: model listing can
    # inherit long transport retries/timeouts from providers.
    try:
        ok, missing = await asyncio.wait_for(
            fn(
                entity.cognition.reflex_model,
                entity.cognition.deliberate_model,
            ),
            timeout=8.0,
        )
    except TimeoutError:
        return False, ["inference_check_timeout"]
    return ok, missing
