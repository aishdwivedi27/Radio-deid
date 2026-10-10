"""FastAPI application factory (SPEC §7, §15.4). TR-REL-NF-01, TR-SEC-01..03.

With settings, the store is opened and ``reconcile`` runs at startup (SPEC §5.3 step 7). Every ``/api``
route other than health, status, setup and the sign-in steps needs a permission (``require``). The log
filter that drops identifying text is installed before anything else runs. ``background`` (given by the
composition root ``app/server.py``) starts the job worker after startup and stops it at shutdown, so this
package never imports ``app.worker``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api import middleware
from app.api.routes import admin, auth, governance, jobs, pending, setup
from app.config import DEFAULT_PORT, Settings
from app.services.context import ServiceContext
from app.services.startup import install_log_filter, startup

WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"

_PLACEHOLDER = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>De-identification Station</title></head>
<body><h1>De-identification Station</h1>
<p>Frontend not built yet. Run <code>npm run build</code> in <code>web/</code>.</p></body></html>
"""


Background = Callable[[ServiceContext], Callable[[], None]]


def create_app(
    web_dist: Path | None = WEB_DIST, settings: Settings | None = None, background: Background | None = None
) -> FastAPI:
    install_log_filter()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        stop: Callable[[], None] | None = None
        if settings is not None:
            app.state.services = startup(settings)
            if background is not None:
                stop = background(app.state.services)
        yield
        if stop is not None:
            stop()
        services = getattr(app.state, "services", None)
        if services is not None:
            services.db.dispose()

    app = FastAPI(
        title="De-identification Station",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    middleware.install(app, settings.port if settings else DEFAULT_PORT)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    for module in (setup, auth, admin, governance, jobs, pending):
        app.include_router(module.router)

    if web_dist is not None and (web_dist / "index.html").is_file():
        app.mount("/", StaticFiles(directory=web_dist, html=True), name="web")
    else:

        @app.get("/", response_class=HTMLResponse)
        def home() -> str:
            return _PLACEHOLDER

    return app
