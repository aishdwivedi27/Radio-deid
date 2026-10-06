"""One study → one pending record folder (SPEC §4, §6.5 loop). TR-DEID-01..12, TR-RPT-01..05, TR-QA-01..05.

screen (§6.4) → images: triage, OCR mask, allowlist rebuild, ``{image_id}.dcm`` → report (optional) →
preview → A1-A7 → rows → record.json → move into ``pending_root/<record_id>/`` → C1-C8.
Nothing is written for an excluded study. Files are built in ``pending_root/.tmp-<record_id>`` and moved
into place in one rename, so a record is never half-written. The source files are only read.
"""

from __future__ import annotations

import shutil
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pydicom
from pydicom.dataset import Dataset

from app.deid import pseudonyms as ps
from app.deid.dicom import triage
from app.deid.dicom.policy import policy_for
from app.deid.dicom.rebuild import RebuildContext, build_dataset, method_text
from app.deid.eligibility.rules import review_flags, screen
from app.deid.identifiers import collect, identifier_regex
from app.deid.keys import key_fingerprint
from app.deid.ocr import engine as ocr_engine
from app.deid.ocr.mask import mask_dataset
from app.deid.qa import a_checks, c_checks
from app.deid.record.canonical import write_record_json
from app.deid.record.preview import write_preview
from app.deid.record.rows import RecordInputs, ReportPart, image_row, now_iso, record_row
from app.deid.report import ner
from app.deid.report.header import build_report
from app.deid.report.read import read_report
from app.deid.report.redact import redact_report
from app.deid.schemas import allowlist
from app.deid.types import (
    DeidError,
    DeidSettings,
    ExtractedReport,
    Finding,
    Identifiers,
    PendingRecord,
    ScreenContext,
    ScreenResult,
    StudyGroup,
)
from app.deid.version import PIPELINE_VERSION


@dataclass
class _Images:
    rows: list[dict[str, Any]] = field(default_factory=list)
    first: Dataset | None = None
    excluded: Counter[str] = field(default_factory=Counter)
    remaining: dict[str, int] = field(default_factory=dict)  # file name -> text boxes left after masking
    interior: int = 0
    ocr_used: bool = False


def _order(group: StudyGroup, key: bytes) -> list[Path]:
    files = sorted(
        group.files,
        key=lambda f: (f.series_number or 0, f.instance_number or 0, ps.hmac_hex(key, "uid", f.sop_uid)),
    )
    return [f.path for f in files]


def _mask(ds: Dataset, use_ocr: bool) -> tuple[str | None, int, int, int]:
    """(exclusion reason, regions, remaining, interior) for one image."""
    if not triage.needs_ocr(ds):
        return None, 0, 0, 0
    if not use_ocr:
        return ("OCR_UNCLEARED" if triage.exclude_without_ocr(ds) else None), 0, 0, 0
    try:
        res = mask_dataset(ds)
    except (ValueError, NotImplementedError, RuntimeError, AttributeError):
        return "OCR_UNCLEARED", 0, 0, 0  # pixels cannot be decoded (e.g. no JPEG plugin): never unmasked
    return None, res.regions, res.remaining, res.interior


def _process_images(
    group: StudyGroup,
    key: bytes,
    s: DeidSettings,
    ids: Identifiers,
    study_mods: frozenset[str],
    out_dir: Path,
    ids_out: tuple[str, str],
) -> tuple[_Images, ps.ImageIdAllocator]:
    rid, pcode = ids_out
    imgs, alloc = _Images(), ps.ImageIdAllocator(rid)
    use_ocr = s.ocr_enabled and ocr_engine.available()
    rx = identifier_regex(ids.values)
    method = method_text(PIPELINE_VERSION, allowlist().version, s.date_mode)
    shift = ps.shift_days(key, group.patient_id)
    for path in _order(group, key):
        ds = pydicom.dcmread(path)
        reason = triage.exclusion_reason(ds, study_mods)
        regions = remaining = interior = 0
        if reason is None:
            reason, regions, remaining, interior = _mask(ds, use_ocr)
        if reason:
            imgs.excluded[reason] += 1
            continue
        imgs.ocr_used |= use_ocr and triage.needs_ocr(ds)
        image_id = alloc.allocate(_num(ds.get("SeriesNumber")), _num(ds.get("InstanceNumber")))
        ctx = RebuildContext(
            key,
            rid,
            pcode,
            image_id,
            group.study_uid,
            shift,
            s.date_mode,
            s.age_cap_years,
            rx,
            method,
            masked=regions > 0,
            mask_verified=remaining == 0,
        )
        out = build_dataset(ds, policy_for(triage.modality(ds)), ctx)
        target = out_dir / f"{image_id}.dcm"
        out.save_as(target, enforce_file_format=True)
        imgs.rows.append(image_row(target, out, rid, pcode, regions))
        imgs.remaining[target.name] = remaining
        imgs.interior += interior
        imgs.first = imgs.first or out
    return imgs, alloc


def _num(value: object) -> int | None:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


def _headers(group: StudyGroup) -> list[Dataset]:
    return [pydicom.dcmread(f.path, stop_before_pixels=True) for f in group.files]


