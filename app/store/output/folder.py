"""Crash-safe folder moves (SPEC §5.3 step 4, §4 withdrawn). TR-REL-NF-01, TR-WDR-01.

``install_folder`` moves a complete pending record folder to ``output/records/<record_id>/`` (replacing the
previous version's folder for a re-sent study). ``move_folder`` moves a folder elsewhere (withdrawals).
Every step is a rename, so each crash point leaves a state the next call recognises and completes:

    pending ──(other volume: copy to .new-<id>)──┐
    target ──rename──► .prev-<id>                 ├─► rename to target ─► delete .prev-<id>, then pending
"""

from __future__ import annotations

import shutil
from pathlib import Path

from app.store.output.jsonl import fsync_dir


class FolderError(RuntimeError):
    """A folder move cannot be completed. Messages carry record IDs only."""


def _rename_or_copy(src: Path, dst: Path, staging: Path) -> Path:
    """Rename ``src`` to ``dst``; across volumes copy to ``staging`` first and rename that."""
    try:
        src.rename(dst)
        return dst
    except OSError:
        if staging.exists():
            shutil.rmtree(staging)
        shutil.copytree(src, staging)
        staging.rename(dst)
        shutil.rmtree(src)
        return dst


def is_installed(target: Path, expected_record_json: bytes) -> bool:
    record = target / "record.json"
    return record.is_file() and record.read_bytes() == expected_record_json


def install_folder(pending: Path, target: Path, expected_record_json: bytes) -> str:
    """Returns ``installed``, ``already`` or raises FolderError if neither the pending folder nor a matching
    target exists (reconcile reports it)."""
    parent, name = target.parent, target.name
    prev, new = parent / f".prev-{name}", parent / f".new-{name}"
    parent.mkdir(parents=True, exist_ok=True)
    if is_installed(target, expected_record_json):
        for leftover in (prev, new):
            if leftover.exists():
                shutil.rmtree(leftover)
        if is_installed(pending, expected_record_json):  # copy finished, delete did not; a newer
            shutil.rmtree(pending)  # pending version (different record.json) is left alone
        return "already"
    if not pending.is_dir():
        if prev.exists() and not target.exists():
            prev.rename(target)  # undo a half-done swap; the content stays the older version
        raise FolderError(f"{name}: pending folder missing and output folder does not match the DB")
    if target.exists():
        if prev.exists():
            shutil.rmtree(prev)
        target.rename(prev)
    _rename_or_copy(pending, target, new)
    fsync_dir(parent)
    if prev.exists():
        shutil.rmtree(prev)
    return "installed"


def move_folder(src: Path, dst: Path) -> str:
    """Idempotent move: ``moved``, ``already`` (only dst exists) or ``missing`` (neither exists)."""
    if not src.exists():
        return "already" if dst.exists() else "missing"
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():  # a cross-volume copy finished its rename but not the delete
        shutil.rmtree(src)
        return "already"
    _rename_or_copy(src, dst, dst.parent / f".new-{dst.name}")
    fsync_dir(dst.parent)
    return "moved"
