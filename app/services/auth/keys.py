"""Key actions: Custodian only (SPEC §2, §7; TR-ROLE-01). T17.

Phase 3 (D-024): the fingerprint can be viewed. Backup and rotation check the role, the user's password
typed again (CR-01) and, for rotation, the typed ``ROTATE``; they audit the request and answer "not
available yet". The backup command and rotation arrive in Phase 7. The key itself is never returned or logged.
"""

from __future__ import annotations

from app.auth.errors import Actor, AppError, bad_request, not_available
from app.deid.keys import key_fingerprint, load_or_create_key
from app.services.auth import access
from app.services.context import ServiceContext
from app.store import audit

LATER = "Key backup and rotation are available in a later release (Phase 7)."


def fingerprint(ctx: ServiceContext, actor: Actor) -> str:
    access.check(ctx, actor, "key.manage", "key.fingerprint")
    if not ctx.paths.key_path.exists():
        raise AppError(404, "no_key", "No key has been created yet.")
    fp = key_fingerprint(load_or_create_key(ctx.paths.key_path))
    with ctx.db.transaction() as s:
        audit.append_event(s, "key.viewed", "key", fp, {}, actor.user_id)
    return fp


def request_backup(ctx: ServiceContext, actor: Actor, password: str | None) -> None:
    access.check(ctx, actor, "key.manage", "key.backup")
    access.confirm_password(ctx, actor, password, "key.manage", "key.backup")
    with ctx.db.transaction() as s:
        audit.append_event(s, "key.backup_requested", "key", "-", {"outcome": "not_available"}, actor.user_id)
    raise not_available(LATER)


def request_rotation(ctx: ServiceContext, actor: Actor, password: str | None, confirm: str | None) -> None:
    access.check(ctx, actor, "key.manage", "key.rotate")
    if confirm != "ROTATE":
        raise bad_request("type ROTATE to confirm", "confirm")
    access.confirm_password(ctx, actor, password, "key.manage", "key.rotate")
    with ctx.db.transaction() as s:
        audit.append_event(s, "key.rotate_requested", "key", "-", {"outcome": "not_available"}, actor.user_id)
    raise not_available(LATER)
