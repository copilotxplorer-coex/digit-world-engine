"""Prototype tuning values, not final economy/balance rules."""
from dataclasses import dataclass, fields
import json
import math

@dataclass(frozen=True)
class WorldConfig:
    energy_max: float = 100.0
    energy_drain_per_second: float = 0.05
    energy_per_soul: float = 20.0
    charge_cooldown_seconds: float = 5.0

    def __post_init__(self):
        for field in fields(self):
            value = getattr(self, field.name)
            if isinstance(value, bool) or not isinstance(value, (int,float)) or not math.isfinite(value):
                raise ValueError('Config values must be finite numbers')
        if self.energy_max <= 0 or self.energy_per_soul <= 0:
            raise ValueError('Energy capacity and conversion must be positive')
        if self.energy_drain_per_second < 0 or self.charge_cooldown_seconds < 0:
            raise ValueError('Drain and cooldown cannot be negative')

    @classmethod
    def load(cls, path):
        if not path: return cls()
        with open(path, encoding='utf-8') as handle: data = json.load(handle)
        if not isinstance(data, dict): raise ValueError('Config must be a JSON object')
        return cls(**data)
