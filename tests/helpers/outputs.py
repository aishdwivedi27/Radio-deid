"""``assert_outputs_consistent(output_root)``: the four output files, the record folders and (optionally) the
DB all agree, and every ID matches everywhere (SPEC §3.1, §3.2 C1-C8, §5; acceptance criterion 2).
Reused by later phases. TR-QA-02, TR-REL-NF-01, TR-DEID-10.
"""

from __future__ import annotations

import csv
import io
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.deid.qa.c_checks import check_consistency
from app.deid.record.canonical import json_line
from app.deid.record.columns import IMAGE_COLUMNS, RECORD_COLUMNS, WITHDRAWAL_COLUMNS
from app.deid.record.flatten import image_csv, record_csv, withdrawal_csv
from app.deid.types import PendingRecord


@dataclass
class OutputSummary:
    record_lines: int
    image_lines: int
    withdrawal_lines: int
    folders: int


def _jsonl(path: Path) -> list[tuple[bytes, dict[str, Any]]]:
    if not path.exists():
        return []
    data = path.read_bytes()
    assert data == b"" or data.endswith(b"\n"), f"{path.name}: incomplete last line"
    return [(line, json.loads(line)) for line in data.split(b"\n") if line]


def _csv(path: Path, columns: tuple[str, ...]) -> list[list[str]]:
    if not path.exists():
        return []
    text = path.read_bytes().decode("utf-8")
    assert "\r" not in text, f"{path.name}: CRLF line ending"
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0] == list(columns), f"{path.name}: header differs from SPEC §5.2"
    return rows[1:]


def _check_pair(
    name: str, lines: list[tuple[bytes, dict[str, Any]]], rows: list[list[str]], flat: Any
) -> None:
    assert len(lines) == len(rows), f"{name}: JSONL and CSV row counts differ"
    for (raw, row), csv_row in zip(lines, rows, strict=True):
        assert raw == json_line(row), f"{name}: JSONL line is not canonical JSON"
        assert csv_row == flat(row), f"{name}: CSV row differs from the flattened JSON row"


def _check_folder(root: Path, latest: dict[str, Any], image_rows: dict[str, dict[str, Any]]) -> None:
    rid = latest["record_id"]
    folder = root / "records" / rid
    assert folder.is_dir(), f"{rid}: record folder missing"
    assert (folder / "record.json").read_bytes() == json_line(latest), f"{rid}: record.json differs (C8)"
    ids = latest["image_ids"]
    assert set(ids) == set(image_rows), f"{rid}: image rows differ from the record's image_ids"
    assert all(r["record_version"] <= latest["record_version"] for r in image_rows.values())
    rec = PendingRecord(
        "awaiting_review",
        rid,
        latest["patient_code"],
        "",
        folder,
        latest,
        [image_rows[i] for i in ids],
    )
    findings = check_consistency(folder, rec, {})
    assert findings == [], f"{rid}: {[str(f) for f in findings]}"


def assert_outputs_consistent(output_root: Path, db: Any = None) -> OutputSummary:
    """``db``: an ``app.services.context.ServiceContext``; if given, the files must equal the DB rows."""
    records = _jsonl(output_root / "records.jsonl")
    images = _jsonl(output_root / "images.jsonl")
    withdrawals = _jsonl(output_root / "withdrawals.jsonl")
    _check_pair("records", records, _csv(output_root / "records.csv", RECORD_COLUMNS), record_csv)
    _check_pair("images", images, _csv(output_root / "images.csv", IMAGE_COLUMNS), image_csv)
    _check_pair(
        "withdrawals", withdrawals, _csv(output_root / "withdrawals.csv", WITHDRAWAL_COLUMNS), withdrawal_csv
    )
    record_keys = Counter((r["record_id"], r["record_version"]) for _, r in records)
    image_keys = Counter(r["image_id"] for _, r in images)
    assert max(record_keys.values(), default=1) == 1, "duplicate record row"
    assert max(image_keys.values(), default=1) == 1, "duplicate image row"
    latest: dict[str, dict[str, Any]] = {}
    for _, row in records:
        if row["record_version"] > latest.get(row["record_id"], {}).get("record_version", 0):
            latest[row["record_id"]] = row
    by_record: dict[str, dict[str, dict[str, Any]]] = {}
    for _, row in images:
        assert row["record_id"] in latest, f"{row['image_id']}: image row without a record row"
        assert row["patient_code"] == latest[row["record_id"]]["patient_code"]
        by_record.setdefault(row["record_id"], {})[row["image_id"]] = row
    withdrawn = {r["record_id"] for _, r in withdrawals}
    for rid, row in latest.items():
        if rid in withdrawn:
            assert not (output_root / "records" / rid).exists(), f"{rid}: withdrawn but still in records/"
        else:
            _check_folder(output_root, row, by_record.get(rid, {}))
    folders = [p for p in (output_root / "records").iterdir()] if (output_root / "records").exists() else []
    assert {p.name for p in folders} == set(latest) - withdrawn, "record folders differ from the record rows"
    if db is not None:
        _check_db(db, records, images, withdrawals)
    return OutputSummary(len(records), len(images), len(withdrawals), len(folders))


def _check_db(ctx: Any, records: list[Any], images: list[Any], withdrawals: list[Any]) -> None:
    from app.store.repos import governance as gov
    from app.store.repos import records as recs

    with ctx.db.session() as s:
        db_records = sorted(v.row_json.encode() for v in recs.finalised_versions(s))
        db_images = sorted(i.row_json.encode() for i in recs.finalised_images(s))
        db_withdrawals = sorted(w.row_json.encode() for w in gov.withdrawals(s))
    assert sorted(raw for raw, _ in records) == db_records, "records.jsonl differs from the DB"
    assert sorted(raw for raw, _ in images) == db_images, "images.jsonl differs from the DB"
    assert sorted(raw for raw, _ in withdrawals) == db_withdrawals, "withdrawals.jsonl differs from the DB"
