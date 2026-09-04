"""The contract between the vision side and the device side.

PORTABLE. No I/O, no third-party imports. This module gets ported verbatim to
TypeScript (phase 2) and Swift/Kotlin (phase 3). Keep it boring.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


class Intent(str, Enum):
    """A verb, never a key code. Adapters own the translation to key codes."""

    # session control - handled locally, never dispatched to a device
    SESSION_WAKE = "SESSION_WAKE"
    SESSION_SLEEP = "SESSION_SLEEP"
    #: one gesture for both: wakes when idle, sleeps when armed. The machine
    #: resolves it and reports the concrete WAKE or SLEEP that happened, so
    #: nothing downstream ever sees a TOGGLE.
    SESSION_TOGGLE = "SESSION_TOGGLE"

    # playback and audio
    VOLUME_UP = "VOLUME_UP"
    VOLUME_DOWN = "VOLUME_DOWN"
    MUTE_TOGGLE = "MUTE_TOGGLE"
    PLAY_PAUSE = "PLAY_PAUSE"
    POWER_TOGGLE = "POWER_TOGGLE"

    # navigation - phase 1.5, driven by motion rather than static poses
    NAV_UP = "NAV_UP"
    NAV_DOWN = "NAV_DOWN"
    NAV_LEFT = "NAV_LEFT"
    NAV_RIGHT = "NAV_RIGHT"
    SELECT = "SELECT"
    BACK = "BACK"
    HOME = "HOME"


#: Intents that make sense to fire repeatedly while a pose is held.
REPEATABLE: frozenset[Intent] = frozenset(
    {Intent.VOLUME_UP, Intent.VOLUME_DOWN, Intent.NAV_UP, Intent.NAV_DOWN,
     Intent.NAV_LEFT, Intent.NAV_RIGHT}
)

#: Intents costly enough to be worth a second confirmation.
#: Turning the TV off mid-film is the failure that gets software uninstalled;
#: nudging the volume is a shrug. Weight the confirmation to the cost of error.
NEEDS_CONFIRMATION: frozenset[Intent] = frozenset({Intent.POWER_TOGGLE})

#: Handled by the session machine itself, never sent to a device.
SESSION_ONLY: frozenset[Intent] = frozenset(
    {Intent.SESSION_WAKE, Intent.SESSION_SLEEP, Intent.SESSION_TOGGLE}
)


class Source(str, Enum):
    GESTURE = "gesture"
    VOICE = "voice"
    APP = "app"
    WEARABLE = "wearable"


@dataclass(frozen=True, slots=True)
class IntentMessage:
    """What crosses the wire. Note what is absent: no IP, no HTTP verb, no key name."""

    intent: Intent
    target: str = "default"
    source: Source = Source.GESTURE
    gesture: str | None = None
    confidence: float = 1.0
    repeat: bool = False
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "intent": self.intent.value,
            "target": self.target,
            "source": self.source.value,
            "gesture": self.gesture,
            "confidence": round(self.confidence, 4),
            "repeat": self.repeat,
            "ts": round(self.ts, 3),
        }
