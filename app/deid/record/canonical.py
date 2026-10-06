"""Canonical JSON for rows (SPEC §3.2 C8). TR-QA-02.

``record.json`` holds exactly the bytes of the JSON line that will be appended to ``records.jsonl``
(without the trailing newline), so C8 is a byte comparison.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def json_line(row: dict[str, Any]) -> bytes:
    return json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def write_record_json(folder: Path, row: dict[str, Any]) -> None:
    (folder / "record.json").write_bytes(json_line(row))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
