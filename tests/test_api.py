"""The contract, tested end to end with no TV and no camera.

A fake adapter stands in for the Roku, so these exercise the real routes, the
real hub and the real session machine - only the thing on the far end of the
network is pretend.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from gesturectl.api.app import create_app
from gesturectl.config import AppConfig, DeviceConfig, VisionConfig
from gesturectl.devices.base import DeviceAdapter, Health, Result
from gesturectl.intents import Intent
from gesturectl.session import SessionConfig


class FakeRoku(DeviceAdapter):
    """Records what it was asked to do, and can be told to refuse."""

    def __init__(self, name: str, host: str, kind: str = "roku") -> None:
        super().__init__(name)
        self.kind = kind
        self.host = host
        self.base_url = host
        self.model = "FakeTV-1"
        self.is_tv = True
        self.sent: list[Intent] = []
        self.fail_next = False

    async def connect(self) -> None:
        self.capabilities = {
            Intent.VOLUME_UP, Intent.VOLUME_DOWN, Intent.MUTE_TOGGLE,
            Intent.PLAY_PAUSE, Intent.POWER_ON, Intent.POWER_OFF,
        }

    async def send(self, intent: Intent) -> Result:
        self.sent.append(intent)
        if self.fail_next:
            self.fail_next = False
            return Result(False, "pretend failure")
        return Result(True, intent.value)

    async def health(self) -> Health:
        return Health(True, f"{self.model} @ {self.host}")


def make_config() -> AppConfig:
    """Fixed thresholds, so these tests never move when Showri retunes his."""
    return AppConfig(
        vision=VisionConfig(),
        session=SessionConfig(
            wake_hold_ms=800, confirm_frames=4, cooldown_ms=600, repeat_ms=60,
            idle_timeout_ms=30_000, min_confidence=0.65,
            sleep_confirm_frames=4, wake_grace_ms=1_000, power_confirm_frames=15,
        ),
        bindings={
            "Victory": Intent.SESSION_TOGGLE,
            "Thumb_Up": Intent.VOLUME_UP,
            "Closed_Fist": Intent.MUTE_TOGGLE,
            "ILoveYou": Intent.POWER_OFF,
            "Pointing_Up": Intent.PLAY_PAUSE,
        },
        hold_ms={},
        devices=[DeviceConfig(name="test-tv", type="roku",
                              host="http://10.0.0.9:8060", default=True)],
    )


@pytest.fixture
def client():
    app = create_app(make_config(), adapter_factory=FakeRoku)
    with TestClient(app) as c:
        yield c


def adapter(client) -> FakeRoku:
    hub = client.app.state.hub
    return hub.selected.adapter


def poses(ws, pose, count, start=0.0, step=33.0, confidence=0.9):
    t = start
    for _ in range(count):
        ws.send_json({"pose": pose, "confidence": confidence, "t_ms": t})
        t += step
    return t


def wake(ws, start=0.0):
    t = poses(ws, "Victory", 32, start=start)      # past wake_hold_ms
    ws.send_json({"pose": None, "confidence": 0.0, "t_ms": t})   # release
    return t + 50.0


# -- the basics --------------------------------------------------------------

def test_health(client):
    body = client.get("/api/health").json()
    assert body["ok"] is True
    assert body["device"] == "test-tv"


def test_configured_device_is_present_and_selected(client):
    devices = client.get("/api/devices").json()
    assert len(devices) == 1
    assert devices[0]["selected"] is True
    assert devices[0]["is_tv"] is True
    assert "VOLUME_UP" in devices[0]["capabilities"]


def test_add_device_by_host(client):
    body = client.post("/api/devices/by-host",
                       json={"host": "http://10.0.0.22:8060", "name": "bedroom"}).json()
    assert body["name"] == "bedroom"
    assert body["reachable"] is True
    assert len(client.get("/api/devices").json()) == 2


def test_select_unknown_device_is_404(client):
    assert client.post("/api/devices/nope/select").status_code == 404


# -- the on-screen remote ----------------------------------------------------

def test_intent_endpoint_reaches_the_device(client):
    assert client.post("/api/intent", json={"intent": "VOLUME_UP"}).json()["ok"] is True
    assert adapter(client).sent == [Intent.VOLUME_UP]


def test_unsupported_intent_is_reported_not_sent(client):
    """A stick has no volume keys. The UI should be told, not silently ignored."""
    adapter(client).capabilities = {Intent.PLAY_PAUSE}
    assert client.post("/api/intent", json={"intent": "VOLUME_UP"}).json()["ok"] is False
    assert adapter(client).sent == []


def test_device_failure_surfaces(client):
    adapter(client).fail_next = True
    assert client.post("/api/intent", json={"intent": "MUTE_TOGGLE"}).json()["ok"] is False


# -- the pose stream, which is the real path ---------------------------------

def test_poses_alone_do_nothing_until_woken(client):
    with client.websocket_connect("/api/pose") as ws:
        poses(ws, "Thumb_Up", 40)
        client.get("/api/health")          # let the server drain
    assert adapter(client).sent == []


def test_wake_then_gesture_drives_the_tv(client):
    with client.websocket_connect("/api/pose") as ws:
        t = wake(ws)
        poses(ws, "Thumb_Up", 10, start=t)
        client.get("/api/health")
    assert Intent.VOLUME_UP in adapter(client).sent


def test_held_volume_ramps_but_a_held_mute_does_not(client):
    with client.websocket_connect("/api/pose") as ws:
        t = wake(ws)
        poses(ws, "Thumb_Up", 60, start=t)
        client.get("/api/health")
    volume = [i for i in adapter(client).sent if i is Intent.VOLUME_UP]
    assert len(volume) > 3, "a held volume pose should ramp"

    with client.websocket_connect("/api/pose") as ws:
        t = wake(ws, start=10_000.0)
        poses(ws, "Closed_Fist", 60, start=t)
        client.get("/api/health")
    mutes = [i for i in adapter(client).sent if i is Intent.MUTE_TOGGLE]
    assert len(mutes) == 1, "a held discrete pose is one press"


def test_power_off_needs_the_long_hold(client):
    with client.websocket_connect("/api/pose") as ws:
        t = wake(ws)
        poses(ws, "ILoveYou", 6, start=t)
        client.get("/api/health")
    assert Intent.POWER_OFF not in adapter(client).sent


def test_a_malformed_frame_does_not_kill_the_stream(client):
    with client.websocket_connect("/api/pose") as ws:
        ws.send_json({"pose": 12345, "confidence": "nonsense"})
        t = wake(ws)
        poses(ws, "Thumb_Up", 10, start=t)
        client.get("/api/health")
    assert Intent.VOLUME_UP in adapter(client).sent


# -- events ------------------------------------------------------------------

def test_events_stream_reports_session_state(client):
    with client.websocket_connect("/api/events") as events:
        with client.websocket_connect("/api/pose") as ws:
            wake(ws)
            seen = [events.receive_json() for _ in range(6)]
    states = {e.get("state") for e in seen if e["type"] == "session_state"}
    assert states, "no session_state events arrived"
    assert "ARMED" in states or "IDLE" in states


# -- config ------------------------------------------------------------------

def test_config_roundtrip(client):
    body = client.get("/api/config").json()
    assert {b["pose"] for b in body["bindings"]} >= {"Victory", "Thumb_Up"}
    assert body["thresholds"]["confirm_frames"] == 4.0


def test_put_config_applies_live(client):
    body = client.get("/api/config").json()
    body["thresholds"]["confirm_frames"] = 40.0
    updated = client.put("/api/config", json=body).json()
    assert updated["thresholds"]["confirm_frames"] == 40.0

    with client.websocket_connect("/api/pose") as ws:
        t = wake(ws)
        poses(ws, "Thumb_Up", 10, start=t)     # enough for 4 frames, not 40
        client.get("/api/health")
    assert Intent.VOLUME_UP not in adapter(client).sent


def test_put_config_rejects_an_unknown_threshold(client):
    resp = client.put("/api/config", json={"thresholds": {"not_a_real_dial": 1}})
    assert resp.status_code == 400


def test_rebinding_a_pose_takes_effect(client):
    client.put("/api/config", json={"bindings": [
        {"pose": "Victory", "intent": "SESSION_TOGGLE"},
        {"pose": "Pointing_Up", "intent": "MUTE_TOGGLE"},
    ]})
    with client.websocket_connect("/api/pose") as ws:
        t = wake(ws)
        poses(ws, "Pointing_Up", 10, start=t)
        client.get("/api/health")
    assert Intent.MUTE_TOGGLE in adapter(client).sent


# -- the app serves the phone page ------------------------------------------

def test_frontend_is_served_at_the_root(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "gesturectl" in page.text
    assert "/src/app.js" in page.text


def test_frontend_modules_are_reachable(client):
    for path in ("/styles.css", "/src/app.js", "/src/api/client.js",
                 "/src/vision/detector.js"):
        assert client.get(path).status_code == 200, path


def test_the_static_mount_does_not_swallow_the_api(client):
    """The frontend is mounted at "/", so this is the regression that would
    quietly break every endpoint at once."""
    assert client.get("/api/health").json()["ok"] is True
