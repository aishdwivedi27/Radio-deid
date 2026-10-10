"""Pure parts of the job layer: upload path sanitising, reconciliation columns, per-folder balance."""

from __future__ import annotations

import json

import pytest

from app.services.jobs import summary
from app.services.jobs.paths import PathRejected, sanitise_relative
from app.store.models.jobs import Job, JobStudy


@pytest.mark.parametrize(
    ("raw", "parts"),
    [
        ("a.dcm", ("a.dcm",)),
        ("CT/Study_A/CT0001.dcm", ("CT", "Study_A", "CT0001.dcm")),
        ("DICOM\\IM000001", ("DICOM", "IM000001")),
        ("reports/ACC-1 report.pdf", ("reports", "ACC-1 report.pdf")),
    ],
)
def test_sanitise_accepts(raw: str, parts: tuple[str, ...]) -> None:
    assert sanitise_relative(raw) == parts


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("", "empty"),
        ("../x", "dotdot"),
        ("a/../b", "dotdot"),
        ("/etc/x", "absolute"),
        ("\\\\srv\\s", "absolute"),
        ("C:\\x", "absolute"),
        ("a/./b", "empty_part"),
        ("a//b", "empty_part"),
        ("nul.txt", "reserved_name"),
        ("COM1", "reserved_name"),
        ("a/b ", "trailing_dot_or_space"),
        ("a/b.", "trailing_dot_or_space"),
        ("a\tb", "control_char"),
        ("ab:stream", "bad_char"),
        ("x" * 256, "too_long"),
        ("/".join(["d"] * 40), "too_deep"),
    ],
)
def test_sanitise_rejects(raw: str, reason: str) -> None:
    with pytest.raises(PathRejected) as err:
        sanitise_relative(raw)
    assert err.value.reason == reason


def test_reconciliation_columns() -> None:
    cols = summary.reconciliation_columns()
    assert cols[:8] == ("folder", "studies_found", "files_found", "processed", "finalised", "awaiting_review",
                        "auto_qa_failed", "skipped")  # fmt: skip
    assert (
        "AGE_UNDER_18" in cols and "SCREEN_SAVE_DOSE" not in cols
    )  # image-level reasons are not study columns
    assert cols[-7:] == (
        "images_excluded",
        "errors",
        "balanced",
        "with_report",
        "without_report",
        "notes",
        "not_done",
    )


def _study(i: int, folder: str, status: str, **kw: object) -> JobStudy:
    return JobStudy(job_id="J", ordinal=i, study_key=f"{i:064x}", record_id=f"S{i:012X}", folder_key=folder,
                    spans_folders=bool(kw.get("spans", False)), n_files=1, status=status,
                    with_report=bool(kw.get("report", False)), reason=kw.get("reason"), detail_json="{}",
                    updated_at="")  # fmt: skip


def test_balance_per_folder_and_total() -> None:
    index = {
        "studies": 4,
        "folders": [{"key": "f01", "files": 3, "non_image": 1}, {"key": "root", "files": 1}],
    }
    job = Job(job_id="J", started_by="u", status="finished", created_at="", source_kind="folder",
              index_json=json.dumps(index))  # fmt: skip
    studies = [
        _study(0, "f01", "awaiting_review", report=True),
        _study(1, "f01", "excluded", reason="OPT_OUT"),
        _study(2, "f01", "not_done"),
        _study(3, "root", "error", spans=True),
    ]
    sm = summary.summarise(job, studies)
    assert sm.balanced
    f01 = sm.folders[0]
    assert (f01.found, f01.processed, f01.counts["skipped"], f01.counts["not_done"]) == (3, 1, 1, 1)
    assert (f01.with_report, f01.without_report, f01.images_excluded) == (1, 0, 1)
    rows = summary.reconciliation_rows(sm, {"f01": "A", "root": "(root)"})
    assert [r["folder"] for r in rows] == ["A", "(root)", "TOTAL"]
    assert rows[1]["notes"] and rows[2]["errors"] == 1 and rows[2]["balanced"] == "yes"
    index["studies"] = 5  # a study found but never recorded: the job does not balance
    job.index_json = json.dumps(index)
    assert not summary.summarise(job, studies).balanced
