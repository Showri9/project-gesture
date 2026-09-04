"""Landmark velocity -> swipes. Phase 1.5.

PORTABLE. Seven static poses can't cover up/down/left/right/select as well as a
remote does, and forcing them to produces an awkward vocabulary. Motion is the
second dimension that fixes it, and it needs no model - just the wrist position
over the last few frames.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from .classify import WRIST, Landmarks, hand_scale
from .intents import Intent

#: Swipe must cover this many hand-widths...
_MIN_TRAVEL = 1.2
#: ...within this many milliseconds, or it's just drift.
_MAX_DURATION_MS = 400
#: and the off-axis component must stay below this fraction of the main axis.
_MAX_SKEW = 0.6


@dataclass(frozen=True, slots=True)
class _Sample:
    x: float
    y: float
    scale: float
    t_ms: float


class SwipeDetector:
    """Feed it every frame; it returns an Intent on the frame a swipe completes.

    Deliberately stateless between swipes: after firing it clears its history so
    one gesture can't fire twice.
    """

    def __init__(self, window: int = 12) -> None:
        self._samples: deque[_Sample] = deque(maxlen=window)

    def reset(self) -> None:
        self._samples.clear()

    def update(self, lm: Landmarks | None, t_ms: float) -> Intent | None:
        if lm is None or len(lm) < 21:
            self._samples.clear()
            return None

        self._samples.append(
            _Sample(lm[WRIST].x, lm[WRIST].y, hand_scale(lm), t_ms)
        )
        if len(self._samples) < 3:
            return None

        first, last = self._samples[0], self._samples[-1]
        if last.t_ms - first.t_ms > _MAX_DURATION_MS:
            self._samples.popleft()
            return None

        scale = max(first.scale, 1e-6)
        dx = (last.x - first.x) / scale
        dy = (last.y - first.y) / scale

        intent = self._axis(dx, dy)
        if intent is not None:
            self._samples.clear()
        return intent

    @staticmethod
    def _axis(dx: float, dy: float) -> Intent | None:
        adx, ady = abs(dx), abs(dy)
        if adx >= _MIN_TRAVEL and ady < adx * _MAX_SKEW:
            # image x grows to the right, but the camera mirrors the user
            return Intent.NAV_LEFT if dx > 0 else Intent.NAV_RIGHT
        if ady >= _MIN_TRAVEL and adx < ady * _MAX_SKEW:
            # image y grows downward
            return Intent.NAV_DOWN if dy > 0 else Intent.NAV_UP
        return None
