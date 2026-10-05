"""Every DICOM file in the sample inbox carries the synthetic markers (SPEC §11, §16 X9; CLAUDE.md 10)."""

import subprocess
import sys
from pathlib import Path

from tests.conftest import PHI
from tools.check_synthetic_markers import has_markers


def _is_dicom(path: Path) -> bool:
    with path.open("rb") as fh:
        head = fh.read(132)
    return len(head) == 132 and head[128:] == b"DICM"


def test_sample_inbox_is_synthetic(repo: Path, tmp_path: Path) -> None:
    out = tmp_path / "inbox"
    script = repo / "tests" / "fixtures" / "make_sample_data.py"
    subprocess.run([sys.executable, str(script), str(out)], check=True, capture_output=True)

    dicoms = [p for p in out.rglob("*") if p.is_file() and _is_dicom(p)]
    assert len(dicoms) == 24  # 2 CR + 20 CT slices + 1 dose screen + 1 US
    assert all(has_markers(p) for p in dicoms), "every sample DICOM file needs SYNTHETIC-DEMO / DEMO-"

    reports = {p.suffix for p in out.rglob("*") if p.suffix in {".txt", ".docx", ".pdf"}}
    assert reports == {".txt", ".docx", ".pdf"}

    # The fixture must still plant identifiers for the PHI leak tests to find.
    planted = (out / "CR_export_0923" / "report.txt").read_text(encoding="utf-8")
    assert "Sharma" in planted and "UHID-778812" in planted and PHI
