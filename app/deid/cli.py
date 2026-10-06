"""Command line: de-identify an input folder into pending record folders (no DB, no review, no append).

    python -m app.deid.cli --input <dir> --pending <dir> [--key app_data/secret.key]

Prints one line per study and a summary. Lines carry only de-identification numbers, statuses, codes and
counts: never names, original IDs or source paths (CLAUDE.md rules 1-2). Synthetic data only unless
``--approval-active`` is given, which only the services may decide in the real app (SPEC §4.1).
"""

from __future__ import annotations

import argparse
import datetime as dt
from collections import Counter
from pathlib import Path

from app.deid.index import index_input
from app.deid.keys import load_or_create_key
from app.deid.pipeline import process_study
from app.deid.report.match import match_reports
from app.deid.types import DeidError, DeidSettings, PendingRecord, ScreenContext


def _line(rec: PendingRecord) -> str:
    if rec.status == "excluded" and rec.screen:
        screen = rec.screen
        return f"{rec.record_id} {rec.patient_code} excluded reason={screen.reason} rule={screen.rule_id}"
    report = (rec.record_row or {}).get("report", {})
    fmt = f"{report.get('source_format')}/{report.get('extraction')}" if report.get("present") else "none"
    excluded = ",".join(f"{k}:{v}" for k, v in sorted(rec.excluded_images.items())) or "0"
    checks = sorted({f.check for f in rec.findings}) or ["pass"]
    return (
        f"{rec.record_id} {rec.patient_code} {rec.status} images={len(rec.image_rows)} report={fmt} "
        f"excluded={excluded} ocr_regions={rec.ocr_regions} redactions={sum(rec.redactions.values())} "
        f"flags={','.join(rec.review_flags) or '-'} checks={','.join(checks)}"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    ap.add_argument("--input", required=True, type=Path)
    ap.add_argument("--pending", required=True, type=Path)
    ap.add_argument("--key", type=Path, default=Path("app_data") / "secret.key")
    ap.add_argument("--job-id", default=dt.datetime.now().strftime("J%Y%m%d-%H%M%S"))
    ap.add_argument("--no-ocr", action="store_true")
    ap.add_argument("--no-ner", action="store_true")
    a = ap.parse_args(argv)
    settings = DeidSettings(
        job_id=a.job_id, ocr_enabled=not a.no_ocr, ner_enabled=not a.no_ner, screen=ScreenContext()
    )
    key = load_or_create_key(a.key)
    a.pending.mkdir(parents=True, exist_ok=True)
    index = index_input(a.input)
    matches = match_reports(index)
    statuses: Counter[str] = Counter()
    for i, group in enumerate(index.studies):
        try:
            rec = process_study(group, matches.by_study.get(i), key, settings, a.pending)
        except DeidError as exc:
            statuses["error"] += 1
            print(f"study {i + 1}: error {exc}")
            continue
        statuses[rec.status] += 1
        print(_line(rec))
    summary = " ".join(f"{k}={v}" for k, v in sorted(statuses.items()))
    skipped = " ".join(f"{k}={v}" for k, v in sorted(index.skipped.items()))
    print(
        f"studies={len(index.studies)} {summary} reports_found={matches.found} "
        f"reports_unmatched={len(matches.unmatched)} skipped: {skipped or 'none'}"
    )
    return 1 if statuses["error"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
