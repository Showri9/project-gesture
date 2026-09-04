"""Wire it together and run.

    gesturectl                 # uses ./config.yaml
    gesturectl --config x.yaml
    gesturectl --dry-run       # detect and log intents, send nothing to the TV

The camera loop runs on the main thread (OpenCV windows insist on it) and device
dispatch runs on an asyncio loop in a background thread, so a slow TV never
stalls the camera. Intents are fire-and-forget: dropping one is better than
queueing them up and firing six volume-ups a second late.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import threading
from pathlib import Path

from . import config as config_module
from .capture import Camera
from .hud import draw
from .intents import SESSION_ONLY, Intent, IntentMessage
from .session import SessionMachine

log = logging.getLogger("gesturectl")


#: how each intent reads in the legend, in the order people use them
_LEGEND_ORDER = [
    Intent.SESSION_TOGGLE, Intent.SESSION_WAKE, Intent.SESSION_SLEEP,
    Intent.VOLUME_UP, Intent.VOLUME_DOWN, Intent.MUTE_TOGGLE,
    Intent.PLAY_PAUSE, Intent.POWER_TOGGLE,
]

_LEGEND_TEXT = {
    Intent.SESSION_TOGGLE: "wake / sleep (hold to wake, release, repeat to sleep)",
    Intent.SESSION_WAKE: "wake",
    Intent.SESSION_SLEEP: "sleep",
    Intent.VOLUME_UP: "volume up (ramps while held)",
    Intent.VOLUME_DOWN: "volume down (ramps while held)",
    Intent.MUTE_TOGGLE: "mute",
    Intent.PLAY_PAUSE: "play / pause",
    Intent.POWER_TOGGLE: "power on / off (longer hold)",
}


def _print_bindings(cfg) -> None:
    """Print the legend from the actual config, so it can never drift from it."""
    by_intent = {intent: pose for pose, intent in cfg.bindings.items()}
    print()
    for intent in _LEGEND_ORDER:
        pose = by_intent.get(intent)
        if pose:
            print(f"  {pose:<14} {_LEGEND_TEXT[intent]}")
    for pose, intent in cfg.bindings.items():
        if intent not in _LEGEND_ORDER:
            print(f"  {pose:<14} {intent.value}")
    print("  q or Esc        quit")
    print()


def build_adapter(device_cfg):
    if device_cfg.type == "roku":
        from .devices.roku import RokuAdapter

        return RokuAdapter(device_cfg.name, device_cfg.host)
    raise ValueError(
        f"Unknown device type '{device_cfg.type}'. "
        "Roku is implemented; Fire TV and Google TV adapters are next."
    )


class Dispatcher:
    """Owns the asyncio loop and the device adapter, on its own thread."""

    def __init__(self, adapter, dry_run: bool = False) -> None:
        self.adapter = adapter
        self.dry_run = dry_run
        self.status_line = "connecting..."
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def start(self) -> None:
        self._thread.start()
        if self.dry_run:
            self.status_line = "dry run - nothing is being sent"
            return
        future = asyncio.run_coroutine_threadsafe(self._connect(), self._loop)
        try:
            self.status_line = future.result(timeout=15)
        except Exception as exc:  # noqa: BLE001 - surfaced in the HUD, not swallowed
            self.status_line = f"device unavailable: {exc}"
            log.warning("could not connect: %s", exc)

    async def _connect(self) -> str:
        await self.adapter.connect()
        kind = "TV" if getattr(self.adapter, "is_tv", False) else "device"
        caps = len(self.adapter.capabilities)
        return f"{self.adapter.name} ({kind}, {caps} intents) @ {self.adapter.base_url}"

    def submit(self, message: IntentMessage) -> None:
        if message.intent in SESSION_ONLY:
            log.info("session: %s", message.intent.value)
            return
        log.info("intent: %s", message.to_dict())
        if self.dry_run:
            return
        if not self.adapter.supports(message.intent):
            log.info("  dropped - %s does not support it", self.adapter.name)
            return
        asyncio.run_coroutine_threadsafe(self._send(message), self._loop)

    async def _send(self, message: IntentMessage) -> None:
        result = await self.adapter.send(message.intent)
        if not result.ok:
            log.warning("  send failed: %s", result.detail)

    def stop(self) -> None:
        if not self.dry_run:
            asyncio.run_coroutine_threadsafe(self.adapter.close(), self._loop)
        self._loop.call_soon_threadsafe(self._loop.stop)


def main() -> int:
    parser = argparse.ArgumentParser(prog="gesturectl")
    parser.add_argument("--config", default="config.yaml", type=Path)
    parser.add_argument("--dry-run", action="store_true",
                        help="detect and log intents without touching the TV")
    parser.add_argument("--no-window", action="store_true",
                        help="skip the HUD window (headless tuning)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s",
                        datefmt="%H:%M:%S")

    cfg = config_module.load(args.config)
    device_cfg = cfg.default_device
    if device_cfg is None:
        log.error("No devices configured. Add one under `devices:` in %s", args.config)
        return 1

    from .vision import VisionEngine  # late import: heavy, and needs the model file

    engine = VisionEngine(
        cfg.vision.model_path,
        cfg.vision.min_detection_confidence,
        cfg.vision.min_tracking_confidence,
    )
    machine = SessionMachine(cfg.session, cfg.bindings, target=device_cfg.name)
    dispatcher = Dispatcher(build_adapter(device_cfg), dry_run=args.dry_run)
    dispatcher.start()
    log.info("%s", dispatcher.status_line)

    _print_bindings(cfg)

    camera = Camera(cfg.vision.camera_index, cfg.vision.width, cfg.vision.height)
    import cv2

    try:
        for frame in camera.frames():
            observation = engine.process(frame.rgb, int(frame.t_ms))
            message = machine.update(observation.pose, observation.confidence, frame.t_ms)
            if message is not None:
                dispatcher.submit(message)

            if not args.no_window:
                draw(frame.bgr, observation.landmarks, observation.pose,
                     observation.confidence, machine.status(frame.t_ms),
                     dispatcher.status_line)
                cv2.imshow("gesturectl", frame.bgr)
                if cv2.waitKey(1) & 0xFF in (27, ord("q")):
                    break
    except KeyboardInterrupt:
        pass
    finally:
        camera.close()
        engine.close()
        dispatcher.stop()
        if not args.no_window:
            cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
