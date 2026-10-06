"""The de-identified report header block (SPEC §3.1). TR-RPT-01, TR-QA-02 (C4).

DE-IDENTIFIED REPORT
Record ID: S7DB2DCB7C0A0
Patient code: P3F9A1C07B2E4
Images: S7DB2DCB7C0A0-0002-000001, S7DB2DCB7C0A0-0003-000001
------------------------------------------------------------
<redacted report text>
"""

from __future__ import annotations

from dataclasses import dataclass

TITLE = "DE-IDENTIFIED REPORT"
RULE = "-" * 60


@dataclass(frozen=True)
class ReportHeader:
    record_id: str
    patient_code: str
    image_ids: tuple[str, ...]


def build_report(record_id: str, patient_code: str, image_ids: list[str], body: str) -> str:
    lines = [
        TITLE,
        f"Record ID: {record_id}",
        f"Patient code: {patient_code}",
        f"Images: {', '.join(image_ids)}",
    ]
    return "\n".join([*lines, RULE, body.strip("\n")]) + "\n"


def parse_header(text: str) -> ReportHeader | None:
    lines = text.split("\n")
    if len(lines) < 5 or lines[0] != TITLE or lines[4] != RULE:
        return None
    fields = {}
    for line in lines[1:4]:
        key, sep, value = line.partition(": ")
        if not sep:
            return None
        fields[key] = value
    if set(fields) != {"Record ID", "Patient code", "Images"}:
        return None
    images = tuple(i.strip() for i in fields["Images"].split(",") if i.strip())
    return ReportHeader(fields["Record ID"], fields["Patient code"], images)
