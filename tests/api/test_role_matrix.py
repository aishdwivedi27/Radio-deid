"""Role matrix (SPEC §2): every protected route called as each role; allow/deny exactly as the table.
TR-ROLE-01..04. Also: the permission map equals the SPEC table, and no /api route is left unguarded.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.auth.permissions import PERMISSIONS
from tests.api.conftest import Station

ADM, CUS, OPS, REV, AUD = "admin", "custodian", "operator", "reviewer", "auditor"
# SPEC §2 table, written out by hand (one row per permission) so a change to either side fails here.
SPEC_TABLE: dict[str, set[str]] = {
    "users.manage": {
        ADM
    },  # First-run setup; create/disable users; reset passwords; roles other than Custodian
    "settings.technical": {ADM},  # Technical settings (input roots, output folder, OCR/NER, retention)
    "key.manage": {CUS},  # Key: back up, view fingerprint, rotate
    "ethics.configure": {CUS},  # Ethics configuration
    "lists.import": {CUS},  # Import patient lists
    "licensees.manage": {CUS},  # Licensee register
    "reid.record": {CUS},  # Record re-identification test results
    "jobs.run": {ADM, OPS},  # Start a job, watch progress, cancel own job
    "pending.view": {ADM, OPS, REV},  # See pending records (de-identified previews only)
    "records.review": {ADM, REV},  # Approve / reject / exclude (ethics) a record (* separation of duties)
    "releases.create": {ADM, REV},  # Create a release or re-identification sample (gate-enforced)
    "breaches.manage": {ADM, CUS},  # Breach log
    "records.view": {ADM, CUS, OPS, REV, AUD},  # Records list, record detail, download JSON/CSV
    "audit.view": {CUS, AUD},  # Audit log view/export; EC annual report export
    "custodian.grant": {CUS},  # Custodian rules: only an existing Custodian (with TOTP) grants Custodian
}

PUBLIC = {
    ("GET", "/api/health"), ("GET", "/api/status"), ("GET", "/api/setup"), ("POST", "/api/setup/key"),
    ("POST", "/api/setup/{role}"), ("POST", "/api/auth/login"),
}  # fmt: skip
SESSION_ONLY = {  # any signed-in session, no permission
    ("POST", "/api/auth/password"), ("POST", "/api/auth/logout"), ("GET", "/api/auth/me"),
}  # fmt: skip

# One call per protected route. Bodies are chosen so an allowed role gets a non-"forbidden" answer
# (200, 400, 404, 422 or 501) without changing anything that matters to the other calls.
CALLS: list[tuple[str, str, dict[str, Any]]] = [
    ("GET", "/api/users", {}),
    ("POST", "/api/users", {"json": {}}),
    ("PUT", "/api/users/u_missing/roles", {"json": {}}),
    ("POST", "/api/users/u_missing/disable", {}),
    ("POST", "/api/users/u_missing/enable", {}),
    ("POST", "/api/users/u_missing/unlock", {}),
    ("POST", "/api/users/u_missing/reset-password", {}),
    ("POST", "/api/users/u_missing/custodian", {"json": {}}),
    ("GET", "/api/settings", {}),
    ("PUT", "/api/settings/input-roots", {"json": {"input_roots": []}}),
    ("PUT", "/api/settings/separation-of-duties", {"json": {}}),
    ("GET", "/api/key/fingerprint", {}),
    ("POST", "/api/key/backup", {"json": {}}),
    ("POST", "/api/key/rotate", {"json": {}}),
    ("POST", "/api/ethics/approval", {"json": {}}),
    ("POST", "/api/lists/OPT_OUT", {"content": b"", "headers": {"Content-Type": "text/csv"}}),
    ("GET", "/api/audit", {}),
    ("GET", "/api/audit/export.csv", {}),
    ("GET", "/api/audit/verify", {}),
]


def _routes(app: Any) -> list[Any]:
    out = []
    for r in app.routes:
        out.extend(r.original_router.routes if hasattr(r, "original_router") else [r])
    return [r for r in out if getattr(r, "path", "").startswith("/api")]


def _permission(route: Any) -> str | None:
    for dep in route.dependant.dependencies:
        if hasattr(dep.call, "permission"):
            return str(dep.call.permission)
    return None


def _template(path: str, routes: list[Any], method: str) -> Any:
    for r in routes:
        if method in r.methods and r.path_regex.match(path):
            return r
    raise AssertionError(f"no route for {method} {path}")


def test_permission_map_is_the_spec_table() -> None:
    assert {k: set(v) for k, v in PERMISSIONS.items()} == SPEC_TABLE


def test_every_api_route_is_guarded(station: Station) -> None:
    routes = _routes(station.app)
    for r in routes:
        for method in r.methods:
            key = (method, r.path)
            if key in PUBLIC or key in SESSION_ONLY:
                continue
            assert _permission(r) is not None, f"{method} {r.path} has no require(...)"
    called = {(m, _template(p, routes, m).path) for m, p, _ in CALLS}
    guarded = {(m, r.path) for r in routes for m in r.methods if _permission(r)}
    assert called == guarded  # the matrix below covers every protected route


@pytest.mark.parametrize("role", [ADM, CUS, OPS, REV, AUD])
def test_role_matrix(staffed: tuple[Station, dict[str, TestClient]], role: str) -> None:
    station, clients = staffed
    client, routes = clients[role], _routes(station.app)
    for method, path, kw in CALLS:
        permission = _permission(_template(path, routes, method))
        assert permission is not None
        r = client.request(method, path, **kw)
        denied = (
            r.status_code == 403
            and r.headers.get("content-type", "").startswith("application/json")
            and (r.json().get("error") == "forbidden")
        )
        expected_denied = role not in SPEC_TABLE[permission]
        assert denied == expected_denied, f"{role} {method} {path} -> {r.status_code} {r.text[:120]}"


def test_denials_are_audited(staffed: tuple[Station, dict[str, TestClient]]) -> None:
    station, clients = staffed
    clients["operator"].get("/api/audit")
    auditor = clients["auditor"]
    rows = auditor.get("/api/audit", params={"action": "access.denied"}).json()
    assert any(r["target_id"] == "audit.view" and r["user_id"] == station.ids["operator1"] for r in rows), (
        rows
    )
