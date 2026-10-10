"""Job files that may identify patients: kept under ``app_data/secure/`` only (SPEC §6.4). TR-ELIG-11..14.

- ``secure/jobs/<job_id>/source.json``: the approved root and the chosen folder (folder names can be names).
- ``secure/jobs/<job_id>/folders.json``: folder key → folder name for the per-folder table.
- ``secure/exclusions/<job_id>/exclusions.csv``: the skip list with plain source paths (Custodian, Reviewer).
- ``secure/exclusions/<job_id>/reconciliation.csv``: the per-folder balance.
- ``secure/exclusions/<job_id>/spotcheck.csv``: skip spot-check results and notes.

These files are never copied to the output folder, a release, logs, audit events or exports.
"""

from __future__ import annotations

import csv
import io
import json
import shutil
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from app.services.context import ServiceContext

EXCLUSION_COLUMNS = (
    "job_id", "study_key", "source_path", "file_names", "file_count", "file_sha256_list", "modality",
    "reason", "rule_id", "rules_version", "evidence", "other_matches", "screened_at",
)  # fmt: skip
SPOTCHECK_COLUMNS = ("study_key", "rule_id", "result", "note", "checked_by", "checked_at")


def jobs_dir(ctx: ServiceContext, job_id: str) -> Path:
    return ctx.paths.secure_dir / "jobs" / job_id


def exclusions_dir(ctx: ServiceContext, job_id: str) -> Path:
    return ctx.paths.secure_dir / "exclusions" / job_id


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8", newline="\n")
    tmp.replace(path)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def write_source(ctx: ServiceContext, job_id: str, approved_root: str, rel: str) -> None:
    _write_json(jobs_dir(ctx, job_id) / "source.json", {"approved_root": approved_root, "rel": rel})


def read_source(ctx: ServiceContext, job_id: str) -> dict[str, str] | None:
    data = _read_json(jobs_dir(ctx, job_id) / "source.json")
    return dict(data) if isinstance(data, dict) else None


def write_folders(ctx: ServiceContext, job_id: str, names: Mapping[str, str]) -> None:
    _write_json(jobs_dir(ctx, job_id) / "folders.json", dict(names))


def read_folders(ctx: ServiceContext, job_id: str) -> dict[str, str]:
    data = _read_json(jobs_dir(ctx, job_id) / "folders.json")
    return dict(data) if isinstance(data, dict) else {}


def csv_text(columns: Sequence[str], rows: Iterable[Mapping[str, Any]]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(columns), lineterminator="\n", extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row.get(k, "") for k in columns})
    return buf.getvalue()


def _write_csv(path: Path, columns: Sequence[str], rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(csv_text(columns, rows), encoding="utf-8", newline="")
    tmp.replace(path)


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def write_exclusions(ctx: ServiceContext, job_id: str, rows: Iterable[Mapping[str, Any]]) -> Path:
    path = exclusions_dir(ctx, job_id) / "exclusions.csv"
    _write_csv(path, EXCLUSION_COLUMNS, rows)
    return path


def read_exclusions(ctx: ServiceContext, job_id: str) -> list[dict[str, str]]:
    return _read_csv(exclusions_dir(ctx, job_id) / "exclusions.csv")


def write_reconciliation(
    ctx: ServiceContext, job_id: str, columns: Sequence[str], rows: Iterable[Mapping[str, Any]]
) -> Path:
    path = exclusions_dir(ctx, job_id) / "reconciliation.csv"
    _write_csv(path, columns, rows)
    return path


def reconciliation_path(ctx: ServiceContext, job_id: str) -> Path:
    return exclusions_dir(ctx, job_id) / "reconciliation.csv"


def exclusions_path(ctx: ServiceContext, job_id: str) -> Path:
    return exclusions_dir(ctx, job_id) / "exclusions.csv"


def append_spotcheck(ctx: ServiceContext, job_id: str, row: Mapping[str, Any]) -> None:
    path = exclusions_dir(ctx, job_id) / "spotcheck.csv"
    rows = [*_read_csv(path), dict(row)]
    _write_csv(path, SPOTCHECK_COLUMNS, rows)


def remove_exclusions(ctx: ServiceContext, job_id: str) -> None:
    """Cancel rolls back the job's skip list (owner decision, 10 Oct 2026); reconciliation is rewritten."""
    for name in ("exclusions.csv", "spotcheck.csv"):
        (exclusions_dir(ctx, job_id) / name).unlink(missing_ok=True)


def delete_staging(ctx: ServiceContext, job_id: str) -> None:
    """SPEC §7: uploaded originals are deleted when the job ends."""
    target = ctx.paths.staging_dir / job_id
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    if target.exists():  # a file still open elsewhere (Windows); retry once
        shutil.rmtree(target, ignore_errors=True)
