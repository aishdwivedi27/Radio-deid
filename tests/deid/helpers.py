"""Small synthetic DICOM studies for tests. Every file carries InstitutionName = SYNTHETIC-DEMO and a
PatientID starting DEMO- (SPEC §11). No real patient data."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

CR_SOP = "1.2.840.10008.5.1.4.1.1.1"
CT_SOP = "1.2.840.10008.5.1.4.1.1.2"
US_SOP = "1.2.840.10008.5.1.4.1.1.6.1"
SOP = {
    "CR": CR_SOP,
    "DX": CR_SOP,
    "CT": CT_SOP,
    "MR": "1.2.840.10008.5.1.4.1.1.4",
    "US": US_SOP,
    "PX": CR_SOP,
}


def make_ds(
    *,
    patient_id: str = "DEMO-T-0001",
    name: str = "Demo^Patient",
    accession: str = "ACC-T0001",
    study_uid: str | None = None,
    modality: str = "CR",
    series: int = 1,
    instance: int = 1,
    size: int = 64,
    **extra: Any,
) -> Dataset:
    ds = Dataset()
    ds.file_meta = FileMetaDataset()
    ds.file_meta.MediaStorageSOPClassUID = SOP.get(modality, CR_SOP)
    ds.file_meta.MediaStorageSOPInstanceUID = generate_uid()
    ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.SOPClassUID, ds.SOPInstanceUID = (
        ds.file_meta.MediaStorageSOPClassUID,
        ds.file_meta.MediaStorageSOPInstanceUID,
    )
    ds.Modality, ds.PatientName, ds.PatientID, ds.PatientSex = modality, name, patient_id, "F"
    ds.PatientAge, ds.InstitutionName = "040Y", "SYNTHETIC-DEMO"
    ds.StudyInstanceUID, ds.SeriesInstanceUID = study_uid or generate_uid(), generate_uid()
    ds.StudyDate, ds.AccessionNumber = "20260901", accession
    ds.SeriesNumber, ds.InstanceNumber = series, instance
    ds.Manufacturer, ds.ManufacturerModelName = "DemoVendor", "DemoModel 5000"
    yy, xx = np.mgrid[:size, :size]
    arr = 800 + 600 * np.exp(-(((xx - size / 2) / (size / 3)) ** 2 + ((yy - size / 2) / (size / 3)) ** 2))
    ds.Rows = ds.Columns = size
    ds.SamplesPerPixel, ds.PhotometricInterpretation = 1, "MONOCHROME2"
    ds.BitsAllocated, ds.BitsStored, ds.HighBit, ds.PixelRepresentation = 16, 12, 11, 0
    ds.PixelData = arr.astype(np.uint16).tobytes()
    for k, v in extra.items():
        if v is None:
            if k in ds:
                delattr(ds, k)
        else:
            setattr(ds, k, v)
    return ds


def burn_text(ds: Dataset, lines: list[str], size: int = 24) -> None:
    """Burn bright text into the top-left corner of the pixels (synthetic burned-in annotation)."""
    from PIL import Image, ImageDraw, ImageFont

    arr = ds.pixel_array.copy()
    mask = Image.new("L", (arr.shape[1], arr.shape[0]))
    draw = ImageDraw.Draw(mask)
    for i, line in enumerate(lines):
        draw.text((20, 20 + i * int(size * 1.4)), line, fill=255, font=ImageFont.load_default(size=size))
    arr[np.asarray(mask) > 128] = arr.max() if arr.max() > 0 else 255
    ds.PixelData = arr.tobytes()


def transcode(path: Path, syntax: str) -> None:
    """Re-encode a DICOM file in place with GDCM, e.g. syntax = "JPEGLosslessProcess14_1"."""
    import gdcm

    reader = gdcm.ImageReader()
    reader.SetFileName(str(path))
    assert reader.Read()
    change = gdcm.ImageChangeTransferSyntax()
    change.SetTransferSyntax(gdcm.TransferSyntax(getattr(gdcm.TransferSyntax, syntax)))
    change.SetInput(reader.GetImage())
    assert change.Change()
    writer = gdcm.ImageWriter()
    writer.SetFileName(str(path))
    writer.SetFile(reader.GetFile())
    writer.SetImage(change.GetOutput())
    assert writer.Write()


def write_study(folder: Path, n: int = 1, **kw: Any) -> str:
    """Write ``n`` images of one study into ``folder``; returns the StudyInstanceUID."""
    uid = kw.pop("study_uid", None) or generate_uid()
    prefix = kw.pop("prefix", "IM")
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(1, n + 1):
        ds = make_ds(study_uid=uid, instance=i, **kw)
        ds.save_as(folder / f"{prefix}{i:04d}", enforce_file_format=True)
    return uid
