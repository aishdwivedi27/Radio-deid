"""Fixed CSV column order (SPEC §5.2). TR-DEID-10, TR-REV-04, TR-COH-03, TR-CC-03, TR-WDR-02.

This is the only copy of the column lists. New columns go at the end and bump ``pipeline_version``.
"""

from __future__ import annotations

RECORD_COLUMNS: tuple[str, ...] = (
    "record_id", "record_version", "patient_code", "modalities", "body_part", "study_description",
    "study_date", "date_mode", "patient_sex", "patient_age",
    "manufacturer", "model", "n_series", "n_images", "image_ids", "report_present", "report_file",
    "report_sha256", "report_source_format", "report_extraction",
    "report_redactions_total", "report_redactions",
    "ocr_regions_masked", "images_excluded_total", "images_excluded", "qa_auto", "qa_consistency",
    "reviewer_id", "decision", "decided_at",
    "job_id", "processed_at", "finalised_at", "pipeline_version", "key_fingerprint",
    "age_band", "finding_category", "consent_basis", "cohort_ref", "rules_version",
)  # fmt: skip

IMAGE_COLUMNS: tuple[str, ...] = (
    "image_id", "record_id", "record_version", "patient_code", "file", "sha256", "bytes", "modality",
    "sop_class", "series_number", "instance_number", "frames",
    "rows", "columns", "bits_stored", "photometric", "pixel_spacing", "view_position", "laterality",
    "body_part", "transfer_syntax", "burned_in_text_masked",
    "ocr_regions", "finalised_at",
)  # fmt: skip

WITHDRAWAL_COLUMNS: tuple[str, ...] = ("record_id", "patient_code", "reason", "list_version", "withdrawn_at")
