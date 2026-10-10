"""Configuration loader (SPEC §5.1, §7, §15.2). TR-PLAT-01.

Reads ``settings.toml`` (path from ``DEID_SETTINGS`` or ``./settings.toml``), then applies
``DEID_*`` environment overrides. Nothing secret is stored here.
"""

from __future__ import annotations

import ipaddress
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_PORT = 8765
LOOPBACK = "127.0.0.1"


class ConfigError(ValueError):
    """Invalid configuration. Messages never include patient data (config holds none)."""


@dataclass(frozen=True)
class Settings:
    data_root: Path
    output_root: Path
    app_data_dir: Path
    input_roots: tuple[Path, ...] = field(default_factory=tuple)
    port: int = DEFAULT_PORT
    bind_address: str = LOOPBACK
    upload_max_file_mb: int = 4096  # one uploaded file (Phase 4 upload jobs)
    upload_max_total_gb: int = 200  # one upload job
    upload_max_files: int = 200_000

    @property
    def inbox_dir(self) -> Path:
        return self.data_root / "inbox"

    @property
    def logs_dir(self) -> Path:
        return self.data_root / "logs"

    @property
    def backups_dir(self) -> Path:
        return self.data_root / "backups"

    @property
    def staging_dir(self) -> Path:
        return self.app_data_dir / "staging"

    @property
    def secure_dir(self) -> Path:
        return self.app_data_dir / "secure"

    @property
    def db_path(self) -> Path:
        return self.app_data_dir / "app.db"


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def _validate_bind(address: str) -> str:
    """SPEC §7: binds to 127.0.0.1 only; no LAN mode (CLAUDE.md non-negotiable 15)."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError as exc:
        raise ConfigError("bind_address must be 127.0.0.1") from exc
    if not ip.is_loopback or ip.version != 4:
        raise ConfigError("bind_address must be 127.0.0.1 (no LAN or remote mode)")
    return str(ip)


def _validate_port(port: Any) -> int:
    try:
        value = int(port)
    except (TypeError, ValueError) as exc:
        raise ConfigError("port must be an integer") from exc
    if not 1024 <= value <= 65535:
        raise ConfigError("port must be between 1024 and 65535")
    return value


def _positive(raw: dict[str, Any], key: str, default: int) -> int:
    try:
        value = int(raw.get(key, default))
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{key} must be an integer") from exc
    if value < 1:
        raise ConfigError(f"{key} must be at least 1")
    return value


def build_settings(raw: dict[str, Any]) -> Settings:
    if "data_root" not in raw:
        raise ConfigError("data_root is required")
    data_root = Path(raw["data_root"]).expanduser().resolve()
    output_root = Path(raw.get("output_root") or data_root / "output").expanduser().resolve()
    app_data_dir = Path(raw.get("app_data_dir") or data_root / "app_data").expanduser().resolve()
    if _is_within(output_root, app_data_dir) or _is_within(app_data_dir, output_root):
        raise ConfigError("output_root and app_data_dir must not contain each other")
    roots = raw.get("input_roots") or [str(data_root / "inbox")]
    if isinstance(roots, str):
        roots = [r for r in roots.split(os.pathsep) if r]
    input_roots = tuple(Path(r).expanduser().resolve() for r in roots)
    for root in input_roots:
        if _is_within(root, app_data_dir) or _is_within(root, output_root):
            raise ConfigError("an input root must not be inside app_data or output")
    return Settings(
        data_root=data_root,
        output_root=output_root,
        app_data_dir=app_data_dir,
        input_roots=input_roots,
        port=_validate_port(raw.get("port", DEFAULT_PORT)),
        bind_address=_validate_bind(str(raw.get("bind_address", LOOPBACK))),
        upload_max_file_mb=_positive(raw, "upload_max_file_mb", 4096),
        upload_max_total_gb=_positive(raw, "upload_max_total_gb", 200),
        upload_max_files=_positive(raw, "upload_max_files", 200_000),
    )


_ENV_KEYS = (
    "data_root",
    "output_root",
    "app_data_dir",
    "input_roots",
    "port",
    "bind_address",
    "upload_max_file_mb",
    "upload_max_total_gb",
    "upload_max_files",
)


def load_settings(path: Path | None = None, env: dict[str, str] | None = None) -> Settings:
    env = dict(os.environ) if env is None else env
    settings_file = path or Path(env.get("DEID_SETTINGS", "settings.toml"))
    raw: dict[str, Any] = {}
    if settings_file.is_file():
        with settings_file.open("rb") as fh:
            raw = tomllib.load(fh)
    for key in _ENV_KEYS:
        value = env.get(f"DEID_{key.upper()}")
        if value:
            raw[key] = value
    raw.setdefault("data_root", str(Path("data").resolve()))
    return build_settings(raw)
