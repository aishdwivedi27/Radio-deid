"""Terminology: pseudonymised, never the forbidden words (SPEC §3; CLAUDE.md rule 14). TR-DEID-09."""

from __future__ import annotations

import re
from pathlib import Path

from tests.deid.conftest import Processed

FORBIDDEN = re.compile(r"anonymi[sz]|irreversib", re.IGNORECASE)


def test_app_source_never_claims_anonymisation(repo: Path) -> None:  # TR-DEID-09
    offenders = [p.as_posix() for p in (repo / "app").rglob("*.py") if FORBIDDEN.search(p.read_text("utf-8"))]
    assert offenders == []


def test_outputs_never_claim_anonymisation(processed: Processed) -> None:  # TR-DEID-09
    for path in processed.pending.rglob("*"):
        if path.suffix in (".txt", ".json") or path.suffix == ".dcm":
            assert not FORBIDDEN.search(path.read_bytes().decode("latin-1")), path.name
