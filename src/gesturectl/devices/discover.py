"""Find devices on the LAN without asking the user for IP addresses."""

from __future__ import annotations

import logging
import re
import socket
import time
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger("gesturectl.discover")

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


def _ssdp_msearch(st: str) -> bytes:
    return (
        "M-SEARCH * HTTP/1.1\r\n"
        "HOST: 239.255.255.250:1900\r\n"
        'MAN: "ssdp:discover"\r\n'
        f"ST: {st}\r\n"
        "MX: 2\r\n\r\n"
    ).encode()


def discover_roku(timeout: float = 5.0) -> list[str]:
    """Return base URLs like http://192.168.1.20:8060 for every Roku that answers.

    SSDP is UDP multicast, which is lossy: a single M-SEARCH gets dropped
    routinely, most often between a router's 2.4GHz and 5GHz bands. So the
    query is re-announced across the listen window rather than sent once, and a
    targeted search that finds nothing falls back to a broad one filtered on
    port 8060, for devices that ignore the targeted form.

    Blocking on purpose - multicast in asyncio is more ceremony than this
    deserves, and the caller runs it in a thread.
    """
    for st in ("roku:ecp", "ssdp:all"):
        found = _ssdp_sweep(st, timeout)
        if found:
            return found
    return []


def _ssdp_sweep(st: str, timeout: float) -> list[str]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    sock.settimeout(0.4)

    packet = _ssdp_msearch(st)
    found: list[str] = []
    deadline = time.monotonic() + timeout
    next_send = 0.0
    try:
        while time.monotonic() < deadline:
            now = time.monotonic()
            if now >= next_send:
                try:
                    sock.sendto(packet, _SSDP_ADDR)
                except OSError as exc:
                    # Worth saying out loud. Swallowing this made a broken
                    # socket look exactly like an empty network.
                    log.info("SSDP send failed (%s): %s", st, exc)
                next_send = now + 1.2
            try:
                data, _addr = sock.recvfrom(2048)
            except (socket.timeout, TimeoutError):
                continue
            except OSError as exc:
                log.info("SSDP receive failed (%s): %s", st, exc)
                break
            match = _LOCATION.search(data)
            if not match:
                continue
            url = match.group(1).decode().rstrip("/")
            if ":8060" not in url:        # ssdp:all returns every device on the LAN
                continue
            parts = url.split("/", 3)
            base = "/".join(parts[:3]) if len(parts) >= 3 else url
            if base not in found:
                found.append(base)
    finally:
        sock.close()
    return found


_GOOGLETV_SERVICE = "_androidtvremote2._tcp.local."
_FIRETV_SERVICE = "_amzn-wplay._tcp.local."


def mdns_discovery_available() -> bool:
    """False when zeroconf is not installed. Worth reporting rather than
    letting an empty result look like an empty network."""
    try:
        import zeroconf  # noqa: F401
    except ImportError:
        return False
    return True


#: How long to wait for one service record to come back. This MUST be well
#: under the browse window: get_service_info blocks the browser's own callback
#: thread, so if it can run as long as the window itself, the sweep can end
#: while the only answer it got is still being resolved - and return nothing at
#: all from a network where the TV was advertising perfectly well.
_INFO_TIMEOUT_MS = 2000


def _mdns_sweep(service: str, timeout: float) -> list[str]:
    """Browse one mDNS service and return the bare IPv4 addresses answering it.

    Returns nothing rather than raising when zeroconf is absent: both mDNS
    device types are optional extras, and a missing one must not break
    discovery for someone who only owns a Roku.
    """
    if not mdns_discovery_available():
        return []
    from zeroconf import ServiceBrowser, ServiceListener, Zeroconf

    found: list[str] = []

    class _Listener(ServiceListener):
        def _record(self, zc, type_, name) -> None:
            info = zc.get_service_info(type_, name, timeout=_INFO_TIMEOUT_MS)
            if info is None:
                return
            for address in info.parsed_addresses():
                if ":" not in address and address not in found:   # IPv4 only
                    found.append(address)

        def add_service(self, zc, type_, name) -> None:
            self._record(zc, type_, name)

        def update_service(self, zc, type_, name) -> None:
            self._record(zc, type_, name)

        def remove_service(self, zc, type_, name) -> None:
            return None

    zc = Zeroconf()
    try:
        ServiceBrowser(zc, service, _Listener())
        time.sleep(timeout)
    except OSError:
        pass
    finally:
        zc.close()
    return found


def discover_googletv(timeout: float = 5.0) -> list[str]:
    """Google TV and Android TV, via the remote service they advertise."""
    return _mdns_sweep(_GOOGLETV_SERVICE, timeout)


def discover_firetv(timeout: float = 5.0) -> list[str]:
    """Fire TV, via Amazon's own service record.

    Note this finds the device whether or not ADB is enabled on it - being
    discoverable and being controllable are separate things here, and the
    adapter reports the difference rather than discovery hiding it.
    """
    return _mdns_sweep(_FIRETV_SERVICE, timeout)


def discover_adb_candidates() -> list[str]:
    """Hosts with the ADB port open - candidates, NOT confirmed Fire TVs.

    The fallback for when mDNS finds nothing: a Fire TV stops advertising when
    it sleeps, and some routers drop multicast altogether. An open port 5555 is
    shared by every Android device with debugging on, including Google TVs, so
    the caller must discard hosts another protocol already claimed and must
    confirm the manufacturer once the device is authorised.
    """
    return _adb_sweep()


_ADB_PORT = 5555


def _local_prefix() -> str | None:
    """'192.168.68.' for the network this machine is on, or None.

    Connecting a UDP socket sends nothing; it only asks the OS which interface
    would be used, which is the cheap way to learn our own address.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("10.255.255.255", 1))
        ip = sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()
    return ip.rsplit(".", 1)[0] + "." if ip.count(".") == 3 else None


def _adb_sweep(hosts: list[str] | None = None, timeout: float = 0.6) -> list[str]:
    """Hosts with the ADB port open. Scans the local /24 unless given a list.

    This cannot tell a Fire TV from any other Android device with debugging on,
    so it is only the fallback: the adapter reports the real model once it
    connects, and the interface shows it.
    """
    if hosts is None:
        prefix = _local_prefix()
        if prefix is None:
            return []
        hosts = [f"{prefix}{i}" for i in range(1, 255)]

    def open_port(host: str) -> bool:
        try:
            with socket.create_connection((host, _ADB_PORT), timeout=timeout):
                return True
        except OSError:
            return False

    with ThreadPoolExecutor(max_workers=64) as pool:
        results = list(pool.map(open_port, hosts))
    return [host for host, ok in zip(hosts, results) if ok]


#: kept for callers that only care about the Google TV case
googletv_discovery_available = mdns_discovery_available
