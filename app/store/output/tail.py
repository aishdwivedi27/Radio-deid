"""Read what was written after the last logged append (SPEC §5.3 step 7; reconcile). TR-REL-NF-01.

Every append is fsynced before its ``append_log`` row commits, so after a crash any unlogged bytes sit
after the largest logged end offset. Complete lines there are matched against the DB by reconcile; a
trailing fragment without a newline was never acknowledged and may be cut off (decision D-019).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Tail:
    lines: list[tuple[bytes, int]] = field(default_factory=list)  # (line without newline, end offset)
    fragment_at: int | None = None  # offset where an incomplete last line starts
    size: int = 0


def read_tail(path: Path, start: int) -> Tail:
    if not path.exists():
        return Tail()
    size = path.stat().st_size
    tail = Tail(size=size)
    if start >= size:
        return tail
    with path.open("rb") as fh:
        fh.seek(start)
        data = fh.read()
    pos = 0
    while pos < len(data):
        nl = data.find(b"\n", pos)
        if nl < 0:
            tail.fragment_at = start + pos
            break
        tail.lines.append((data[pos:nl], start + nl + 1))
        pos = nl + 1
    return tail


def truncate_fragment(path: Path, offset: int) -> None:
    """Cut an unacknowledged incomplete last line (never a complete line) and make the cut durable."""
    with path.open("r+b") as fh:
        fh.seek(offset)
        if b"\n" in fh.read():
            raise ValueError(f"{path.name}: refusing to truncate complete lines")
        fh.truncate(offset)
        fh.flush()
        os.fsync(fh.fileno())
