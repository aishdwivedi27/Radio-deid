"""Rows as finalised: the pending rows plus the reviewer's decision (SPEC §5.2, §5.3). TR-REV-04, TR-COH-03.

Pure: the caller passes the decision (reviewer, times, finding category, cohort reference). The record row
gets one ``finalised_at`` shared with the image rows it adds.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from app.deid.schemas import load_finding_categories
from app.deid.types import DeidError

PRE_APPROVAL_COHORT = "PRE-APPROVAL"


@dataclass(frozen=True)
class Decision:
    reviewer_id: str
    decided_at: str
    finalised_at: str
    finding_category: str
    cohort_ref: str


def finding_codes() -> frozenset[str]:
    return frozenset(str(c["code"]) for c in load_finding_categories()["categories"])


def final_record_row(pending: dict[str, Any], decision: Decision) -> dict[str, Any]:
    if decision.finding_category not in finding_codes():
        raise DeidError("finding_category is not in finding_categories.json")
    if not decision.reviewer_id or not decision.cohort_ref:
        raise DeidError("reviewer_id and cohort_ref are required to finalise")
    row = copy.deepcopy(pending)
    row["qa"].update(
        {"decision": "approved", "reviewer_id": decision.reviewer_id, "decided_at": decision.decided_at}
    )
    row["finalised_at"] = decision.finalised_at
    row["finding_category"] = decision.finding_category
    row["cohort_ref"] = decision.cohort_ref
    return row


def final_image_row(pending: dict[str, Any], finalised_at: str) -> dict[str, Any]:
    row = dict(pending)
    row["finalised_at"] = finalised_at
    return row
