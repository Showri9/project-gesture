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


def test_firetv_falls_back_to_adb_when_mdns_finds_nothing(monkeypatch):
    monkeypatch.setattr(discover, "_mdns_sweep", lambda service, timeout: [])
    monkeypatch.setattr(discover, "_adb_sweep", lambda: ["10.0.0.9"])
    assert discover.discover_firetv() == ["10.0.0.9"]


def test_firetv_skips_the_fallback_when_mdns_answers(monkeypatch):
    monkeypatch.setattr(discover, "_mdns_sweep", lambda service, timeout: ["10.0.0.8"])

    def boom():
        raise AssertionError("fallback should not run")

    monkeypatch.setattr(discover, "_adb_sweep", boom)
    assert discover.discover_firetv() == ["10.0.0.8"]
