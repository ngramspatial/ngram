"""Internal drives: curiosity, connection, expression, autonomy, comfort."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from ngram.config import EntityConfig


@dataclass
class Drive:
    name: str
    level: float = 0.0
    growth_rate: float = 0.02
    decay_rate: float = 0.05
    threshold: float = 0.72


@dataclass
class DriveState:
    curiosity: Drive = field(default_factory=lambda: Drive("curiosity"))
    connection: Drive = field(default_factory=lambda: Drive("connection"))
    expression: Drive = field(default_factory=lambda: Drive("expression"))
    autonomy: Drive = field(default_factory=lambda: Drive("autonomy"))
    comfort: Drive = field(default_factory=lambda: Drive("comfort"))


class DriveSystem:
    def __init__(self, entity: EntityConfig) -> None:
        self.entity = entity
        t = entity.personality.core_traits
        self._d = DriveState()
        self._d.curiosity.growth_rate = 0.015 + 0.04 * t.get("curiosity", 0.5)
        self._d.connection.growth_rate = 0.012 + 0.03 * t.get("warmth", 0.5)
        self._d.expression.growth_rate = 0.01 + 0.035 * t.get("openness", 0.5)
        self._d.autonomy.growth_rate = 0.008 + 0.03 * t.get("assertiveness", 0.5)
        self._d.comfort.growth_rate = 0.01 + 0.04 * t.get("neuroticism", 0.3)
        self._last_initiative: float = time.time()

    def all_drives(self) -> list[Drive]:
        return [
            self._d.curiosity,
            self._d.connection,
            self._d.expression,
            self._d.autonomy,
            self._d.comfort,
        ]

    def recalibrate_rates_from_traits(self) -> None:
        t = self.entity.personality.core_traits
        self._d.curiosity.growth_rate = 0.015 + 0.04 * t.get("curiosity", 0.5)
        self._d.connection.growth_rate = 0.012 + 0.03 * t.get("warmth", 0.5)
        self._d.expression.growth_rate = 0.01 + 0.035 * t.get("openness", 0.5)
        self._d.autonomy.growth_rate = 0.008 + 0.03 * t.get("assertiveness", 0.5)
        self._d.comfort.growth_rate = 0.01 + 0.04 * t.get("neuroticism", 0.3)

    def reset_levels(self) -> None:
        for d in self.all_drives():
            d.level = 0.0
        self._last_initiative = time.time()

    def tick(self, silence_seconds: float = 0.0) -> list[Drive]:
        """Grow drives on heartbeat; return drives that crossed threshold."""
        crossed: list[Drive] = []
        # Silence grows connection / restlessness proxy
        if silence_seconds > 30:
            self._d.connection.level = min(1.0, self._d.connection.level + self._d.connection.growth_rate * 0.5)
        self._d.curiosity.level = min(1.0, self._d.curiosity.level + self._d.curiosity.growth_rate * 0.3)
        self._d.expression.level = min(1.0, self._d.expression.level + self._d.expression.growth_rate * 0.2)
        # Long idle (YAML ``restlessness_decay`` seconds): mind wants stimulation again
        try:
            rd = float(getattr(self.entity.drives, "restlessness_decay", 3600) or 3600)
        except (TypeError, ValueError):
            rd = 3600.0
        rd = max(60.0, rd)
        if silence_seconds > rd:
            self._d.curiosity.level = min(
                1.0,
                self._d.curiosity.level + self._d.curiosity.growth_rate * 0.55,
            )
            self._d.connection.level = min(
                1.0,
                self._d.connection.level + self._d.connection.growth_rate * 0.25,
            )
        for d in self.all_drives():
            if d.level >= d.threshold:
                crossed.append(d)
        return crossed

    def satisfy(self, name: str, amount: float = 0.45) -> None:
        for d in self.all_drives():
            if d.name == name:
                d.level = max(0.0, d.level - amount)
                break

    def on_interaction(self, meaningful: bool) -> None:
        if meaningful:
            self.satisfy("connection", 0.35)
            self.satisfy("expression", 0.2)
        self.satisfy("curiosity", 0.15)

    def register_initiative_time(self, now: float) -> None:
        self._last_initiative = now

    def can_initiate(self, now: float, cooldown: float) -> bool:
        return (now - self._last_initiative) >= cooldown
