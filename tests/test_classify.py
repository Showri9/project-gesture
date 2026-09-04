"""classify.py is pure arithmetic, so it tests without a camera."""

import hands
import pytest

from gesturectl import classify


@pytest.mark.parametrize(
    "landmarks,expected",
    [
        (hands.OPEN_PALM, classify.OPEN_PALM),
        (hands.CLOSED_FIST, classify.CLOSED_FIST),
        (hands.THUMB_UP, classify.THUMB_UP),
        (hands.THUMB_DOWN, classify.THUMB_DOWN),
        (hands.VICTORY, classify.VICTORY),
        (hands.POINTING_UP, classify.POINTING_UP),
        (hands.I_LOVE_YOU, classify.I_LOVE_YOU),
    ],
)
def test_poses(landmarks, expected):
    assert classify.classify_pose(landmarks) == expected


def test_sideways_thumb_is_refused_not_guessed():
    assert classify.classify_pose(hands.hand("out")) is None


def test_too_few_landmarks():
    assert classify.classify_pose(hands.OPEN_PALM[:10]) is None


def test_classification_is_scale_invariant():
    """The whole point: a hand 4m away must classify like a hand at the desk."""
    for scale in (0.25, 0.5, 2.0, 4.0):
        shrunk = [
            classify.Point(0.5 + (p.x - 0.5) * scale, 0.5 + (p.y - 0.5) * scale, p.z)
            for p in hands.VICTORY
        ]
        assert classify.classify_pose(shrunk) == classify.VICTORY


def test_classification_is_translation_invariant():
    for dx, dy in ((0.2, 0.0), (-0.3, 0.05), (0.0, -0.25)):
        moved = [classify.Point(p.x + dx, p.y + dy, p.z) for p in hands.THUMB_UP]
        assert classify.classify_pose(moved) == classify.THUMB_UP


def test_from_flat_roundtrip():
    flat = [c for p in hands.VICTORY for c in (p.x, p.y, p.z)]
    assert classify.classify_pose(classify.from_flat(flat)) == classify.VICTORY


def test_from_flat_rejects_ragged_input():
    with pytest.raises(ValueError):
        classify.from_flat([0.1, 0.2])
