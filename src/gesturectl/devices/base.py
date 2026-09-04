"""The one interface every device speaks.

Adapters DISCOVER what they can do rather than assuming it - a Roku stick has no
volume keys, a Roku TV does, and the same class serves both. The router drops
intents nothing can serve and says so in the HUD, which is what lets phase 3 add
lights and plugs without touching a line of vision code.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass

from ..intents import Intent


@dataclass(frozen=True, slots=True)
class Result:
    ok: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class Health:
    reachable: bool
    detail: str = ""


class DeviceAdapter(abc.ABC):
    #: logical name from config.yaml, e.g. "living-room"
    name: str
    #: populated by connect(), never hardcoded
    capabilities: set[Intent]

    def __init__(self, name: str) -> None:
        self.name = name
        self.capabilities = set()

    @abc.abstractmethod
    async def connect(self) -> None:
        """Resolve the host, probe it, and fill in `capabilities`."""

    @abc.abstractmethod
    async def send(self, intent: Intent) -> Result:
        ...

    @abc.abstractmethod
    async def health(self) -> Health:
        ...

    async def close(self) -> None:
        return None

    def supports(self, intent: Intent) -> bool:
        return intent in self.capabilities
