#!/usr/bin/env python3
"""Why isn't the Google TV showing up?

    python3 scripts/check_googletv.py

Browses mDNS for the remote service the TV must be advertising, and - just as
usefully - for the Chromecast service almost every Google TV also advertises.
The difference between the two tells you which problem you have:

  both found        -> it should be appearing; that is a bug, tell Claude
  cast only         -> mDNS works, but the remote service is not being
                       advertised. The TV is probably asleep, or it is not
                       actually a Google TV.
  neither found     -> mDNS is not crossing your network at all. Usually the
                       laptop and the TV are on different wifi bands, or the
                       router is not forwarding multicast.
"""

from __future__ import annotations

import sys
import time

SERVICES = {
    "_androidtvremote2._tcp.local.": "Android TV Remote (what we need)",
    "_googlecast._tcp.local.": "Chromecast (most Google TVs advertise this too)",
    "_airplay._tcp.local.": "AirPlay (other TVs, useful as a control)",
}


def main() -> int:
    try:
        from zeroconf import ServiceBrowser, ServiceListener, Zeroconf
    except ImportError:
        print("\nzeroconf is not installed, so mDNS discovery cannot run at all.")
        print("That alone would explain an empty scan.\n")
        print("  pip install -e '.[googletv]'\n")
        return 2

    import zeroconf as zc_module
    print(f"zeroconf {zc_module.__version__}")
    print("Browsing for 6 seconds...\n")

    found: dict[str, list[tuple[str, str]]] = {s: [] for s in SERVICES}

    class Listener(ServiceListener):
        def _record(self, zc, type_, name):
            info = zc.get_service_info(type_, name, timeout=3000)
            if info is None:
                return
            addresses = [a for a in info.parsed_addresses() if ":" not in a]
            for address in addresses:
                entry = (name.split(".")[0], address)
                if entry not in found[type_]:
                    found[type_].append(entry)

        add_service = update_service = _record

        def remove_service(self, zc, type_, name):
            return None

    zc = Zeroconf()
    try:
        for service in SERVICES:
            ServiceBrowser(zc, service, Listener())
        time.sleep(6)
    finally:
        zc.close()

    for service, label in SERVICES.items():
        hits = found[service]
        print(f"{label}")
        if hits:
            for name, address in hits:
                print(f"    {address:<16} {name}")
        else:
            print("    nothing")
        print()

    remote = found["_androidtvremote2._tcp.local."]
    cast = found["_googlecast._tcp.local."]

    if remote:
        print("The TV is advertising. Add it on the Devices screen, or:")
        print(f"  Google TV at {remote[0][1]}")
        return 0
    if cast:
        print("mDNS works - your network is fine - but the remote service is not")
        print("being advertised. Most likely the TV is asleep: turn it on with the")
        print("remote and run this again. Adding it by IP works regardless:")
        print(f"  try {cast[0][1]}")
        return 1
    print("Nothing answered on any service, so mDNS is not reaching this machine.")
    print("Usually the laptop is on one wifi band and the TV on the other, or the")
    print("router does not forward multicast between them. Find the IP on the TV")
    print("under Settings > Network, and add it by IP - that always works.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
