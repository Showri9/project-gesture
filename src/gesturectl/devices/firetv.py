"""Fire TV, over ADB on port 5555.

Fire OS is Android, but Amazon ships none of Google's remote service, so the
clean protocol used for Google TV is not available here. ADB is what is left.
That is a real downside and worth stating plainly: it means leaving developer
debugging switched on. It is the only route Amazon offers.

Authorisation works differently from Google TV's six-digit code. The first
connection puts an RSA fingerprint prompt on the television and waits; there is
nothing to type, only something to accept. Both end up in the same place - a
device that answers but refuses commands until a person walks over - so both
report `needs_pairing`, and `pairing_kind` tells the interface which words to
use.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..intents import Intent
from .base import DeviceAdapter, Health, Result

log = logging.getLogger("gesturectl.firetv")

#: Android key codes. Sent as `input keyevent <n>`.
_KEYS: dict[Intent, int] = {
    Intent.VOLUME_UP: 24,
    Intent.VOLUME_DOWN: 25,
    Intent.MUTE_TOGGLE: 164,
    Intent.PLAY_PAUSE: 85,
    Intent.NAV_UP: 19,
    Intent.NAV_DOWN: 20,
    Intent.NAV_LEFT: 21,
    Intent.NAV_RIGHT: 22,
    Intent.SELECT: 23,
    Intent.BACK: 4,
    Intent.HOME: 3,
    # Android has explicit SLEEP and WAKEUP codes, so power needs none of the
    # read-the-state-first guesswork that KEYCODE_POWER would.
    Intent.POWER_OFF: 223,
    Intent.POWER_ON: 224,
}

#: Fire TV normally hands audio to the television over HDMI-CEC, so a volume
#: key can be accepted by the stick and change nothing you can hear. Said out
#: loud on the result, because otherwise it reads as a broken gesture.
_CEC_NOTE = "sent — Fire TV usually routes volume to the TV over HDMI-CEC"
_VOLUME = {Intent.VOLUME_UP, Intent.VOLUME_DOWN, Intent.MUTE_TOGGLE}


class FireTVAdapter(DeviceAdapter):
    #: the interface asks for a code for Google TV, and for a tap here
    pairing_kind = "confirm"

    def __init__(self, name: str, host: str, key_dir: str | Path = "certs",
                 port: int = 5555) -> None:
        super().__init__(name)
        self.host = self._bare_host(host)
        self.port = port
        self.base_url = f"{self.host}:{port}"
        self.model = "Fire TV"
        self.is_tv = True
        self.needs_pairing = False
        #: True / False once the manufacturer has been read; None while it
        #: cannot be (an unauthorised device will not run shell commands).
        self.verified: bool | None = None

        key_dir = Path(key_dir)
        key_dir.mkdir(parents=True, exist_ok=True)
        self._key_path = key_dir / "adbkey"
        self._device = None

    @staticmethod
    def _bare_host(host: str) -> str:
        host = host.strip().replace("http://", "").replace("https://", "").rstrip("/")
        return host.split(":", 1)[0]

    def _signer(self):
        from adb_shell.auth.keygen import keygen
        from adb_shell.auth.sign_cryptography import CryptographySigner

        if not self._key_path.exists():
            keygen(str(self._key_path))
            log.info("generated an ADB key at %s", self._key_path)
        return CryptographySigner(str(self._key_path))

    # -- lifecycle ----------------------------------------------------------

    async def connect(self, auth_timeout_s: float = 3.0) -> None:
        """A device awaiting the on-screen prompt is not an error.

        The short auth timeout is deliberate: on a first connection nobody has
        accepted anything yet, and blocking the whole app for the library's
        default while a prompt sits unread on a television is the wrong
        trade. Pairing raises it, because then somebody is standing there.
        """
        from adb_shell.adb_device_async import AdbDeviceTcpAsync
        from adb_shell.exceptions import DeviceAuthError, TcpTimeoutException

        if self._device is not None:
            try:
                await self._device.close()
            except Exception:  # noqa: BLE001 - reconnect with a fresh transport
                log.debug("failed to close stale ADB transport", exc_info=True)
        self._device = AdbDeviceTcpAsync(self.host, self.port,
                                         default_transport_timeout_s=5.0)
        try:
            await self._device.connect(rsa_keys=[self._signer()],
                                       auth_timeout_s=auth_timeout_s)
        except DeviceAuthError:
            self.needs_pairing = True
            self.capabilities = set()
            log.info("%s is waiting for the prompt on screen", self.host)
            return
        except (TcpTimeoutException, OSError) as exc:
            self.capabilities = set()
            raise RuntimeError(
                f"cannot reach {self.host}:{self.port} — is ADB debugging on? "
                "Settings > My Fire TV > Developer Options > ADB Debugging"
            ) from exc

        self.needs_pairing = False
        self.capabilities = set(_KEYS)
        await self._read_model()

    async def _read_model(self) -> None:
        try:
            name = (await self._device.shell("getprop ro.product.model")).strip()
            if name:
                self.model = name
            maker = (await self._device.shell("getprop ro.product.manufacturer")).strip()
            if maker:
                self.verified = maker.lower() == "amazon"
        except Exception:  # noqa: BLE001 - cosmetic, never worth failing on
            pass

    # -- authorisation ------------------------------------------------------

    async def start_pairing(self) -> None:
        """Provokes the prompt. There is no code - the fingerprint appears on
        the television and somebody has to accept it."""
        await self.connect(auth_timeout_s=0.5)

    async def finish_pairing(self, code: str = "") -> None:
        """`code` is ignored; Fire TV has none. This is the retry after the
        prompt was accepted, with a long timeout because a person is now
        standing in front of the television doing the accepting."""
        await self.connect(auth_timeout_s=30.0)
        if self.needs_pairing:
            raise RuntimeError(
                "still not authorised — accept the prompt on the TV, and tick "
                "'always allow from this computer' so it is not asked again"
            )

    # -- control ------------------------------------------------------------

    async def send(self, intent: Intent) -> Result:
        if self._device is None:
            return Result(False, "not connected")
        if self.needs_pairing:
            return Result(False, "not authorised — accept the prompt on the TV")

        code = _KEYS.get(intent)
        if code is None:
            return Result(False, f"no key for {intent.value}")

        try:
            if not self._device.available:
                await self.connect()
                if self.needs_pairing or not self._device.available:
                    return Result(False, "connection lost")
            await self._device.shell(f"input keyevent {code}")
        except Exception as exc:  # noqa: BLE001 - reported to the UI, not hidden
            return Result(False, f"{type(exc).__name__}: {exc}")

        return Result(True, _CEC_NOTE if intent in _VOLUME else str(code))

    async def health(self) -> Health:
        if self._device is None:
            return Health(False, "not connected")
        if self.needs_pairing:
            return Health(False, "waiting for the prompt on screen")
        return Health(bool(self._device.available), f"{self.model} @ {self.host}")

    async def close(self) -> None:
        if self._device is not None:
            try:
                await self._device.close()
            except Exception:  # noqa: BLE001
                pass
            self._device = None
