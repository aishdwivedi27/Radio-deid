"""Folder jobs (SPEC §6.5, §7; TR-ING-01, TR-ING-05; T34). Path rules, read-only input, symlink escapes, and
an approved removable drive found again under another letter or mount path."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from app.services.jobs import inputs
from app.store.volumes import Volume
from tests.deid.helpers import write_study
from tests.jobs.conftest import Site, sse


def _snapshot(folder: Path) -> dict[str, tuple[str, int]]:
    return {
        p.relative_to(folder).as_posix(): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
        for p in folder.rglob("*")
        if p.is_file()
    }


def _link(link: Path, target: Path) -> bool:
    """A directory symlink, or a junction on Windows without symlink rights."""
    try:
        os.symlink(target, link, target_is_directory=True)
        return True
    except OSError:
        pass
    if sys.platform == "win32":
        r = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, check=False
        )
        return r.returncode == 0
    return False


def test_folder_job_reads_in_place_and_never_writes(site: Site) -> None:
    write_study(site.inbox / "batch1" / "A", n=2, patient_id="DEMO-F-1", accession="ACC-F1")
    (site.inbox / "batch1" / "notes.csv").write_text("x\n", encoding="utf-8")
    before = _snapshot(site.inbox)
    r = site.clients["operator"].post("/api/jobs/folder", json={"path": str(site.inbox / "batch1")})
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    assert site.run() == 1
    assert _snapshot(site.inbox) == before
    body = site.clients["operator"].get(f"/api/jobs/{job_id}").json()
    assert body["status"] == "finished" and body["counts"]["awaiting_review"] == 1
    assert body["images_excluded"] == {"NON_IMAGE": 1}
    assert str(site.inbox) not in str(body) and "batch1" not in str(body)


def _post(site: Site, path: str) -> tuple[int, dict[str, str], str]:
    r = site.clients["operator"].post("/api/jobs/folder", json={"path": path})
    return r.status_code, r.json(), r.text


def test_folder_path_rules(site: Site) -> None:
    (site.inbox / "ok").mkdir()
    (site.inbox / "file.dcm").write_bytes(b"x")
    out = site.ctx.paths.output_root
    out.mkdir(parents=True, exist_ok=True)
    cases = {
        str(site.outside): (400, "outside_roots"),
        str(site.inbox / ".." / "outside"): (400, "dotdot"),
        str(site.inbox) + "/ok/../../outside": (400, "dotdot"),
        "relative/path": (400, "not_absolute"),
        str(site.inbox / "missing"): (400, "not_found"),
        str(site.inbox / "file.dcm"): (400, "not_folder"),
        str(out): (400, "inside_output"),
        str(site.ctx.paths.app_data_dir / "staging"): (400, "inside_app_data"),
        str(site.ctx.paths.app_data_dir): (400, "inside_app_data"),
    }
    site.ctx.paths.staging_dir.mkdir(parents=True, exist_ok=True)
    for path, (status, reason) in cases.items():
        code, body, text = _post(site, path)
        assert code == status, (path, text)
        assert body["error"] == "path_invalid" and reason in body["message"], (path, text)
        assert path not in text and "outside" not in body["message"].replace("outside_roots", "")
    assert site.clients["admin"].get("/api/jobs").json() == []


def test_symlink_escape_refused(site: Site) -> None:
    write_study(site.outside / "secret", n=1, patient_id="DEMO-F-2", accession="ACC-F2")
    if not _link(site.inbox / "escape", site.outside):
        pytest.skip("this account can create neither symlinks nor junctions")
    code, body, _ = _post(site, str(site.inbox / "escape"))
    assert code == 400 and "symlink_escape" in body["message"]
    code, body, _ = _post(site, str(site.inbox / "escape" / "secret"))
    assert code == 400 and "symlink_escape" in body["message"]


def test_walk_skips_links_out_of_the_root(site: Site) -> None:
    write_study(site.inbox / "job" / "A", n=1, patient_id="DEMO-F-3", accession="ACC-F3")
    write_study(site.outside / "other", n=1, patient_id="DEMO-F-4", accession="ACC-F4")
    if not _link(site.inbox / "job" / "linked", site.outside / "other"):
        pytest.skip("this account can create neither symlinks nor junctions")
    walked = inputs.walk(site.inbox / "job", site.inbox)
    assert all("linked" not in p.parts for p in walked.files)
    r = site.clients["operator"].post("/api/jobs/folder", json={"path": str(site.inbox / "job")})
    assert r.status_code == 202
    site.run()
    body = site.clients["operator"].get(f"/api/jobs/{r.json()['job_id']}").json()
    assert body["total"] == 1  # the linked study outside the root was not read


@dataclass
class FakeProbe:
    """Mount point → volume id. ``volume_of`` finds the mount a path is under."""

    mounts: dict[Path, str] = field(default_factory=dict)

    def volume_of(self, path: Path) -> Volume | None:
        p = path.resolve()
        for mount, vid in self.mounts.items():
            if p == mount or mount in p.parents:
                return Volume(mount, vid, "BACKUP", True)
        return None

    def mounted(self) -> list[Volume]:
        return [Volume(m, v, "BACKUP", True) for m, v in self.mounts.items() if m.is_dir()]


def _use_probe(site: Site, probe: FakeProbe) -> None:
    object.__setattr__(site.ctx, "volumes", probe)


def test_t34_drive_found_under_new_letter(site: Site, tmp_path: Path) -> None:
    drive_e = (tmp_path / "mnt_E").resolve()
    write_study(drive_e / "scans" / "S1", n=1, patient_id="DEMO-D-1", accession="ACC-D1")
    probe = FakeProbe({drive_e: "win:AAAA0001"})
    _use_probe(site, probe)
    r = site.clients["admin"].put("/api/settings/input-roots", json={"input_roots": [str(drive_e / "scans")]})
    assert r.status_code == 200, r.text
    # the drive comes back as F: (another mount path)
    drive_f = (tmp_path / "mnt_F").resolve()
    drive_e.rename(drive_f)
    probe.mounts = {drive_f: "win:AAAA0001"}
    r = site.clients["operator"].post("/api/jobs/folder", json={"path": str(drive_f / "scans" / "S1")})
    assert r.status_code == 202, r.text
    site.run()
    body = site.clients["operator"].get(f"/api/jobs/{r.json()['job_id']}").json()
    assert body["status"] == "finished" and body["counts"]["awaiting_review"] == 1
    # an unapproved drive mounted at the old letter is refused
    write_study(drive_e / "scans" / "S2", n=1, patient_id="DEMO-D-2", accession="ACC-D2")
    probe.mounts = {drive_e: "win:BBBB0002"}
    r = site.clients["operator"].post("/api/jobs/folder", json={"path": str(drive_e / "scans" / "S2")})
    assert r.status_code == 409 and r.json()["message"] == inputs.DRIVE_MISSING


def test_t34_no_drive_no_job(site: Site, tmp_path: Path) -> None:
    drive = (tmp_path / "usb").resolve()
    write_study(drive / "S1", n=1, patient_id="DEMO-D-3", accession="ACC-D3")
    probe = FakeProbe({drive: "mac:UUID-1"})
    _use_probe(site, probe)
    assert (
        site.clients["admin"].put("/api/settings/input-roots", json={"input_roots": [str(drive)]}).status_code
        == 200
    )
    r = site.clients["operator"].post("/api/jobs/folder", json={"path": str(drive / "S1")})
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    drive.rename(tmp_path / "unplugged")  # removed before the worker reached the job
    probe.mounts = {}
    site.run()
    msgs = sse(site.clients["operator"], job_id)
    assert msgs[-1][0] == "error" and msgs[-1][1]["code"] == "input_drive_missing"
    assert msgs[-1][1]["message"] == "Input drive not found — connect the approved drive."
    r = site.clients["operator"].post("/api/jobs/folder", json={"path": str(drive / "S1")})
    assert r.status_code == 409 and r.json()["error"] == "input_drive_missing"


def test_real_probe_gives_an_identity(tmp_path: Path) -> None:
    from app.store.volumes import system_probe

    vol = system_probe().volume_of(tmp_path)
    assert vol is not None and vol.volume_id
