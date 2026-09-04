"""Google TV, including the pairing dance, with no TV on the network.

A real Google TV will not take a command until a six-digit code shown on its
screen has been typed back. That is the behaviour worth testing here: not the
protocol itself, which is the library's job, but that an unpaired TV is treated
as a normal state with a remedy rather than as a broken device.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from gesturectl.api.app import create_app
from gesturectl.devices.base import DeviceAdapter, Health, Result
from gesturectl.intents import Intent
from tests.test_api import make_config


class FakeGoogleTV(DeviceAdapter):
    def __init__(self, name: str, host: str, kind: str = "googletv") -> None:
        super().__init__(name)
        self.host = host
        self.kind = kind
        self.model = "TCL Google TV"
        self.is_tv = True
        self.needs_pairing = True          # as a real one is, the first time
        self.sent: list[Intent] = []
        self.pairing_started = False
        self.code = "123456"

    async def connect(self) -> None:
        self.capabilities = set() if self.needs_pairing else {
            Intent.VOLUME_UP, Intent.VOLUME_DOWN, Intent.MUTE_TOGGLE,
            Intent.PLAY_PAUSE, Intent.POWER_ON, Intent.POWER_OFF,
        }

    async def start_pairing(self) -> None:
        self.pairing_started = True

    async def finish_pairing(self, code: str) -> None:
        if not self.pairing_started:
            raise RuntimeError("pairing was not started")
        if code.strip() != self.code:
            raise RuntimeError("wrong code - the TV shows a new one each attempt")
        self.needs_pairing = False
        await self.connect()

    async def send(self, intent: Intent) -> Result:
        if self.needs_pairing:
            return Result(False, "not paired - enter the code shown on the TV")
        self.sent.append(intent)
        return Result(True, intent.value)

    async def health(self) -> Health:
        return Health(not self.needs_pairing, self.model)


def factory(name: str, host: str, kind: str = "roku") -> DeviceAdapter:
    from tests.test_api import FakeRoku

    return FakeGoogleTV(name, host) if kind == "googletv" else FakeRoku(name, host, kind)


@pytest.fixture
def client():
    app = create_app(make_config(), adapter_factory=factory)
    with TestClient(app) as c:
        yield c


def add_tv(client, host="192.168.68.77"):
    return client.post("/api/devices/by-host",
                       json={"host": host, "name": "tcl", "kind": "googletv"}).json()


def adapter_for(client, device_id):
    return client.app.state.hub.devices[device_id].adapter


# -- adding ------------------------------------------------------------------

def test_add_a_google_tv(client):
    body = add_tv(client)
    assert body["kind"] == "googletv"
    assert body["needs_pairing"] is True
    assert body["reachable"] is False, "unpaired is not reachable, but is not an error"


def test_unknown_device_kind_is_rejected(client):
    resp = client.post("/api/devices/by-host",
                       json={"host": "10.0.0.5", "kind": "firetv"})
    assert resp.status_code == 422        # rejected by the schema pattern


# -- pairing -----------------------------------------------------------------

def test_pairing_start_then_finish(client):
    device = add_tv(client)
    assert client.post(f"/api/devices/{device['id']}/pair/start").json()["ok"] is True
    assert adapter_for(client, device["id"]).pairing_started is True

    done = client.post(f"/api/devices/{device['id']}/pair/finish",
                       json={"code": "123456"}).json()
    assert done["needs_pairing"] is False
    assert done["reachable"] is True
    assert "VOLUME_UP" in done["capabilities"]


def test_a_wrong_code_is_a_400_with_the_reason(client):
    device = add_tv(client)
    client.post(f"/api/devices/{device['id']}/pair/start")
    resp = client.post(f"/api/devices/{device['id']}/pair/finish",
                       json={"code": "000000"})
    assert resp.status_code == 400
    assert "new one" in resp.json()["detail"]


def test_finishing_without_starting_fails_cleanly(client):
    device = add_tv(client)
    resp = client.post(f"/api/devices/{device['id']}/pair/finish",
                       json={"code": "123456"})
    assert resp.status_code == 400


def test_pairing_a_roku_is_a_400_not_a_crash(client):
    roku = client.get("/api/devices").json()[0]
    resp = client.post(f"/api/devices/{roku['id']}/pair/start")
    assert resp.status_code == 400
    assert "do not need pairing" in resp.json()["detail"]


def test_pairing_an_unknown_device_is_404(client):
    assert client.post("/api/devices/nope/pair/start").status_code == 404


# -- control -----------------------------------------------------------------

def test_an_unpaired_tv_refuses_rather_than_pretending(client):
    device = add_tv(client)
    client.post(f"/api/devices/{device['id']}/select")
    assert client.post("/api/intent", json={"intent": "VOLUME_UP"}).json()["ok"] is False


def test_a_paired_tv_takes_commands(client):
    device = add_tv(client)
    client.post(f"/api/devices/{device['id']}/pair/start")
    client.post(f"/api/devices/{device['id']}/pair/finish", json={"code": "123456"})
    client.post(f"/api/devices/{device['id']}/select")

    assert client.post("/api/intent", json={"intent": "VOLUME_UP"}).json()["ok"] is True
    assert adapter_for(client, device["id"]).sent == [Intent.VOLUME_UP]


def test_both_tvs_coexist_and_intents_go_to_the_selected_one(client):
    """Three devices on one network is the actual situation, so the selected
    device - not the first or the newest - must be the one that gets the key."""
    google = add_tv(client)
    client.post(f"/api/devices/{google['id']}/pair/start")
    client.post(f"/api/devices/{google['id']}/pair/finish", json={"code": "123456"})

    roku = next(d for d in client.get("/api/devices").json() if d["kind"] == "roku")
    client.post(f"/api/devices/{roku['id']}/select")
    client.post("/api/intent", json={"intent": "MUTE_TOGGLE"})
    assert adapter_for(client, roku["id"]).sent == [Intent.MUTE_TOGGLE]
    assert adapter_for(client, google["id"]).sent == []

    client.post(f"/api/devices/{google['id']}/select")
    client.post("/api/intent", json={"intent": "MUTE_TOGGLE"})
    assert adapter_for(client, google["id"]).sent == [Intent.MUTE_TOGGLE]
