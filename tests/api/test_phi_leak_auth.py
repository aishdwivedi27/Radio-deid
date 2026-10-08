"""PHI leak test, Phase 3 scope (CLAUDE.md rules 1, 2, 9, 13; SPEC §7, TR-SEC-02, T20).

Planted identifiers go in through every new path: a patient-list import keyed by UHID, a username typed at
login, a non-synthetic study refused in pre-approval mode, a validation error. None may come out in an API
response, the audit log or its CSV export, the logs, the database files or the output folder.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.deid.keys import load_or_create_key
from tests.api.conftest import Station
from tests.conftest import PHI
from tests.deid.helpers import write_study
from tests.services.conftest import FAST, ingest_folder


def _leaks(blob: str) -> list[str]:
    return [p for p in PHI if p.lower() in blob.lower()]


def test_auth_governance_paths_leak_nothing(
    staffed: tuple[Station, dict[str, TestClient]], tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    station, clients = staffed
    cust, auditor, admin = clients["custodian"], clients["auditor"], clients["admin"]
    texts: list[str] = []
    csv_body = b"uhid\nUHID-778812\nUHID-903311\nUHID-440021\n"
    for list_type in ("OPT_OUT", "STAFF_VIP", "MEDICO_LEGAL"):
        r = cust.post(f"/api/lists/{list_type}", content=csv_body, headers={"Content-Type": "text/csv"})
        texts.append(r.text)
    texts.append(cust.post("/api/lists/CONSENT", content=b"uhid,consent\nUHID-778812,maybe\n",
                           headers={"Content-Type": "text/csv"}).text)  # fmt: skip
    typed = {"username": "Sharma", "password": "Ramesh"}  # pragma: allowlist secret
    texts.append(station.new_client().post("/api/auth/login", json=typed).text)
    texts.append(station.new_client().post("/api/auth/login", json={"username": "Lakshmi Iyer"}).text)
    write_study(tmp_path / "in" / "real", 1, patient_id="UHID-903311", name="Iyer^Lakshmi",
                InstitutionName="Sunrise Diagnostics")  # fmt: skip
    ingest_folder(station.ctx, tmp_path / "in" / "real", load_or_create_key(station.ctx.paths.key_path), FAST)
    for path in ("/api/audit?limit=500", "/api/audit/export.csv", "/api/audit/verify", "/api/status"):
        texts.append(auditor.get(path).text)
    texts.append(admin.get("/api/users").text)
    texts.append(admin.get("/api/settings").text)
    assert not _leaks("\n".join(texts) + caplog.text)

    station.ctx.db.engine.dispose()
    app_data, output = station.ctx.paths.app_data_dir, station.ctx.paths.output_root
    db_bytes = b"".join(p.read_bytes() for p in app_data.glob("app.db*")).decode("latin-1")
    assert not _leaks(db_bytes)
    out_files = [p for p in output.rglob("*") if p.is_file()] if output.exists() else []
    assert not _leaks("".join(p.read_bytes().decode("latin-1") for p in out_files))