def _write_report(
    out_dir: Path,
    rid: str,
    pcode: str,
    image_ids: list[str],
    src: ExtractedReport,
    ids: Identifiers,
    use_ner: bool,
) -> ReportPart:
    body, counts = redact_report(src.text, ids.values, use_ner)
    path = out_dir / f"{rid}_report.txt"
    path.write_text(build_report(rid, pcode, image_ids, body), encoding="utf-8", newline="\n")
    return ReportPart(present=True, file=path, source=src, redactions=counts)


def _fresh(path: Path) -> Path:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def _swap_in(tmp: Path, final: Path) -> Path:
    if final.exists():
        shutil.rmtree(final)  # a re-run replaces its own pending copy; nothing here is finalised output
    tmp.rename(final)
    return final


def process_study(
    group: StudyGroup,
    report: Path | ExtractedReport | None,
    key: bytes,
    settings: DeidSettings,
    pending_root: Path,
    finalised: dict[str, str] | None = None,
) -> PendingRecord:
    """Screen and de-identify one study into ``pending_root/<record_id>/``. See the module docstring."""
    headers = _headers(group)
    rid, pcode = ps.record_id(key, group.study_uid), ps.patient_code(key, group.patient_id)
    rec = PendingRecord(
        status="excluded",
        record_id=rid,
        patient_code=pcode,
        study_key=ps.hmac_hex(key, "study", group.study_uid),
    )
    src = read_report(report) if isinstance(report, Path) else report
    rec.screen = screen(headers, src.text if src else None, pcode, settings.screen)
    rec.report_matched = src is not None
    if rec.screen.excluded:
        return rec
    tmp = _fresh(pending_root / f".tmp-{rid}")
    try:
        _build(rec, group, headers, src, key, settings, tmp)
        if rec.record_row is None:
            shutil.rmtree(tmp)
            return rec
        rec.folder = _swap_in(tmp, pending_root / rid)
    except Exception as exc:  # noqa: BLE001 - re-raised without the source chain (it may hold a path)
        shutil.rmtree(tmp, ignore_errors=True)
        raise DeidError(f"processing failed ({type(exc).__name__})") from None
    _consistency(rec, finalised or {})
    return rec


def _build(
    rec: PendingRecord,
    group: StudyGroup,
    headers: list[Dataset],
    src: ExtractedReport | None,
    key: bytes,
    s: DeidSettings,
    tmp: Path,
) -> None:
    screen_result = rec.screen
    assert screen_result is not None
    ids = collect(headers)
    mods = frozenset(str(h.get("Modality", "")).upper() for h in headers)
    imgs, alloc = _process_images(group, key, s, ids, mods, tmp, (rec.record_id, rec.patient_code))
    rec.excluded_images, rec.notes["ID_TRUNCATED"] = imgs.excluded, alloc.truncated
    if not imgs.rows or imgs.first is None:
        rec.status = "auto_qa_failed"
        rec.findings = [Finding("NO_IMAGES", "no releasable image in this study")]
        return
    image_ids = [str(r["image_id"]) for r in imgs.rows]
    use_ner = s.ner_enabled and ner.available()
    part = (
        _write_report(tmp, rec.record_id, rec.patient_code, image_ids, src, ids, use_ner)
        if src
        else ReportPart(False)
    )
    write_preview(tmp / f"{image_ids[len(image_ids) // 2]}.dcm", tmp / f"{rec.record_id}_preview.png")
    rec.findings = a_checks.check_deid(tmp, ids, imgs.remaining)
    not_applicable = ({"A4"} if not part.present else set()) | ({"A7"} if not imgs.ocr_used else set())
    inputs = RecordInputs(
        rec.record_id,
        rec.patient_code,
        imgs.first,
        imgs.rows,
        part,
        imgs.excluded,
        screen_result,
        s.date_mode,
        s.job_id,
        PIPELINE_VERSION,
        key_fingerprint(key),
        now_iso(),
    )
    rec.image_rows = list(imgs.rows)
    rec.redactions = dict(part.redactions or {})
    rec.ocr_regions = sum(int(r["ocr_regions"]) for r in imgs.rows)
    rec.review_flags = (["A7-I"] if imgs.interior else []) + review_flags(headers)
    planned = c_checks.status([], part.present)
    rec.record_row = record_row(inputs, a_checks.status(rec.findings, not_applicable), planned)
    write_record_json(tmp, rec.record_row)


def _consistency(rec: PendingRecord, finalised: dict[str, str]) -> None:
    assert rec.folder is not None and rec.record_row is not None
    present = bool(rec.record_row["report"]["present"])
    found = c_checks.check_consistency(rec.folder, rec, finalised)
    if found:  # record the real C results in the row, then re-check (C8 compares with the updated row)
        rec.record_row["qa"]["consistency_checks"] = c_checks.status(found, present)
        write_record_json(rec.folder, rec.record_row)
        found = c_checks.check_consistency(rec.folder, rec, finalised)
    rec.findings += found
    rec.status = "auto_qa_failed" if rec.findings else "awaiting_review"


def screen_study(
    group: StudyGroup, report: Path | ExtractedReport | None, key: bytes, ctx: ScreenContext
) -> ScreenResult:
    """The eligibility screen alone (SPEC §6.4), e.g. for a skip spot-check; ``process_study`` runs it too."""
    src = read_report(report) if isinstance(report, Path) else report
    return screen(_headers(group), src.text if src else None, ps.patient_code(key, group.patient_id), ctx)
