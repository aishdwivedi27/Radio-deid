"""What every service needs: the database and the data folders (SPEC §5.1)."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings
from app.store.db import Database, init_db


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass(frozen=True)
class Paths:
    output_root: Path
    app_data_dir: Path

    @property
    def records_dir(self) -> Path:
        return self.output_root / "records"

    @property
    def pending_root(self) -> Path:
        return self.app_data_dir / "work" / "pending"

    @property
    def withdrawn_dir(self) -> Path:
        return self.app_data_dir / "withdrawn"

    def output_file(self, name: str) -> Path:
        return self.output_root / name


@dataclass(frozen=True)
class ServiceContext:
    db: Database
    paths: Paths

    @classmethod
    def open(cls, output_root: Path, app_data_dir: Path) -> ServiceContext:
        return cls(init_db(app_data_dir / "app.db"), Paths(output_root, app_data_dir))

    @classmethod
    def from_settings(cls, settings: Settings) -> ServiceContext:
        return cls.open(settings.output_root, settings.app_data_dir)
