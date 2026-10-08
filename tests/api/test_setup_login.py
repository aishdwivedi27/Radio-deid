"""First run, sign-in, sessions, CSRF and lockout (SPEC §7). TR-SEC-01, TR-ROLE-02, TR-COH-05."""

from __future__ import annotations

import pytest

from app.auth import sessions as sess
from app.store.repos import users as repo
from tests.api.conftest import BAD_GUESS, BASE, GOOD, Station, run_setup


def _actions(st: Station) -> list[str]:
    from app.store.models.audit import AuditEvent

    with st.ctx.db.session() as s:
        return [e.action for e in s.query(AuditEvent).order_by(AuditEvent.id)]


def test_first_run_only_setup_works_then_closes_for_good(fresh: Station) -> None:
    c = fresh.client
    assert c.get("/api/status").json()["setup_required"] is True
    assert c.get("/api/users").status_code == 503
    assert c.post("/api/auth/login", json={"username": "x", "password": "y"}).status_code == 503
    assert c.get("/api/health").status_code == 200
    run_setup(fresh)
    status = c.get("/api/status").json()
    assert status == {
        "setup_required": False,
        "setup_step": "done",
        "preapproval": True,
        "banner": "Pre-approval mode — synthetic data only.",
    }
    for path in ("/api/setup/admin", "/api/setup/custodian", "/api/setup/key"):
        r = c.post(path, json={"username": "late", "password": GOOD})
        assert r.status_code == 410, path
    assert fresh.ctx.paths.key_path.exists()
    assert {"user.created", "totp.enrolled", "key.created", "setup.completed"} <= set(_actions(fresh))


def test_setup_order_custodian_attestation_and_token(fresh: Station) -> None:
    c = fresh.client
    r = c.post("/api/setup/custodian", json={"username": "cust", "password": GOOD})
    assert r.status_code == 409  # the admin comes first
    r = c.post("/api/setup/admin", json={"username": "boss", "password": "short"})
    assert r.status_code == 400 and "short" not in r.text
    r = c.post("/api/setup/admin", json={"username": "boss", "password": GOOD})
    secret = r.json()["totp_secret"]
    other = fresh.new_client()  # a different browser without the setup cookie
    assert other.post("/api/setup/admin/totp", json={"code": fresh.clock.code(secret)}).status_code == 403
    assert c.post("/api/setup/admin/totp", json={"code": fresh.clock.code(secret)}).status_code == 200
    r = c.post("/api/setup/custodian", json={"username": "cust", "password": GOOD})
    assert r.status_code == 400 and r.json()["error"] == "attestation"
    r = c.post(
        "/api/setup/custodian", json={"username": "BOSS", "password": GOOD, "attest_not_consultant": True}
    )
    assert r.status_code == 409  # same person (case-insensitive username)


def test_login_requires_totp_and_rotates_the_session(station: Station) -> None:
    c = station.new_client()
    r = c.post("/api/auth/login", json={"username": "admin1", "password": GOOD})
    assert r.json()["stage"] == "mfa_pending"
    pre = c.cookies.get("deid_session")
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    assert c.get("/api/users").status_code == 403  # not active yet
    r = c.post("/api/auth/totp", json={"code": station.code("admin1")})
    assert r.json()["stage"] == "active"
    post = c.cookies.get("deid_session")
    assert pre and post and pre != post
    with station.ctx.db.session() as s:
        assert repo.get_session(s, sess.token_id(pre)) is None  # the old ID is gone
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    assert c.get("/api/users").status_code == 200
    me = c.get("/api/auth/me").json()
    assert me["roles"] == ["admin"] and "users.manage" in me["permissions"]
    assert "key.manage" not in me["permissions"]


def test_totp_replay_and_backup_code_used_once(station: Station) -> None:
    c = station.new_client()
    r = c.post("/api/auth/login", json={"username": "admin1", "password": GOOD})
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    code = station.code("admin1")
    assert c.post("/api/auth/totp", json={"code": code}).status_code == 200
    c2 = station.new_client()
    r = c2.post("/api/auth/login", json={"username": "admin1", "password": GOOD})
    c2.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    assert c2.post("/api/auth/totp", json={"code": code}).status_code == 401  # replay
    # backup codes: re-enrol through admin-reset to learn a fresh set
    from app.services.auth import login

    with station.ctx.db.session() as s:
        uid = repo.by_username(s, "admin1").id  # type: ignore[union-attr]
    login.start_enrolment(station.ctx, uid, may_replace=True)
    with station.ctx.db.session() as s:
        pending = repo.get_user(s, uid).totp_pending_secret  # type: ignore[union-attr]
    codes = login.confirm_enrolment(station.ctx, uid, station.clock.code(pending or ""))
    station.secrets["admin1"] = pending or ""
    for expected in (200, 401):
        c3 = station.new_client()
        r = c3.post("/api/auth/login", json={"username": "admin1", "password": GOOD})
        c3.headers["X-CSRF-Token"] = r.json()["csrf_token"]
        assert c3.post("/api/auth/totp", json={"code": codes[0]}).status_code == expected
    assert "backup_code.used" in _actions(station)


