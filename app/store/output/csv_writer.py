"""Append-only CSV writer with a fixed header (SPEC §5.2). TR-DEID-10, TR-REL-NF-01.

The header is written only when the file is new. An existing file whose header differs from the expected
columns is refused: appending never changes the header. LF line endings (.gitattributes, examples).
"""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence
from pathlib import Path

from app.store.output.jsonl import AppendError, append_bytes


def encode_row(values: Sequence[str]) -> bytes:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n", quoting=csv.QUOTE_MINIMAL).writerow(values)
    return buf.getvalue().removesuffix("\n").encode("utf-8")


class CsvAppender:
    def __init__(self, path: Path, columns: Sequence[str]) -> None:
        self.path = path
        self.columns = tuple(columns)

    @property
    def header(self) -> bytes:
        return encode_row(self.columns)

    def check_header(self) -> bool:
        """True if the file exists with the right header; False if it is new or empty; raises otherwise."""
        if not self.path.exists() or self.path.stat().st_size == 0:
            return False
        with self.path.open("rb") as fh:
            first = fh.readline().removesuffix(b"\n")
        if first != self.header:
            raise AppendError(f"{self.path.name}: existing header differs from the fixed column order")
        return True

    def append(self, rows: Sequence[Sequence[str]]) -> list[int]:
        for row in rows:
            if len(row) != len(self.columns):
                raise AppendError(
                    f"{self.path.name}: row has {len(row)} values, expected {len(self.columns)}"
                )
        lines = [encode_row(r) for r in rows]
        if not self.check_header():
            append_bytes(self.path, [self.header])
        return append_bytes(self.path, lines)
