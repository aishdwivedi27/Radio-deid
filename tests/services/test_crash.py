"""Crash safety of finalise and reconcile (acceptance criterion 4; SPEC §5.3 step 7; D-019). TR-REL-NF-01.

Each test interrupts finalise at a different point, then runs ``reconcile()``. Afterwards the four files
have exactly the right number of rows, nothing is duplicated and everything matches the DB.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.services.context import ServiceContext
from app.services.records import append as append_mod
from app.services.records import finalise as finalise_mod
from app.services.records.approve import record_decision
from app.services.records.finalise import finalise
from app.services.records.reconcile import reconcile
from app.store.output import folder as folder_mod
from app.store.output.csv_writer import CsvAppender
from app.store.output.jsonl import JsonlAppender
from tests.deid.helpers import write_study
from tests.helpers.outputs import assert_outputs_consistent
from tests.services.conftest import (
    CATEGORY,
    REVIEWER,
    add_image,
    approve_finalise,
    csv_rows,
    ingest_folder,
    lines,
    make_ctx,
)

REPO = Path(__file__).resolve().parents[2]


class Crash(RuntimeError):
    pass


def _boom(*_a: object, **_k: object) -> None:
    raise Crash("simulated crash")


def _pending(
    ctx: ServiceContext, tmp_path: Path, key: bytes, n: int = 2, name: str = "s1"
) -> tuple[str, int]:
    write_study(tmp_path / "in" / name, n, modality="CT", patient_id=f"DEMO-CR-{name}")
    res = [r for r in ingest_folder(ctx, tmp_path / "in" / name, key) if r.status == "awaiting_review"]
    assert len(res) == 1
    return res[0].record_id, res[0].version or 0


def _counts(out: Path) -> tuple[int, int, int, int]:
    return (
        len(lines(out / "records.jsonl")),
        len(lines(out / "images.jsonl")),
        csv_rows(out / "records.csv"),
        csv_rows(out / "images.csv"),
    )


def test_crash_after_commit_before_csv(
    ctx: ServiceContext, tmp_path: Path, key: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    rid, v = _pending(ctx, tmp_path, key)
    monkeypatch.setattr(CsvAppender, "append", _boom)
    with pytest.raises(Crash):
        approve_finalise(ctx, rid, v)
    out = ctx.paths.output_root
    assert _counts(out) == (1, 2, 0, 0)  # DB committed, JSONL written, CSV not
    monkeypatch.undo()
    report = reconcile(ctx)
    assert report.appended == 3 and report.mismatches == []  # 1 record + 2 image CSV rows
    assert _counts(out) == (1, 2, 1, 2)
    assert_outputs_consistent(out, ctx)
    assert reconcile(ctx).appended == 0 and _counts(out) == (1, 2, 1, 2)


def test_crash_after_fsync_before_log(
    ctx: ServiceContext, tmp_path: Path, key: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The line is on disk but its append_log row never committed: reconcile logs it, never re-writes it."""
    rid, v = _pending(ctx, tmp_path, key)
    monkeypatch.setattr(append_mod.append_log, "add", _boom)
    with pytest.raises(Crash):
        approve_finalise(ctx, rid, v)
    monkeypatch.undo()
    report = reconcile(ctx)
    assert report.absorbed == 1 and report.mismatches == []
    assert _counts(ctx.paths.output_root) == (1, 2, 1, 2)
    assert_outputs_consistent(ctx.paths.output_root, ctx)


