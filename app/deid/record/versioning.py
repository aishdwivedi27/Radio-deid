"""Re-sent studies: version n+1 of a record (SPEC §4, §6.5; acceptance criterion 5, T35). TR-REL-NF-01.

Only the new source files are de-identified. The prior de-identified images (finalised in ``output/`` or
still pending) are hard-linked (copied across volumes) into the new pending folder, so the folder is a
complete record and C1-C8 run on it unchanged. The report header block is rebuilt to list every image
(C4): from the newly matched report if there is one, otherwise from the prior report body.
"""

from __future__ import annotations

import os
import shutil
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.deid.report.header import RULE
from app.deid.types import ExtractedReport


@dataclass(frozen=True)
class PriorVersion:
    folder: Path = field(repr=False)  # holds the prior {image_id}.dcm files and report
    version: int  # the version number the rebuilt pending record will carry
    image_rows: tuple[dict[str, Any], ...]
    record_row: dict[str, Any]

    @property
    def image_ids(self) -> frozenset[str]:
        return frozenset(str(r["image_id"]) for r in self.image_rows)

    @property
    def excluded(self) -> Counter[str]:
        return Counter({e["reason"]: int(e["count"]) for e in self.record_row.get("images_excluded", [])})


def link_or_copy(src: Path, dst: Path) -> None:
    try:
        os.link(src, dst)
    except OSError:  # other volume, or a file system without hard links (FAT/exFAT)
        shutil.copy2(src, dst)


def link_prior_images(prior: PriorVersion, out_dir: Path) -> None:
    for row in prior.image_rows:
        name = f"{row['image_id']}.dcm"
        link_or_copy(prior.folder / name, out_dir / name)


def prior_report(prior: PriorVersion) -> tuple[ExtractedReport, dict[str, int]] | None:
    """The prior report's redacted body (already de-identified) and its counts, or None."""
    report = prior.record_row.get("report") or {}
    if not report.get("present"):
        return None
    path = prior.folder / Path(str(report["file"])).name
    body = path.read_text(encoding="utf-8").split(RULE + "\n", 1)[-1]
    src = ExtractedReport(body, report["source_format"], report["extraction"])
    return src, dict(report.get("redactions") or {})
