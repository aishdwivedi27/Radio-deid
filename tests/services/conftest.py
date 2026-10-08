"""Fixtures for the store/finalise tests. Synthetic data only (SYNTHETIC-DEMO / DEMO-)."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.deid.index import index_input
from app.deid.report.match import match_reports
from app.deid.types import DeidSettings
from app.services.context import ServiceContext
from app.services.records.approve import record_decision
from app.services.records.finalise import FinaliseResult, finalise
from app.services.records.ingest import IngestResult, ingest_study

REVIEWER = "u_0003"
CATEGORY = "NORMAL"
FAST = DeidSettings(job_id="J-TEST", ocr_enabled=False, ner_enabled=False)


def make_ctx(root: Path) -> ServiceContext:
    return ServiceContext.open(root / "output", root / "app_data")


@pytest.fixture
def ctx(tmp_path: Path) -> Iterator[ServiceContext]:
    c = make_ctx(tmp_path)
    yield c
    c.db.dispose()


def ingest_folder(
    ctx: ServiceContext, folder: Path, key: bytes, settings: DeidSettings = FAST
) -> list[IngestResult]:
    index = index_input(folder)
    matches = match_reports(index)
    return [ingest_study(ctx, g, matches.by_study.get(i), key, settings) for i, g in enumerate(index.studies)]


def approve_finalise(ctx: ServiceContext, rid: str, version: int, category: str = CATEGORY) -> FinaliseResult:
    record_decision(ctx, rid, version, REVIEWER, "approved", category)
    return finalise(ctx, rid, version, REVIEWER, category)


def add_image(folder: Path, instance: int, prefix: str = "NEW", **kw: object) -> None:
    """Add one new image (a new SOP instance) to the study already in ``folder``."""
    import pydicom

    from tests.deid.helpers import make_ds

    first = pydicom.dcmread(sorted(folder.iterdir())[0], stop_before_pixels=True)
    kw.setdefault("modality", str(first.Modality))
    ds = make_ds(
        study_uid=str(first.StudyInstanceUID),
        patient_id=str(first.PatientID),
        instance=instance,
        **kw,  # type: ignore[arg-type]
    )
    ds.save_as(folder / f"{prefix}{instance:04d}", enforce_file_format=True)


def lines(path: Path) -> list[bytes]:
    return [x for x in path.read_bytes().split(b"\n") if x] if path.exists() else []


def csv_rows(path: Path) -> int:
    return len(lines(path)) - 1 if path.exists() else 0


@dataclass
class Finalised:
    ctx: ServiceContext
    root: Path
    inbox: Path
    results: list[FinaliseResult]


@pytest.fixture(scope="session")
def finalised_sample(
    sample_inbox: Path, key: bytes, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[Finalised]:
    """The sample inbox ingested (OCR and NER on) and all 4 records finalised once; read-only for tests."""
    root = tmp_path_factory.mktemp("store")
    c = make_ctx(root)
    results = ingest_folder(c, sample_inbox, key, DeidSettings(job_id="J-SAMPLE"))
    done = [
        approve_finalise(c, r.record_id, r.version or 0) for r in results if r.status == "awaiting_review"
    ]
    yield Finalised(c, root, sample_inbox, done)
    c.db.dispose()
