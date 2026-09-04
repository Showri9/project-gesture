#!/usr/bin/env python3
"""Step 1. Prove the TV is controllable before writing anything else.

Standard library only - run it before you install a single dependency.

    python3 scripts/check_roku.py                 # discover and report
    python3 scripts/check_roku.py --poke          # wake if asleep, then nudge volume
    python3 scripts/check_roku.py --poke --no-wake  # poke without waking (to reproduce a hang)
    python3 scripts/check_roku.py 192.168.68.84 --poke

GET and POST fail differently on Roku, and the difference is the diagnosis:

  403 / 401  -> keypresses are blocked by a setting, the TV is fine
  timeout    -> nothing answered at all; usually the panel is asleep
  200        -> working
"""

from __future__ import annotations

import argparse
import http.client
import re
import socket
import sys
import time
import urllib.parse

SSDP_ADDR = ("239.255.255.250", 1900)
MSEARCH = (
    "M-SEARCH * HTTP/1.1\r\n"
    "HOST: 239.255.255.250:1900\r\n"
    'MAN: "ssdp:discover"\r\n'
    "ST: roku:ecp\r\n"
    "MX: 3\r\n\r\n"
).encode()

#: power-mode values that mean the panel is actually on
AWAKE = {"poweron"}

TIMEOUT = object()  # sentinel: the request went unanswered


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


def _conn(base: str, timeout: float) -> http.client.HTTPConnection:
    parts = urllib.parse.urlparse(base)
    return http.client.HTTPConnection(parts.hostname, parts.port or 8060, timeout=timeout)


def request(base: str, method: str, path: str, timeout: float = 5.0):
    """Return (status, body) or (TIMEOUT, reason). Explicit Content-Length,
    because Roku's HTTP server is fussy about bodyless POSTs."""
    conn = _conn(base, timeout)
    try:
        conn.request(method, path, body=b"", headers={"Content-Length": "0"})
        resp = conn.getresponse()
        return resp.status, resp.read().decode("utf-8", "replace")
    except (socket.timeout, TimeoutError):
        return TIMEOUT, "no response within %.0fs" % timeout
    except OSError as exc:
        return TIMEOUT, str(exc)
    finally:
        conn.close()


def field(xml: str, tag: str) -> str:
    m = re.search(rf"<{tag}>([^<]*)</{tag}>", xml, re.I)
    return m.group(1).strip() if m else "?"


def explain_failure(status, note: str) -> None:
    if status is TIMEOUT:
        print(f"  TIMED OUT ({note})")
        print()
        print("  A timeout is not a permissions problem - a blocked keypress")
        print("  answers 403 immediately. Nothing answered at all, which means:")
        print()
        print("   1. The panel is asleep. Turn the TV on with the remote and re-run.")
        print("      (--poke wakes it first; --no-wake skips that, to reproduce this.)")
        print("   2. If it hangs with the TV visibly ON, reboot the TV:")
        print("      Settings > System > Power > System restart. Roku's ECP server")
        print("      is known to wedge until a restart on recent firmware.")
        return
    if status in (401, 403):
        print(f"  REFUSED (HTTP {status})")
        print()
        print("  The TV answered, it just won't accept keypresses.")
        print("  On the TV, with the physical remote:")
        print()
        print("    Settings > System > Advanced system settings >")
        print("    Control by mobile apps > Network access")
        print()
        print("  Yours is set to Disabled. Set it to Default.")
        print("  Default  - accepts control from the same subnet (what you want)")
        print("  Permissive - also accepts it from other subnets; only needed if")
        print("               your laptop and TV sit on different networks")
        print("  Roku OS 14.1+ enforces this; older firmware ignored it.")
        return
    print(f"  Unexpected HTTP {status}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host", nargs="?", help="IP or URL; omit to discover")
    parser.add_argument("--poke", action="store_true", help="actually send keypresses")
    parser.add_argument("--no-wake", action="store_true",
                        help="don't send PowerOn first, even if the TV is asleep")
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()

    if args.host:
        base = args.host if args.host.startswith("http") else f"http://{args.host}:8060"
        targets = [base.rstrip("/")]
    else:
        print("Searching the LAN for a Roku (SSDP, 4s)...")
        targets = discover()
        if not targets:
            print("\n  Nothing answered.")
            print("  - same wifi/subnet as the TV?")
            print("  - some routers block multicast between 2.4GHz and 5GHz bands")
            print("  - try naming the IP: python3 scripts/check_roku.py 192.168.68.84")
            return 1
        print(f"  found {len(targets)}: {', '.join(targets)}\n")

    ok = True
    for base in targets:
        print(f"=== {base} ===")
        status, body = request(base, "GET", "/query/device-info", args.timeout)
        if status is TIMEOUT or status >= 400:
            print(f"  device-info failed: {body}\n")
            ok = False
            continue

        is_tv = field(body, "is-tv").lower() == "true"
        power = field(body, "power-mode")
        awake = power.lower() in AWAKE

        print(f"  model        {field(body, 'model-name')}")
        print(f"  software     Roku OS {field(body, 'software-version')}")
        print(f"  power-mode   {power}" + ("" if awake else "   <- panel is ASLEEP"))
        print(f"  is-tv        {is_tv}")
        print()

        if is_tv:
            print("  Volume, mute and power keys are available on this device.")
        else:
            print("  Stick or box, not a TV: no volume or power keys.")
        if not awake:
            print("  Reachable while off, so Fast TV Start is already enabled -")
            print("  PowerOn will work. (A fully-off Roku TV answers nothing at all.)")
        print()

        if not args.poke:
            print("  Re-run with --poke to actually move the volume.\n")
            continue

        if not awake and not args.no_wake:
            print("  Waking the TV (PowerOn)...")
            status, note = request(base, "POST", "/keypress/PowerOn", args.timeout)
            if status is TIMEOUT or status >= 400:
                explain_failure(status, note)
                ok = False
                print()
                continue
            print("  sent. Waiting 6s for the panel to come up...")
            time.sleep(6)

        print("  Poking VolumeUp, then VolumeDown - listen for a change...")
        up, up_note = request(base, "POST", "/keypress/VolumeUp", args.timeout)
        time.sleep(0.4)
        down, _ = request(base, "POST", "/keypress/VolumeDown", args.timeout)

        if up is not TIMEOUT and up < 400:
            print(f"  HTTP {up} / {down}   ECP is working. You are unblocked.")
        else:
            explain_failure(up, up_note)
            ok = False
        print()

    if ok and args.poke:
        print("Next: python3 scripts/range_test.py, standing where you watch TV.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
