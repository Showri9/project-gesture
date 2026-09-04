"""Wire shapes. Deliberately narrow - a field here is a promise to the frontend."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..intents import Intent


class Health(BaseModel):
    ok: bool = True
    version: str
    device: str | None = None


class DeviceOut(BaseModel):
    id: str
    name: str
    kind: str = "roku"
    model: str = "unknown"
    is_tv: bool = False
    #: Google TV answers but refuses commands until paired. A normal state with
    #: a normal remedy, so it is a field rather than an error.
    needs_pairing: bool = False
    host: str
    reachable: bool = False
    power: str = "unknown"          # on | standby | unknown
    selected: bool = False
    capabilities: list[Intent] = Field(default_factory=list)


class AddByHost(BaseModel):
    host: str = Field(min_length=3, description="IP or http://ip:8060")
    name: str | None = None
    kind: str = Field(default="roku", pattern="^(roku|googletv)$")


class PairingCode(BaseModel):
    code: str = Field(min_length=4, max_length=12)


class IntentIn(BaseModel):
    """What the on-screen remote posts. The same shape a gesture produces, so a
    button and a hand travel the identical path through the system."""

    intent: Intent
    target: str | None = None


class PoseIn(BaseModel):
    """One frame's worth of what the phone's camera saw. About forty bytes.

    Note what is NOT here: no image, no landmarks. Frames never leave the phone.
    """

    pose: str | None = None
    confidence: float = 0.0
    t_ms: float | None = None


class BindingOut(BaseModel):
    pose: str
    intent: Intent
    hold_ms: float | None = None


class ConfigOut(BaseModel):
    bindings: list[BindingOut]
    thresholds: dict[str, float]


class ConfigIn(BaseModel):
    bindings: list[BindingOut] | None = None
    thresholds: dict[str, float] | None = None
