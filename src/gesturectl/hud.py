"""On-screen overlay. Costs an hour, and turns every tuning session from
guesswork into observation. Do not skip it.

Draws the landmark skeleton, the session state, the pose the model sees, and a
ring that fills as the confirm counter climbs - so when a gesture doesn't fire
you can see whether it was never detected, detected but not confirmed, or
confirmed but blocked by the cooldown.
"""

from __future__ import annotations

from .classify import Landmarks
from .session import State, Status

#: BGR, because OpenCV.
_INK = (236, 238, 231)
_DIM = (148, 153, 139)
_GOOD = (149, 200, 70)
_WARN = (79, 150, 224)
_BONE = (120, 128, 118)

_SKELETON = (
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (17, 18), (18, 19), (19, 20),
    (0, 17),
)

_STATE_COLOR = {
    State.IDLE: _DIM,
    State.ARMED: _GOOD,
    State.CONFIRMING: _WARN,
}


def draw(frame_bgr, landmarks: Landmarks | None, pose: str | None,
         confidence: float, status: Status, device_line: str = "") -> None:
    """Mutates `frame_bgr` in place."""
    import cv2

    h, w = frame_bgr.shape[:2]

    if landmarks:
        pts = [(int(p.x * w), int(p.y * h)) for p in landmarks]
        for a, b in _SKELETON:
            cv2.line(frame_bgr, pts[a], pts[b], _BONE, 2, cv2.LINE_AA)
        for x, y in pts:
            cv2.circle(frame_bgr, (x, y), 3, _INK, -1, cv2.LINE_AA)

    colour = _STATE_COLOR[status.state]
    cv2.rectangle(frame_bgr, (0, 0), (w, 34), (28, 34, 26), -1)
    cv2.putText(frame_bgr, status.state.value, (12, 23),
                cv2.FONT_HERSHEY_DUPLEX, 0.6, colour, 1, cv2.LINE_AA)

    label = f"{pose}  {confidence:.2f}" if pose else "-"
    cv2.putText(frame_bgr, label, (120, 23),
                cv2.FONT_HERSHEY_SIMPLEX, 0.52, _INK, 1, cv2.LINE_AA)

    if device_line:
        cv2.putText(frame_bgr, device_line, (12, h - 14),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.44, _DIM, 1, cv2.LINE_AA)

    if status.state is State.IDLE:
        cv2.putText(frame_bgr, "hold an open palm to wake", (12, h - 38),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.46, _DIM, 1, cv2.LINE_AA)

    if status.pending is not None:
        secs = status.pending_remaining_ms / 1000.0
        cv2.putText(frame_bgr,
                    f"repeat gesture to confirm {status.pending.value}  ({secs:.1f}s)",
                    (12, h - 38), cv2.FONT_HERSHEY_SIMPLEX, 0.5, _WARN, 1, cv2.LINE_AA)

    _confirm_ring(frame_bgr, cv2, w, status)


def _confirm_ring(frame_bgr, cv2, w: int, status: Status) -> None:
    if status.progress <= 0.0:
        return
    centre = (w - 46, 62)
    cv2.circle(frame_bgr, centre, 22, (60, 66, 58), 3, cv2.LINE_AA)
    cv2.ellipse(frame_bgr, centre, (22, 22), -90, 0,
                int(360 * status.progress), _WARN, 3, cv2.LINE_AA)
    if status.candidate is not None:
        cv2.putText(frame_bgr, status.candidate.value, (w - 210, 100),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, _WARN, 1, cv2.LINE_AA)
