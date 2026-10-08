"""Reconcile runs when the app starts (SPEC §5.3 step 7, §8 reliability). TR-REL-NF-01."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.config import build_settings
from app.services.records.approve import record_decision
from app.services.records.finalise import finalise
from app.store.output.csv_writer import CsvAppender
from tests.deid.helpers import write_study
from tests.services.conftest import CATEGORY, REVIEWER, csv_rows, ingest_folder, make_ctx


def test_startup_reconciles_a_crashed_finalise(
    tmp_path: Path, key: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = make_ctx(tmp_path)
    write_study(tmp_path / "inbox" / "s", 1, modality="CT", patient_id="DEMO-ST-1")
    [res] = ingest_folder(ctx, tmp_path / "inbox" / "s", key)
    record_decision(ctx, res.record_id, 1, REVIEWER, "approved", CATEGORY)

    def boom(*_a: object) -> None:
        raise RuntimeError("crash")

    monkeypatch.setattr(CsvAppender, "append", boom)
    with pytest.raises(RuntimeError):
        finalise(ctx, res.record_id, 1, REVIEWER, CATEGORY)
    monkeypatch.undo()
    ctx.db.dispose()
    settings = build_settings({"data_root": str(tmp_path)})
    with TestClient(create_app(web_dist=tmp_path / "no-web", settings=settings)) as client:
        assert client.get("/api/health").json() == {"status": "ok"}
        client.app.state.services.db.dispose()  # type: ignore[attr-defined]
    out = tmp_path / "output"
    assert csv_rows(out / "records.csv") == 1 and csv_rows(out / "images.csv") == 1
