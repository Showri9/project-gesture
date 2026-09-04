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
    #: optional per-intent hold time, from `hold_ms` on a binding
    hold_ms: dict[Intent, float] = field(default_factory=dict)
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
    hold_ms: dict[Intent, float] = {}
    for pose, spec in (raw.get("bindings") or {}).items():
        # a binding is either  Pose: INTENT
        # or                   Pose: {intent: INTENT, hold_ms: 3000}
        if isinstance(spec, dict):
            intent_name = spec.get("intent")
            hold = spec.get("hold_ms")
        else:
            intent_name, hold = spec, None

        try:
            intent = Intent(intent_name)
        except ValueError as exc:
            valid = ", ".join(sorted(i.value for i in Intent))
            raise ValueError(
                f"config.yaml: '{intent_name}' is not a known intent "
                f"(bound to {pose}). Valid intents: {valid}"
            ) from exc

        bindings[pose] = intent
        if hold is not None:
            try:
                hold_ms[intent] = float(hold)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"config.yaml: hold_ms for {pose} must be a number of "
                    f"milliseconds, got {hold!r}"
                ) from exc

    return AppConfig(
        vision=VisionConfig(**(raw.get("vision") or {})),
        session=SessionConfig(**(raw.get("session") or {})),
        bindings=bindings,
        hold_ms=hold_ms,
        devices=[DeviceConfig(**d) for d in (raw.get("devices") or [])],
    )
