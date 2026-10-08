"""Roles and permissions: the only mapping (SPEC §2). TR-ROLE-01..04.

One permission per row of the SPEC §2 table, plus ``custodian.grant`` from the Custodian rules under it.
A user holds a set of roles; Admin and Custodian are never held by the same account (D-025).
"""

from __future__ import annotations

from collections.abc import Iterable

ADMIN, CUSTODIAN, OPERATOR, REVIEWER, AUDITOR = "admin", "custodian", "operator", "reviewer", "auditor"
ROLES: tuple[str, ...] = (ADMIN, CUSTODIAN, OPERATOR, REVIEWER, AUDITOR)
EXCLUSIVE_ROLES = frozenset({ADMIN, CUSTODIAN})  # different people (SPEC §7 first run, D-025)

PERMISSIONS: dict[str, frozenset[str]] = {
    # First-run setup; create/disable users; reset passwords; assign roles other than Custodian
    "users.manage": frozenset({ADMIN}),
    # Technical settings (input roots, output folder, OCR/NER, retention); separation of duties toggle
    "settings.technical": frozenset({ADMIN}),
    # Key: back up, view fingerprint, rotate
    "key.manage": frozenset({CUSTODIAN}),
    # Ethics configuration: approval reference, dates, cohort cap, notice window, legal-opinion flag
    "ethics.configure": frozenset({CUSTODIAN}),
    # Import patient lists (opt-out, consent, staff/VIP, medico-legal)
    "lists.import": frozenset({CUSTODIAN}),
    # Licensee register: DAC approval reference, EC notified date, deletion certificates
    "licensees.manage": frozenset({CUSTODIAN}),
    # Record re-identification test results
    "reid.record": frozenset({CUSTODIAN}),
    # Start a job, watch progress, cancel own job
    "jobs.run": frozenset({ADMIN, OPERATOR}),
    # See pending records (de-identified previews only)
    "pending.view": frozenset({ADMIN, OPERATOR, REVIEWER}),
    # Approve / reject / exclude (ethics) a record (separation of duties applies)
    "records.review": frozenset({ADMIN, REVIEWER}),
    # Create a release or re-identification sample (gate-enforced)
    "releases.create": frozenset({ADMIN, REVIEWER}),
    # Breach log
    "breaches.manage": frozenset({ADMIN, CUSTODIAN}),
    # Records list, record detail, download JSON/CSV
    "records.view": frozenset(ROLES),
    # Audit log view/export; EC annual report export
    "audit.view": frozenset({CUSTODIAN, AUDITOR}),
    # Custodian rules: granting Custodian needs an existing Custodian (password re-entry, CR-01)
    "custodian.grant": frozenset({CUSTODIAN}),
}


class RoleError(ValueError):
    """An invalid set of roles. The message names roles only."""


def has_permission(roles: Iterable[str], permission: str) -> bool:
    allowed = PERMISSIONS[permission]  # KeyError for an unknown name: a programming error
    return any(r in allowed for r in roles)


def validate_roles(roles: Iterable[str]) -> frozenset[str]:
    out = frozenset(roles)
    unknown = out - set(ROLES)
    if unknown:
        raise RoleError(f"unknown role(s): {', '.join(sorted(unknown))}")
    if not out:
        raise RoleError("a user needs at least one role")
    if EXCLUSIVE_ROLES <= out:
        raise RoleError("admin and custodian cannot be held by the same account")
    return out
