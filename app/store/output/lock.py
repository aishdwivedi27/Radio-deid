"""One cross-process lock for every write to the output files (SPEC §5.3 step 5). TR-REL-NF-01.

``filelock`` uses ``msvcrt.locking`` on Windows and ``fcntl.flock`` on macOS/Linux. The lock file lives in
``app_data/locks/`` so nothing extra appears in ``output/``.
"""

from __future__ import annotations

from pathlib import Path

from filelock import FileLock

LOCK_TIMEOUT_S = 120


def output_lock(app_data_dir: Path) -> FileLock:
    locks = app_data_dir / "locks"
    locks.mkdir(parents=True, exist_ok=True)
    return FileLock(str(locks / "output.lock"), timeout=LOCK_TIMEOUT_S)


def worker_lock(app_data_dir: Path) -> FileLock:
    """Held by the job worker for as long as it runs, so a second app instance never runs jobs (Phase 4)."""
    locks = app_data_dir / "locks"
    locks.mkdir(parents=True, exist_ok=True)
    return FileLock(str(locks / "worker.lock"), timeout=0)
