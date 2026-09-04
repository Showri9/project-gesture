"""The endpoints. Thin - every one of them is a call into the Hub."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect

from .. import __version__
from ..intents import Intent
from .hub import Hub
from .schemas import (
    AddByHost,
    BindingOut,
    ConfigIn,
    ConfigOut,
    DeviceOut,
    Health,
    IntentIn,
    PairingCode,
    PoseIn,
)

log = logging.getLogger("gesturectl.api")
router = APIRouter(prefix="/api")


def get_hub(request: Request) -> Hub:
    return request.app.state.hub


def _device_out(hub: Hub, record) -> DeviceOut:
    adapter = record.adapter
    return DeviceOut(
        id=record.id,
        name=record.name,
        kind=record.kind,
        needs_pairing=record.needs_pairing,
        model=record.model,
        is_tv=record.is_tv,
        host=record.host,
        reachable=record.reachable,
        power=record.power,
        selected=record.id == hub.selected_id,
        capabilities=sorted(adapter.capabilities, key=lambda i: i.value)
        if adapter else [],
    )


@router.get("/health", response_model=Health)
async def health(request: Request) -> Health:
    hub = get_hub(request)
    return Health(version=__version__,
                  device=hub.selected.name if hub.selected else None)


# -- devices ----------------------------------------------------------------

@router.get("/devices", response_model=list[DeviceOut])
async def list_devices(request: Request) -> list[DeviceOut]:
    hub = get_hub(request)
    return [_device_out(hub, r) for r in hub.devices.values()]


@router.post("/discover", response_model=list[DeviceOut])
async def discover(request: Request) -> list[DeviceOut]:
    """SSDP is blocking and lossy, so it runs in a thread and the UI is told it
    is scanning rather than left looking frozen."""
    hub = get_hub(request)
    found = await asyncio.to_thread(_discover_sync, hub)
    return [_device_out(hub, r) for r in found]


def _discover_sync(hub: Hub):
    from ..devices.discover import discover_roku

    return [hub.add_device(host) for host in discover_roku()]


@router.post("/devices/by-host", response_model=DeviceOut)
async def add_by_host(body: AddByHost, request: Request) -> DeviceOut:
    """For the routers that drop multicast between wifi bands."""
    hub = get_hub(request)
    try:
        record = hub.add_device(body.host, body.name, kind=body.kind)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    await hub.refresh(record)
    return _device_out(hub, record)


@router.post("/devices/{device_id}/select", response_model=DeviceOut)
async def select_device(device_id: str, request: Request) -> DeviceOut:
    hub = get_hub(request)
    try:
        hub.select(device_id)
    except KeyError:
        raise HTTPException(404, f"no device {device_id!r}") from None
    record = hub.devices[device_id]
    await hub.refresh(record)
    return _device_out(hub, record)


@router.post("/devices/{device_id}/refresh", response_model=DeviceOut)
async def refresh_device(device_id: str, request: Request) -> DeviceOut:
    hub = get_hub(request)
    record = hub.devices.get(device_id)
    if record is None:
        raise HTTPException(404, f"no device {device_id!r}")
    await hub.refresh(record)
    return _device_out(hub, record)


# -- pairing ----------------------------------------------------------------
#
# Google TV will not take a command until a six-digit code shown on the screen
# has been typed back. That is two round trips through the UI, so it is two
# endpoints rather than one.

def _pairable(hub: Hub, device_id: str):
    record = hub.devices.get(device_id)
    if record is None:
        raise HTTPException(404, f"no device {device_id!r}")
    if not hasattr(record.adapter, "start_pairing"):
        raise HTTPException(400, f"{record.kind} devices do not need pairing")
    return record


@router.post("/devices/{device_id}/pair/start")
async def pair_start(device_id: str, request: Request) -> dict:
    """Asks the TV to put a code on screen."""
    hub = get_hub(request)
    record = _pairable(hub, device_id)
    try:
        await record.adapter.start_pairing()
    except Exception as exc:  # noqa: BLE001 - the reason belongs in the UI
        raise HTTPException(502, f"could not start pairing: {exc}") from None
    return {"ok": True, "message": "Enter the code shown on the TV."}


@router.post("/devices/{device_id}/pair/finish", response_model=DeviceOut)
async def pair_finish(device_id: str, body: PairingCode, request: Request) -> DeviceOut:
    hub = get_hub(request)
    record = _pairable(hub, device_id)
    try:
        await record.adapter.finish_pairing(body.code)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc)) from None
    await hub.refresh(record)
    return _device_out(hub, record)


# -- control ----------------------------------------------------------------

@router.post("/intent")
async def send_intent(body: IntentIn, request: Request) -> dict:
    hub = get_hub(request)
    ok = await hub.send_intent(body.intent, body.target)
    return {"ok": ok, "intent": body.intent.value}


@router.post("/session/reset")
async def reset_session(request: Request) -> dict:
    get_hub(request).reset_session()
    return {"ok": True}


# -- config -----------------------------------------------------------------

def _config_out(hub: Hub) -> ConfigOut:
    session = hub.config.session
    return ConfigOut(
        bindings=[
            BindingOut(pose=pose, intent=intent, hold_ms=hub.config.hold_ms.get(intent))
            for pose, intent in hub.config.bindings.items()
        ],
        thresholds={
            "wake_hold_ms": session.wake_hold_ms,
            "confirm_frames": float(session.confirm_frames),
            "cooldown_ms": session.cooldown_ms,
            "repeat_ms": session.repeat_ms,
            "idle_timeout_ms": session.idle_timeout_ms,
            "min_confidence": session.min_confidence,
            "sleep_confirm_frames": float(session.sleep_confirm_frames),
            "wake_grace_ms": session.wake_grace_ms,
            "power_confirm_frames": float(session.power_confirm_frames),
        },
    )


@router.get("/config", response_model=ConfigOut)
async def get_config(request: Request) -> ConfigOut:
    return _config_out(get_hub(request))


@router.put("/config", response_model=ConfigOut)
async def put_config(body: ConfigIn, request: Request) -> ConfigOut:
    """Applies live. Tuning from the sofa is the whole reason these are not
    constants, and a restart between every adjustment would defeat it."""
    hub = get_hub(request)

    if body.bindings is not None:
        hub.config.bindings = {b.pose: b.intent for b in body.bindings}
        hub.config.hold_ms = {
            b.intent: b.hold_ms for b in body.bindings if b.hold_ms is not None
        }

    if body.thresholds is not None:
        session = hub.config.session
        for key, value in body.thresholds.items():
            if not hasattr(session, key):
                raise HTTPException(400, f"unknown threshold {key!r}")
            current = getattr(session, key)
            setattr(session, key, int(value) if isinstance(current, int) else float(value))

    hub.reset_session()
    hub.events.publish("config_changed")
    return _config_out(hub)


# -- streams ----------------------------------------------------------------

@router.websocket("/pose")
async def pose_socket(websocket: WebSocket) -> None:
    """Phone to server, roughly 30 a second. Forty bytes a frame - no image and
    no landmarks, so video never leaves the phone."""
    hub: Hub = websocket.app.state.hub
    await websocket.accept()
    log.info("pose stream connected")
    try:
        while True:
            raw = await websocket.receive_json()
            try:
                frame = PoseIn.model_validate(raw)
            except Exception:  # noqa: BLE001 - one bad frame must not kill the stream
                continue
            await hub.ingest_pose(frame.pose, frame.confidence, frame.t_ms)
    except WebSocketDisconnect:
        log.info("pose stream disconnected")
        hub.reset_session()
    except Exception as exc:  # noqa: BLE001
        log.warning("pose stream error: %s", exc)
        hub.reset_session()


@router.websocket("/events")
async def event_socket(websocket: WebSocket) -> None:
    """Server to clients. Broadcast, so every open page stays in step."""
    hub: Hub = websocket.app.state.hub
    await websocket.accept()
    async with hub.events.subscribe() as queue:
        try:
            while True:
                await websocket.send_json(await queue.get())
        except (WebSocketDisconnect, RuntimeError):
            return
