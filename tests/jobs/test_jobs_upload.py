"""Upload jobs end to end (SPEC §4, §6.5, §7; TR-ING-01..04). Sample inbox → 4 records awaiting review, the
SSE stream shows them, staging is deleted. Path sanitising, limits and aborted uploads clean up staging."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from app.services.jobs import run
from tests.jobs.conftest import Site, folder_files, multipart, sse, upload


def _wait(site: Site, job_id: str, timeout: float = 300) -> dict[str, object]:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        body = site.clients["operator"].get(f"/api/jobs/{job_id}").json()
        if body["status"] in ("finished", "reconcile_failed", "cancelled", "failed"):
            return dict(body)
        time.sleep(0.5)
    raise AssertionError("job did not finish")


def test_sample_inbox_upload_live_worker(
    live_site: Site, sample_inbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(run, "OCR_ENABLED", True)
    monkeypatch.setattr(run, "NER_ENABLED", True)
    op = live_site.clients["operator"]
    r = upload(op, folder_files(sample_inbox))
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    assert r.json()["status"] == "queued" and r.json()["files"] == len(folder_files(sample_inbox))
    msgs = sse(op, job_id)
    kinds = [k for k, _, _ in msgs]
    assert kinds[0] == "indexed" and kinds[-1] == "finished"
    ready = [d for k, d, _ in msgs if k == "record_ready"]
    assert len(ready) == 4 and {d["state"] for d in ready} == {"awaiting_review"}
    assert all(ident is not None for k, _, ident in msgs if k != "progress")
    summary = _wait(live_site, job_id)
    assert summary["status"] == "finished" and summary["balanced"] is True
    assert summary["counts"]["awaiting_review"] == 4  # type: ignore[index]
    assert not (live_site.ctx.paths.staging_dir / job_id).exists()
    pending = op.get("/api/pending").json()
    assert len(pending) == 4 and {p["state"] for p in pending} == {"awaiting_review"}
    assert {p["record_id"] for p in pending} == {d["record_id"] for d in ready}
    # separation of duties reads jobs.started_by (Phase 3): the operator started this job
    with live_site.ctx.db.session() as s:
        from app.store.repos import settings as cfg

        assert cfg.job_starter(s, job_id) == live_site.station.ids["operator1"]


def test_upload_queues_and_runs(site: Site, tmp_path: Path) -> None:
    from tests.deid.helpers import write_study

    src = tmp_path / "src"
    write_study(src / "A", n=2, patient_id="DEMO-U-1", accession="ACC-U1")
    r = upload(site.clients["operator"], folder_files(src))
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    assert (site.ctx.paths.staging_dir / job_id / "files" / "A" / "IM0001").is_file()
    assert site.run() == 1
    body = site.clients["operator"].get(f"/api/jobs/{job_id}").json()
    assert body["status"] == "finished" and body["counts"]["awaiting_review"] == 1
    assert not (site.ctx.paths.staging_dir / job_id).exists()


@pytest.mark.parametrize(
    "rel",
    [
        "../escape.dcm",
        "/abs.dcm",
        "C:/x.dcm",
        "a/../../b",
        "a//b",
        "CON",
        "a/b./c",
        "x\x00y",
        "\\\\server\\s",
    ],
)
def test_bad_upload_paths_refused_and_cleaned(site: Site, rel: str) -> None:
    r = upload(site.clients["operator"], [("ok/file.dcm", b"x"), (rel, b"y")])
    assert r.status_code == 400, r.text
    assert r.json()["error"] == "bad_path" and rel not in r.text
    staging = site.ctx.paths.staging_dir
    assert not staging.exists() or not any(staging.iterdir())


def test_duplicate_path_case_insensitive(site: Site) -> None:
    r = upload(site.clients["operator"], [("A/x.dcm", b"1"), ("a/X.dcm", b"2")])
    assert r.status_code == 400 and r.json()["error"] == "bad_path"


def test_upload_limits(site: Site, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.jobs import upload as up

    monkeypatch.setattr(up.Limits, "of", classmethod(lambda cls, s: cls(10, 100, 2)))
    r = upload(site.clients["operator"], [("a.dcm", b"x" * 11)])
    assert r.status_code == 413
    r = upload(site.clients["operator"], [("a", b"1"), ("b", b"2"), ("c", b"3")])
    assert r.status_code == 413
    jobs = site.clients["admin"].get("/api/jobs").json()
    assert {j["status"] for j in jobs} == {"failed"}
    staging = site.ctx.paths.staging_dir
    assert not any(staging.iterdir())


def test_aborted_upload_cleans_staging(site: Site) -> None:
    body, ctype = multipart([("a/file.dcm", b"x" * 1000)])
    truncated = body[: len(body) // 2]  # the client went away mid-file
    r = site.clients["operator"].post("/api/jobs/upload", content=truncated, headers={"Content-Type": ctype})
    assert r.status_code == 400 and r.json()["error"] in ("upload_aborted", "bad_path")
    assert not any(site.ctx.paths.staging_dir.iterdir())


def test_only_job_runners_upload(site: Site) -> None:
    for role in ("reviewer", "custodian", "auditor"):
        r = upload(site.clients[role], [("a", b"1")])
        assert r.status_code == 403
