"""Embedded SQLite database (SPEC §5.1 ``app_data/app.db``; §8: no database server). TR-REL-NF-01.

WAL journal, ``synchronous=FULL`` (a committed finalise survives a power cut), foreign keys on, and a busy
timeout so the worker and the web app can share the file. The schema is created and upgraded only by the
Alembic migrations in ``app/store/migrations`` (``init_db``).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

MIGRATIONS = Path(__file__).resolve().parent / "migrations"


def _pragmas(dbapi_conn: Any, _record: Any) -> None:
    dbapi_conn.isolation_level = None  # SQLAlchemy issues BEGIN itself (below)
    cur = dbapi_conn.cursor()
    for pragma in ("journal_mode=WAL", "synchronous=FULL", "foreign_keys=ON", "busy_timeout=10000"):
        cur.execute(f"PRAGMA {pragma}")
    cur.close()


def _begin_immediate(conn: Any) -> None:
    # Take the write lock at BEGIN, so two writers can never both read the same "last audit hash" or the
    # same "next version" and then race to commit.
    conn.exec_driver_sql("BEGIN IMMEDIATE")


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.engine: Engine = create_engine(f"sqlite:///{path.as_posix()}")
        event.listen(self.engine, "connect", _pragmas)
        event.listen(self.engine, "begin", _begin_immediate)
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        """One unit of work: commits on success, rolls back on any exception."""
        with self._sessions.begin() as session:
            yield session

    def session(self) -> Session:
        return self._sessions()

    def dispose(self) -> None:
        self.engine.dispose()


def alembic_config() -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", MIGRATIONS.as_posix())
    return cfg  # the connection is passed in cfg.attributes (no URL in an ini option)


def init_db(path: Path) -> Database:
    """Open (creating if needed) ``app.db`` and run every pending migration."""
    path.parent.mkdir(parents=True, exist_ok=True)
    db = Database(path)
    cfg = alembic_config()
    with db.engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
    return db
