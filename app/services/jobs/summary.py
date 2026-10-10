"""Job summary, per-folder table and reconciliation (SPEC §6.4). TR-ELIG-10, TR-ELIG-12, TR-ELIG-14,
TR-ELIG-15.

Each row balances on its own: ``found = finalised + awaiting_review + auto_qa_failed + skipped + errors +
not_done`` and ``with_report + without_report = processed``; the rows sum to ``TOTAL``. ``not_done`` (studies
a cancelled or failed job never completed) is an addition to the SPEC columns (owner decision, 10 Oct 2026).
Folders appear by key only; their names live in ``secure/`` (``secure_files.read_folders``).
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.deid.schemas import load_exclusion_rules
from app.store.models.jobs import Job, JobStudy

PROCESSED = ("finalised", "awaiting_review", "auto_qa_failed")
PRE_APPROVAL_REASON = "PRE_APPROVAL_REAL_DATA"
PRE_APPROVAL_NOTICE = "No real patient data may be processed before the ethics approval is recorded."
COUNTS = ("finalised", "awaiting_review", "auto_qa_failed", "skipped", "errors", "not_done")


def study_reasons() -> tuple[str, ...]:
    """Study-level reason codes in rule order (image-level reasons, order 11, go to images_excluded)."""
    rules = load_exclusion_rules()["rules"]
    return tuple(r["reason"] for r in sorted(rules, key=lambda r: r["order"]) if int(r["order"]) < 11)


def reconciliation_columns() -> tuple[str, ...]:
    head = ("folder", "studies_found", "files_found", "processed", "finalised", "awaiting_review",
            "auto_qa_failed", "skipped")  # fmt: skip
    tail = ("images_excluded", "errors", "balanced", "with_report", "without_report", "notes", "not_done")
    return head + study_reasons() + tail


@dataclass
class FolderCounts:
    key: str
    found: int = 0
    files: int = 0
    counts: Counter[str] = field(default_factory=Counter)
    by_reason: Counter[str] = field(default_factory=Counter)
    images_excluded: int = 0
    with_report: int = 0
    without_report: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def processed(self) -> int:
        return sum(self.counts[k] for k in PROCESSED)

    @property
    def balanced(self) -> bool:
        return self.found == sum(self.counts[k] for k in COUNTS) and (
            self.with_report + self.without_report == self.processed
        )

    def add(self, other: FolderCounts) -> None:
        self.found += other.found
        self.files += other.files
        self.counts.update(other.counts)
        self.by_reason.update(other.by_reason)
        self.images_excluded += other.images_excluded
        self.with_report += other.with_report
        self.without_report += other.without_report

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "found": self.found,
            "files": self.files,
            "processed": self.processed,
            **{k: self.counts[k] for k in COUNTS},
            "by_reason": dict(sorted(self.by_reason.items())),
            "images_excluded": self.images_excluded,
            "with_report": self.with_report,
            "without_report": self.without_report,
            "balanced": self.balanced,
        }


def _bucket(status: str) -> str:
    return {"excluded": "skipped", "error": "errors"}.get(status, status)


def _study_into(row: FolderCounts, st: JobStudy, images: Counter[str]) -> None:
    row.found += 1
    bucket = _bucket(st.status)
    row.counts[bucket] += 1
    if bucket == "skipped":
        row.by_reason[st.reason or "OTHER"] += 1
    if st.status in PROCESSED:
        if st.with_report:
            row.with_report += 1
        else:
            row.without_report += 1
    detail = json.loads(st.detail_json or "{}")
    for reason, n in (detail.get("images_excluded") or {}).items():
        images[reason] += int(n)
        row.images_excluded += int(n)
    if st.spans_folders:
        row.notes.append(f"{st.record_id} has files in more than one folder")


@dataclass
class Summary:
    folders: list[FolderCounts]
    total: FolderCounts
    images_excluded: Counter[str]
    index: dict[str, Any]

    @property
    def balanced(self) -> bool:
        rows_ok = all(f.balanced for f in self.folders) and self.total.balanced
        found = int(self.index.get("studies", self.total.found))
        return rows_ok and found == self.total.found


def summarise(job: Job, studies: Sequence[JobStudy]) -> Summary:
    index = json.loads(job.index_json or "{}")
    folders = {
        f["key"]: FolderCounts(f["key"], files=int(f.get("files", 0))) for f in index.get("folders", [])
    }
    images: Counter[str] = Counter()
    for f in index.get("folders", []):
        extra = int(f.get("non_image", 0)) + int(f.get("unmatched_reports", 0))
        folders[f["key"]].images_excluded += extra
        if extra:
            images["NON_IMAGE"] += extra
    for st in studies:
        row = folders.setdefault(st.folder_key, FolderCounts(st.folder_key))
        _study_into(row, st, images)
    total = FolderCounts("TOTAL")
    for row in folders.values():
        total.add(row)
    return Summary(list(folders.values()), total, images, index)


def job_summary(job: Job, studies: Sequence[JobStudy]) -> dict[str, Any]:
    sm = summarise(job, studies)
    t = sm.total
    counts = {
        "awaiting_review": t.counts["awaiting_review"],
        "auto_qa_failed": t.counts["auto_qa_failed"],
        "finalised": t.counts["finalised"],
        "excluded": t.counts["skipped"],
        "errors": t.counts["errors"],
        "not_done": t.counts["not_done"],
    }
    refused = t.by_reason.get(PRE_APPROVAL_REASON, 0) > 0
    return {
        "job_id": job.job_id,
        "status": job.status,
        "source_kind": job.source_kind,
        "done": t.found - t.counts["not_done"],
        "total": t.found,
        "counts": counts,
        "by_reason": dict(sorted(t.by_reason.items())),
        "images_excluded": dict(sorted(sm.images_excluded.items())),
        "reports_found": int(sm.index.get("reports_found", 0)),
        "reports_unmatched": int(sm.index.get("reports_unmatched", 0)),
        "with_report": t.with_report,
        "without_report": t.without_report,
        "folders": [f.as_dict() for f in sm.folders],
        "balanced": sm.balanced,
        "notice": PRE_APPROVAL_NOTICE if refused else None,
    }


def reconciliation_rows(sm: Summary, names: dict[str, str]) -> list[dict[str, Any]]:
    """Rows of reconciliation.csv: one per top-level folder, ``(root)``, then ``TOTAL``."""
    out = []
    for row in [*sm.folders, sm.total]:
        name = "TOTAL" if row is sm.total else names.get(row.key, row.key)
        values: dict[str, Any] = {
            "folder": name,
            "studies_found": row.found,
            "files_found": row.files,
            "processed": row.processed,
            "skipped": row.counts["skipped"],
            "images_excluded": row.images_excluded,
            "errors": row.counts["errors"],
            "balanced": "yes" if row.balanced and (row is not sm.total or sm.balanced) else "no",
            "with_report": row.with_report,
            "without_report": row.without_report,
            "notes": "; ".join(row.notes),
            "not_done": row.counts["not_done"],
        }
        for k in ("finalised", "awaiting_review", "auto_qa_failed"):
            values[k] = row.counts[k]
        reasons = study_reasons()
        for reason in reasons:
            values[reason] = row.by_reason.get(reason, 0)
        other = {r: n for r, n in row.by_reason.items() if r not in reasons}
        if other:
            values["notes"] = "; ".join(
                [*row.notes, *(f"{n} skipped as {r}" for r, n in sorted(other.items()))]
            )
        out.append(values)
    return out
