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
        sleep_confirm_frames=4, wake_grace_ms=1_000,
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

@pytest.fixture
def strict_power():
    """Power with the optional double-confirm turned on."""
    cfg = SessionConfig(
        wake_hold_ms=800, confirm_frames=4, cooldown_ms=600,
        repeat_ms=60, idle_timeout_ms=10_000, min_confidence=0.65,
        sleep_confirm_frames=4, wake_grace_ms=1_000,
        power_confirm_frames=4, double_confirm_power=True,
    )
    return SessionMachine(cfg, BINDINGS, target="living-room")


def test_power_fires_on_one_deliberate_hold_by_default(machine):
    """Default is a single long hold, not a double-tap: 'show it to turn on,
    show it again to turn off' is the mental model users actually have."""
    t = wake(machine)
    msgs, _ = feed(machine, "ILoveYou", 20, start_ms=t)
    assert [m.intent for _, m in msgs] == [Intent.POWER_TOGGLE]


def test_power_holds_longer_than_other_intents(machine):
    """power_confirm_frames is 15 by default against confirm_frames of 4."""
    t = wake(machine)
    msgs, _ = feed(machine, "ILoveYou", 10, start_ms=t)
    assert msgs == [], "10 frames is enough for a normal intent, not for power"


def test_power_requires_a_second_confirmation(strict_power):
    machine = strict_power
    t = wake(machine)
    msgs, t = feed(machine, "ILoveYou", 10, start_ms=t)
    assert msgs == [], "power must not fire on the first pass"
    assert machine.status(t).pending is Intent.POWER_TOGGLE

    machine.update(None, 0.0, t)                     # drop the pose
    msgs, _ = feed(machine, "ILoveYou", 10, start_ms=t + 100.0)
    assert [m.intent for _, m in msgs] == [Intent.POWER_TOGGLE]


def test_pending_power_confirmation_expires(strict_power):
    machine = strict_power
    t = wake(machine)
    _, t = feed(machine, "ILoveYou", 10, start_ms=t)
    assert machine.status(t).pending is Intent.POWER_TOGGLE
    machine.update("Open_Palm", 0.9, t + 4_000.0)    # past confirm_window_ms
    assert machine.status(t + 4_000.0).pending is None


# -- sleeping ----------------------------------------------------------------

def test_fist_right_after_waking_does_not_disarm(machine):
    """Observed live: lowering the hand from the wake pose reads as a fist for a
    few frames, and the session slept a fraction of a second after waking."""
    t = wake(machine)
    msgs, _ = feed(machine, "Closed_Fist", 8, start_ms=t)
    assert msgs == []
    assert machine.state is State.ARMED


def test_a_single_fist_frame_does_not_disarm(machine):
    t = wake(machine)
    _, t = feed(machine, "Victory", 2, start_ms=t + 1_500.0)   # past the grace
    msgs = machine.update("Closed_Fist", 0.9, t)
    assert msgs is None
    assert machine.state is State.ARMED


def test_fist_disarms_once_held_past_the_grace(machine):
    t = wake(machine)
    msgs, _ = feed(machine, "Closed_Fist", 8, start_ms=t + 1_500.0)
    assert [m.intent for _, m in msgs] == [Intent.SESSION_SLEEP]
    assert machine.state is State.IDLE


def test_idle_timeout_disarms(machine):
    t = wake(machine)
    machine.update(None, 0.0, t + 11_000.0)
    assert machine.state is State.IDLE


def test_after_sleep_gestures_are_ignored_again(machine):
    t = wake(machine)
    _, t = feed(machine, "Closed_Fist", 8, start_ms=t + 1_500.0)
    msgs, _ = feed(machine, "Victory", 30, start_ms=t + 100.0)
    assert msgs == []


# -- one gesture toggling wake and sleep -------------------------------------

TOGGLE_BINDINGS = {
    "Victory": Intent.SESSION_TOGGLE,
    "Thumb_Up": Intent.VOLUME_UP,
    "Open_Palm": Intent.POWER_TOGGLE,
    "Closed_Fist": Intent.MUTE_TOGGLE,
}


@pytest.fixture
def toggler():
    cfg = SessionConfig(
        wake_hold_ms=800, confirm_frames=4, cooldown_ms=600,
        repeat_ms=60, idle_timeout_ms=10_000, min_confidence=0.65,
        sleep_confirm_frames=4, wake_grace_ms=1_000, power_confirm_frames=15,
    )
    return SessionMachine(cfg, TOGGLE_BINDINGS, target="living-room")


