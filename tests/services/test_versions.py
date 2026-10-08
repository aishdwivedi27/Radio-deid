"""Re-sent studies, duplicates and idempotency (acceptance criterion 5; T35; SPEC §4, §6.5). TR-REL-NF-01.

- a re-run of the same input creates no rows;
- one new image for a finalised study → version 2, +1 image row, +1 record row carrying all image_ids;
- a study split across two batches → version 2 holding only the batch-2 images;
- new images while version 1 is still under review are added to version 1 (no extra version);
- finalising the same version twice does nothing the second time.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from app.deid.report.header import parse_header
from app.services.context import ServiceContext
from app.services.records.finalise import finalise
from app.services.records.reconcile import reconcile
from tests.deid.helpers import write_study
from tests.helpers.outputs import assert_outputs_consistent
from tests.services.conftest import CATEGORY, REVIEWER, add_image, approve_finalise, ingest_folder, lines


def _one(results: list, status: str = "awaiting_review"):  # type: ignore[no-untyped-def]
    assert [r.status for r in results] == [status]
    return results[0]


def _snapshot(out: Path) -> dict[str, bytes]:
    return {n: (out / n).read_bytes() for n in ("records.jsonl", "images.jsonl", "records.csv", "images.csv")}


def test_rerun_same_input_creates_nothing(ctx: ServiceContext, tmp_path: Path, key: bytes) -> None:
    folder = tmp_path / "in" / "s"
    write_study(folder, 2, modality="CT", patient_id="DEMO-V-1")
    res = _one(ingest_folder(ctx, folder, key))
    approve_finalise(ctx, res.record_id, res.version)
    before = _snapshot(ctx.paths.output_root)
    assert _one(ingest_folder(ctx, folder, key), "duplicate").version == 1
    copy = tmp_path / "in" / "copy"  # same files under another name: same SHA-256
    shutil.copytree(folder, copy)
    assert _one(ingest_folder(ctx, copy, key), "duplicate").record_id == res.record_id
    assert _snapshot(ctx.paths.output_root) == before and reconcile(ctx).appended == 0


def test_finalise_twice_is_a_no_op(ctx: ServiceContext, tmp_path: Path, key: bytes) -> None:
    write_study(tmp_path / "in", 1, modality="CT", patient_id="DEMO-V-2")
    res = _one(ingest_folder(ctx, tmp_path / "in", key))
    assert approve_finalise(ctx, res.record_id, res.version).status == "finalised"
    before = _snapshot(ctx.paths.output_root)
    again = finalise(ctx, res.record_id, res.version, REVIEWER, CATEGORY)
    assert again.status == "already_finalised" and again.image_rows == []
    assert _snapshot(ctx.paths.output_root) == before


@pytest.mark.parametrize("hard_links", [True, False])
def test_new_image_makes_version_two(
    ctx: ServiceContext, tmp_path: Path, key: bytes, monkeypatch: pytest.MonkeyPatch, hard_links: bool
) -> None:
    if not hard_links:  # FAT/exFAT or another volume: prior images are copied instead
        import os

        def no_link(*_a: object) -> None:
            raise OSError("no hard links here")

        monkeypatch.setattr(os, "link", no_link)
    folder = tmp_path / "in" / "s"
    write_study(folder, 2, modality="CT", patient_id="DEMO-V-3")
    v1 = _one(ingest_folder(ctx, folder, key))
    approve_finalise(ctx, v1.record_id, 1)
    add_image(folder, 3)
    v2 = _one(ingest_folder(ctx, folder, key))  # the whole folder again: 2 duplicates + 1 new
    assert (v2.record_id, v2.version) == (v1.record_id, 2)
    result = approve_finalise(ctx, v2.record_id, 2)
    assert result.status == "finalised" and len(result.image_rows) == 1
    out = ctx.paths.output_root
    rows = [json.loads(x) for x in lines(out / "records.jsonl")]
    images = [json.loads(x) for x in lines(out / "images.jsonl")]
    assert [r["record_version"] for r in rows] == [1, 2]
    assert rows[1]["n_images"] == 3 and len(rows[1]["image_ids"]) == 3
    assert set(rows[0]["image_ids"]) < set(rows[1]["image_ids"])
    assert [i["record_version"] for i in images] == [1, 1, 2]
    assert assert_outputs_consistent(out, ctx).folders == 1


def test_split_study_across_batches(ctx: ServiceContext, tmp_path: Path, key: bytes) -> None:
    """T35: batch 1 (re-run once) then batch 2 with the rest of the same study."""
    batch1, batch2 = tmp_path / "inbox" / "batch1" / "s", tmp_path / "inbox" / "batch2" / "s"
    write_study(batch1, 2, modality="CT", patient_id="DEMO-V-4")
    batch2.mkdir(parents=True)
    add_image(batch1, 3, prefix="B2_")
    (batch1 / "B2_0003").rename(batch2 / "B2_0003")
    v1 = _one(ingest_folder(ctx, batch1, key))
    approve_finalise(ctx, v1.record_id, 1)
    assert _one(ingest_folder(ctx, batch1, key), "duplicate")
    v2 = _one(ingest_folder(ctx, batch2, key))
    assert v2.version == 2 and v2.record is not None
    assert [r["record_version"] for r in v2.record.image_rows] == [1, 1, 2]
    approve_finalise(ctx, v1.record_id, 2)
    out = ctx.paths.output_root
    assert len(lines(out / "records.jsonl")) == 2 and len(lines(out / "images.jsonl")) == 3
    assert_outputs_consistent(out, ctx)


def test_new_images_while_under_review_join_the_pending_version(
    ctx: ServiceContext, tmp_path: Path, key: bytes
) -> None:
    folder, extra = tmp_path / "in" / "a", tmp_path / "in" / "b"
    write_study(folder, 2, modality="CT", patient_id="DEMO-V-5")
    v1 = _one(ingest_folder(ctx, folder, key))
    add_image(folder, 3, prefix="X")
    extra.mkdir()
    (folder / "X0003").rename(extra / "X0003")
    again = _one(ingest_folder(ctx, extra, key))
    assert (again.record_id, again.version) == (v1.record_id, 1)
    assert again.record is not None and len(again.record.image_rows) == 3
    approve_finalise(ctx, v1.record_id, 1)
    out = ctx.paths.output_root
    assert len(lines(out / "records.jsonl")) == 1 and len(lines(out / "images.jsonl")) == 3
    assert_outputs_consistent(out, ctx)


def test_version_two_keeps_the_report_and_lists_every_image(
    ctx: ServiceContext, tmp_path: Path, key: bytes
) -> None:
    """C4: the report header block of version 2 lists all images, the body is the prior redacted body."""
    folder = tmp_path / "in" / "s"
    # not the default "Demo^Patient": its name part "Patient" is in every report header ("Patient code:")
    write_study(
        folder, 1, modality="CT", patient_id="DEMO-V-6", accession="ACC-V6", name="Testcase^Volunteer"
    )
    (folder / "ACC-V6.txt").write_text(
        "FINDINGS: Normal study.\nIMPRESSION: No abnormality.\n", encoding="utf-8"
    )
    v1 = _one(ingest_folder(ctx, folder, key))
    approve_finalise(ctx, v1.record_id, 1)
    (folder / "ACC-V6.txt").unlink()
    add_image(folder, 2, accession="ACC-V6", name="Testcase^Volunteer")
    v2 = _one(ingest_folder(ctx, folder, key))
    approve_finalise(ctx, v2.record_id, 2)
    rid = v1.record_id
    report = (ctx.paths.records_dir / rid / f"{rid}_report.txt").read_text(encoding="utf-8")
    head = parse_header(report)
    assert head is not None and len(head.image_ids) == 2 and "No abnormality" in report
    rows = [json.loads(x) for x in lines(ctx.paths.output_root / "records.jsonl")]
    assert rows[1]["report"]["present"] and rows[1]["report"]["sha256"] != rows[0]["report"]["sha256"]
    assert_outputs_consistent(ctx.paths.output_root, ctx)
