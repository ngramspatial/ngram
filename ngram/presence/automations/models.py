from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class AutomationOrigin(Enum):
    USER = "user"
    SELF = "self"
    INTERNAL = "internal"


class AutomationPriority(Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


@dataclass
class Automation:
    id: str = field(default_factory=lambda: f"auto_{uuid.uuid4().hex[:8]}")
    name: str = ""
    description: str = ""
    schedule_natural: str = ""
    cron_expression: str = ""
    origin: AutomationOrigin = AutomationOrigin.USER
    created_by: str = ""
    created_at: float = field(default_factory=time.time)
    deliver_to: Optional[str] = None
    deliver_platform: Optional[str] = None
    tools_hint: list[str] = field(default_factory=list)
    enabled: bool = True
    last_run: Optional[float] = None
    last_result_summary: Optional[str] = None
    next_run: Optional[float] = None
    run_count: int = 0
    context: str = ""
    priority: AutomationPriority = AutomationPriority.NORMAL
    consecutive_failures: int = 0
    max_failures: int = 5
    self_destruct_condition: Optional[str] = None
    tags: list[str] = field(default_factory=list)
