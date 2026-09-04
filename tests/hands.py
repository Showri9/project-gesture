"""Synthetic hands for tests. Geometrically valid, camera not required.

Real recorded fixtures go in tests/fixtures/ as flat [x,y,z,...] JSON - and
those same files become the test suite for the TypeScript and Swift ports.
"""

from __future__ import annotations

from gesturectl.classify import Point

_MCP_X = {"index": 0.45, "middle": 0.50, "ring": 0.55, "pinky": 0.59}
_WRIST = Point(0.50, 0.80)

_THUMB = {
    "up":     (Point(0.38, 0.55), Point(0.34, 0.44)),   # (ip, tip)
    "down":   (Point(0.38, 0.70), Point(0.34, 0.80)),
    "out":    (Point(0.36, 0.63), Point(0.28, 0.62)),   # sideways: ambiguous
    "curled": (Point(0.40, 0.66), Point(0.44, 0.62)),
}


def _finger(mx: float, extended: bool) -> list[Point]:
    mcp = Point(mx, 0.60)
    if extended:
        return [mcp, Point(mx, 0.52), Point(mx, 0.46), Point(mx, 0.40)]
    return [mcp, Point(mx, 0.52), Point(mx, 0.57), Point(mx, 0.62)]


def hand(thumb: str = "curled", index=False, middle=False, ring=False, pinky=False):
    """Build a 21-point hand. `thumb` is one of up / down / out / curled."""
    ip, tip = _THUMB[thumb]
    points = [_WRIST, Point(0.46, 0.76), Point(0.43, 0.72), ip, tip]
    for name, ext in (("index", index), ("middle", middle),
                      ("ring", ring), ("pinky", pinky)):
        points.extend(_finger(_MCP_X[name], ext))
    assert len(points) == 21
    return points


OPEN_PALM = hand("up", True, True, True, True)
CLOSED_FIST = hand("curled")
THUMB_UP = hand("up")
THUMB_DOWN = hand("down")
VICTORY = hand("curled", index=True, middle=True)
POINTING_UP = hand("curled", index=True)
I_LOVE_YOU = hand("up", index=True, pinky=True)
