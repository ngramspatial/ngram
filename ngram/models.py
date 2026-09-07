"""Core datatypes for identity, memory, and perception."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class EmotionCategory(str, Enum):
    CONTENT = "content"
    CURIOUS = "curious"
    EXCITED = "excited"
    AMUSED = "amused"
    AFFECTIONATE = "affectionate"
    CONCERNED = "concerned"
    MELANCHOLY = "melancholy"
    FRUSTRATED = "frustrated"
    ANXIOUS = "anxious"
    WITHDRAWN = "withdrawn"
    RESTLESS = "restless"
    NEUTRAL = "neutral"
    DORMANT = "dormant"


@dataclass
class EmotionalState:
    primary: EmotionCategory = EmotionCategory.NEUTRAL
    intensity: float = 0.5
    secondary: Optional[EmotionCategory] = None
    secondary_intensity: float = 0.0
    last_transition: float = field(default_factory=time.time)
    stability: float = 0.7

    def decay_toward_baseline(self, baseline: EmotionCategory, rate: float) -> None:
        elapsed = time.time() - self.last_transition
        decay = min(1.0, elapsed * rate)
        self.intensity = max(0.1, self.intensity * (1.0 - decay))
        if self.intensity < 0.15:
            self.primary = baseline
            self.intensity = 0.3


@dataclass
class ImprintRecord:
    id: str
    episode_id: str
    emotion: str
    intensity: float
    trigger: str


@dataclass
class Episode:
    id: str
    timestamp: float
    summary: str
    participants: list[str]
    emotional_imprint: EmotionCategory
    emotional_intensity: float
    significance: float
    tags: list[str]
    raw_context: Optional[str] = None
    self_reflection: Optional[str] = None
    embedding: Optional[list[float]] = None


@dataclass
class Relationship:
    person_id: str
    name: str
    first_met: float
    last_interaction: float
    interaction_count: int
    familiarity: float
    warmth: float
    trust: float
    dynamic: str
    notes: list[str]
    topics_shared: list[str]
    unresolved: list[str]


@dataclass
class Input:
    """Normalized stimulus from any platform."""

    text: str
    person_id: str
    person_name: str
    channel: str = "cli"
    platform: str = "cli"
    images: list[dict[str, Any]] = field(default_factory=list)
    audio: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def is_group_like_chat(inp: Input) -> bool:
    """True when multiple humans can post in the same channel (prefix speaker in model context)."""
    ct = str(inp.metadata.get("chat_type") or "").lower()
    if inp.platform == "telegram" and ct in ("group", "supergroup"):
        return True
    if inp.platform == "discord" and ct == "guild":
        return True
    return False


def speaker_label_for_model(inp: Input) -> str:
    """Prefix for user-visible lines in group-like chats so the model can tell speakers apart."""
    if not is_group_like_chat(inp):
        return ""
    name = (inp.person_name or "someone").strip()
    pid = (inp.person_id or "?").strip()
    ch = str(inp.metadata.get("channel_name") or "").strip()
    if inp.platform == "discord" and ch:
        return f"[{name} · id {pid} · #{ch}] "
    return f"[{name} · id {pid}] "


@dataclass
class ChatMessage:
    role: str
    content: str
    tool_calls: Optional[list[dict[str, Any]]] = None
    tool_responses: Optional[list[dict[str, Any]]] = None
    thinking: Optional[str] = None


def new_id(prefix: str = "") -> str:
    u = uuid.uuid4().hex[:12]
    return f"{prefix}{u}" if prefix else u
