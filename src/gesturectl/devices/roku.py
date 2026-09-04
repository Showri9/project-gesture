"""Roku External Control Protocol.

The easiest target in the whole project: a plain unauthenticated HTTP API on port
8060, documented publicly and essentially unchanged since 2011.

Two things this adapter refuses to assume:

1. That the device has volume and power keys. VolumeUp, VolumeDown, VolumeMute,
   PowerOff and the InputHDMI* keys exist on Roku *TVs*, not on sticks and boxes,
   so capabilities are read from query/device-info rather than hardcoded.
2. That PowerOn will work. A TV that fully powers down leaves the network and
   cannot be reached; PowerOn only works with "Fast TV Start" enabled under
   Settings > System > Power.

One more thing worth knowing: Roku OS 14.1 began requiring
Settings > System > Advanced system settings > Control by mobile apps >
Network access to be permissive before keypresses are accepted. Older firmware
generally just answers.
"""

from __future__ import annotations

import re

import httpx

from ..intents import Intent
from .base import DeviceAdapter, Health, Result
from .discover import discover_roku

#: Intents that map to a single keypress on any Roku.
_BASE_KEYS: dict[Intent, str] = {
    Intent.PLAY_PAUSE: "Play",
    Intent.NAV_UP: "Up",
    Intent.NAV_DOWN: "Down",
    Intent.NAV_LEFT: "Left",
    Intent.NAV_RIGHT: "Right",
    Intent.SELECT: "Select",
    Intent.BACK: "Back",
    Intent.HOME: "Home",
}

#: Intents that only exist on a Roku TV, not on a stick or box.
_TV_KEYS: dict[Intent, str] = {
    Intent.VOLUME_UP: "VolumeUp",
    Intent.VOLUME_DOWN: "VolumeDown",
    Intent.MUTE_TOGGLE: "VolumeMute",
}

_IS_TV = re.compile(r"<is-tv>(\w+)</is-tv>", re.IGNORECASE)
_POWER_MODE = re.compile(r"<power-mode>([\w\s]+)</power-mode>", re.IGNORECASE)
_MODEL = re.compile(r"<model-name>([^<]+)</model-name>", re.IGNORECASE)


class RokuAdapter(DeviceAdapter):
    def __init__(self, name: str, host: str | None = None, timeout: float = 3.0) -> None:
        super().__init__(name)
        self.base_url = self._normalize(host) if host else None
        self.is_tv = False
        self.model = "unknown"
        self._client = httpx.AsyncClient(timeout=timeout)

    @staticmethod
    def _normalize(host: str) -> str:
        host = host.strip().rstrip("/")
        if not host.startswith("http"):
            host = f"http://{host}"
        if ":" not in host.split("//", 1)[1]:
            host = f"{host}:8060"
        return host

    async def connect(self) -> None:
        if self.base_url is None:
            candidates = discover_roku()
            if not candidates:
                raise RuntimeError(
                    "No Roku found via SSDP. Set devices[].host in config.yaml, or "
                    "check that this machine is on the same subnet as the TV."
                )
            self.base_url = candidates[0]

        info = await self._client.get(f"{self.base_url}/query/device-info")
        info.raise_for_status()
        xml = info.text

        is_tv_match = _IS_TV.search(xml)
        self.is_tv = bool(is_tv_match) and is_tv_match.group(1).lower() == "true"
        model_match = _MODEL.search(xml)
        self.model = model_match.group(1) if model_match else "unknown"

        self.capabilities = set(_BASE_KEYS)
        if self.is_tv:
            self.capabilities |= set(_TV_KEYS)
            self.capabilities.add(Intent.POWER_TOGGLE)

    async def send(self, intent: Intent) -> Result:
        if self.base_url is None:
            return Result(False, "not connected")
        if intent is Intent.POWER_TOGGLE:
            return await self._power_toggle()

        key = _BASE_KEYS.get(intent) or _TV_KEYS.get(intent)
        if key is None:
            return Result(False, f"{self.model} has no key for {intent.value}")
        return await self._keypress(key)

    async def _keypress(self, key: str) -> Result:
        try:
            resp = await self._client.post(f"{self.base_url}/keypress/{key}")
        except httpx.HTTPError as exc:
            return Result(False, f"{type(exc).__name__}: {exc}")
        if resp.status_code >= 400:
            return Result(
                False,
                f"HTTP {resp.status_code} - on Roku OS 14.1+ check Settings > System > "
                "Advanced system settings > Control by mobile apps",
            )
        return Result(True, key)

    async def _power_toggle(self) -> Result:
        """PowerOn only reaches a TV that kept its network interface alive, which
        means Fast TV Start. Without it, off is a one-way trip."""
        try:
            info = await self._client.get(f"{self.base_url}/query/device-info")
            mode_match = _POWER_MODE.search(info.text)
            mode = mode_match.group(1).strip().lower() if mode_match else "poweron"
        except httpx.HTTPError as exc:
            return Result(False, f"could not read power mode: {exc}")

        if mode == "poweron":
            return await self._keypress("PowerOff")
        result = await self._keypress("PowerOn")
        if not result.ok:
            return Result(
                False,
                "PowerOn failed - enable Fast TV Start under Settings > System > Power",
            )
        return result

    async def health(self) -> Health:
        if self.base_url is None:
            return Health(False, "no host")
        try:
            resp = await self._client.get(f"{self.base_url}/query/device-info")
            return Health(resp.status_code < 400, f"{self.model} @ {self.base_url}")
        except httpx.HTTPError as exc:
            return Health(False, str(exc))

    async def close(self) -> None:
        await self._client.aclose()
