"""The one stateful object. Routes are thin wrappers over this.

Poses come in from whatever is looking at a hand - a phone's camera, the laptop
webcam, or a test - and intents go out to whatever device is selected. The
session machine in between is unchanged from the version that has 47 tests
against it; this file only feeds it.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from ..config import AppConfig, DeviceConfig
from ..devices.base import DeviceAdapter
from ..devices.discover import discover_googletv, discover_roku
from ..intents import SESSION_ONLY, Intent, IntentMessage, Source
from ..session import SessionMachine
from .events import EventBus

log = logging.getLogger("gesturectl.hub")

#: A pose stream whose clock jumps backwards by more than this is a new client
#: (a reloaded page, a second phone). Carrying the old timestamps forward would
#: leave the session machine holding timers set in a future that never arrives.
_CLOCK_REWIND_MS = 1_000.0


def device_id(host: str) -> str:
    """Stable id from the host, so a rediscovered TV keeps its identity."""
    return (
        host.replace("http://", "").replace("https://", "")
        .replace(":", "-").replace(".", "-").rstrip("/")
    )


@dataclass(slots=True)
class DeviceRecord:
    id: str
    host: str
    name: str
    kind: str = "roku"
    adapter: DeviceAdapter | None = None
    model: str = "unknown"
    is_tv: bool = False
    reachable: bool = False
    power: str = "unknown"
    #: Google TV answers but refuses commands until a code is entered. That is a
    #: normal state with a normal remedy, not a broken device.
    needs_pairing: bool = False


def _default_adapter(name: str, host: str, kind: str = "roku") -> DeviceAdapter:
    if kind == "googletv":
        from ..devices.googletv import GoogleTVAdapter

        return GoogleTVAdapter(name, host)
    if kind == "roku":
        from ..devices.roku import RokuAdapter

        return RokuAdapter(name, host)
    raise ValueError(
        f"unknown device type {kind!r}. Known: roku, googletv. "
        "Fire TV is not implemented yet."
    )


class Hub:
    def __init__(self, config: AppConfig, adapter_factory=None) -> None:
        self.config = config
        #: (name, host) -> DeviceAdapter. Injectable so tests can drive the whole
        #: contract without a TV on the network.
        self.adapter_factory = adapter_factory or _default_adapter
        self.events = EventBus()
        self.devices: dict[str, DeviceRecord] = {}
        self.selected_id: str | None = None
        self._machine = self._build_machine()
        self._last_t_ms: float | None = None
        self._t0 = time.monotonic()

        for device_cfg in config.devices:
            if device_cfg.host:
                self.add_device(device_cfg.host, device_cfg.name,
                                select=device_cfg.default, kind=device_cfg.type)

    # -- session ------------------------------------------------------------

    def _build_machine(self) -> SessionMachine:
        return SessionMachine(
            self.config.session,
            self.config.bindings,
            target=self.selected_id or "default",
            hold_ms=self.config.hold_ms,
        )

    def reset_session(self) -> None:
        self._machine = self._build_machine()
        self._last_t_ms = None
        self._publish_state(0.0)

    def _server_ms(self) -> float:
        return (time.monotonic() - self._t0) * 1000.0

    async def ingest_pose(
        self, pose: str | None, confidence: float, t_ms: float | None
    ) -> IntentMessage | None:
        """One frame. Returns the intent it produced, if any."""
        # Prefer the sender's clock: it preserves the real spacing between
        # captures, which network jitter would otherwise smear. Fall back to
        # ours when it is absent or has plainly restarted.
        if t_ms is None:
            t_ms = self._server_ms()
        elif self._last_t_ms is not None and t_ms < self._last_t_ms - _CLOCK_REWIND_MS:
            log.info("pose clock went backwards - resetting session")
            self.reset_session()
            t_ms = self._server_ms()
        self._last_t_ms = t_ms

        message = self._machine.update(pose, confidence, t_ms)
        self._publish_state(t_ms, pose, confidence)

        if message is None:
            return None
        if message.intent in SESSION_ONLY:
            self.events.publish("intent", intent=message.intent.value,
                                target=message.target, result="session")
            return message
        await self.dispatch(message)
        return message

    def _publish_state(
        self, t_ms: float, pose: str | None = None, confidence: float = 0.0
    ) -> None:
        status = self._machine.status(t_ms)
        self.events.publish(
            "session_state",
            state=status.state.value,
            candidate=status.candidate.value if status.candidate else None,
            progress=round(status.progress, 3),
            pending=status.pending.value if status.pending else None,
            pose=pose,
            confidence=round(confidence, 3),
        )

    # -- devices ------------------------------------------------------------

    def add_device(self, host: str, name: str | None = None, select: bool = False,
                   kind: str = "roku") -> DeviceRecord:
        did = device_id(host)
        record = self.devices.get(did)
        if record is None:
            record = DeviceRecord(id=did, host=host, name=name or did, kind=kind)
            record.adapter = self.adapter_factory(record.name, host, kind)
            self.devices[did] = record
        if select or self.selected_id is None:
            self.select(did)
        return record

    def select(self, did: str) -> None:
        if did not in self.devices:
            raise KeyError(did)
        self.selected_id = did
        self._machine.target = self.devices[did].name
        self.events.publish("device_selected", id=did)

    @property
    def selected(self) -> DeviceRecord | None:
        return self.devices.get(self.selected_id) if self.selected_id else None

    async def refresh(self, record: DeviceRecord) -> None:
        if record.adapter is None:
            return
        try:
            await record.adapter.connect()
            health = await record.adapter.health()
            record.reachable = health.reachable
            record.model = getattr(record.adapter, "model", "unknown")
            record.is_tv = getattr(record.adapter, "is_tv", False)
            record.needs_pairing = getattr(record.adapter, "needs_pairing", False)
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI, not swallowed
            record.reachable = False
            log.info("device %s unreachable: %s", record.id, exc)
        self.events.publish(
            "device_status", id=record.id, reachable=record.reachable,
            model=record.model, is_tv=record.is_tv,
            needs_pairing=record.needs_pairing,
        )

    async def discover(self) -> list[DeviceRecord]:
        """Two protocols, two sweeps: Roku answers SSDP, Google TV answers mDNS.

        Run together rather than one after the other - both are fixed-duration
        listens, so doing them in sequence would double the wait for no reason.
        Both are blocking, hence the threads.
        """
        self.events.publish("discovery", scanning=True, found=[])
        roku_hosts, google_hosts = await asyncio.gather(
            asyncio.to_thread(discover_roku),
            asyncio.to_thread(discover_googletv),
        )
        found = [self.add_device(host, kind="roku") for host in roku_hosts]
        found += [self.add_device(host, kind="googletv") for host in google_hosts]
        for record in found:
            await self.refresh(record)
        self.events.publish("discovery", scanning=False, found=[r.id for r in found])
        return found

    # -- dispatch -----------------------------------------------------------

    async def dispatch(self, message: IntentMessage) -> bool:
        record = self.selected
        if record is None or record.adapter is None:
            self.events.publish("intent", intent=message.intent.value,
                                target=message.target, result="failed",
                                detail="no device selected")
            return False

        if record.adapter.capabilities and not record.adapter.supports(message.intent):
            self.events.publish("intent", intent=message.intent.value,
                                target=record.name, result="unsupported",
                                detail=f"{record.model} has no key for this")
            return False

        result = await record.adapter.send(message.intent)
        self.events.publish(
            "intent", intent=message.intent.value, target=record.name,
            result="ok" if result.ok else "failed", detail=result.detail,
            repeat=message.repeat,
        )
        return result.ok

    async def send_intent(self, intent: Intent, target: str | None = None) -> bool:
        """The on-screen remote. Travels the exact path a gesture takes."""
        return await self.dispatch(
            IntentMessage(intent=intent, target=target or (self.selected.name
                          if self.selected else "default"), source=Source.APP)
        )

    async def close(self) -> None:
        for record in self.devices.values():
            if record.adapter is not None:
                await record.adapter.close()