def test_lockout_after_five_failures_for_fifteen_minutes(
    station: Station, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = [2_000_000_000]
    monkeypatch.setattr(sess, "now", lambda: now[0])
    c = station.new_client()
    for _ in range(4):
        assert (
            c.post("/api/auth/login", json={"username": "admin1", "password": BAD_GUESS}).status_code == 401
        )
    r = c.post("/api/auth/login", json={"username": "admin1", "password": BAD_GUESS})
    assert r.status_code == 423
    assert c.post("/api/auth/login", json={"username": "admin1", "password": GOOD}).status_code == 423
    now[0] += 15 * 60 + 1
    assert c.post("/api/auth/login", json={"username": "admin1", "password": GOOD}).status_code == 200
    # an unknown user gets the same answer and the typed name is not audited
    r = c.post("/api/auth/login", json={"username": "nobody-typed-this", "password": GOOD})
    assert r.status_code == 401 and r.json()["message"] == "Invalid username or password."
    with station.ctx.db.session() as s:
        from app.store.models.audit import AuditEvent

        blob = " ".join(e.details_json + e.target_id for e in s.query(AuditEvent))
    assert "nobody-typed-this" not in blob and "Wrong-Pass" not in blob


def test_session_idle_and_absolute_expiry(station: Station, monkeypatch: pytest.MonkeyPatch) -> None:
    now = [2_000_000_000]
    monkeypatch.setattr(sess, "now", lambda: now[0])
    c = station.login("custodian1")
    now[0] += 14 * 60
    assert c.get("/api/auth/me").status_code == 200
    now[0] += 15 * 60 + 1
    assert c.get("/api/auth/me").status_code == 401  # idle
    c = station.login("custodian1")
    start = now[0]
    for _ in range(45):  # keep it busy in 12-minute steps until the 8 h limit ends it
        now[0] += 12 * 60
        if c.get("/api/auth/me").status_code != 200:
            break
    assert sess.ABSOLUTE_SECONDS < now[0] - start <= sess.ABSOLUTE_SECONDS + 12 * 60


def test_csrf_host_origin_and_content_type(station: Station) -> None:
    c = station.login("admin1")
    token = c.headers.pop("X-CSRF-Token")
    body = {"username": "op9", "roles": ["operator"]}
    assert c.post("/api/users", json=body).json()["error"] == "csrf"
    assert c.post("/api/users", json=body, headers={"X-CSRF-Token": "x" * 43}).json()["error"] == "csrf"
    evil = {"X-CSRF-Token": token, "Origin": "http://evil.example"}
    assert c.post("/api/users", json=body, headers=evil).status_code == 403
    form = {"X-CSRF-Token": token, "Content-Type": "application/x-www-form-urlencoded"}
    assert c.post("/api/users", content=b"username=a", headers=form).status_code == 415
    empty_form = c.post("/api/users/u_missing/disable", headers=form)  # no body: the type does not matter
    assert empty_form.status_code == 404
    rebind = station.new_client()
    rebind.base_url = rebind.base_url.copy_with(host="attacker.example")  # type: ignore[attr-defined]
    assert rebind.get("/api/status").status_code == 400
    ok = {"X-CSRF-Token": token, "Origin": BASE}
    assert c.post("/api/users", json=body, headers=ok).status_code == 200


def test_validation_errors_never_echo_input(station: Station) -> None:
    r = station.client.post("/api/auth/login", json={"username": "UHID-0000-PLANTED"})
    assert r.status_code == 422 and "PLANTED" not in r.text and "password" in r.text


def test_admin_without_totp_must_enrol(station: Station) -> None:
    admin = station.login("admin1")
    r = admin.post("/api/users", json={"username": "admin2", "roles": ["admin"]})
    station.passwords["admin2"] = r.json()["temporary_password"]
    c = station.new_client()
    r = c.post("/api/auth/login", json={"username": "admin2", "password": station.passwords["admin2"]})
    assert r.json()["stage"] == "password_change"
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    r = c.post("/api/auth/password", json={"current_password": station.passwords["admin2"],
                                           "new_password": GOOD})  # fmt: skip
    assert r.json()["stage"] == "totp_enrol"
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    assert c.get("/api/users").status_code == 403  # admin powers wait for TOTP
    station.passwords["admin2"] = GOOD
    assert station.login("admin2").get("/api/users").status_code == 200


def test_logout_ends_the_session(station: Station) -> None:
    c = station.login("custodian1")
    assert c.post("/api/auth/logout").status_code == 200
    assert c.get("/api/auth/me").status_code == 401
