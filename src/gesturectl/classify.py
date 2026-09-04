"""Landmarks -> pose. Pure arithmetic on 21 points.

PORTABLE. No I/O, no third-party imports (math only). Ports to TypeScript and
Swift almost line for line, and the JSON fixtures in tests/fixtures/ test all
three implementations with identical inputs.

MediaPipe emits normalized image coordinates: x and y in [0, 1] with the ORIGIN
AT TOP-LEFT, so a smaller y is higher up the frame. z is depth relative to the
wrist, roughly in the same scale as x. Every function here is invariant to where
the hand is in frame and how far away it is - which is what lets the same
thresholds work at the desk and on the sofa.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# --- landmark indices (MediaPipe hand model) --------------------------------

WRIST = 0
THUMB_CMC, THUMB_MCP, THUMB_IP, THUMB_TIP = 1, 2, 3, 4
INDEX_MCP, INDEX_PIP, INDEX_DIP, INDEX_TIP = 5, 6, 7, 8
MIDDLE_MCP, MIDDLE_PIP, MIDDLE_DIP, MIDDLE_TIP = 9, 10, 11, 12
RING_MCP, RING_PIP, RING_DIP, RING_TIP = 13, 14, 15, 16
PINKY_MCP, PINKY_PIP, PINKY_DIP, PINKY_TIP = 17, 18, 19, 20

#: (tip, pip, mcp) for the four fingers that share a geometry. The thumb doesn't.
_FINGERS = (
    ("index", INDEX_TIP, INDEX_PIP, INDEX_MCP),
    ("middle", MIDDLE_TIP, MIDDLE_PIP, MIDDLE_MCP),
    ("ring", RING_TIP, RING_PIP, RING_MCP),
    ("pinky", PINKY_TIP, PINKY_PIP, PINKY_MCP),
)


@dataclass(frozen=True, slots=True)
class Point:
    x: float
    y: float
    z: float = 0.0


Landmarks = list[Point]


# --- geometry ---------------------------------------------------------------


def _dist(a: Point, b: Point) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def hand_scale(lm: Landmarks) -> float:
    """A distance-invariant unit for this hand: wrist to middle-finger knuckle.

    Every threshold below is expressed as a multiple of this, so a hand 4m away
    that occupies a tenth of the pixels still classifies identically.
    """
    return max(_dist(lm[WRIST], lm[MIDDLE_MCP]), 1e-6)


def normalize(lm: Landmarks) -> Landmarks:
    """Translate the wrist to the origin and scale by hand size."""
    s = hand_scale(lm)
    w = lm[WRIST]
    return [Point((p.x - w.x) / s, (p.y - w.y) / s, (p.z - w.z) / s) for p in lm]


def finger_extended(lm: Landmarks, tip: int, pip: int) -> bool:
    """A finger is extended when its tip is further from the wrist than its
    middle joint. Robust to hand rotation, unlike comparing y coordinates."""
    return _dist(lm[tip], lm[WRIST]) > _dist(lm[pip], lm[WRIST])


def thumb_extended(lm: Landmarks) -> bool:
    """The thumb folds sideways, not down, so it needs its own test: measure
    away from the index knuckle rather than away from the wrist."""
    anchor = lm[INDEX_MCP]
    return _dist(lm[THUMB_TIP], anchor) > _dist(lm[THUMB_IP], anchor) * 1.12


def extended_fingers(lm: Landmarks) -> tuple[bool, bool, bool, bool, bool]:
    """(thumb, index, middle, ring, pinky)."""
    return (
        thumb_extended(lm),
        *(finger_extended(lm, tip, pip) for _, tip, pip, _ in _FINGERS),
    )  # type: ignore[return-value]


def palm_facing_camera(lm: Landmarks) -> bool:
    """Cross product of two palm vectors; sign tells us which way the palm points.

    Note this flips between left and right hands - useful mainly as a tiebreak,
    not as a gate on its own.
    """
    a = lm[INDEX_MCP]
    b = lm[PINKY_MCP]
    w = lm[WRIST]
    return ((a.x - w.x) * (b.y - w.y) - (a.y - w.y) * (b.x - w.x)) < 0


# --- pose classification ----------------------------------------------------

# Names match MediaPipe's built-in GestureRecognizer labels, so this module is a
# drop-in fallback for it - and the extension point for gestures it doesn't know.

OPEN_PALM = "Open_Palm"
CLOSED_FIST = "Closed_Fist"
THUMB_UP = "Thumb_Up"
THUMB_DOWN = "Thumb_Down"
VICTORY = "Victory"
POINTING_UP = "Pointing_Up"
I_LOVE_YOU = "ILoveYou"


def classify_pose(lm: Landmarks) -> str | None:
    """Rule-based pose classification. Returns None for anything unrecognised -
    which is the common case and must stay cheap."""
    if len(lm) < 21:
        return None

    thumb, index, middle, ring, pinky = extended_fingers(lm)
    scale = hand_scale(lm)
    # Measure the thumb against the KNUCKLE ROW, not the wrist. Every thumb
    # sits above the wrist, including a sideways one, so the wrist is a
    # reference that reports thumbs-up for a hand held out flat.
    up = lm[INDEX_MCP].y - lm[THUMB_TIP].y  # positive means the thumb is higher

    if index and middle and ring and pinky:
        return OPEN_PALM

    if not any((thumb, index, middle, ring, pinky)):
        return CLOSED_FIST

    if thumb and not any((index, middle, ring, pinky)):
        if up > 0.35 * scale:
            return THUMB_UP
        if up < -0.35 * scale:
            return THUMB_DOWN
        return None  # sideways thumb is ambiguous; refuse rather than guess

    if index and middle and not ring and not pinky:
        return VICTORY

    if index and not middle and not ring and not pinky:
        if lm[INDEX_TIP].y < lm[INDEX_PIP].y:
            return POINTING_UP
        return None

    if thumb and index and pinky and not middle and not ring:
        return I_LOVE_YOU

    return None


def from_flat(coords: list[float]) -> Landmarks:
    """Build Landmarks from a flat [x,y,z, x,y,z, ...] array - the shape the
    JSON test fixtures use, and the shape the JS and Swift ports will receive."""
    if len(coords) % 3 != 0:
        raise ValueError(f"expected a multiple of 3 coordinates, got {len(coords)}")
    return [Point(*coords[i : i + 3]) for i in range(0, len(coords), 3)]