def test_toggle_wakes_from_idle(toggler):
    msgs, _ = feed(toggler, "Victory", 30)
    assert [m.intent for _, m in msgs] == [Intent.SESSION_WAKE]
    assert toggler.state is State.ARMED


def test_holding_the_toggle_does_not_immediately_sleep(toggler):
    """The whole hazard of one-gesture toggling: the hold that wakes you must
    not roll straight into a sleep. wake_grace_ms is what prevents it."""
    msgs, _ = feed(toggler, "Victory", 60)   # ~2s of continuous Victory
    assert [m.intent for _, m in msgs] == [Intent.SESSION_WAKE]
    assert toggler.state is State.ARMED


def test_toggle_again_later_sleeps(toggler):
    _, t = feed(toggler, "Victory", 30)
    toggler.update(None, 0.0, t)                       # hand down
    msgs, _ = feed(toggler, "Victory", 10, start_ms=t + 1_500.0)
    assert [m.intent for _, m in msgs] == [Intent.SESSION_SLEEP]
    assert toggler.state is State.IDLE


def test_toggle_reports_concrete_actions_never_the_toggle(toggler):
    """Nothing downstream should ever see SESSION_TOGGLE."""
    _, t = feed(toggler, "Victory", 30)
    toggler.update(None, 0.0, t)
    msgs, _ = feed(toggler, "Victory", 10, start_ms=t + 1_500.0)
    seen = {m.intent for _, m in msgs}
    assert Intent.SESSION_TOGGLE not in seen


def test_full_cycle_wake_act_sleep(toggler):
    _, t = feed(toggler, "Victory", 30)
    toggler.update(None, 0.0, t)
    vol, t2 = feed(toggler, "Thumb_Up", 10, start_ms=t + 200.0)
    assert vol and vol[0][1].intent is Intent.VOLUME_UP
    toggler.update(None, 0.0, t2)
    sleep, _ = feed(toggler, "Victory", 10, start_ms=t2 + 100.0)
    assert [m.intent for _, m in sleep] == [Intent.SESSION_SLEEP]


# -- power split across two gestures -----------------------------------------

POWER_BINDINGS = {
    "Victory": Intent.SESSION_TOGGLE,
    "Open_Palm": Intent.POWER_ON,
    "ILoveYou": Intent.POWER_OFF,
    "Thumb_Up": Intent.VOLUME_UP,
}


@pytest.fixture
def powered():
    cfg = SessionConfig(
        wake_hold_ms=800, confirm_frames=4, cooldown_ms=600,
        repeat_ms=60, idle_timeout_ms=10_000, min_confidence=0.65,
        sleep_confirm_frames=4, wake_grace_ms=1_000, power_confirm_frames=15,
    )
    return SessionMachine(cfg, POWER_BINDINGS, target="living-room")


def _awake(m):
    _, t = feed(m, "Victory", 30)
    m.update(None, 0.0, t)
    return t + 100.0


def test_power_on_fires_at_the_normal_speed(powered):
    """Switching a TV on by accident costs nothing, so it should not feel
    sluggish - POWER_ON is deliberately outside COSTLY."""
    t = _awake(powered)
    msgs, _ = feed(powered, "Open_Palm", 6, start_ms=t)
    assert [m.intent for _, m in msgs] == [Intent.POWER_ON]


def test_power_off_needs_the_long_hold(powered):
    t = _awake(powered)
    msgs, _ = feed(powered, "ILoveYou", 6, start_ms=t)
    assert msgs == [], "6 frames turns the TV on but must not turn it off"


def test_power_off_fires_once_held(powered):
    t = _awake(powered)
    msgs, _ = feed(powered, "ILoveYou", 20, start_ms=t)
    assert [m.intent for _, m in msgs] == [Intent.POWER_OFF]


def test_power_off_is_harder_than_power_on(powered):
    """The asymmetry is the point, so assert it rather than trusting config."""
    on = powered._frames_needed(Intent.POWER_ON)
    off = powered._frames_needed(Intent.POWER_OFF)
    assert off > on


def test_holding_power_on_does_not_fire_twice(powered):
    t = _awake(powered)
    msgs, _ = feed(powered, "Open_Palm", 60, start_ms=t)
    assert len(msgs) == 1
