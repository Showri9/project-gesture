"""Fire TV, including its authorisation step, with no stick on the network.

Fire TV's authorisation is not Google TV's. There is no code to type: a
fingerprint prompt appears on the television and somebody accepts it. Both
devices end up in the same state - answering, but refusing commands until a
person walks over - so the interesting thing to test is that the shared
`needs_pairing` machinery serves both without pretending they are identical.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from gesturectl.api.app import create_app
from gesturectl.devices.base import DeviceAdapter, Health, Result
from gesturectl.intents import Intent
from tests.test_api import FakeRoku, make_config


class FakeFireTV(DeviceAdapter):
    pairing_kind = "confirm"

    def __init__(self, name: str, host: str, kind: str = "firetv") -> None:
        super().__init__(name)
        self.host = host
        self.kind = kind
        self.model = "AFTKA"
        self.is_tv = True
        self.needs_pairing = True
        self.accepted_on_tv = False        # what the human does, out of band
        self.sent: list[Intent] = []

    async def connect(self, auth_timeout_s: float = 3.0) -> None:
        self.needs_pairing = not self.accepted_on_tv
        self.capabilities = set() if self.needs_pairing else {
            Intent.VOLUME_UP, Intent.VOLUME_DOWN, Intent.MUTE_TOGGLE,
            Intent.PLAY_PAUSE, Intent.POWER_ON, Intent.POWER_OFF, Intent.HOME,
        }

    async def start_pairing(self) -> None:
        await self.connect(auth_timeout_s=0.5)

    async def finish_pairing(self, code: str = "") -> None:
        await self.connect(auth_timeout_s=30.0)
        if self.needs_pairing:
            raise RuntimeError("still not authorised — accept the prompt on the TV")

    async def send(self, intent: Intent) -> Result:
        if self.needs_pairing:
            return Result(False, "not authorised — accept the prompt on the TV")
        self.sent.append(intent)
        return Result(True, intent.value)

    async def health(self) -> Health:
        return Health(not self.needs_pairing, self.model)


def factory(name: str, host: str, kind: str = "roku") -> DeviceAdapter:
    if kind == "firetv":
        return FakeFireTV(name, host)
    return FakeRoku(name, host, kind)


@pytest.fixture
def client():
    app = create_app(make_config(), adapter_factory=factory)
    with TestClient(app) as c:
        yield c


def add_stick(client, host="192.168.68.90"):
    return client.post("/api/devices/by-host",
                       json={"host": host, "name": "firestick", "kind": "firetv"}).json()


def adapter_for(client, device_id):
    return client.app.state.hub.devices[device_id].adapter


# -- adding ------------------------------------------------------------------

def test_add_a_fire_tv(client):
    body = add_stick(client)
    assert body["kind"] == "firetv"
    assert body["needs_pairing"] is True
    assert body["pairing_kind"] == "confirm", "no code to type on Fire TV"


def test_google_tv_still_asks_for_a_code(client):
    """The two must not collapse into one flow just because they share a flag."""
    body = client.get("/api/devices").json()[0]
    assert body["pairing_kind"] == "code"


# -- authorisation -----------------------------------------------------------

def test_pair_start_says_accept_rather_than_type(client):
    device = add_stick(client)
    body = client.post(f"/api/devices/{device['id']}/pair/start").json()
    assert "Accept" in body["message"], body
    assert "code" not in body["message"].lower()


def test_confirm_before_accepting_on_the_tv_fails_with_the_reason(client):
    device = add_stick(client)
    client.post(f"/api/devices/{device['id']}/pair/start")
    resp = client.post(f"/api/devices/{device['id']}/pair/finish", json={"code": ""})
    assert resp.status_code == 400
    assert "accept the prompt" in resp.json()["detail"].lower()


def test_confirm_after_accepting_authorises(client):
    device = add_stick(client)
    client.post(f"/api/devices/{device['id']}/pair/start")
    adapter_for(client, device["id"]).accepted_on_tv = True      # the human walks over

    done = client.post(f"/api/devices/{device['id']}/pair/finish",
                       json={"code": ""}).json()
    assert done["needs_pairing"] is False
    assert done["reachable"] is True


def test_an_empty_code_is_accepted_by_the_schema(client):
    """Google TV needs six digits; Fire TV needs none. The shared endpoint must
    not demand a minimum length that only one of them can meet."""
    device = add_stick(client)
    client.post(f"/api/devices/{device['id']}/pair/start")
    adapter_for(client, device["id"]).accepted_on_tv = True
    resp = client.post(f"/api/devices/{device['id']}/pair/finish", json={})
    assert resp.status_code == 200


# -- control -----------------------------------------------------------------

def test_unauthorised_stick_refuses_rather_than_pretending(client):
    device = add_stick(client)
    client.post(f"/api/devices/{device['id']}/select")
    assert client.post("/api/intent", json={"intent": "HOME"}).json()["ok"] is False


def test_authorised_stick_takes_commands(client):
    device = add_stick(client)
    client.post(f"/api/devices/{device['id']}/pair/start")
    adapter_for(client, device["id"]).accepted_on_tv = True
    client.post(f"/api/devices/{device['id']}/pair/finish", json={})
    client.post(f"/api/devices/{device['id']}/select")

    assert client.post("/api/intent", json={"intent": "PLAY_PAUSE"}).json()["ok"] is True
    assert adapter_for(client, device["id"]).sent == [Intent.PLAY_PAUSE]


# -- the real adapter's key map ----------------------------------------------

def test_power_uses_explicit_sleep_and_wake_not_a_toggle():
    """Android has real SLEEP and WAKEUP codes, so unlike Google TV this needs
    no read-the-state-first guesswork - and POWER_ON must never send SLEEP."""
    from gesturectl.devices.firetv import _KEYS

    assert _KEYS[Intent.POWER_OFF] == 223      # KEYCODE_SLEEP
    assert _KEYS[Intent.POWER_ON] == 224       # KEYCODE_WAKEUP
    assert _KEYS[Intent.POWER_ON] != _KEYS[Intent.POWER_OFF]


def test_volume_results_mention_cec():
    """A volume key the stick accepts can still change nothing audible, because
    the TV owns the audio. Silence with a green tick is the confusing case."""
    from gesturectl.devices.firetv import _CEC_NOTE, _VOLUME

    assert Intent.VOLUME_UP in _VOLUME
    assert "CEC" in _CEC_NOTE
