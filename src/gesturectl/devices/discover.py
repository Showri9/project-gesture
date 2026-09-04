"""Find devices on the LAN without asking the user for IP addresses."""

from __future__ import annotations

import re
import socket
import time

_SSDP_ADDR = ("239.255.255.250", 1900)
_ROKU_MSEARCH = (
    "M-SEARCH * HTTP/1.1\r\n"
    "HOST: 239.255.255.250:1900\r\n"
    'MAN: "ssdp:discover"\r\n'
    "ST: roku:ecp\r\n"
    "MX: 3\r\n"
    "\r\n"
).encode()

_LOCATION = re.compile(rb"^LOCATION:\s*(\S+)", re.IGNORECASE | re.MULTILINE)


def discover_roku(timeout: float = 4.0) -> list[str]:
    """Return base URLs like http://192.168.1.20:8060 for every Roku that answers.

    Blocking on purpose - it runs once at startup, and UDP multicast in asyncio is
    more ceremony than this deserves. Note this needs the machine to be on the
    same subnet as the TV, and some routers block multicast between wifi bands.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    sock.settimeout(0.5)

    found: list[str] = []
    deadline = time.monotonic() + timeout
    try:
        sock.sendto(_ROKU_MSEARCH, _SSDP_ADDR)
        while time.monotonic() < deadline:
            try:
                data, _addr = sock.recvfrom(2048)
            except (socket.timeout, TimeoutError):
                continue
            match = _LOCATION.search(data)
            if not match:
                continue
            url = match.group(1).decode().rstrip("/")
            if url not in found:
                found.append(url)
    except OSError:
        pass
    finally:
        sock.close()
    return found
