"""Append-only JSON Lines writer (SPEC §5.1, §5.3). TR-REL-NF-01.

Files are opened in append mode only; nothing here can rewrite or reorder a line. The caller holds the
output lock (``lock.py``). Each batch is flushed and fsynced before the offsets are returned.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path


class AppendError(RuntimeError):
    """An output file cannot be appended safely. Messages carry file names only."""


def fsync_dir(folder: Path) -> None:
    """Make a new directory entry durable (POSIX). Windows has no directory fsync; NTFS journals it."""
    if os.name == "nt":
        return
    fd = os.open(folder, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def append_bytes(path: Path, lines: Sequence[bytes]) -> list[int]:
    """Append each line + ``\\n``, flush and fsync; return each line's end offset in the file."""
    for line in lines:
        if b"\n" in line or b"\r" in line:
            raise AppendError(f"{path.name}: a line may not contain a line break")
    new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    ends: list[int] = []
    with path.open("ab") as fh:
        for line in lines:
            fh.write(line + b"\n")
            ends.append(fh.tell())
        fh.flush()
        os.fsync(fh.fileno())
    if new:
        fsync_dir(path.parent)
    return ends


class JsonlAppender:
    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, lines: Sequence[bytes]) -> list[int]:
        """``lines`` are canonical JSON (``record/canonical.json_line``), already validated."""
        return append_bytes(self.path, lines)
