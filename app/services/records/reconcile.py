"""Repair the output after a crash; the DB is the source of truth (SPEC §5.3 step 7). TR-REL-NF-01, TR-WDR-02.

Runs at startup and as ``python -m app reconcile``:
1. finish interrupted folder moves of finalised records (pending → ``output/records/<id>/``);
2. absorb/append every finalised record and image row missing from records/images .jsonl/.csv;
3. finish withdrawals: folder to ``app_data/withdrawn/``, rows missing from withdrawals.jsonl/.csv;
4. release records must refer to an existing release (packages are checked with releases, Phase 5+).
It never writes a row twice and never rewrites a complete line; what it cannot fix is reported, with
record IDs, file names and counts only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from sqlalchemy import select

from app.deid.record.canonical import json_line
from app.services.context import ServiceContext
from app.services.records.append import ALL_FILES, append_rows
from app.services.records.withdraw import move_withdrawn_folders
from app.store import audit
from app.store.models.release import Release, ReleaseRecord
from app.store.output.folder import FolderError, install_folder, is_installed
from app.store.repos import records as recs


@dataclass
class ReconcileReport:
    appended: int = 0
    absorbed: int = 0
    truncated: int = 0
    folders_installed: int = 0
    folders_withdrawn: int = 0
    mismatches: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.appended or self.absorbed or self.truncated or self.folders_installed)

    def as_dict(self) -> dict[str, object]:
        return {
            "appended": self.appended,
            "absorbed": self.absorbed,
            "truncated": self.truncated,
            "folders_installed": self.folders_installed,
            "folders_withdrawn": self.folders_withdrawn,
            "mismatches": len(self.mismatches),
        }


def _folders(ctx: ServiceContext, report: ReconcileReport) -> None:
    with ctx.db.session() as s:
        todo = []
        for record in recs.finalised_records(s):
            if record.state == "withdrawn":
                continue
            ver = recs.get_version(s, record.record_id, record.finalised_version or 0)
            assert ver is not None
            todo.append((record.record_id, json_line(json.loads(ver.row_json))))
    for rid, expected in todo:
        target = ctx.paths.records_dir / rid
        if is_installed(target, expected) and not (ctx.paths.records_dir / f".prev-{rid}").exists():
            continue
        try:
            if install_folder(ctx.paths.pending_root / rid, target, expected) == "installed":
                report.folders_installed += 1
        except FolderError:
            report.mismatches.append(f"{rid}: output folder does not match the finalised version")


def _release_records(ctx: ServiceContext, report: ReconcileReport) -> None:
    with ctx.db.session() as s:
        orphans = s.scalars(
            select(ReleaseRecord.release_id).where(ReleaseRecord.release_id.not_in(select(Release.id)))
        ).all()
    if orphans:
        report.mismatches.append(f"release_records: {len(orphans)} row(s) without a release")


def reconcile(ctx: ServiceContext) -> ReconcileReport:
    report = ReconcileReport()
    _folders(ctx, report)
    report.folders_withdrawn = move_withdrawn_folders(ctx)
    appended = append_rows(ctx, files=ALL_FILES)
    report.appended, report.absorbed, report.truncated = (
        appended.appended,
        appended.absorbed,
        appended.truncated,
    )
    report.mismatches += appended.mismatches
    _release_records(ctx, report)
    if report.changed or report.mismatches:
        with ctx.db.transaction() as s:
            audit.append_event(s, "reconcile.run", "output", "output", report.as_dict())
    return report
