"""FastAPI wiring. Everything interesting is in hub.py."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .. import __version__
from .. import config as config_module
from .hub import Hub
from .routes import router

log = logging.getLogger("gesturectl")

FRONTEND = Path(__file__).resolve().parents[3] / "frontend"


def create_app(
    config: "str | Path | config_module.AppConfig" = "config.yaml",
    adapter_factory=None,
) -> FastAPI:
    cfg = (
        config
        if isinstance(config, config_module.AppConfig)
        else config_module.load(config)
    )

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        hub = Hub(cfg, adapter_factory)
        app.state.hub = hub
        for record in list(hub.devices.values()):
            await hub.refresh(record)
        yield
        await hub.close()

    app = FastAPI(title="gesturectl", version=__version__, lifespan=lifespan)

    # The phone loads the page from this same server, so same-origin covers the
    # real case. CORS is open only to keep a separately-served dev frontend
    # workable; it is a localhost service on a home LAN, not a public API.
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
        allow_headers=["*"], allow_credentials=False,
    )
    app.include_router(router)

    if FRONTEND.is_dir():
        app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
    else:
        @app.get("/")
        async def _placeholder() -> dict:
            return {"ok": True, "note": "frontend/ not built yet; API is at /api"}

    return app
