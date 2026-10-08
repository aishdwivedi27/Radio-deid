"""A running station for API tests: first-run setup done, one signed-in client per role.

Sign-in is username + password (no second factor, CR-01). Session times use the real clock unless a test
patches ``app.auth.sessions.now``. Synthetic data only.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.config import build_settings
from app.services.context import ServiceContext

BASE = "http://127.0.0.1:8765"
# Synthetic test credentials (policy: 12+ chars, capital, small, number, special).
GOOD = "Synthetic-Demo-2026!"
NEW_GOOD = "Changed-Demo-2027?"
BAD_GUESS = "Wrong-Pass-1!"
ROLE_USERS = {"admin": "admin1", "custodian": "custodian1", "operator": "operator1",
              "reviewer": "reviewer1", "auditor": "auditor1"}  # fmt: skip


@dataclass
class Station:
    app: Any
    client: TestClient
    root: Path
    passwords: dict[str, str] = field(default_factory=dict)
    ids: dict[str, str] = field(default_factory=dict)

    @property
    def ctx(self) -> ServiceContext:
        return self.app.state.services  # type: ignore[no-any-return]

    def new_client(self) -> TestClient:
        return TestClient(self.app, base_url=BASE)

    def login(self, username: str) -> TestClient:
        """Sign in; a temporary password is changed to ``NEW_GOOD`` on the way."""
        c = self.new_client()
        r = c.post("/api/auth/login", json={"username": username, "password": self.passwords[username]})
        assert r.status_code == 200, r.text
        body = r.json()
        if body["stage"] == "password_change":
            c.headers["X-CSRF-Token"] = body["csrf_token"]
            r = c.post(
                "/api/auth/password",
                json={"current_password": self.passwords[username], "new_password": NEW_GOOD},
            )
            assert r.status_code == 200, r.text
            self.passwords[username], body = NEW_GOOD, r.json()
        assert body["stage"] == "active", body
        c.headers["X-CSRF-Token"] = body["csrf_token"]
        return c


def run_setup(st: Station, admin: str = "admin1", custodian: str = "custodian1") -> None:
    c = st.client
    for role, name in (("admin", admin), ("custodian", custodian)):
        r = c.post(
            f"/api/setup/{role}",
            json={"username": name, "password": GOOD, "attest_not_consultant": role == "custodian"},
        )
        assert r.status_code == 200, r.text
        st.passwords[name], st.ids[name] = GOOD, r.json()["user_id"]
    r = c.post("/api/setup/key")
    assert r.status_code == 200, r.text


@pytest.fixture
def fresh(tmp_path: Path) -> Iterator[Station]:
    """A started app with no users (first run)."""
    (tmp_path / "inbox").mkdir()
    settings = build_settings({"data_root": str(tmp_path), "port": 8765})
    app = create_app(web_dist=tmp_path / "no-web", settings=settings)
    with TestClient(app, base_url=BASE) as client:
        yield Station(app, client, tmp_path)


@pytest.fixture
def station(fresh: Station) -> Station:
    """Setup done (admin1 + custodian1, key created); no one signed in."""
    run_setup(fresh)
    return fresh


@pytest.fixture
def staffed(station: Station) -> tuple[Station, dict[str, TestClient]]:
    """Plus operator1, reviewer1 and auditor1 created by the admin; one signed-in client per role."""
    admin = station.login("admin1")
    for role in ("operator", "reviewer", "auditor"):
        name = ROLE_USERS[role]
        r = admin.post("/api/users", json={"username": name, "roles": [role]})
        assert r.status_code == 200, r.text
        station.passwords[name], station.ids[name] = r.json()["temporary_password"], r.json()["user"]["id"]
    clients = {"admin": admin, "custodian": station.login("custodian1")}
    for role in ("operator", "reviewer", "auditor"):
        clients[role] = station.login(ROLE_USERS[role])
    return station, clients
