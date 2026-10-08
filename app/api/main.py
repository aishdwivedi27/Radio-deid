"""FastAPI application factory: health endpoint and placeholder home page; with settings, the store is
opened and ``reconcile`` runs at startup (SPEC §5.3 step 7). TR-REL-NF-01."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.config import Settings
from app.services.startup import startup

WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"

_PLACEHOLDER = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>De-identification Station</title></head>
<body><h1>De-identification Station</h1>
<p>Frontend not built yet. Run <code>npm run build</code> in <code>web/</code>.</p></body></html>
"""


def create_app(web_dist: Path | None = WEB_DIST, settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if settings is not None:
            app.state.services = startup(settings)
        yield

    app = FastAPI(
        title="De-identification Station",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    if web_dist is not None and (web_dist / "index.html").is_file():
        app.mount("/", StaticFiles(directory=web_dist, html=True), name="web")
    else:

        @app.get("/", response_class=HTMLResponse)
        def home() -> str:
            return _PLACEHOLDER

    return app
