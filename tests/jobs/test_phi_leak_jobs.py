"""PHI leak test for jobs (CLAUDE.md rules 1, 2, 9; SPEC §6.4, §7; T20 API part). TR-SEC-02.

The sample inbox is uploaded and processed with OCR and NER on. Every API response body and every SSE
message of the run, the audit log (list and CSV export), the captured logs, and every file under ``output/``
and ``app_data/`` outside ``secure/`` are scanned: no planted identifier, original UID or uploaded file name
appears. The skip list / folder-name routes are role-restricted.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.jobs import run
from tests.deid.test_phi_leak_core import PLANTED, _leaks, _original_uids
from tests.jobs.conftest import Site, folder_files, sse, upload


def _files_blob(site: Site) -> str:
    parts = []
    for base in (site.ctx.paths.output_root, site.ctx.paths.app_data_dir):
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if p.is_relative_to(site.ctx.paths.secure_dir):
                continue
            parts.append(p.name)
            if p.is_file() and not p.name.endswith(".lock"):
                parts.append(p.read_bytes().decode("latin-1"))
    return "\n".join(parts)


def test_no_identifier_in_any_api_body_or_sse_message(
    site: Site, sample_inbox: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("DEBUG")
    monkeypatch.setattr(run, "OCR_ENABLED", True)
    monkeypatch.setattr(run, "NER_ENABLED", True)
    op, rev, cus, admin = (site.clients[r] for r in ("operator", "reviewer", "custodian", "admin"))
    files = folder_files(sample_inbox)
    bodies: list[str] = []
    r = upload(op, files)
    bodies.append(r.text)
    job_id = r.json()["job_id"]
    site.run()
    bodies.append(json.dumps(sse(op, job_id)))
    for client in (op, admin):
        bodies += [client.get("/api/jobs").text, client.get(f"/api/jobs/{job_id}").text]
    queue = rev.get("/api/pending").json()
    assert len(queue) == 4
    bodies.append(json.dumps(queue))
    for item in queue:
        detail = rev.get(f"/api/pending/{item['record_id']}")
        bodies.append(detail.text)
    bodies += [op.get(f"/api/jobs/{job_id}/folders").text, cus.get(f"/api/jobs/{job_id}/exclusions").text,
               rev.get(f"/api/jobs/{job_id}/reconciliation.csv").text]  # fmt: skip
    bodies += [cus.get("/api/audit", params={"limit": 500}).text, cus.get("/api/audit/export.csv").text]
    needles = PLANTED + _original_uids(sample_inbox)
    assert _leaks("\n".join(bodies), needles) == []
    assert _leaks(caplog.text, needles) == []
    assert _leaks(_files_blob(site), needles) == []
    names = {Path(rel).name for rel, _ in files} - {"report.txt"}  # generic names would match anything
    leaked = [n for n in names if len(n) > 6 and n in "\n".join(bodies[:6])]
    assert leaked == [], leaked  # no uploaded file name in the job, list or SSE responses


def test_secure_routes_are_restricted(site: Site) -> None:
    job = "J20260101-000000-0000"
    for role in ("operator", "admin", "auditor"):
        assert site.clients[role].get(f"/api/jobs/{job}/exclusions").status_code == 403
        assert site.clients[role].get(f"/api/jobs/{job}/reconciliation.csv").status_code == 403
    for role in ("reviewer", "custodian", "auditor"):
        assert site.clients[role].get(f"/api/jobs/{job}/folders").status_code == 403
