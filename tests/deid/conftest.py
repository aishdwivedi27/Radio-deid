"""Shared fixtures for the de-identification core tests. Synthetic data only (SYNTHETIC-DEMO / DEMO-)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from app.deid.index import index_input
from app.deid.pipeline import process_study
from app.deid.report.match import ReportMatches, match_reports
from app.deid.types import DeidSettings, PendingRecord


@dataclass
class Processed:
    inbox: Path
    pending: Path
    records: list[PendingRecord]
    matches: ReportMatches


@pytest.fixture(scope="session")
def processed(sample_inbox: Path, key: bytes, tmp_path_factory: pytest.TempPathFactory) -> Processed:
    """The whole sample inbox processed once (OCR and NER on), shared by the read-only tests."""
    pending = tmp_path_factory.mktemp("pending")
    index = index_input(sample_inbox)
    matches = match_reports(index)
    settings = DeidSettings(job_id="J-TEST")
    records = [
        process_study(g, matches.by_study.get(i), key, settings, pending) for i, g in enumerate(index.studies)
    ]
    return Processed(sample_inbox, pending, records, matches)
