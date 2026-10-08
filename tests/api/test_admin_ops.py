"""User administration, the Custodian grant, settings, key routes and admin-reset (SPEC §2, §7).
TR-ROLE-01..04, TR-SEC-01, TR-SEC-03. T17."""

from __future__ import annotations

import os

from fastapi.testclient import TestClient

from app.__main__ import main as cli
from tests.api.conftest import BAD_GUESS, GOOD, Station


def _events(station: Station, action: str) -> list[tuple[str | None, str, str]]:
    from app.store.models.audit import AuditEvent

    with station.ctx.db.session() as s:
        q = s.query(AuditEvent).filter(AuditEvent.action == action).order_by(AuditEvent.id)
        return [(e.user_id, e.target_id, e.details_json) for e in q]


def test_t17_admin_cannot_touch_the_key_or_grant_custodian(
    staffed: tuple[Station, dict[str, TestClient]],
) -> None:
    station, clients = staffed
    admin = clients["admin"]
    calls = [
        admin.get("/api/key/fingerprint"),
        admin.post("/api/key/backup", json={"password": GOOD}),
        admin.post("/api/key/rotate", json={"password": GOOD, "confirm": "ROTATE"}),
        admin.post(f"/api/users/{station.ids['reviewer1']}/custodian",
                   json={"password": GOOD, "attest_centre_staff": True}),
        admin.post("/api/users", json={"username": "cust2", "roles": ["custodian"]}),
        admin.put(f"/api/users/{station.ids['reviewer1']}/roles", json={"roles": ["reviewer", "custodian"]}),
    ]  # fmt: skip
    assert [r.status_code for r in calls] == [403] * 6
    denied = {(uid, target) for uid, target, _ in _events(station, "access.denied")}
    admin_id = station.ids["admin1"]
    assert {(admin_id, "key.manage"), (admin_id, "custodian.grant")} <= denied
    assert not _events(station, "custodian.granted") and not _events(station, "key.viewed")


def test_custodian_key_routes(staffed: tuple[Station, dict[str, TestClient]]) -> None:
    station, clients = staffed
    cust = clients["custodian"]
    fp = cust.get("/api/key/fingerprint").json()["key_fingerprint"]
    assert len(fp) == 16 and _events(station, "key.viewed")
    assert cust.post("/api/key/backup", json={"password": BAD_GUESS}).json()["error"] == "reauth_invalid"
    r = cust.post("/api/key/backup", json={"password": GOOD})
    assert r.status_code == 501 and _events(station, "key.backup_requested")
    r = cust.post("/api/key/rotate", json={"password": GOOD, "confirm": "rotate"})
    assert r.status_code == 400
    r = cust.post("/api/key/rotate", json={"password": GOOD, "confirm": "ROTATE"})
    assert r.status_code == 501 and _events(station, "key.rotate_requested")
    assert fp not in r.text and not _events(station, "key.rotated")


def test_custodian_grant_rules(staffed: tuple[Station, dict[str, TestClient]]) -> None:
    station, clients = staffed
    admin, cust = clients["admin"], clients["custodian"]
    consultant = admin.post("/api/users", json={"username": "consult1", "roles": ["reviewer"],
                                                "is_consultant": True}).json()["user"]["id"]  # fmt: skip
    target = station.ids["reviewer1"]
    wrong = {"password": BAD_GUESS, "attest_centre_staff": True}
    assert cust.post(f"/api/users/{target}/custodian", json=wrong).json()["error"] == "reauth_invalid"
    no_attest = {"password": GOOD, "attest_centre_staff": False}
    assert cust.post(f"/api/users/{target}/custodian", json=no_attest).json()["error"] == "attestation"
    grant = {"password": GOOD, "attest_centre_staff": True}
    assert cust.post(f"/api/users/{consultant}/custodian", json=grant).json()["error"] == "consultant"
    assert cust.post(f"/api/users/{station.ids['admin1']}/custodian", json=grant).status_code == 409
    r = cust.post(f"/api/users/{target}/custodian", json=grant)
    assert r.status_code == 200 and r.json()["roles"] == ["custodian", "reviewer"]
    [(by, who, details)] = _events(station, "custodian.granted")
    assert by == station.ids["custodian1"] and who == target and '"attested_centre_staff":true' in details
    # the new role applies from the next login (sessions ended)
    assert clients["reviewer"].get("/api/auth/me").status_code == 401
    assert "key.manage" in station.login("reviewer1").get("/api/auth/me").json()["permissions"]


