"""A running station for API tests: first-run setup done, one signed-in client per role.

TOTP codes come from a fake clock that moves one 30 s step per code, so codes are never replays. Session
times use the real clock unless a test patches ``app.auth.sessions.now``. Synthetic data only.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pyotp
import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.auth import totp
from app.config import build_settings
from app.services.context import ServiceContext

BASE = "http://127.0.0.1:8765"
# Synthetic test credentials (policy: 12+ chars, capital, small, number, special).
GOOD = "Synthetic-Demo-2026!"
NEW_GOOD = "Changed-Demo-2027?"
BAD_GUESS = "Wrong-Pass-1!"
ROLE_USERS = {"admin": "admin1", "custodian": "custodian1", "operator": "operator1",
              "reviewer": "reviewer1", "auditor": "auditor1"}  # fmt: skip


class FakeTotpClock:
    def __init__(self) -> None:
        self.t = 1_900_000_000.0

    def time(self) -> float:
        return self.t

    def code(self, secret: str) -> str:
        self.t += totp.STEP
        return pyotp.TOTP(secret).at(self.t)


@dataclass
class Station:
    app: Any
    client: TestClient
    root: Path
    clock: FakeTotpClock
    secrets: dict[str, str] = field(default_factory=dict)
    passwords: dict[str, str] = field(default_factory=dict)
    ids: dict[str, str] = field(default_factory=dict)

    @property
    def ctx(self) -> ServiceContext:
        return self.app.state.services  # type: ignore[no-any-return]

    def new_client(self) -> TestClient:
        return TestClient(self.app, base_url=BASE)

    def code(self, username: str) -> str:
        return self.clock.code(self.secrets[username])

    def login(self, username: str) -> TestClient:
        c = self.new_client()
        r = c.post("/api/auth/login", json={"username": username, "password": self.passwords[username]})
        assert r.status_code == 200, r.text
        body = r.json()
        while body["stage"] != "active":
            c.headers["X-CSRF-Token"] = body["csrf_token"]
            body = self._next(c, username, body["stage"])
        c.headers["X-CSRF-Token"] = body["csrf_token"]
        return c

    def _next(self, c: TestClient, username: str, stage: str) -> dict[str, Any]:
        if stage == "mfa_pending":
            r = c.post("/api/auth/totp", json={"code": self.code(username)})
        elif stage == "password_change":
            r = c.post(
                "/api/auth/password",
                json={"current_password": self.passwords[username], "new_password": NEW_GOOD},
            )
            self.passwords[username] = NEW_GOOD
        else:  # totp_enrol
            secret = c.post("/api/auth/totp/enrol").json()["totp_secret"]
            self.secrets[username] = secret
            r = c.post("/api/auth/totp/confirm", json={"code": self.code(username)})
        assert r.status_code == 200, r.text
        return r.json()  # type: ignore[no-any-return]


def run_setup(st: Station, admin: str = "admin1", custodian: str = "custodian1") -> None:
    c = st.client
    for role, name in (("admin", admin), ("custodian", custodian)):
        r = c.post(
            f"/api/setup/{role}",
            json={"username": name, "password": GOOD, "attest_not_consultant": role == "custodian"},
        )
        assert r.status_code == 200, r.text
        st.secrets[name], st.passwords[name], st.ids[name] = (
            r.json()["totp_secret"],
            GOOD,
            r.json()["user_id"],
        )
        r = c.post(f"/api/setup/{role}/totp", json={"code": st.code(name)})
        assert r.status_code == 200 and len(r.json()["backup_codes"]) == 10, r.text
    r = c.post("/api/setup/key")
    assert r.status_code == 200, r.text


@pytest.fixture
def totp_clock(monkeypatch: pytest.MonkeyPatch) -> FakeTotpClock:
    clock = FakeTotpClock()
    monkeypatch.setattr(totp, "time", clock)
    return clock


@pytest.fixture
def fresh(tmp_path: Path, totp_clock: FakeTotpClock) -> Iterator[Station]:
    """A started app with no users (first run)."""
    (tmp_path / "inbox").mkdir()
    settings = build_settings({"data_root": str(tmp_path), "port": 8765})
    app = create_app(web_dist=tmp_path / "no-web", settings=settings)
    with TestClient(app, base_url=BASE) as client:
        yield Station(app, client, tmp_path, totp_clock)


@pytest.fixture
def station(fresh: Station) -> Station:
    """Setup done (admin1 + custodian1 with TOTP, key created); no one signed in."""
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
