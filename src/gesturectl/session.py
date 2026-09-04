"""The wake / confirm / cooldown state machine.

PORTABLE. This is where the product actually lives. The naive loop fires
VOLUME_UP thirty times a second and changes the channel when you scratch your
nose; everything that makes gesture control feel like a product rather than a
demo is in this file, and none of it is machine learning.

The clock is injected as a parameter rather than read from time.time(), which is
what lets the whole thing be tested headlessly with no camera and no TV.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .intents import (
    NEEDS_CONFIRMATION,
    REPEATABLE,
    SESSION_ONLY,
    Intent,
    IntentMessage,
    Source,
)


class State(str, Enum):
    IDLE = "IDLE"           # nothing dispatches; waiting for the wake gesture
    ARMED = "ARMED"         # listening; HUD visible
    CONFIRMING = "CONFIRMING"  # counting frames on a candidate pose


@dataclass(slots=True)
class SessionConfig:
    wake_hold_ms: float = 800.0
    confirm_frames: int = 8
    cooldown_ms: float = 600.0
    repeat_ms: float = 60.0
    idle_timeout_ms: float = 10_000.0
    min_confidence: float = 0.65
    #: how long the second half of a double-confirm may take
    confirm_window_ms: float = 3_000.0


@dataclass(slots=True)
class Status:
    """Everything the HUD needs. Read-only snapshot."""

    state: State
    candidate: Intent | None = None
    progress: float = 0.0          # 0..1, fills the confirm ring
    pending: Intent | None = None  # awaiting a second confirmation
    pending_remaining_ms: float = 0.0


class SessionMachine:
    """Feed it one pose per frame; it returns an IntentMessage on the frames
    something should actually happen, and None the rest of the time."""

    def __init__(
        self,
        config: SessionConfig | None = None,
        bindings: dict[str, Intent] | None = None,
        target: str = "default",
    ) -> None:
        self.config = config or SessionConfig()
        self.bindings = bindings or {}
        self.target = target

        self._state = State.IDLE
        self._wake_started_ms: float | None = None
        self._last_pose_ms: float = 0.0

        self._candidate: Intent | None = None
        self._candidate_pose: str | None = None
        self._frames = 0

        self._repeating: Intent | None = None
        #: a fired (or pending) intent stays latched until the pose is
        #: released, so holding a discrete pose is one press, not a stream
        self._latched: Intent | None = None
        self._last_fire_ms: dict[Intent, float] = {}

        self._pending: Intent | None = None
        self._pending_until_ms: float = 0.0

    # -- introspection for the HUD -----------------------------------------

    @property
    def state(self) -> State:
        return self._state

    def status(self, now_ms: float = 0.0) -> Status:
        progress = (
            min(1.0, self._frames / self.config.confirm_frames)
            if self.config.confirm_frames
            else 1.0
        )
        return Status(
            state=self._state,
            candidate=self._candidate,
            progress=progress if self._state is State.CONFIRMING else 0.0,
            pending=self._pending,
            pending_remaining_ms=max(0.0, self._pending_until_ms - now_ms),
        )

    # -- the loop -----------------------------------------------------------

    def update(
        self, pose: str | None, confidence: float, now_ms: float
    ) -> IntentMessage | None:
        """One frame. `pose` is None when no hand is visible or nothing matched."""
        cfg = self.config

        if confidence < cfg.min_confidence:
            pose = None
        if pose is not None:
            self._last_pose_ms = now_ms

        # an unconsummated double-confirm expires on its own
        if self._pending is not None and now_ms > self._pending_until_ms:
            self._pending = None

        intent = self.bindings.get(pose) if pose else None

        if self._state is State.IDLE:
            return self._tick_idle(pose, intent, now_ms)
        return self._tick_active(pose, intent, confidence, now_ms)

    # -- states -------------------------------------------------------------

    def _tick_idle(
        self, pose: str | None, intent: Intent | None, now_ms: float
    ) -> IntentMessage | None:
        """Nothing dispatches until the wake gesture is held. This single gate
        is what kills ambient false positives."""
        if intent is not Intent.SESSION_WAKE:
            self._wake_started_ms = None
            return None

        if self._wake_started_ms is None:
            self._wake_started_ms = now_ms
            return None

        if now_ms - self._wake_started_ms >= self.config.wake_hold_ms:
            self._arm(now_ms)
            return self._message(Intent.SESSION_WAKE, pose, 1.0, repeat=False)
        return None

    def _tick_active(
        self, pose: str | None, intent: Intent | None, confidence: float, now_ms: float
    ) -> IntentMessage | None:
        cfg = self.config

        # hands down long enough -> disarm, so the system spends most of its
        # life in IDLE and the user's arm gets a rest
        if now_ms - self._last_pose_ms >= cfg.idle_timeout_ms:
            self._to_idle()
            return None

        if intent is Intent.SESSION_SLEEP:
            self._to_idle()
            return self._message(Intent.SESSION_SLEEP, pose, confidence, repeat=False)

        if intent is Intent.SESSION_WAKE or intent is None:
            self._latched = None
            self._reset_candidate()
            return None

        # the pose that just fired is still being held: wait for a release.
        # Without this, two seconds of Victory is four mutes.
        if self._latched is intent:
            return None
        self._latched = None

        # a repeatable pose that is still held keeps firing at repeat_ms
        if self._repeating is intent:
            last = self._last_fire_ms.get(intent, 0.0)
            if now_ms - last >= cfg.repeat_ms:
                self._last_fire_ms[intent] = now_ms
                return self._message(intent, pose, confidence, repeat=True)
            return None

        # a new candidate resets the counter
        if intent is not self._candidate:
            self._candidate = intent
            self._candidate_pose = pose
            self._frames = 1
            self._state = State.CONFIRMING
            return None

        self._frames += 1
        if self._frames < cfg.confirm_frames:
            return None

        # counter satisfied - but the cooldown may still block it
        last = self._last_fire_ms.get(intent, float("-inf"))
        if now_ms - last < cfg.cooldown_ms:
            return None

        return self._fire(intent, pose, confidence, now_ms)

    # -- firing --------------------------------------------------------------

    def _fire(
        self, intent: Intent, pose: str | None, confidence: float, now_ms: float
    ) -> IntentMessage | None:
        cfg = self.config

        if intent in NEEDS_CONFIRMATION:
            # first pass arms the confirmation and fires nothing; the user must
            # drop the pose and make it again. Costly actions earn a second ask.
            if self._pending is not intent:
                self._pending = intent
                self._pending_until_ms = now_ms + cfg.confirm_window_ms
                self._latched = intent
                self._reset_candidate()
                self._state = State.ARMED
                return None
            self._pending = None

        self._last_fire_ms[intent] = now_ms
        self._repeating = intent if intent in REPEATABLE else None
        if self._repeating is None:
            self._latched = intent
        self._frames = 0
        self._state = State.ARMED if self._repeating is None else State.CONFIRMING
        return self._message(intent, pose, confidence, repeat=False)

    def _message(
        self, intent: Intent, pose: str | None, confidence: float, *, repeat: bool
    ) -> IntentMessage:
        return IntentMessage(
            intent=intent,
            target=self.target,
            source=Source.GESTURE,
            gesture=pose,
            confidence=confidence,
            repeat=repeat,
        )

    # -- transitions ---------------------------------------------------------

    def _arm(self, now_ms: float) -> None:
        self._state = State.ARMED
        self._wake_started_ms = None
        self._last_pose_ms = now_ms
        self._reset_candidate()

    def _to_idle(self) -> None:
        self._state = State.IDLE
        self._wake_started_ms = None
        self._pending = None
        self._latched = None
        self._reset_candidate()

    def _reset_candidate(self) -> None:
        self._candidate = None
        self._candidate_pose = None
        self._frames = 0
        self._repeating = None
        if self._state is State.CONFIRMING:
            self._state = State.ARMED
