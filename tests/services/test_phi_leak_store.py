"""PHI leak test for the store and the output folder (CLAUDE.md rules 1, 2, 9; SPEC §5, T20). TR-SEC-02,
TR-QA-04, TR-REL-NF-01.

After the sample inbox is finalised, no planted identifier and no original UID appears in any file under
``output/`` (raw bytes, file names), in ``app.db`` (and its WAL), in the work folders, in the reconcile
report or in the ``python -m app reconcile`` output.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from app.services.records.reconcile import reconcile
from tests.deid.test_phi_leak_core import PLANTED, _leaks, _original_uids
from tests.services.conftest import Finalised

REPO = Path(__file__).resolve().parents[2]


def _blob(root: Path) -> str:
    parts = []
    for path in root.rglob("*"):
        parts.append(path.name)
        if path.is_file() and not path.name.endswith(".lock"):
            parts.append(path.read_bytes().decode("latin-1"))
    return "\n".join(parts)


def test_no_identifier_in_output_or_db(finalised_sample: Finalised) -> None:
    needles = PLANTED + _original_uids(finalised_sample.inbox)
    assert _leaks(_blob(finalised_sample.root / "output"), needles) == []
    assert _leaks(_blob(finalised_sample.root / "app_data"), needles) == []  # app.db, -wal, work/


def test_reconcile_report_and_cli_carry_no_identifier(finalised_sample: Finalised) -> None:
    report = reconcile(finalised_sample.ctx)
    assert report.mismatches == [] and report.changed is False
    env = {
        **os.environ,
        "PYTHONPATH": str(REPO),
        "DEID_DATA_ROOT": str(finalised_sample.root),
        "DEID_OUTPUT_ROOT": str(finalised_sample.root / "output"),
        "DEID_APP_DATA_DIR": str(finalised_sample.root / "app_data"),
        "DEID_SETTINGS": str(finalised_sample.root / "no-settings.toml"),
    }
    proc = subprocess.run(
        [sys.executable, "-m", "app", "reconcile"],
        env=env,
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "appended=0" in proc.stdout and "mismatches=0" in proc.stdout
    assert _leaks(proc.stdout + proc.stderr + repr(report), PLANTED) == []
