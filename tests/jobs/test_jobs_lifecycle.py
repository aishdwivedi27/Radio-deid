"""Cancel (rolls back the job's work) and restart (resumes without duplicates). SPEC §4, §7, §8; TR-ING-04.
Owner decision 10 Oct 2026: cancel discards what the job produced and a re-run starts again; a crash or
restart resumes and skips studies already done."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.services.jobs import create, run, secure_files
from app.store.models.jobs import JobStudy
from app.store.models.records import Exclusion, Image, Record, RecordVersion, SourceFile
from tests.deid.helpers import write_study
from tests.jobs.conftest import Site, sse, upload

REPO = Path(__file__).resolve().parents[2]


def _four_studies(folder: Path) -> None:
    write_study(folder / "A", n=2, patient_id="DEMO-L-1", accession="ACC-L1")
    write_study(folder / "B", n=1, patient_id="DEMO-L-2", accession="ACC-L2", PatientAge="016Y")  # excluded
    write_study(folder / "C", n=1, patient_id="DEMO-L-3", accession="ACC-L3")
    write_study(folder / "D", n=3, patient_id="DEMO-L-4", accession="ACC-L4")


def _counts(site: Site) -> dict[str, int]:
    with site.ctx.db.session() as s:
        return {
            m.__tablename__: int(s.scalar(select(func.count()).select_from(m)) or 0)
            for m in (Record, RecordVersion, Image, SourceFile, Exclusion)
        }


def _audit(site: Site, action: str) -> list[dict[str, object]]:
    rows = site.clients["custodian"].get("/api/audit", params={"action": action}).json()
    return list(rows)


def test_cancel_rolls_back_and_rerun_starts_again(site: Site, monkeypatch: pytest.MonkeyPatch) -> None:
    _four_studies(site.inbox / "batch")
    op = site.clients["operator"]
    job_id = op.post("/api/jobs/folder", json={"path": str(site.inbox / "batch")}).json()["job_id"]
    real, calls = run.ingest_study, []

    def ingest_then_cancel(*a: object, **kw: object) -> object:
        res = real(*a, **kw)  # type: ignore[arg-type]
        calls.append(1)
        if len(calls) == 2:  # after A (processed) and B (excluded)
            assert op.post(f"/api/jobs/{job_id}/cancel").json() == {"status": "cancelling"}
        return res

    monkeypatch.setattr(run, "ingest_study", ingest_then_cancel)
    site.run()
    assert len(calls) == 2  # cancel took effect after the current study
    body = op.get(f"/api/jobs/{job_id}").json()
    assert body["status"] == "cancelled" and body["counts"]["not_done"] == 4 and body["balanced"]
    assert op.get("/api/pending").json() == []
    assert _counts(site) == {
        "records": 0,
        "record_versions": 0,
        "images": 0,
        "source_files": 0,
        "exclusions": 0,
    }
    pending_root = site.ctx.paths.pending_root
    assert not pending_root.exists() or not list(pending_root.iterdir())
    cancelled = _audit(site, "job.cancelled")
    assert cancelled and json.loads(str(cancelled[0]["details_json"]))["rolled_back_records"] == 1
    assert _audit(site, "exclusion.recorded")  # audit events are kept
    recon = secure_files.reconciliation_path(site.ctx, job_id).read_text(encoding="utf-8").splitlines()
    total = dict(zip(recon[0].split(","), recon[-1].split(","), strict=True))
    assert total["folder"] == "TOTAL" and total["not_done"] == "4" and total["balanced"] == "yes"
    assert [k for k, _, _ in sse(op, job_id)][-1] == "cancelled"
    # running the same input again starts from the first study
    monkeypatch.setattr(run, "ingest_study", real)
    job2 = op.post("/api/jobs/folder", json={"path": str(site.inbox / "batch")}).json()["job_id"]
    site.run()
    body = op.get(f"/api/jobs/{job2}").json()
    assert body["counts"]["awaiting_review"] == 3 and body["counts"]["excluded"] == 1
    assert _counts(site)["records"] == 3 and _counts(site)["exclusions"] == 1


def test_cancel_queued_and_permissions(site: Site, tmp_path: Path) -> None:
    write_study(tmp_path / "up" / "A", n=1, patient_id="DEMO-L-5", accession="ACC-L5")
    op, admin = site.clients["operator"], site.clients["admin"]
    job_id = upload(op, [("A/IM0001", (tmp_path / "up" / "A" / "IM0001").read_bytes())]).json()["job_id"]
    assert (site.ctx.paths.staging_dir / job_id).exists()
    assert site.clients["reviewer"].post(f"/api/jobs/{job_id}/cancel").status_code == 403
    assert op.post(f"/api/jobs/{job_id}/cancel").json() == {"status": "cancelled"}
    assert not (site.ctx.paths.staging_dir / job_id).exists()  # staging deleted on cancel
    assert op.post(f"/api/jobs/{job_id}/cancel").status_code == 409
    other = admin.post("/api/jobs/folder", json={"path": str(site.inbox)}).json()["job_id"]
    assert op.get(f"/api/jobs/{other}").status_code == 404  # operators see their own jobs only
    assert op.post(f"/api/jobs/{other}/cancel").status_code == 404
    assert admin.get(f"/api/jobs/{job_id}").status_code == 200
    assert {j["job_id"] for j in op.get("/api/jobs").json()} == {job_id}
    assert {j["job_id"] for j in admin.get("/api/jobs").json()} == {job_id, other}


CRASH = textwrap.dedent(
    """
    import os, sys
    from app.config import build_settings
    from app.services.context import ServiceContext
    from app.services.jobs import run
    from app.worker.runner import Worker
    run.OCR_ENABLED = run.NER_ENABLED = False
    ctx = ServiceContext.from_settings(build_settings({"data_root": sys.argv[1], "port": 8765}))
    real, calls = run.ingest_study, []
    def crash(*a, **kw):
        res = real(*a, **kw)
        calls.append(1)
        if len(calls) == 2:
            os._exit(9)  # killed after study 2 committed, before the job recorded it
        return res
    run.ingest_study = crash
    Worker(ctx).run_pending()
    """
)


def test_restart_mid_job_resumes_without_duplicates(site: Site) -> None:
    _four_studies(site.inbox / "batch")
    op = site.clients["operator"]
    job_id = op.post("/api/jobs/folder", json={"path": str(site.inbox / "batch")}).json()["job_id"]
    proc = subprocess.run(
        [sys.executable, "-c", CRASH, str(site.data)], cwd=REPO, capture_output=True, timeout=600, check=False
    )
    assert proc.returncode == 9, proc.stderr.decode(errors="replace")[-2000:]
    assert op.get(f"/api/jobs/{job_id}").json()["status"] == "processing"
    with site.ctx.db.session() as s:
        done = [st.status for st in s.scalars(select(JobStudy).where(JobStudy.job_id == job_id))]
    assert done.count("not_done") == 3  # study 2's outcome was never recorded
    create.recover(site.ctx)  # what the worker does when the app starts again
    site.run()
    body = op.get(f"/api/jobs/{job_id}").json()
    assert body["status"] == "finished" and body["balanced"]
    assert body["counts"]["awaiting_review"] == 3 and body["counts"]["excluded"] == 1
    counts = _counts(site)
    assert counts["records"] == 3 and counts["record_versions"] == 3 and counts["exclusions"] == 1
    assert counts["images"] == 6 and counts["source_files"] == 6
    ready = [d["record_id"] for k, d, _ in sse(op, job_id) if k in ("record_ready", "record_excluded")]
    assert len(ready) == len(set(ready)) == 4


def test_restart_fails_interrupted_upload(site: Site) -> None:
    job_id = create.begin_upload(site.ctx, _actor(site, "operator1"))
    part = site.ctx.paths.staging_dir / job_id / "files"
    part.mkdir(parents=True)
    (part / "half.dcm").write_bytes(b"x")
    orphan = site.ctx.paths.staging_dir / "J20200101-000000-dead"
    orphan.mkdir()
    create.recover(site.ctx)
    body = site.clients["operator"].get(f"/api/jobs/{job_id}").json()
    assert body["status"] == "failed" and body["error"] == "upload_interrupted"
    assert not any(site.ctx.paths.staging_dir.iterdir())


def _actor(site: Site, username: str) -> object:
    from app.auth.errors import Actor

    return Actor(site.station.ids[username], frozenset({"operator"}))