def test_torn_fragment_is_cut(
    ctx: ServiceContext, tmp_path: Path, key: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash in the middle of a write leaves half a line; reconcile cuts only that fragment (D-019)."""
    rid1, v1 = _pending(ctx, tmp_path, key, name="a")
    approve_finalise(ctx, rid1, v1)
    rid2, v2 = _pending(ctx, tmp_path, key, name="b")
    before = (ctx.paths.output_root / "records.jsonl").read_bytes()

    def half_write(self: JsonlAppender, batch: list[bytes]) -> list[int]:
        with self.path.open("ab") as fh:
            fh.write(batch[0][: len(batch[0]) // 2])
        raise Crash("power cut")

    monkeypatch.setattr(JsonlAppender, "append", half_write)
    with pytest.raises(Crash):
        approve_finalise(ctx, rid2, v2)
    monkeypatch.undo()
    report = reconcile(ctx)
    assert report.truncated == 1 and report.mismatches == []
    data = (ctx.paths.output_root / "records.jsonl").read_bytes()
    assert data.startswith(before) and _counts(ctx.paths.output_root) == (2, 4, 2, 4)
    assert_outputs_consistent(ctx.paths.output_root, ctx)


def test_crash_between_folder_renames(
    ctx: ServiceContext, tmp_path: Path, key: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Version 2 replaces the folder: crash after the old folder became .prev-<id>, before the rename."""
    folder = tmp_path / "in" / "s1"
    rid, v = _pending(ctx, tmp_path, key, n=2)
    approve_finalise(ctx, rid, v)
    add_image(folder, 3)
    [res] = [r for r in ingest_folder(ctx, folder, key) if r.status == "awaiting_review"]
    monkeypatch.setattr(folder_mod, "_rename_or_copy", _boom)
    with pytest.raises(Crash):
        approve_finalise(ctx, rid, res.version or 0)
    monkeypatch.undo()
    assert (ctx.paths.records_dir / f".prev-{rid}").exists() and not (ctx.paths.records_dir / rid).exists()
    report = reconcile(ctx)
    assert report.folders_installed == 1 and report.mismatches == []
    assert not (ctx.paths.records_dir / f".prev-{rid}").exists()
    assert _counts(ctx.paths.output_root) == (2, 3, 2, 3)
    assert_outputs_consistent(ctx.paths.output_root, ctx)


def test_crash_before_folder_move(
    ctx: ServiceContext, tmp_path: Path, key: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    rid, v = _pending(ctx, tmp_path, key)
    monkeypatch.setattr(finalise_mod, "install_folder", _boom)
    with pytest.raises(Crash):
        approve_finalise(ctx, rid, v)
    monkeypatch.undo()
    assert _counts(ctx.paths.output_root) == (0, 0, 0, 0)
    report = reconcile(ctx)
    assert report.folders_installed == 1 and report.appended == 6
    assert_outputs_consistent(ctx.paths.output_root, ctx)


KILL_SCRIPT = """
import os, sys
from pathlib import Path
from app.services.context import ServiceContext
from app.services.records.finalise import finalise
from app.store.output.csv_writer import CsvAppender
root, rid, version = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
ctx = ServiceContext.open(root / "output", root / "app_data")
CsvAppender.append = lambda *a, **k: os._exit(9)  # the process dies here: no cleanup, no rollback
finalise(ctx, rid, version, "u_0003", "NORMAL")
"""


def test_killed_process_then_restart(tmp_path: Path, key: bytes) -> None:
    """Criterion 4 with a real process kill (os._exit) during finalise, then a fresh process reconciles."""
    ctx = make_ctx(tmp_path)
    rid, v = _pending(ctx, tmp_path, key)
    record_decision(ctx, rid, v, REVIEWER, "approved", CATEGORY)
    ctx.db.dispose()
    env = {**os.environ, "PYTHONPATH": str(REPO)}
    proc = subprocess.run(
        [sys.executable, "-c", KILL_SCRIPT, str(tmp_path), rid, str(v)], env=env, cwd=REPO, check=False
    )
    assert proc.returncode == 9
    restarted = make_ctx(tmp_path)  # "restart": a new process would do exactly this at startup
    try:
        assert _counts(restarted.paths.output_root) == (1, 2, 0, 0)
        assert reconcile(restarted).mismatches == []
        assert _counts(restarted.paths.output_root) == (1, 2, 1, 2)
        assert_outputs_consistent(restarted.paths.output_root, restarted)
        assert finalise(restarted, rid, v, REVIEWER, CATEGORY).status == "already_finalised"
    finally:
        restarted.db.dispose()
