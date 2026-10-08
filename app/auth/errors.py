"""Errors that carry an HTTP-style status, raised by services and mapped by the API. TR-SEC-02.

Messages are fixed strings naming fields, roles or permissions; they never contain a password, a token,
a typed username or any patient data.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Actor:
    """The signed-in user, as the services see it (only ``active`` sessions produce an Actor)."""

    user_id: str
    roles: frozenset[str]
    session_id: str = ""


class AppError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def bad_request(message: str, code: str = "invalid") -> AppError:
    return AppError(400, code, message)


def unauthenticated(message: str = "Sign in required.") -> AppError:
    return AppError(401, "unauthenticated", message)


def forbidden(message: str = "You do not have permission for this action.") -> AppError:
    return AppError(403, "forbidden", message)


def not_found(message: str = "Not found.") -> AppError:
    return AppError(404, "not_found", message)


def conflict(message: str, code: str = "conflict") -> AppError:
    return AppError(409, code, message)


def gone(message: str) -> AppError:
    return AppError(410, "gone", message)


def locked(message: str = "Too many failed attempts. Try again in 15 minutes.") -> AppError:
    return AppError(423, "locked", message)


def not_available(message: str) -> AppError:
    return AppError(501, "not_available", message)


def setup_required() -> AppError:
    return AppError(503, "setup_required", "First-run setup has not been completed.")
