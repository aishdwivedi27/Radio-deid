"""Record and image rows validate against docs/schemas (SPEC §5.2). TR-DEID-10, TR-QA-02.

Reviewer fields are added at finalisation (Phase 3); here they are filled with dummies so the full schema
applies to everything the library produced.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from app.deid.index import index_input
from app.deid.pipeline import process_study
from app.deid.schemas import SCHEMA_DIR
from app.deid.types import DeidSettings
from tests.deid.conftest import Processed

NOW = "2026-10-06T10:00:00+05:30"
REVIEWER = {"decision": "approved", "reviewer_id": "u_test", "decided_at": NOW}
FINAL = {"finalised_at": NOW, "finding_category": "NORMAL", "cohort_ref": "EC-TEST-001"}


def _validator(name: str) -> Draft202012Validator:
    schema = json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _finalise(row: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(row) | FINAL
    out["qa"] |= REVIEWER
    return out


def _errors(v: Draft202012Validator, row: dict[str, Any]) -> list[str]:
    return [f"{list(e.path)}: {e.message}" for e in v.iter_errors(row)]


def test_rows_validate(processed: Processed) -> None:  # TR-DEID-10
    records, images = _validator("record.schema.json"), _validator("image.schema.json")
    for rec in processed.records:
        assert rec.record_row is not None
        assert _errors(records, _finalise(rec.record_row)) == []
        for row in rec.image_rows:
            assert _errors(images, row | {"finalised_at": NOW}) == []


def test_pending_row_lacks_only_reviewer_fields(processed: Processed) -> None:  # TR-DEID-10
    row = processed.records[0].record_row
    assert row is not None
    missing = {
        e.message.split("'")[1]
        for e in _validator("record.schema.json").iter_errors(row)
        if e.validator == "required"
    }
    assert missing == {
        "finalised_at",
        "finding_category",
        "cohort_ref",
        "decision",
        "reviewer_id",
        "decided_at",
    }


def test_image_only_row_validates(processed: Processed, key: bytes, tmp_path: Path) -> None:  # T31, TR-RPT-05
    group = index_input(processed.inbox).studies[0]
    rec = process_study(group, None, key, DeidSettings(), tmp_path)
    assert rec.record_row is not None
    assert _errors(_validator("record.schema.json"), _finalise(rec.record_row)) == []


def test_row_values(processed: Processed) -> None:  # TR-DEID-10, SPEC §5.2
    by_mod = {r.record_row["modalities"][0]: r.record_row for r in processed.records if r.record_row}
    ct = by_mod["CT"]
    assert ct["n_images"] == 20 and ct["images_excluded"] == [{"reason": "SCREEN_SAVE_DOSE", "count": 1}]
    assert ct["age_band"] == "40-44" and ct["date_mode"] == "shift" and ct["consent_basis"] == "waiver"
    assert ct["report"]["source_format"] == "docx" and len(ct["key_fingerprint"]) == 16
    assert all(r.record_row["study_date"] != "2026-09-01" for r in processed.records if r.record_row)
