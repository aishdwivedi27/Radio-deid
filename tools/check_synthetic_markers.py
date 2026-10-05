"""Synthetic-marker check for committed DICOM files (CLAUDE.md non-negotiable 10, Conventions).

Every committed or staged ``.dcm`` must carry ``InstitutionName == "SYNTHETIC-DEMO"`` and a PatientID that
starts with ``DEMO-``. The only exceptions are the public NEMA source radiographs, pinned by SHA-256 in
``tools/synthetic_marker_exempt.txt`` (DECISIONS D-008). Messages name the file only, never header values.

Usage: python tools/check_synthetic_markers.py [files...]   (no args: every .dcm tracked by git)
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pydicom
from pydicom.errors import InvalidDicomError

REPO = Path(__file__).resolve().parents[1]
EXEMPT_FILE = Path(__file__).resolve().parent / "synthetic_marker_exempt.txt"
INSTITUTION = "SYNTHETIC-DEMO"
ID_PREFIX = "DEMO-"


def load_exempt(path: Path = EXEMPT_FILE) -> set[str]:
    hashes = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            hashes.add(line.split()[0].lower())
    return hashes


def git_dicom_files(repo: Path = REPO) -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--", "*.dcm"],  # noqa: S607 - git on PATH
        cwd=repo,
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8")
    return [repo / name for name in out.split("\0") if name]


def has_markers(path: Path) -> bool:
    try:
        ds = pydicom.dcmread(path, stop_before_pixels=True, force=True)
    except (InvalidDicomError, OSError, ValueError):
        return False
    institution = str(ds.get("InstitutionName", "") or "")
    patient_id = str(ds.get("PatientID", "") or "")
    return institution == INSTITUTION and patient_id.startswith(ID_PREFIX)


def check(files: list[Path], exempt: set[str]) -> list[str]:
    failures = []
    for path in files:
        if hashlib.sha256(path.read_bytes()).hexdigest() in exempt:
            continue
        if not has_markers(path):
            failures.append(f"{path.name}: missing synthetic markers (SYNTHETIC-DEMO / DEMO-)")
    return failures


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    files = [Path(a) for a in args if a.lower().endswith(".dcm")] if args else git_dicom_files()
    failures = check(files, load_exempt())
    for f in failures:
        print(f"FAIL  {f}")
    print(f"synthetic markers: {len(files)} file(s) checked, {len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
