"""TOTP and one-time backup codes (SPEC §7 MFA). TR-SEC-01.

Codes are accepted one step either side of now; a step already used is refused (replay). Backup codes are
10 characters, shown once and stored argon2id-hashed.
"""

from __future__ import annotations

import secrets
import time

import pyotp

from app.auth.passwords import hash_password, verify_password

ISSUER = "De-identification Station"
STEP = 30
BACKUP_CODES = 10
_BACKUP_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I  # pragma: allowlist secret


def new_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, username: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=ISSUER)


def matching_step(secret: str, code: str, last_step: int | None, now: float | None = None) -> int | None:
    """The time step the code belongs to (now ± 1), or None if wrong or already used."""
    code = (code or "").strip().replace(" ", "")
    if len(code) != 6 or not code.isdigit():
        return None
    totp = pyotp.TOTP(secret)
    t = time.time() if now is None else now
    current = int(t // STEP)
    for step in (current, current - 1, current + 1):
        if secrets.compare_digest(totp.at(step * STEP), code):
            if last_step is not None and step <= last_step:
                return None
            return step
    return None


def new_backup_codes() -> list[str]:
    return ["".join(secrets.choice(_BACKUP_ALPHABET) for _ in range(10)) for _ in range(BACKUP_CODES)]


def hash_backup_code(code: str) -> str:
    return hash_password(_normalise(code))


def verify_backup_code(stored_hash: str, code: str) -> bool:
    return verify_password(stored_hash, _normalise(code))


def _normalise(code: str) -> str:
    return (code or "").strip().replace("-", "").replace(" ", "").upper()
