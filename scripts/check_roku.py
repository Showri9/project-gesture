#!/usr/bin/env python3
"""Step 1. Prove the TV is controllable before writing anything else.

Standard library only - run it before you install a single dependency:

    python3 scripts/check_roku.py              # discover on the LAN
    python3 scripts/check_roku.py 192.168.1.20 # or name the host

It reports what the device is, what it can do, and - with --poke - actually
nudges the volume so you can confirm with your own ears.
"""

from __future__ import annotations

import re
import socket
import sys
import time
import urllib.error
import urllib.request

SSDP_ADDR = ("239.255.255.250", 1900)
MSEARCH = (
    "M-SEARCH * HTTP/1.1\r\n"
    "HOST: 239.255.255.250:1900\r\n"
    'MAN: "ssdp:discover"\r\n'
    "ST: roku:ecp\r\n"
    "MX: 3\r\n\r\n"
).encode()


def discover(timeout: float = 4.0) -> list[str]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    sock.settimeout(0.5)
    found: list[str] = []
    deadline = time.monotonic() + timeout
    try:
        sock.sendto(MSEARCH, SSDP_ADDR)
        while time.monotonic() < deadline:
            try:
                data, _ = sock.recvfrom(2048)
            except (socket.timeout, TimeoutError):
                continue
            m = re.search(rb"^LOCATION:\s*(\S+)", data, re.I | re.M)
            if m:
                url = m.group(1).decode().rstrip("/")
                if url not in found:
                    found.append(url)
    finally:
        sock.close()
    return found


def field(xml: str, tag: str) -> str:
    m = re.search(rf"<{tag}>([^<]*)</{tag}>", xml, re.I)
    return m.group(1).strip() if m else "?"


def get(url: str, timeout: float = 4.0) -> str:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def post(url: str, timeout: float = 4.0) -> int:
    req = urllib.request.Request(url, data=b"", method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    poke = "--poke" in sys.argv

    if args:
        host = args[0]
        base = host if host.startswith("http") else f"http://{host}:8060"
        targets = [base.rstrip("/")]
    else:
        print("Searching the LAN for a Roku (SSDP, 4s)...")
        targets = discover()
        if not targets:
            print("\n  Nothing answered.")
            print("  - is this machine on the same wifi/subnet as the TV?")
            print("  - some routers block multicast between 2.4GHz and 5GHz bands")
            print("  - try again naming the IP: python3 scripts/check_roku.py 192.168.1.20")
            return 1
        print(f"  found {len(targets)}: {', '.join(targets)}\n")

    ok = True
    for base in targets:
        print(f"=== {base} ===")
        try:
            xml = get(f"{base}/query/device-info")
        except Exception as exc:  # noqa: BLE001
            print(f"  UNREACHABLE: {exc}\n")
            ok = False
            continue

        is_tv = field(xml, "is-tv").lower() == "true"
        print(f"  model        {field(xml, 'model-name')}")
        print(f"  software     {field(xml, 'software-version')}")
        print(f"  power-mode   {field(xml, 'power-mode')}")
        print(f"  is-tv        {is_tv}")

        if is_tv:
            print("\n  -> Volume, mute and power keys are available.")
        else:
            print("\n  -> This is a stick or box, not a TV: no volume or power keys.")
            print("     Volume will have to come from a TV adapter or HDMI-CEC.")

        if poke:
            print("\n  Poking VolumeUp then VolumeDown - listen for a change...")
            up = post(f"{base}/keypress/VolumeUp")
            time.sleep(0.4)
            down = post(f"{base}/keypress/VolumeDown")
            print(f"  HTTP {up} / {down}")
            if up >= 400:
                print("\n  Refused. On Roku OS 14.1+ enable:")
                print("  Settings > System > Advanced system settings >")
                print("  Control by mobile apps > Network access")
                ok = False
            else:
                print("  Accepted. ECP is working.")
        else:
            print("\n  Re-run with --poke to actually move the volume.")
        print()

    if ok:
        print("Next: enable Fast TV Start (Settings > System > Power) so PowerOn works,")
        print("then run scripts/range_test.py from the sofa.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
