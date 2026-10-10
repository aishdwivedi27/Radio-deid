"""Store adapters on their own: migration, appenders, tail scan, folder moves, audit chain (SPEC §5, §7).
TR-REL-NF-01, TR-SEC-03, TR-WDR-01.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import inspect

from app.store import audit
from app.store.db import init_db
from app.store.models.audit import AuditEvent
from app.store.output.csv_writer import CsvAppender, encode_row
from app.store.output.folder import FolderError, install_folder, move_folder
from app.store.output.jsonl import AppendError, JsonlAppender
from app.store.output.tail import read_tail, truncate_fragment

TABLES = {
    "records", "record_versions", "images", "source_files", "append_log", "exclusions", "ethics_approval",
    "list_versions", "patient_flags", "withdrawals", "licensees", "releases", "release_records", "reid_tests",
    "breaches", "audit_events", "alembic_version", "users", "user_roles", "sessions",
    "settings", "jobs", "job_studies", "job_events",
}  # fmt: skip


def test_migration_creates_every_table_and_is_rerunnable(tmp_path: Path) -> None:
    db = init_db(tmp_path / "app.db")
    assert set(inspect(db.engine).get_table_names()) == TABLES
    uniques = inspect(db.engine).get_unique_constraints("append_log")
    assert any(set(u["column_names"]) == {"file", "row_key"} for u in uniques)
    indexes = inspect(db.engine).get_indexes("exclusions")  # Phase 4: one row per job and study
    assert any(i["unique"] and set(i["column_names"]) == {"job_id", "study_key"} for i in indexes)
    db.dispose()
    init_db(tmp_path / "app.db").dispose()  # second start: nothing to do, no error


def test_jsonl_appender(tmp_path: Path) -> None:
    app = JsonlAppender(tmp_path / "x.jsonl")
    assert app.append([b'{"a":1}', b'{"a":2}']) == [8, 16]
    with pytest.raises(AppendError):
        app.append([b'{"a":\n3}'])
    assert (tmp_path / "x.jsonl").read_bytes() == b'{"a":1}\n{"a":2}\n'


def test_csv_appender_header_once(tmp_path: Path) -> None:
    app = CsvAppender(tmp_path / "x.csv", ("a", "b"))
    app.append([["1", "x,y"]])
    app.append([["2", ""]])
    assert (tmp_path / "x.csv").read_bytes() == b'a,b\n1,"x,y"\n2,\n'
    with pytest.raises(AppendError):
        app.append([["only one"]])
    assert encode_row(['q"t']) == b'"q""t"'


def test_tail_and_fragment(tmp_path: Path) -> None:
    path = tmp_path / "x.jsonl"
    path.write_bytes(b"one\ntwo\nthr")
    tail = read_tail(path, 4)
    assert tail.lines == [(b"two", 8)] and tail.fragment_at == 8
    with pytest.raises(ValueError):
        truncate_fragment(path, 4)  # would cut a complete line
    truncate_fragment(path, 8)
    assert path.read_bytes() == b"one\ntwo\n" and read_tail(path, 8).lines == []


def _folder(path: Path, marker: bytes) -> Path:
    path.mkdir(parents=True)
    (path / "record.json").write_bytes(marker)
    (path / "a.dcm").write_bytes(b"x")
    return path


def test_install_folder_states(tmp_path: Path) -> None:
    target = tmp_path / "records" / "S1"
    pending = _folder(tmp_path / "pending" / "S1", b"v1")
    assert install_folder(pending, target, b"v1") == "installed" and not pending.exists()
    assert install_folder(pending, target, b"v1") == "already"
    pending = _folder(tmp_path / "pending" / "S1", b"v2")
    target.rename(tmp_path / "records" / ".prev-S1")  # crash after the first rename of a v2 install
    assert install_folder(pending, target, b"v2") == "installed"
    assert (target / "record.json").read_bytes() == b"v2" and not (tmp_path / "records" / ".prev-S1").exists()
    newer = _folder(tmp_path / "pending" / "S1", b"v3-under-review")
    assert install_folder(newer, target, b"v2") == "already" and newer.exists()  # a newer pending is kept
    with pytest.raises(FolderError):
        install_folder(tmp_path / "missing", tmp_path / "records" / "S2", b"v1")


def test_move_folder(tmp_path: Path) -> None:
    src, dst = _folder(tmp_path / "records" / "S1", b"v1"), tmp_path / "withdrawn" / "S1"
    assert move_folder(src, dst) == "moved" and not src.exists()
    assert move_folder(src, dst) == "already"
    _folder(src, b"v1")  # both exist: a cross-volume copy that finished its rename but not the delete
    assert move_folder(src, dst) == "already" and not src.exists()
    assert move_folder(tmp_path / "x", tmp_path / "y") == "missing"


def test_audit_chain(tmp_path: Path) -> None:
    db = init_db(tmp_path / "app.db")
    with db.transaction() as s:
        audit.append_event(s, "record.finalised", "record", "S1", {"version": 1})
        audit.append_event(s, "record.finalised", "record", "S2", {"version": 1})
    with db.transaction() as s:
        assert audit.chain_ok(s)
        first = s.query(AuditEvent).order_by(AuditEvent.id).first()
        assert first is not None and first.prev_hash == audit.GENESIS
        first.details_json = '{"version":2}'
        with pytest.raises(Exception, match="append-only"):  # the trigger refuses edits (migration 0002)
            s.flush()
    with db.engine.begin() as conn:
        conn.exec_driver_sql("DROP TRIGGER audit_events_no_update")
        conn.exec_driver_sql("UPDATE audit_events SET details_json='{\"version\":2}' WHERE id=1")
    with db.session() as s:
        assert audit.verify_chain(s) == audit.ChainResult(False, 0, 1)  # any edit breaks the chain
    db.dispose()
