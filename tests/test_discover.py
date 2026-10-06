"""The Fire TV fallback: mDNS first, then a direct look for open ADB ports."""

from __future__ import annotations

import socket

from gesturectl.devices import discover


def test_adb_sweep_finds_only_hosts_with_the_port_open(monkeypatch):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    monkeypatch.setattr(discover, "_ADB_PORT", listener.getsockname()[1])
    try:
        assert discover._adb_sweep(["127.0.0.1", "127.0.0.2"], timeout=0.3) == ["127.0.0.1"]
    finally:
        listener.close()


def test_firetv_discovery_is_mdns_only(monkeypatch):
    """An open ADB port is not proof of a Fire TV, so discover_firetv must not
    quietly widen into the port sweep."""
    monkeypatch.setattr(discover, "_mdns_sweep", lambda service, timeout: [])

    def boom():
        raise AssertionError("port sweep must not run from discover_firetv")

    monkeypatch.setattr(discover, "_adb_sweep", boom)
    assert discover.discover_firetv() == []


def test_adb_candidates_come_from_the_port_sweep(monkeypatch):
    monkeypatch.setattr(discover, "_adb_sweep", lambda: ["10.0.0.9"])
    assert discover.discover_adb_candidates() == ["10.0.0.9"]