def test_user_lifecycle_and_last_admin_custodian(staffed: tuple[Station, dict[str, TestClient]]) -> None:
    station, clients = staffed
    admin = clients["admin"]
    admin_id, cust_id = station.ids["admin1"], station.ids["custodian1"]
    assert admin.post(f"/api/users/{admin_id}/disable").json()["error"] == "last_admin"
    assert (
        admin.put(f"/api/users/{admin_id}/roles", json={"roles": ["reviewer"]}).json()["error"]
        == "last_admin"
    )
    assert admin.post(f"/api/users/{cust_id}/disable").json()["error"] == "last_custodian"
    r = admin.put(f"/api/users/{admin_id}/roles", json={"roles": ["admin", "custodian"]})
    assert r.status_code == 403  # custodian is never assigned by an admin
    r = admin.post("/api/users", json={"username": "multi1", "roles": ["operator", "reviewer", "auditor"]})
    assert r.status_code == 200 and r.json()["user"]["must_change_password"] is True
    multi, temp = r.json()["user"]["id"], r.json()["temporary_password"]
    assert admin.post("/api/users", json={"username": "multi1", "roles": ["operator"]}).status_code == 409
    assert (
        admin.post("/api/users", json={"username": "x2", "roles": ["admin", "custodian"]}).status_code == 400
    )
    r = admin.put(f"/api/users/{multi}/roles", json={"roles": ["reviewer"]})
    assert r.json()["roles"] == ["reviewer"]
    assert admin.post(f"/api/users/{multi}/disable").json()["disabled"] is True
    c = station.new_client()
    assert c.post("/api/auth/login", json={"username": "multi1", "password": temp}).status_code == 401
    admin.post(f"/api/users/{multi}/enable")
    new_temp = admin.post(f"/api/users/{multi}/reset-password").json()["temporary_password"]
    assert c.post("/api/auth/login", json={"username": "multi1", "password": temp}).status_code == 401
    r = c.post("/api/auth/login", json={"username": "multi1", "password": new_temp})
    assert r.json()["stage"] == "password_change"
    listed = {u["username"]: u for u in admin.get("/api/users").json()}
    assert listed["multi1"]["roles"] == ["reviewer"] and "pw_hash" not in listed["multi1"]
    for action in ("user.created", "user.changed", "user.disabled", "user.enabled", "user.password_reset"):
        assert _events(station, action), action


def test_input_roots_and_separation_of_duties(staffed: tuple[Station, dict[str, TestClient]]) -> None:
    station, clients = staffed
    admin = clients["admin"]
    good = station.root / "drive"
    good.mkdir()
    bad = [["relative/path"], [str(station.root / "missing")], [str(station.root / "app_data")]]
    for roots in bad:
        assert admin.put("/api/settings/input-roots", json={"input_roots": roots}).status_code == 400
    r = admin.put("/api/settings/input-roots", json={"input_roots": [str(good), str(good)]})
    assert r.json()["input_roots"] == [str(good.resolve())]
    assert admin.get("/api/settings").json() == {"input_roots": [str(good.resolve())], "sod_enabled": True}
    [(_, _, details)] = _events(station, "settings.changed")
    assert str(good) not in details and '"count":1' in details
    assert (
        admin.put("/api/settings/separation-of-duties", json={"enabled": False}).json()["sod_enabled"]
        is False
    )
    [(by, _, details)] = _events(station, "sod.toggled")
    assert by == station.ids["admin1"] and '"to":false' in details


def test_admin_reset_cli(station: Station, capsys: object) -> None:
    c = station.new_client()
    for _ in range(5):
        c.post("/api/auth/login", json={"username": "admin1", "password": BAD_GUESS})
    assert c.post("/api/auth/login", json={"username": "admin1", "password": GOOD}).status_code == 423
    os.environ["DEID_DATA_ROOT"] = str(station.root)
    try:
        assert cli(["admin-reset", "--username", "admin1"]) == 0
        assert cli(["admin-reset", "--username", "nobody"]) == 1
    finally:
        del os.environ["DEID_DATA_ROOT"]
    out = capsys.readouterr().out  # type: ignore[attr-defined]
    temp = out.strip().splitlines()[1]
    r = c.post("/api/auth/login", json={"username": "admin1", "password": temp})
    assert r.status_code == 200 and r.json()["stage"] == "password_change"
    [(by, _, _)] = _events(station, "user.admin_reset")
    assert by == "cli"


def test_wrong_reauth_counts_towards_the_lockout(staffed: tuple[Station, dict[str, TestClient]]) -> None:
    station, clients = staffed
    cust = clients["custodian"]
    for _ in range(4):
        assert cust.post("/api/key/backup", json={"password": BAD_GUESS}).status_code == 403
    assert cust.post("/api/key/backup", json={"password": BAD_GUESS}).status_code == 423
    assert cust.get("/api/auth/me").status_code == 401  # locked: the session ends
    c = station.new_client()
    good = {"username": "custodian1", "password": station.passwords["custodian1"]}
    assert c.post("/api/auth/login", json=good).status_code == 423
    clients["admin"].post(f"/api/users/{station.ids['custodian1']}/unlock")
    assert c.post("/api/auth/login", json=good).status_code == 200
