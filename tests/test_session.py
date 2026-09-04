"""The state machine, tested headlessly. No camera, no TV, no MediaPipe.

This is the suite that matters: every false-positive and runaway-repeat bug the
product could have is a bug in here, and all of it is deterministic.
"""

import pytest

from gesturectl.intents import Intent
from gesturectl.session import SessionConfig, SessionMachine, State

BINDINGS = {
    "Open_Palm": Intent.SESSION_WAKE,
    "Closed_Fist": Intent.SESSION_SLEEP,
    "Thumb_Up": Intent.VOLUME_UP,
    "Victory": Intent.MUTE_TOGGLE,
    "ILoveYou": Intent.POWER_TOGGLE,
}


@pytest.fixture
def machine():
    cfg = SessionConfig(
        wake_hold_ms=800, confirm_frames=4, cooldown_ms=600,
        repeat_ms=60, idle_timeout_ms=10_000, min_confidence=0.65,
    )
    return SessionMachine(cfg, BINDINGS, target="living-room")


def feed(m, pose, frames, start_ms=0.0, step=33.0, confidence=0.9):
    """Run `frames` frames of one pose; return every message it produced."""
    out = []
    t = start_ms
    for _ in range(frames):
        msg = m.update(pose, confidence, t)
        if msg is not None:
            out.append((t, msg))
        t += step
    return out, t


def wake(m, at=0.0):
    _, t = feed(m, "Open_Palm", 30, start_ms=at)   # ~1s, past wake_hold_ms
    assert m.state is State.ARMED
    return t


# -- the wake gate -----------------------------------------------------------

def test_nothing_fires_before_wake(machine):
    msgs, _ = feed(machine, "Thumb_Up", 60)
    assert msgs == []
    assert machine.state is State.IDLE


def test_wake_needs_the_pose_held(machine):
    msgs, _ = feed(machine, "Open_Palm", 10)   # ~330ms, short of 800ms
    assert msgs == []
    assert machine.state is State.IDLE


def test_wake_arms_after_the_hold(machine):
    msgs, _ = feed(machine, "Open_Palm", 30)
    assert [m.intent for _, m in msgs] == [Intent.SESSION_WAKE]
    assert machine.state is State.ARMED


def test_interrupted_wake_restarts_the_hold(machine):
    feed(machine, "Open_Palm", 15)
    machine.update(None, 0.0, 500.0)           # hand drops
    msgs, _ = feed(machine, "Open_Palm", 15, start_ms=533.0)
    assert msgs == []                          # not long enough on its own
    assert machine.state is State.IDLE


# -- confirmation ------------------------------------------------------------

def test_pose_must_be_held_to_confirm(machine):
    t = wake(machine)
    msgs, _ = feed(machine, "Victory", 3, start_ms=t)   # confirm_frames is 4
    assert msgs == []
    assert machine.state is State.CONFIRMING


def test_confirmed_pose_fires_once(machine):
    t = wake(machine)
    msgs, _ = feed(machine, "Victory", 8, start_ms=t)
    assert [m.intent for _, m in msgs] == [Intent.MUTE_TOGGLE]


def test_flicker_never_fires(machine):
    """Alternating poses must not accumulate toward a confirmation."""
    t = wake(machine)
    for i in range(40):
        pose = "Victory" if i % 2 == 0 else "Pointing_Up"
        assert machine.update(pose, 0.9, t + i * 33.0) is None


def test_low_confidence_is_treated_as_no_pose(machine):
    t = wake(machine)
    msgs, _ = feed(machine, "Victory", 20, start_ms=t, confidence=0.4)
    assert msgs == []


def test_message_carries_the_target_and_gesture(machine):
    t = wake(machine)
    msgs, _ = feed(machine, "Victory", 8, start_ms=t)
    _, msg = msgs[0]
    assert msg.target == "living-room"
    assert msg.gesture == "Victory"
    assert msg.to_dict()["intent"] == "MUTE_TOGGLE"


# -- cooldown and repeat -----------------------------------------------------

def test_discrete_intent_does_not_runaway(machine):
    """Two seconds of Victory must not fire sixty mutes."""
    t = wake(machine)
    msgs, _ = feed(machine, "Victory", 60, start_ms=t)
    assert len(msgs) == 1


def test_repeatable_intent_ramps_at_the_configured_rate(machine):
    t = wake(machine)
    msgs, _ = feed(machine, "Thumb_Up", 60, start_ms=t)   # ~2s held
    assert len(msgs) > 5, "a held volume gesture should ramp"
    assert msgs[0][1].repeat is False
    assert all(m.repeat for _, m in msgs[1:])
    gaps = [b[0] - a[0] for a, b in zip(msgs[1:], msgs[2:])]
    assert all(g >= 60.0 for g in gaps), "repeats must respect repeat_ms"


def test_same_discrete_intent_can_fire_again_after_cooldown(machine):
    t = wake(machine)
    first, t = feed(machine, "Victory", 8, start_ms=t)
    machine.update(None, 0.0, t)                     # drop the pose
    second, _ = feed(machine, "Victory", 8, start_ms=t + 900.0)
    assert len(first) == 1 and len(second) == 1


# -- power needs asking twice ------------------------------------------------

def test_power_requires_a_second_confirmation(machine):
    t = wake(machine)
    msgs, t = feed(machine, "ILoveYou", 10, start_ms=t)
    assert msgs == [], "power must not fire on the first pass"
    assert machine.status(t).pending is Intent.POWER_TOGGLE

    machine.update(None, 0.0, t)                     # drop the pose
    msgs, _ = feed(machine, "ILoveYou", 10, start_ms=t + 100.0)
    assert [m.intent for _, m in msgs] == [Intent.POWER_TOGGLE]


def test_pending_power_confirmation_expires(machine):
    t = wake(machine)
    _, t = feed(machine, "ILoveYou", 10, start_ms=t)
    assert machine.status(t).pending is Intent.POWER_TOGGLE
    machine.update("Open_Palm", 0.9, t + 4_000.0)    # past confirm_window_ms
    assert machine.status(t + 4_000.0).pending is None


# -- sleeping ----------------------------------------------------------------

def test_fist_disarms_immediately(machine):
    t = wake(machine)
    msgs, _ = feed(machine, "Closed_Fist", 5, start_ms=t)
    assert [m.intent for _, m in msgs] == [Intent.SESSION_SLEEP]
    assert machine.state is State.IDLE


def test_idle_timeout_disarms(machine):
    t = wake(machine)
    machine.update(None, 0.0, t + 11_000.0)
    assert machine.state is State.IDLE


def test_after_sleep_gestures_are_ignored_again(machine):
    t = wake(machine)
    _, t = feed(machine, "Closed_Fist", 5, start_ms=t)
    msgs, _ = feed(machine, "Victory", 30, start_ms=t + 100.0)
    assert msgs == []
