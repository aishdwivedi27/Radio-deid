"""Run one job, one study at a time (SPEC §4, §6.4, §6.5 loop). TR-ING-01..05, TR-ELIG-10..15.

find the input again → walk → index (DICOM by content, DICOMDIR skipped) → match reports by ID → one
``job_studies`` row per study → for each study not yet done: ``ingest_study`` (screen → de-identify → A1-A7,
C1-C8 → pending rows) → event → next. A failing study is recorded as an error and the job goes on. Cancel
is honoured between studies (rollback, ``rollback.py``). A job interrupted by a shutdown or a crash keeps its
status and resumes at the next start, skipping studies already done (``job_studies`` and the SHA-256 ledger).
At the end: ``exclusions.csv`` and ``reconciliation.csv`` (``finish.py``) and the staging folder is deleted.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from app.auth.errors import AppError
from app.deid import pseudonyms as ps
from app.deid.index import index_input
from app.deid.keys import load_or_create_key
from app.deid.report.match import ReportMatches, match_reports
from app.deid.types import DeidSettings, InputIndex, StudyGroup
from app.services.context import ServiceContext, now_iso
from app.services.jobs import events, finish, inputs, secure_files
from app.services.records.ingest import IngestResult, ingest_study
from app.store.models.jobs import JobStudy
from app.store.repos import jobs as jobs_repo
from app.store.repos import records as recs

log = logging.getLogger(__name__)

OCR_ENABLED = True  # technical settings (SPEC §2) arrive with the settings screen; tests switch these off
NER_ENABLED = True
ROOT_KEY, ROOT_NAME = "root", "(root)"


@dataclass
class Indexed:
    base: Path
    index: InputIndex
    matches: ReportMatches
    groups: dict[str, tuple[int, StudyGroup]] = field(default_factory=dict)  # study_key → (index, group)


def _set(ctx: ServiceContext, job_id: str, **fields: object) -> None:
    with ctx.db.transaction() as s:
        job = jobs_repo.get_job(s, job_id)
        assert job is not None
        for k, v in fields.items():
            setattr(job, k, v)


def input_location(ctx: ServiceContext, job_id: str, source_kind: str) -> tuple[Path, Path]:
    """(folder to read, root it must stay inside), found again now."""
    if source_kind == "upload":
        files = ctx.paths.staging_dir / job_id / "files"
        if not files.is_dir():
            raise AppError(409, "input_missing", "The uploaded files are no longer available.")
        return files, files
    source = secure_files.read_source(ctx, job_id) or {}
    for r in inputs.resolve_roots(ctx):
        if str(r.approved) == source.get("approved_root"):
            if r.current is None:
                raise inputs.drive_missing()
            folder = r.current / source.get("rel", "") if source.get("rel") else r.current
            if not folder.is_dir():
                raise AppError(409, "input_missing", "The input folder is no longer available.")
            return folder, r.current
    raise inputs.drive_missing()


def _folder_keys(base: Path, files: list[Path]) -> dict[str, str]:
    names = {p.name for p in base.iterdir() if p.is_dir() and not inputs.is_link(p)}
    names |= {f.relative_to(base).parts[0] for f in files if len(f.relative_to(base).parts) > 1}
    return {name: f"f{i:02d}" for i, name in enumerate(sorted(names), start=1)}


def _index(ctx: ServiceContext, job_id: str, base: Path, root: Path, key: bytes) -> Indexed:
    walked = inputs.walk(base, root)
    index = index_input(base, walked.files)
    matches = match_reports(index)
    keys = _folder_keys(base, walked.files)

    def folder_of(path: Path) -> str:
        parts = path.relative_to(base).parts
        return ROOT_KEY if len(parts) == 1 else keys[parts[0]]

    hidden = {p for p, kind in index.skipped_files if kind == "hidden"}
    per: dict[str, dict[str, int]] = {
        k: {"files": 0, "non_image": 0, "unmatched_reports": 0} for k in keys.values()
    }
    per[ROOT_KEY] = {"files": 0, "non_image": 0, "unmatched_reports": 0}
    for f in walked.files:
        if f not in hidden:
            per[folder_of(f)]["files"] += 1
    for p, kind in index.skipped_files:
        if kind in ("non_dicom", "unreadable"):
            per[folder_of(p)]["non_image"] += 1
    unmatched = set(index.reports) - set(matches.by_study.values())
    for p in unmatched:
        per[folder_of(p)]["unmatched_reports"] += 1
    out = Indexed(base, index, matches)
    with ctx.db.transaction() as s:
        for i, g in enumerate(index.studies):
            sk = ps.hmac_hex(key, "study", g.study_uid)
            first = min(f.path for f in g.files)
            spans = len({folder_of(f.path) for f in g.files}) > 1
            fields = {
                "ordinal": i,
                "study_key": sk,
                "record_id": ps.record_id(key, g.study_uid),
                "folder_key": folder_of(first),
                "spans_folders": spans,
                "n_files": g.n_files,
            }
            jobs_repo.add_study(s, job_id, fields, now_iso())
            out.groups[sk] = (i, g)
        job = jobs_repo.get_job(s, job_id)
        assert job is not None
        order = [*sorted(keys.values()), ROOT_KEY]
        job.index_json = json.dumps(
            {
                "studies": len(index.studies),
                "files": len(walked.files) - len(hidden),
                "reports_found": len(index.reports),
                "reports_unmatched": len(unmatched),
                "links_outside": walked.links_outside,
                "folders": [{"key": k, **per[k]} for k in order],
            }  # fmt: skip
        )
    secure_files.write_folders(ctx, job_id, {**{v: k for k, v in keys.items()}, ROOT_KEY: ROOT_NAME})
    return out


def _state_of(ctx: ServiceContext, record_id: str) -> str:
    with ctx.db.session() as s:
        record = recs.get_record(s, record_id)
    state = record.state if record else "auto_qa_failed"
    return {"withdrawn": "finalised", "approved": "awaiting_review"}.get(state, state)


def _outcome(
    ctx: ServiceContext, res: IngestResult, group: StudyGroup, had_report: bool
) -> dict[str, object]:
    rec = res.record
    images = dict(rec.excluded_images) if rec else {}
    if res.status == "excluded" and rec and rec.screen:
        sc = rec.screen
        detail = {
            "evidence": sc.evidence,
            "other_matches": list(sc.other_matches),
            "modality": "|".join(sorted(m for m in group.modalities if m)),
            "rules_version": sc.rules_version,
            "screened_at": now_iso(),
        }
        return {
            "status": "excluded",
            "reason": sc.reason,
            "rule_id": sc.rule_id,
            "detail_json": json.dumps(detail),
        }
    if res.status in ("duplicate", "unchanged"):
        status = _state_of(ctx, res.record_id)
        if status not in ("finalised", "awaiting_review", "auto_qa_failed"):
            return {"status": "excluded", "reason": f"REVIEW_{status.upper()}", "detail_json": "{}"}
        return {
            "status": status,
            "with_report": had_report,
            "detail_json": json.dumps({"images_excluded": images}),
        }
    wrote = res.version is not None
    return {"status": res.status, "version": res.version, "wrote_version": wrote,
            "with_report": bool(rec and rec.report_matched),
            "detail_json": json.dumps({"images_excluded": images})}  # fmt: skip


def _event_for(st: JobStudy, res: IngestResult | None, total: int) -> tuple[str, dict[str, object]]:
    base: dict[str, object] = {"index": st.ordinal + 1, "total": total, "record_id": st.record_id}
    if st.status == "excluded":
        return "record_excluded", {**base, "reason": st.reason, "rule_id": st.rule_id}
    rec = res.record if res else None
    return "record_ready", {
        **base,
        "version": st.version,
        "state": st.status,
        "n_images": len(rec.image_rows) if rec else 0,
        "with_report": st.with_report,
        "review_flags": list(rec.review_flags) if rec else [],
        "findings": len(rec.findings) if rec else 0,
    }


def _one(ctx: ServiceContext, job_id: str, st: JobStudy, ix: Indexed, key: bytes, total: int) -> None:
    i, group = ix.groups[st.study_key]
    report = ix.matches.by_study.get(i)
    folder = st.folder_key
    events.emit(ctx, job_id, "record_start", {"index": st.ordinal + 1, "total": total,
                                              "record_id": st.record_id, "folder": folder})  # fmt: skip
    settings = DeidSettings(job_id=job_id, ocr_enabled=OCR_ENABLED, ner_enabled=NER_ENABLED)
    res: IngestResult | None = None
    try:
        res = ingest_study(ctx, group, report, key, settings)
        fields = _outcome(ctx, res, group, report is not None)
    except Exception as exc:  # noqa: BLE001 - one bad study never stops the job; type name only (no paths)
        log.warning("job %s study %d failed (%s)", job_id, st.ordinal + 1, type(exc).__name__)
        fields = {"status": "error"}
    with ctx.db.transaction() as s:
        row = jobs_repo.get_study(s, job_id, st.study_key)
        assert row is not None
        for k, v in fields.items():
            setattr(row, k, v)
        row.updated_at = now_iso()
    done = row  # sessions keep loaded values after commit (expire_on_commit=False)
    if done.status == "error":
        events.emit(
            ctx,
            job_id,
            "record_failed",
            {
                "index": done.ordinal + 1,
                "total": total,
                "record_id": done.record_id,
                "error": "processing_failed",
            },
        )
    else:
        type_, payload = _event_for(done, res, total)
        events.emit(ctx, job_id, type_, payload)
    events.emit(ctx, job_id, "progress", finish.summary_of(ctx, job_id))


def _key(ctx: ServiceContext) -> bytes:
    if not ctx.paths.key_path.exists():
        raise AppError(409, "no_key", "The centre key has not been created yet.")
    return load_or_create_key(ctx.paths.key_path)


def run_job(ctx: ServiceContext, job_id: str, should_stop: Callable[[], bool] = lambda: False) -> str:
    """Run (or resume) a job; returns its status afterwards (``processing`` if stopped by a shutdown)."""
    with ctx.db.session() as s:
        job = jobs_repo.get_job(s, job_id)
        assert job is not None
        kind, cancel = job.source_kind, job.cancel_requested
    if cancel:
        return finish.cancel(ctx, job_id)
    _set(ctx, job_id, status="indexing", started_at=now_iso())
    try:
        key = _key(ctx)
        base, root = input_location(ctx, job_id, kind)
        ix = _index(ctx, job_id, base, root, key)
    except AppError as exc:
        return finish.fail(ctx, job_id, exc.code, exc.message)
    except Exception as exc:  # noqa: BLE001 - reported by code only
        log.warning("job %s indexing failed (%s)", job_id, type(exc).__name__)
        return finish.fail(ctx, job_id, "index_failed", "The input could not be read.")
    _set(ctx, job_id, status="processing")
    events.emit(ctx, job_id, "indexed", finish.indexed_payload(ctx, job_id))
    with ctx.db.session() as s:
        todo = [st for st in jobs_repo.studies(s, job_id) if st.status == "not_done"]
        total = len(jobs_repo.studies(s, job_id))
    for st in todo:
        with ctx.db.session() as s:
            job = jobs_repo.get_job(s, job_id)
            assert job is not None
            cancel = job.cancel_requested
        if cancel:
            return finish.cancel(ctx, job_id)
        if should_stop():
            return "processing"
        _one(ctx, job_id, st, ix, key, total)
    return finish.complete(ctx, job_id, ix)
