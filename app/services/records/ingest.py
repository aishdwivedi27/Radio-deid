"""One study in → one pending record version in the DB (SPEC §4, §6.5; criterion 5, T35). TR-REL-NF-01,
TR-COH-02, TR-COH-03, TR-ELIG-01.

Source files are hashed first. A file whose SHA-256 or SOP instance is already in a finalised version is a
duplicate and is dropped; a re-run of the same input therefore creates nothing. New files for a finalised
record make version n+1 holding only the new images; new files for a record still under review are added
to that pending version. An excluded study writes one hashed ``exclusions`` row and no files.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from app.deid import pseudonyms as ps
from app.deid.pipeline import process_study
from app.deid.record.canonical import json_line, sha256_file
from app.deid.record.versioning import PriorVersion
from app.deid.types import DeidSettings, ExtractedReport, PendingRecord, SourceFile, StudyGroup
from app.services.context import ServiceContext, now_iso
from app.services.governance.ethics import screen_context
from app.store import audit
from app.store.models.records import Exclusion, Record
from app.store.repos import ledger
from app.store.repos import records as recs

UNDER_REVIEW = ("awaiting_review", "auto_qa_failed", "approved")


@dataclass
class IngestResult:
    status: str  # excluded | duplicate | unchanged | awaiting_review | auto_qa_failed
    record_id: str
    version: int | None = None
    record: PendingRecord | None = None


@dataclass(frozen=True)
class _File:
    src: SourceFile
    sha: str
    sop_key: str


def _ordered_rows(s: Session, record_id: str, image_ids: list[str]) -> tuple[dict[str, object], ...]:
    rows = {i.image_id: json.loads(i.row_json) for i in recs.images(s, record_id)}
    return tuple(rows[i] for i in image_ids)


def _prior(ctx: ServiceContext, s: Session, record: Record | None, rerun: bool) -> PriorVersion | None:
    if record is None:
        return None
    latest = recs.get_version(s, record.record_id, record.latest_version)
    if latest and latest.finalised_at is None and latest.state in UNDER_REVIEW and not rerun:
        row = json.loads(latest.row_json)
        rows = _ordered_rows(s, record.record_id, row["image_ids"])
        return PriorVersion(ctx.paths.pending_root / record.record_id, latest.version, rows, row)
    if record.finalised_version is None:
        return None
    fin = recs.get_version(s, record.record_id, record.finalised_version)
    assert fin is not None
    row = json.loads(fin.row_json)
    rows = _ordered_rows(s, record.record_id, row["image_ids"])
    return PriorVersion(ctx.paths.records_dir / record.record_id, record.finalised_version + 1, rows, row)


def _new_files(s: Session, record_id: str, files: list[_File], use_pending: bool) -> list[_File]:
    fin_sha, fin_sop = ledger.finalised_keys(s, record_id)
    pend_sha, pend_sop = ledger.pending_keys(s, record_id) if use_pending else (set(), set())
    return [f for f in files if f.sha not in fin_sha | pend_sha and f.sop_key not in fin_sop | pend_sop]


def _exclusion(ctx: ServiceContext, rec: PendingRecord, group: StudyGroup, key: bytes, job_id: str) -> None:
    screen = rec.screen
    assert screen is not None
    source = ";".join(sorted(p.as_posix() for p in group.folders))
    with ctx.db.transaction() as s:
        s.add(
            Exclusion(
                job_id=job_id,
                study_key=rec.study_key,
                source_name_hash=ps.hmac_hex(key, "source", source),
                patient_code=rec.patient_code,
                reason=screen.reason or "",
                rule_id=screen.rule_id or "",
                rules_version=screen.rules_version,
                evidence=screen.evidence,
                created_at=now_iso(),
            )
        )
        audit.append_event(
            s,
            "exclusion.recorded",
            "record",
            rec.record_id,
            {"reason": screen.reason, "rule": screen.rule_id},
        )


def _register(ctx: ServiceContext, rec: PendingRecord, new: list[_File], job_id: str, rerun: bool) -> int:
    assert rec.record_row is not None
    version = int(rec.record_row["record_version"])
    fields = {
        "version": version,
        "state": rec.status,
        "row_json": json_line(rec.record_row).decode("utf-8"),
        "job_id": job_id,
        "review_flags_json": json.dumps(rec.review_flags),
        "findings_json": json.dumps([str(f) for f in rec.findings]),
    }
    with ctx.db.transaction() as s:
        finalised = {i.image_id for i in recs.images(s, rec.record_id, recs.FINALISED)}
        rows = [
            (r["image_id"], int(r["record_version"]), r["sha256"], json_line(r).decode("utf-8"))
            for r in rec.image_rows
            if r["image_id"] not in finalised
        ]
        record = {
            "record_id": rec.record_id,
            "patient_code": rec.patient_code,
            "study_key": rec.study_key,
            "state": rec.status,
        }
        latest = recs.get_version(s, rec.record_id, version)
        if rerun or (latest is not None and latest.state not in UNDER_REVIEW):
            ledger.drop_pending(s, rec.record_id)
        recs.save_pending(s, record, fields, rows, now_iso())
        ledger.add_pending(s, rec.record_id, version, ((f.sha, f.sop_key) for f in new), job_id, now_iso())
    return version


def ingest_study(
    ctx: ServiceContext,
    group: StudyGroup,
    report: Path | ExtractedReport | None,
    key: bytes,
    settings: DeidSettings,
    rerun: bool = False,
) -> IngestResult:
    """Screen, de-identify and register one study. ``rerun`` rebuilds a pending version from these files
    (after a rule or code fix) instead of adding to it."""
    rid = ps.record_id(key, group.study_uid)
    files = [_File(f, sha256_file(f.path), ps.hmac_hex(key, "sop", f.sop_uid)) for f in group.files]
    with ctx.db.session() as s:
        record = recs.get_record(s, rid)
        prior = _prior(ctx, s, record, rerun)
        new = _new_files(s, rid, files, use_pending=not rerun) if record else files
        c7 = recs.c7_index(s)
        screen = screen_context(s)
    if not new:
        return IngestResult("duplicate", rid, record.latest_version if record else None)
    subset = dataclasses.replace(group, files=[f.src for f in new])
    run_settings = dataclasses.replace(settings, screen=screen)
    rec = process_study(subset, report, key, run_settings, ctx.paths.pending_root, c7, prior)
    if rec.status == "excluded":
        _exclusion(ctx, rec, group, key, settings.job_id)
        return IngestResult("excluded", rid, record=rec)
    if rec.record_row is None:  # nothing new was releasable, or no releasable image at all (NO_IMAGES)
        return IngestResult(rec.status, rid, record=rec)
    version = _register(ctx, rec, new, settings.job_id, rerun)
    return IngestResult(rec.status, rid, version, rec)
