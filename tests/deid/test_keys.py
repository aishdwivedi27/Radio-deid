"""Key file and fingerprint (SPEC §3, §5.1). TR-DEID-03."""

import hashlib
import os
import stat
from pathlib import Path

import pytest

from app.deid.keys import key_fingerprint, load_or_create_key
from app.deid.types import DeidError


def test_create_then_reload(tmp_path: Path) -> None:  # TR-DEID-03
    path = tmp_path / "app_data" / "secret.key"
    key = load_or_create_key(path)
    assert len(key) == 32 and load_or_create_key(path) == key
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_fingerprint_is_not_the_key() -> None:  # TR-DEID-03
    key = bytes(range(32))
    fp = key_fingerprint(key)
    assert fp == hashlib.sha256(key).hexdigest()[:16] and key.hex() not in fp


def test_bad_key_file(tmp_path: Path) -> None:
    path = tmp_path / "secret.key"
    path.write_text("not hex", encoding="ascii")
    with pytest.raises(DeidError):
        load_or_create_key(path)
