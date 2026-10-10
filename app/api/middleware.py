"""HTTP hardening for a loopback-only app (SPEC §7). TR-SEC-01, TR-SEC-02.

- Host allowlist (127.0.0.1 / localhost on the configured port): blocks DNS-rebinding pages.
- Origin check on every state-changing request, including login and setup (which have no session yet), and
  a JSON (or, for list imports, CSV; for uploads, multipart) content type. A cross-site form cannot reach
  the upload either: it has no session CSRF header.
- First-run gate: until setup is complete only health, status and setup routes answer (503 otherwise).
- Error bodies are fixed codes and messages. Validation errors list field locations only and never echo
  what was sent (it could be a password or a patient ID).
- Security headers; API responses are never cached.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response

from app.api.deps import UNSAFE
from app.auth.errors import AppError
from app.services.auth.setup import completed

OPEN_BEFORE_SETUP = ("/api/health", "/api/status", "/api/setup")
UPLOAD_PATH = "/api/jobs/upload"
_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'",
}


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": code, "message": message}, status_code=status)


def _content_type_ok(request: Request, path: str) -> bool:
    """An empty body is fine whatever its declared type (some clients send a form type with no body);
    a body must be JSON, CSV for a list import, or multipart for an upload job."""
    if request.headers.get("content-length", "0") == "0" and "transfer-encoding" not in request.headers:
        return True
    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    return (
        ctype == "application/json"
        or (ctype == "text/csv" and path.startswith("/api/lists/"))
        or (ctype == "multipart/form-data" and path == UPLOAD_PATH)
    )


def install(app: FastAPI, port: int) -> None:
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}

    @app.exception_handler(AppError)
    async def app_error(_: Request, exc: AppError) -> JSONResponse:
        return _error(exc.status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def invalid(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields = sorted({".".join(str(p) for p in e.get("loc", ())[1:]) or "body" for e in exc.errors()})
        return _error(422, "invalid", "Invalid or missing fields: " + ", ".join(fields))

    @app.middleware("http")
    async def guard(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        if request.headers.get("host", "") not in hosts:
            return _error(400, "host", "Unknown host.")
        origin = request.headers.get("origin")
        if request.method in UNSAFE and origin is not None and origin not in origins:
            return _error(403, "origin", "Cross-origin request refused.")
        path = request.url.path
        if request.method in UNSAFE and path.startswith("/api") and not _content_type_ok(request, path):
            return _error(
                415, "content_type", "Send JSON (text/csv for a list import, multipart for uploads)."
            )
        if path.startswith("/api") and not path.startswith(OPEN_BEFORE_SETUP):
            ctx = getattr(request.app.state, "services", None)
            if ctx is not None and not completed(ctx):
                return _error(503, "setup_required", "First-run setup has not been completed.")
        response = await call_next(request)
        for name, value in _HEADERS.items():
            response.headers.setdefault(name, value)
        if path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response
