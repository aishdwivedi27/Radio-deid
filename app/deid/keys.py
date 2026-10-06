"""The centre's HMAC key (SPEC §3, §5.1). TR-DEID-09.

The key is stored as hex in ``app_data/secret.key`` with owner-only permissions. Only its fingerprint
(first 16 hex of sha256) is ever shown or written to a row; the key itself never is.
"""

from __future__ import annotations

import hashlib
import os
import secrets
from pathlib import Path

from app.deid.types import DeidError

KEY_BYTES = 32


def load_or_create_key(path: Path) -> bytes:
    if path.exists():
        try:
            key = bytes.fromhex(path.read_text(encoding="ascii").strip())
        except ValueError as exc:
            raise DeidError("secret key file is not valid hex") from exc
        if len(key) < KEY_BYTES:
            raise DeidError("secret key is too short")
        return key
    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(KEY_BYTES)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as fh:
        fh.write(key.hex())
    try:
        os.chmod(path, 0o600)
    except OSError:  # Windows: ACLs are set by the installer (Phase 7)
        pass
    return key


def key_fingerprint(key: bytes) -> str:
    return hashlib.sha256(key).hexdigest()[:16]
