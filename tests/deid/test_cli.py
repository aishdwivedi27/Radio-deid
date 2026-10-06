"""CLI: ``python -m app.deid.cli --input <dir> --pending <dir>`` prints one line per record (phase-1
acceptance). Output carries de-identification numbers, codes and counts only. TR-DEID-03, TR-SEC-02."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.deid.cli import main
from tests.deid.test_phi_leak_core import PLANTED


def test_cli_sample_inbox(sample_inbox: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        [
            "--input",
            str(sample_inbox),
            "--pending",
            str(tmp_path / "pending"),
            "--key",
            str(tmp_path / "app_data" / "secret.key"),
            "--job-id",
            "J-CLI",
        ]
    )
    out = capsys.readouterr().out
    lines = out.strip().splitlines()
    assert code == 0 and len(lines) == 5
    assert all(re.match(r"S[0-9A-F]{12} P[0-9A-F]{12} awaiting_review ", ln) for ln in lines[:4])
    assert "reports_found=4 reports_unmatched=0" in lines[4]
    low = out.lower()
    assert [p for p in PLANTED if p.lower() in low] == [] and str(sample_inbox).lower() not in low
    assert (tmp_path / "app_data" / "secret.key").exists()
