"""Alembic environment: runs on the connection handed over by ``app.store.db.init_db`` (no logging config,
so migrations never write to the console or a log file)."""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine

from app.store.models import Base

config = context.config
target_metadata = Base.metadata


def run() -> None:
    conn = config.attributes.get("connection")
    if conn is None:  # command-line use (alembic revision --autogenerate) with an URL
        engine = create_engine(config.get_main_option("sqlalchemy.url") or "sqlite://")
        with engine.begin() as own:
            context.configure(connection=own, target_metadata=target_metadata, render_as_batch=True)
            with context.begin_transaction():
                context.run_migrations()
        return
    context.configure(connection=conn, target_metadata=target_metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


run()
