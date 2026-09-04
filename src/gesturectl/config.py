"""Load config.yaml into typed objects, so a typo fails at startup, not mid-film."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .intents import Intent
from .session import SessionConfig


@dataclass(slots=True)
class VisionConfig:
    camera_index: int = 0
    width: int = 640
    height: int = 480
    target_fps: int = 30
    min_detection_confidence: float = 0.5
    min_tracking_confidence: float = 0.5
    model_path: str = "models/gesture_recognizer.task"


@dataclass(slots=True)
class DeviceConfig:
    name: str
    type: str
    host: str | None = None
    default: bool = False


@dataclass(slots=True)
class AppConfig:
    vision: VisionConfig = field(default_factory=VisionConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    bindings: dict[str, Intent] = field(default_factory=dict)
    devices: list[DeviceConfig] = field(default_factory=list)

    @property
    def default_device(self) -> DeviceConfig | None:
        for device in self.devices:
            if device.default:
                return device
        return self.devices[0] if self.devices else None


def load(path: str | Path = "config.yaml") -> AppConfig:
    raw = yaml.safe_load(Path(path).read_text()) or {}

    bindings: dict[str, Intent] = {}
    for pose, intent_name in (raw.get("bindings") or {}).items():
        try:
            bindings[pose] = Intent(intent_name)
        except ValueError as exc:
            valid = ", ".join(sorted(i.value for i in Intent))
            raise ValueError(
                f"config.yaml: '{intent_name}' is not a known intent "
                f"(bound to {pose}). Valid intents: {valid}"
            ) from exc

    return AppConfig(
        vision=VisionConfig(**(raw.get("vision") or {})),
        session=SessionConfig(**(raw.get("session") or {})),
        bindings=bindings,
        devices=[DeviceConfig(**d) for d in (raw.get("devices") or [])],
    )
