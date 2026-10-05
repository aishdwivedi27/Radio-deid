"""TR-PLAT-01: Python 3.12, loopback-only bind, embedded SQLite, configurable folders (SPEC §5.1, §7)."""

import sqlite3
import sys
from pathlib import Path

import pytest

from app.config import DEFAULT_PORT, ConfigError, build_settings, load_settings


def test_python_is_312() -> None:  # TR-PLAT-01
    assert sys.version_info[:2] == (3, 12)


def test_sqlite_is_embedded() -> None:  # TR-PLAT-01: no database server
    con = sqlite3.connect(":memory:")
    assert con.execute("select 1").fetchone() == (1,)


def test_defaults_follow_spec_layout(tmp_path: Path) -> None:
    s = build_settings({"data_root": str(tmp_path)})
    assert s.bind_address == "127.0.0.1" and s.port == DEFAULT_PORT == 8765
    assert s.output_root == tmp_path.resolve() / "output"
    assert s.app_data_dir == tmp_path.resolve() / "app_data"
    assert s.secure_dir == s.app_data_dir / "secure"
    assert s.staging_dir == s.app_data_dir / "staging"
    assert s.input_roots == (tmp_path.resolve() / "inbox",)


@pytest.mark.parametrize("address", ["0.0.0.0", "192.168.1.10", "::1", "localhost", "127.0.0.2x"])
def test_non_loopback_bind_refused(tmp_path: Path, address: str) -> None:  # TR-PLAT-01, SPEC §7
    with pytest.raises(ConfigError):
        build_settings({"data_root": str(tmp_path), "bind_address": address})


def test_output_inside_app_data_refused(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        build_settings({"data_root": str(tmp_path), "output_root": str(tmp_path / "app_data" / "out")})


def test_input_root_inside_output_refused(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        build_settings({"data_root": str(tmp_path), "input_roots": [str(tmp_path / "output" / "x")]})


def test_bad_port_refused(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        build_settings({"data_root": str(tmp_path), "port": 80})


def test_toml_then_env_override(tmp_path: Path) -> None:
    toml = tmp_path / "settings.toml"
    toml.write_text(f'data_root = "{tmp_path.as_posix()}"\nport = 9001\n', encoding="utf-8")
    assert load_settings(toml, env={}).port == 9001
    assert load_settings(toml, env={"DEID_PORT": "9002"}).port == 9002
