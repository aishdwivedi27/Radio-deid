"""Shared fixtures for the de-identification core tests. Synthetic data only (SYNTHETIC-DEMO / DEMO-)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _fixture_module():  # type: ignore[no-untyped-def]
    path = REPO / "tests/fixtures/make_sample_data.py"
    spec = importlib.util.spec_from_file_location("make_sample_data", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def sample_inbox(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("inbox") / "sample_inbox"
    _fixture_module().main(str(out))
    return out


@pytest.fixture(scope="session")
def key() -> bytes:
    return bytes(range(32))
