"""What every service needs: the database, the data folders (SPEC §5.1), the app settings (input roots,
upload limits) and the volume probe that finds input drives again (SPEC §6.5)."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

from app.config import Settings
from app.store.db import Database, init_db
from app.store.volumes import VolumeProbe, system_probe


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
    def key_path(self) -> Path:
        return self.app_data_dir / "secret.key"

    @property
    def withdrawn_dir(self) -> Path:
        return self.app_data_dir / "withdrawn"

    @property
    def staging_dir(self) -> Path:
        return self.app_data_dir / "staging"

    @property
    def secure_dir(self) -> Path:
        return self.app_data_dir / "secure"

    def output_file(self, name: str) -> Path:
        return self.output_root / name


@dataclass(frozen=True)
class ServiceContext:
    db: Database
    paths: Paths
    settings: Settings | None = None
    volumes: VolumeProbe = field(default_factory=system_probe)

    @classmethod
    def open(cls, output_root: Path, app_data_dir: Path, settings: Settings | None = None) -> ServiceContext:
        return cls(init_db(app_data_dir / "app.db"), Paths(output_root, app_data_dir), settings)

    @classmethod
    def from_settings(cls, settings: Settings) -> ServiceContext:
        return cls.open(settings.output_root, settings.app_data_dir, settings)
