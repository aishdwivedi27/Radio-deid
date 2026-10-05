"""tools/check_synthetic_markers.py fails on an unmarked DICOM file (CLAUDE.md rule 10; DECISIONS D-008)."""

from pathlib import Path

import pydicom
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, SecondaryCaptureImageStorage, generate_uid

from tools import check_synthetic_markers as markers


def _dicom(path: Path, institution: str, patient_id: str) -> Path:
    ds = Dataset()
    ds.file_meta = FileMetaDataset()
    ds.file_meta.MediaStorageSOPClassUID = SecondaryCaptureImageStorage
    ds.file_meta.MediaStorageSOPInstanceUID = generate_uid()
    ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.SOPClassUID = SecondaryCaptureImageStorage
    ds.SOPInstanceUID = ds.file_meta.MediaStorageSOPInstanceUID
    ds.InstitutionName = institution
    ds.PatientID = patient_id
    ds.save_as(path, enforce_file_format=True)
    return path


def test_marked_file_passes(tmp_path: Path) -> None:
    f = _dicom(tmp_path / "ok.dcm", "SYNTHETIC-DEMO", "DEMO-0001")
    assert markers.check([f], set()) == []


def test_unmarked_file_fails_without_leaking_values(tmp_path: Path) -> None:
    f = _dicom(tmp_path / "planted.dcm", "Some Hospital", "UHID-123")
    failures = markers.check([f], set())
    assert len(failures) == 1
    assert "Some Hospital" not in failures[0] and "UHID-123" not in failures[0]
    assert markers.main([str(f)]) == 1


def test_half_marked_file_fails(tmp_path: Path) -> None:
    f = _dicom(tmp_path / "half.dcm", "SYNTHETIC-DEMO", "UHID-123")
    assert markers.check([f], set())


def test_non_dicom_with_dcm_suffix_fails(tmp_path: Path) -> None:
    f = tmp_path / "junk.dcm"
    f.write_bytes(b"not dicom")
    assert markers.check([f], set())


def test_pinned_public_samples_are_exempt(repo: Path) -> None:
    exempt = markers.load_exempt()
    samples = sorted((repo / "reference" / "samples").glob("*.dcm"))
    assert samples and markers.check(samples, exempt) == []
    assert all(not markers.has_markers(s) for s in samples)  # exempt by hash, not by markers


def test_exemption_is_by_exact_hash(tmp_path: Path, repo: Path) -> None:
    src = repo / "reference" / "samples" / "chest_pa_RG1.dcm"
    ds = pydicom.dcmread(src)
    ds.StudyDescription = "modified"
    changed = tmp_path / "changed.dcm"
    ds.save_as(changed)
    assert markers.check([changed], markers.load_exempt())


def test_repo_passes() -> None:
    assert markers.main([]) == 0
