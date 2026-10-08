"""Unit tests: password policy, roles, TOTP, audit registry, list parsing and the PHI log filter.
TR-SEC-01..03, TR-ROLE-01, TR-LIST-01."""

from __future__ import annotations

import logging
from pathlib import Path

import pyotp
import pytest

from app.auth import passwords as pw
from app.auth import totp
from app.auth.errors import AppError
from app.auth.permissions import RoleError, has_permission, needs_totp, validate_roles
from app.deid import logsafe
from app.deid import pseudonyms as ps
from app.services.governance.lists import parse_csv
from app.store import audit
from app.store.db import init_db
from tests.conftest import PHI

KEY = bytes(range(32))


@pytest.mark.parametrize(
    "candidate",
    ["Short-1a!", "alllowercase-12!", "ALLUPPERCASE-12!", "NoDigitsHere-!!", "NoSpecials12345a", "x" * 129],
)
def test_password_policy_rejects(candidate: str) -> None:
    with pytest.raises(pw.PasswordPolicyError) as err:
        pw.check_policy(candidate)
    assert candidate not in str(err.value)


def test_password_policy_accepts_and_hashes_argon2id() -> None:
    pw.check_policy("Synthetic-Demo-2026!", "admin1")
    with pytest.raises(pw.PasswordPolicyError):
        pw.check_policy("Admin1-is-My-Pass!", "admin1")
    h = pw.hash_password("Synthetic-Demo-2026!")
    assert h.startswith("$argon2id$") and pw.verify_password(h, "Synthetic-Demo-2026!")
    assert not pw.verify_password(h, "Synthetic-Demo-2026?") and not pw.verify_password(None, "anything")
    for _ in range(20):
        pw.check_policy(pw.generate_temporary())


def test_roles() -> None:
    assert validate_roles(["operator", "reviewer"]) == {"operator", "reviewer"}
    for bad in (["admin", "custodian"], [], ["owner"]):
        with pytest.raises(RoleError):
            validate_roles(bad)
    assert needs_totp({"custodian"}) and needs_totp({"admin", "reviewer"}) and not needs_totp({"auditor"})
    assert has_permission({"operator", "reviewer"}, "records.review")
    assert not has_permission({"admin"}, "key.manage") and not has_permission({"admin"}, "audit.view")


