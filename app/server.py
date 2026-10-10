"""Composition root for the running app: the web app plus the job worker (SPEC §15.4 ``serve``).

Like ``app.config`` and ``app.__main__`` this module sits outside the layers: it joins ``app.api`` and
``app.worker``, which must never import each other.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from app.api.main import WEB_DIST, create_app
from app.config import Settings
from app.worker.runner import start_worker


def build_app(settings: Settings, web_dist: Path | None = WEB_DIST, worker: bool = True) -> FastAPI:
    return create_app(web_dist=web_dist, settings=settings, background=start_worker if worker else None)
