"""The single path to the append-only output files (SPEC §5.2, §5.3 steps 5 and 7). TR-REL-NF-01, TR-WDR-02,
TR-DEID-10.

The DB is the source of truth. For each file, under the output lock:
1. absorb the tail: complete lines after the last logged offset that equal an unlogged DB row are logged
   without writing them again (a crash between fsync and the log commit); an incomplete trailing fragment
   is cut (D-019); any other line is a mismatch, reported and never "fixed";
2. append every finalised row not yet in ``append_log``, validated against its schema, then fsync;
3. log each appended line (row key, line SHA-256, end offset).
Running it twice appends nothing the second time.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.deid.record.canonical import json_line
from app.deid.record.columns import IMAGE_COLUMNS, RECORD_COLUMNS, WITHDRAWAL_COLUMNS
from app.deid.record.flatten import image_csv, record_csv, withdrawal_csv
from app.deid.record.validate import validate_row
from app.services.context import ServiceContext, now_iso
from app.store.output.csv_writer import CsvAppender, encode_row
from app.store.output.jsonl import AppendError, JsonlAppender
from app.store.output.lock import output_lock
from app.store.output.tail import read_tail, truncate_fragment
from app.store.repos import append_log
from app.store.repos import governance as gov
from app.store.repos import records as recs

Row = tuple[str, dict[str, Any]]


@dataclass(frozen=True)
class OutputFile:
    name: str
    kind: str  # record | image | withdrawal
    columns: tuple[str, ...] | None = None  # None = JSONL
    flatten: Callable[[dict[str, Any]], list[str]] | None = None

    def encode(self, row: dict[str, Any]) -> bytes:
        if self.flatten is None:
            return json_line(row)
        return encode_row(self.flatten(row))


RECORDS_JSONL = OutputFile("records.jsonl", "record")
IMAGES_JSONL = OutputFile("images.jsonl", "image")
RECORDS_CSV = OutputFile("records.csv", "record", RECORD_COLUMNS, record_csv)
IMAGES_CSV = OutputFile("images.csv", "image", IMAGE_COLUMNS, image_csv)
WITHDRAWALS_JSONL = OutputFile("withdrawals.jsonl", "withdrawal")
WITHDRAWALS_CSV = OutputFile("withdrawals.csv", "withdrawal", WITHDRAWAL_COLUMNS, withdrawal_csv)
RECORD_FILES = (RECORDS_JSONL, IMAGES_JSONL, RECORDS_CSV, IMAGES_CSV)  # SPEC §5.3 step 5 order
WITHDRAWAL_FILES = (WITHDRAWALS_JSONL, WITHDRAWALS_CSV)
ALL_FILES = RECORD_FILES + WITHDRAWAL_FILES


@dataclass
class AppendReport:
    appended: int = 0
    absorbed: int = 0  # lines found on disk and logged without writing them again
    truncated: int = 0
    mismatches: list[str] = field(default_factory=list)  # file name + reason; no row content

    def merge(self, other: AppendReport) -> None:
        self.appended += other.appended
        self.absorbed += other.absorbed
        self.truncated += other.truncated
        self.mismatches += other.mismatches


def db_rows(s: Session, kind: str, record_id: str | None = None) -> list[Row]:
    if kind == "record":
        return [
            (f"{v.record_id}:v{v.version}", json.loads(v.row_json))
            for v in recs.finalised_versions(s, record_id)
        ]
    if kind == "image":
        return [(i.image_id, json.loads(i.row_json)) for i in recs.finalised_images(s, record_id)]
    return [
        (w.record_id, json.loads(w.row_json)) for w in gov.withdrawals(s) if record_id in (None, w.record_id)
    ]


def _sha(line: bytes) -> str:
    return hashlib.sha256(line).hexdigest()


def _absorb_tail(ctx: ServiceContext, f: OutputFile, report: AppendReport) -> bool:
    """Step 1. Returns False if the file cannot be appended safely (reported as a mismatch)."""
    path = ctx.paths.output_file(f.name)
    with ctx.db.transaction() as s:
        start, n_logged = append_log.max_offset(s, f.name), append_log.count(s, f.name)
        logged = append_log.logged_keys(s, f.name)
        unlogged = {f.encode(row): key for key, row in db_rows(s, f.kind) if key not in logged}
        if n_logged and (not path.exists() or path.stat().st_size < start):
            report.mismatches.append(f"{f.name}: file missing or shorter than logged")
            return False
        tail = read_tail(path, start)
        header = encode_row(f.columns) if f.columns else None
        for line, end in tail.lines:
            if header is not None and end == len(header) + 1 and line == header:
                continue
            key = unlogged.pop(line, None)
            if key is None:
                report.mismatches.append(f"{f.name}: line at offset {end - len(line) - 1} not in the DB")
                continue
            append_log.add(s, f.name, key, _sha(line), end, now_iso())
            report.absorbed += 1
    if tail.fragment_at is not None:
        truncate_fragment(path, tail.fragment_at)
        report.truncated += 1
    return True


def _append_file(ctx: ServiceContext, f: OutputFile, record_id: str | None, report: AppendReport) -> None:
    if not _absorb_tail(ctx, f, report):
        return
    with ctx.db.session() as s:
        logged = append_log.logged_keys(s, f.name)
        todo = [(k, row) for k, row in db_rows(s, f.kind, record_id) if k not in logged]
    if not todo:
        return
    for _, row in todo:
        validate_row(f.kind, row)
    path = ctx.paths.output_file(f.name)
    lines = [f.encode(row) for _, row in todo]
    if f.columns is None:
        ends = JsonlAppender(path).append(lines)
    else:
        assert f.flatten is not None
        ends = CsvAppender(path, f.columns).append([f.flatten(row) for _, row in todo])
    with ctx.db.transaction() as s:
        for (key, _), line, end in zip(todo, lines, ends, strict=True):
            append_log.add(s, f.name, key, _sha(line), end, now_iso())
    report.appended += len(todo)


def append_rows(
    ctx: ServiceContext, record_id: str | None = None, files: Sequence[OutputFile] = RECORD_FILES
) -> AppendReport:
    report = AppendReport()
    with output_lock(ctx.paths.app_data_dir):
        for f in files:
            try:
                _append_file(ctx, f, record_id, report)
            except AppendError as exc:
                report.mismatches.append(str(exc))
    return report