def test_totp_window_replay_and_backup_codes() -> None:
    secret, t = pyotp.random_base32(), 1_900_000_000.0
    code = pyotp.TOTP(secret).at(t)
    step = totp.matching_step(secret, code, None, now=t)
    assert step == int(t // 30)
    assert totp.matching_step(secret, code, step, now=t) is None  # replay
    assert totp.matching_step(secret, pyotp.TOTP(secret).at(t - 30), None, now=t) is not None  # one step back
    assert totp.matching_step(secret, pyotp.TOTP(secret).at(t - 90), None, now=t) is None
    assert totp.matching_step(secret, "12345x", None, now=t) is None
    codes = totp.new_backup_codes()
    assert len(codes) == 10 and len(set(codes)) == 10
    h = totp.hash_backup_code(codes[0])
    assert totp.verify_backup_code(h, codes[0].lower()[:5] + "-" + codes[0][5:])
    assert not totp.verify_backup_code(h, codes[1])


# SPEC §7 audit list, phrase by phrase, and the registered action names that implement each phrase.
SPEC_7 = {
    "login success/fail, logout": {"auth.login_success", "auth.login_failure", "auth.logout"},
    "user created/changed/disabled": {"user.created", "user.changed", "user.disabled"},
    "custodian.granted": {"custodian.granted"},
    "settings changed": {"settings.changed"},
    "ethics.config_changed": {"ethics.config_changed"},
    "preapproval.refused_file": {"preapproval.refused_file"},
    "job started/cancelled/finished": {"job.started", "job.cancelled", "job.finished"},
    "exclusion.recorded": {"exclusion.recorded"},
    "record approved/rejected/excluded/finalised/withdrawn": {
        "record.approved", "record.rejected", "record.excluded", "record.finalised", "record.withdrawn"},
    "list.imported": {"list.imported"},
    "review_mode.changed": {"review_mode.changed"},
    "licensee.changed": {"licensee.changed"},
    "gate.passed/gate.failed": {"gate.passed", "gate.failed"},
    "release.created": {"release.created"},
    "reid.recorded": {"reid.recorded"},
    "breach.opened/ec_notified/closed": {"breach.opened", "breach.ec_notified", "breach.closed"},
    "export created": {"export.created"},
    "key backed up/rotated": {"key.backed_up", "key.rotated"},
    "separation-of-duties toggled": {"sod.toggled"},
}  # fmt: skip


def test_audit_refuses_unknown_actions_and_covers_spec_events(tmp_path: Path) -> None:
    assert set().union(*SPEC_7.values()) == audit.SPEC_EVENTS
    assert not audit.SPEC_EVENTS & audit.APP_EVENTS
    db = init_db(tmp_path / "app.db")
    with db.transaction() as s, pytest.raises(audit.AuditError):
        audit.append_event(s, "made.up", "x", "y")
    db.dispose()


def test_list_csv_parsing() -> None:
    entries = parse_csv(b"\xef\xbb\xbfUHID,Consent,consent_date\nDEMO-L-1,yes,2026-09-01\nDEMO-L-2,No,\n",
                        "CONSENT", KEY)  # fmt: skip
    assert entries == [(ps.patient_code(KEY, "DEMO-L-1"), "Yes", "2026-09-01"),
                       (ps.patient_code(KEY, "DEMO-L-2"), "No", None)]  # fmt: skip
    for body, code in ((b"name\nx\n", "list_format"), (b"uhid,consent\nDEMO-L-3,maybe\n", "list_format"),
                       (b"uhid\n", "list_format"), (b"\xff\xfe", "list_format")):  # fmt: skip
        with pytest.raises(AppError) as err:
            parse_csv(body, "CONSENT", KEY)
        assert err.value.code == code and "DEMO-L-3" not in err.value.message
    dupes = parse_csv(b"uhid\nDEMO-L-1\nDEMO-L-1\n", "OPT_OUT", KEY)
    assert len(dupes) == 1


PLANTED_LINES = [
    "matched UHID-778812 to study",
    "call 98220 45671 for results",
    "aadhaar 4821 7730 1192 on file",
    "referred by Dr. Kulkarni today",
    "Patient Name: Lakshmi Iyer",
    "reading C:\\inbox\\Sharma_Ramesh\\IM0001",
    "reading /Users/staff/inbox/Patel/IM0001",
    "original uid 1.3.6.1.4.1.5962.1.2.3.4",
    "mail lakshmi.iyer@example.com",
]


@pytest.mark.parametrize("message", PLANTED_LINES)
def test_log_filter_suppresses_identifiers(message: str, caplog: pytest.LogCaptureFixture) -> None:
    logsafe.install()
    caplog.set_level(logging.INFO)
    logging.getLogger("app.test").info(message)
    logging.getLogger("uvicorn.error").warning("%s", message)  # args are checked after formatting
    assert caplog.records and all(logsafe.SUPPRESSED.split("(")[0] in r.getMessage() for r in caplog.records)
    assert not [p for p in PHI if p.lower() in caplog.text.lower()]


def test_log_filter_keeps_clean_messages_and_drops_tracebacks(caplog: pytest.LogCaptureFixture) -> None:
    logsafe.install()
    caplog.set_level(logging.INFO)
    log = logging.getLogger("app.test")
    clean = [
        "record S7DB2DCB7C0A0 finalised: 3 images, job J-20261008-01",
        '127.0.0.1:52344 - "GET /api/records?limit=50 HTTP/1.1" 200',
        "uid 2.25.906512345678901234 and transfer syntax 1.2.840.10008.1.2.1",
    ]
    for msg in clean:
        log.info(msg)
    try:
        raise ValueError("bad header for Sharma")
    except ValueError:
        log.exception("ingest failed for S7DB2DCB7C0A0")
    msgs = [r.getMessage() for r in caplog.records]
    assert msgs[:3] == clean
    assert "suppressed" in msgs[3] and "Sharma" not in caplog.text
    assert logsafe.find_identifier("ID12 lookalike 1234 5678 9012") == "ID12"
