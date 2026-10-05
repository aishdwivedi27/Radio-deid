"""PHI leak test, Phase 0 scope (CLAUDE.md rules 1, 2, 9; SPEC §7, T20).

The skeleton touches no patient data. This test proves that API responses, logs and app source carry
none of the planted identifiers. Later phases add tests/test_phi_leak_*.py for real outputs.
"""

import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from tests.conftest import PHI


def test_api_and_logs_free_of_phi(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    caplog.set_level(logging.DEBUG)
    client = TestClient(create_app(web_dist=tmp_path))
    blob = "".join(client.get(url).text for url in ("/", "/api/health", "/api/missing"))
    blob += caplog.text
    leaks = [p for p in PHI if p.lower() in blob.lower()]
    assert not leaks, leaks


def test_app_source_free_of_phi(repo: Path) -> None:
    blob = "".join(p.read_text(encoding="utf-8") for p in (repo / "app").rglob("*.py")).lower()
    assert not [p for p in PHI if p.lower() in blob]
