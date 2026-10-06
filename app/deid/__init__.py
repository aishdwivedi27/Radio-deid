"""Domain core: pure de-identification logic. No web, DB or audit code (CLAUDE.md, Architecture).

Public API (SPEC §3, §6; phase-1 prompt):

    index_input(input_dir) -> InputIndex
    match_reports(index) -> ReportMatches
    read_report(path) -> ExtractedReport
    screen_study(group, report, key, ctx) -> ScreenResult
    process_study(group, report, key, settings, pending_root) -> PendingRecord
    check_consistency(pending_dir, pending_record, finalised=None) -> list[Finding]   # C1-C8
    check_deid(pending_dir, identifiers, ocr_remaining=None) -> list[Finding]         # A1-A7
    load_or_create_key(path) -> bytes;  key_fingerprint(key) -> str

Names are resolved lazily so that importing a small module (e.g. ``app.deid.schemas``) stays cheap.
"""

from __future__ import annotations

import importlib
from typing import Any

_EXPORTS = {
    "index_input": "app.deid.index",
    "match_reports": "app.deid.report.match",
    "read_report": "app.deid.report.read",
    "screen_study": "app.deid.pipeline",
    "process_study": "app.deid.pipeline",
    "check_consistency": "app.deid.qa.c_checks",
    "check_deid": "app.deid.qa.a_checks",
    "load_or_create_key": "app.deid.keys",
    "key_fingerprint": "app.deid.keys",
    "PIPELINE_VERSION": "app.deid.version",
}
__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    if name in _EXPORTS:
        return getattr(importlib.import_module(_EXPORTS[name]), name)
    raise AttributeError(name)
