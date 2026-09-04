"""Google TV / Android TV, over the Android TV Remote Service v2.

Chosen over ADB deliberately. ADB works, but it means leaving developer-mode
debugging permanently enabled on a television in the living room, which is a
poor default for something meant to be used rather than tinkered with. The
remote protocol is what the official app speaks: TLS on 6466, a one-time pairing
with a six-digit code shown on screen, and no developer options at all.

The cost is that pairing is a real step with real UI, not a config value. That
shows up here as three extra methods and a `needs_pairing` flag the API surfaces
so the interface can ask for the code.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from ..intents import Intent
from .base import DeviceAdapter, Health, Result

log = logging.getLogger("gesturectl.googletv")

#: Android key codes, by intent. The library accepts these names directly.
_KEYS: dict[Intent, str] = {
    Intent.VOLUME_UP: "VOLUME_UP",
    Intent.VOLUME_DOWN: "VOLUME_DOWN",
    Intent.MUTE_TOGGLE: "VOLUME_MUTE",
    Intent.PLAY_PAUSE: "MEDIA_PLAY_PAUSE",
    Intent.NAV_UP: "DPAD_UP",
    Intent.NAV_DOWN: "DPAD_DOWN",
    Intent.NAV_LEFT: "DPAD_LEFT",
    Intent.NAV_RIGHT: "DPAD_RIGHT",
    Intent.SELECT: "DPAD_CENTER",
    Intent.BACK: "BACK",
    Intent.HOME: "HOME",
}

_ALL = set(_KEYS) | {Intent.POWER_ON, Intent.POWER_OFF, Intent.POWER_TOGGLE}


class GoogleTVAdapter(DeviceAdapter):
    def __init__(
        self,
        name: str,
        host: str,
        cert_dir: str | Path = "certs",
        client_name: str = "gesturectl",
    ) -> None:
        super().__init__(name)
        self.host = self._bare_host(host)
        self.base_url = f"{self.host}:6466"
        self.model = "Google TV"
        self.is_tv = True
        #: set when the TV answered but has not been paired with us yet
        self.needs_pairing = False
        self._pairing = False

        cert_dir = Path(cert_dir)
        cert_dir.mkdir(parents=True, exist_ok=True)
        slug = self.host.replace(".", "-")
        self._certfile = str(cert_dir / f"googletv-{slug}-cert.pem")
        self._keyfile = str(cert_dir / f"googletv-{slug}-key.pem")
        self._client_name = client_name
        self._remote = None

    @staticmethod
    def _bare_host(host: str) -> str:
        host = host.strip().replace("http://", "").replace("https://", "").rstrip("/")
        return host.split(":", 1)[0]

    def _build(self):
        from androidtvremote2 import AndroidTVRemote

        return AndroidTVRemote(
            client_name=self._client_name,
            certfile=self._certfile,
            keyfile=self._keyfile,
            host=self.host,
        )

    # -- lifecycle ----------------------------------------------------------

    async def connect(self) -> None:
        """Never raises on an unpaired TV. Not being paired yet is a normal
        state with a normal remedy, and the interface needs to be able to say so
        rather than showing the device as broken."""
        from androidtvremote2 import CannotConnect, InvalidAuth

        self._remote = self._build()
        try:
            await self._remote.async_generate_cert_if_missing()
            await self._remote.async_connect()
        except InvalidAuth:
            self.needs_pairing = True
            self.capabilities = set()
            log.info("%s needs pairing", self.host)
            return
        except (CannotConnect, OSError) as exc:
            self.capabilities = set()
            raise RuntimeError(f"cannot reach {self.host}: {exc}") from exc

        self.needs_pairing = False
        self.capabilities = set(_ALL)
        info = getattr(self._remote, "device_info", None) or {}
        self.model = info.get("model") or "Google TV"
        # Reconnect in the background; a TV that sleeps drops the socket, and a
        # gesture made on waking should not be the thing that discovers that.
        self._remote.keep_reconnecting(self._on_auth_lost)

    def _on_auth_lost(self) -> None:
        self.needs_pairing = True
        self.capabilities = set()

    # -- pairing ------------------------------------------------------------

    async def start_pairing(self) -> None:
        """Ask the TV to show a six-digit code."""
        if self._remote is None:
            self._remote = self._build()
            await self._remote.async_generate_cert_if_missing()
        await self._remote.async_start_pairing()
        self._pairing = True

    async def finish_pairing(self, code: str) -> None:
        from androidtvremote2 import InvalidAuth

        if self._remote is None or not self._pairing:
            raise RuntimeError("pairing was not started")
        try:
            await self._remote.async_finish_pairing(code.strip())
        except InvalidAuth as exc:
            raise RuntimeError("wrong code - the TV shows a new one each attempt") from exc
        finally:
            self._pairing = False
        await self.connect()

    # -- control ------------------------------------------------------------

    async def send(self, intent: Intent) -> Result:
        from androidtvremote2 import ConnectionClosed

        if self._remote is None:
            return Result(False, "not connected")
        if self.needs_pairing:
            return Result(False, "not paired - enter the code shown on the TV")

        if intent in (Intent.POWER_ON, Intent.POWER_OFF, Intent.POWER_TOGGLE):
            return await self._power(intent)

        key = _KEYS.get(intent)
        if key is None:
            return Result(False, f"no key for {intent.value}")
        return self._press(key)

    async def _power(self, intent: Intent) -> Result:
        """POWER is a toggle on Android, so explicit on and off have to look
        first. is_on can be None when the TV has not reported yet - in that case
        send the toggle rather than refuse, since doing nothing is the worse
        failure for a power button."""
        is_on = getattr(self._remote, "is_on", None)
        if intent is Intent.POWER_ON and is_on is True:
            return Result(True, "already on")
        if intent is Intent.POWER_OFF and is_on is False:
            return Result(True, "already off")
        return self._press("POWER")

    def _press(self, key: str) -> Result:
        from androidtvremote2 import ConnectionClosed

        try:
            self._remote.send_key_command(key)
        except ConnectionClosed:
            return Result(False, "connection dropped - it will reconnect, try again")
        except ValueError as exc:
            return Result(False, f"unknown key {key}: {exc}")
        return Result(True, key)

    async def health(self) -> Health:
        if self._remote is None:
            return Health(False, "not connected")
        if self.needs_pairing:
            return Health(False, "needs pairing")
        return Health(True, f"{self.model} @ {self.host}"
                      + (" (on)" if getattr(self._remote, "is_on", None) else ""))

    async def close(self) -> None:
        if self._remote is not None:
            with_disconnect = getattr(self._remote, "disconnect", None)
            if with_disconnect:
                await asyncio.get_running_loop().run_in_executor(None, with_disconnect)
            self._remote = None
