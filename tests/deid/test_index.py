"""Input indexing: DICOM by content, DICOMDIR skipped, header-only (SPEC §6.1, §6.5). TR-ING-03."""

import shutil
from pathlib import Path

from app.deid.index import index_input


def test_sample_inbox_grouping(sample_inbox: Path) -> None:  # TR-ING-03
    idx = index_input(sample_inbox)
    sizes = sorted(len(g.files) for g in idx.studies)
    assert sizes == [1, 1, 1, 21]                       # CR, leg, US, CT 20 + dose screen
    assert sorted(p.suffix for p in idx.reports) == [".docx", ".pdf", ".pdf", ".txt"]
    assert idx.skipped["non_dicom"] == 2                # Thumbs.db, DICOMDIR_notes.csv
    assert idx.skipped["hidden"] == 1
    assert any(f.path.name == "IMG0001" for g in idx.studies for f in g.files)   # no extension


def test_dicomdir_skipped(sample_inbox: Path, tmp_path: Path) -> None:  # TR-ING-03
    inbox = tmp_path / "in"
    shutil.copytree(sample_inbox / "US", inbox / "DICOM")
    shutil.copy(inbox / "DICOM" / "us_0001.dcm", inbox / "DICOMDIR")
    idx = index_input(inbox)
    assert idx.skipped["dicomdir"] == 1 and len(idx.studies) == 1


def test_repr_hides_identifiers(sample_inbox: Path) -> None:  # TR-SEC-02
    text = repr(index_input(sample_inbox))
    assert "DEMO-" not in text and "UHID" not in text and str(sample_inbox) not in text
