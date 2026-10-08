"""Technical settings held in the DB: allowed input roots and separation of duties (SPEC §2, §7).
TR-ROLE-04, TR-SEC-03.

Admin only, each change audited (``settings.changed``, ``sod.toggled``). Input roots set here replace
``input_roots`` from settings.toml; each must be an existing absolute folder outside app_data and output.
The values are folder paths chosen by staff (not patient data); the audit event records only the count and
a hash of each path.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from sqlalchemy.orm import Session

from app.auth.errors import Actor, bad_request
from app.services.auth import access
from app.services.context import ServiceContext, now_iso
from app.store import audit
from app.store.repos import settings as cfg

INPUT_ROOTS = "input_roots"
SOD = "sod_enabled"


def sod_enabled(s: Session) -> bool:
    return cfg.get(s, SOD) != "false"  # on by default (SPEC §2 footnote)


def input_roots(ctx: ServiceContext, configured: tuple[Path, ...]) -> tuple[Path, ...]:
    with ctx.db.session() as s:
        stored = cfg.get(s, INPUT_ROOTS)
    return tuple(Path(p) for p in json.loads(stored)) if stored else configured


def _inside(child: Path, parent: Path) -> bool:
    return child == parent or parent in child.parents


def validate_roots(ctx: ServiceContext, roots: list[str]) -> list[Path]:
    out: list[Path] = []
    blocked = (ctx.paths.app_data_dir.resolve(), ctx.paths.output_root.resolve())
    for i, raw in enumerate(roots, start=1):
        p = Path(str(raw or "").strip())
        if not p.is_absolute():
            raise bad_request(f"input root {i} must be an absolute path", "input_roots")
        if not p.is_dir():
            raise bad_request(f"input root {i} does not exist or is not a folder", "input_roots")
        real = p.resolve()
        if any(_inside(real, b) or _inside(b, real) for b in blocked):
            raise bad_request(f"input root {i} overlaps app_data or output", "input_roots")
        if real not in out:
            out.append(real)
    if not out:
        raise bad_request("at least one input root is required", "input_roots")
    return out


def set_input_roots(ctx: ServiceContext, actor: Actor, roots: list[str]) -> list[str]:
    access.check(ctx, actor, "settings.technical", "settings.input_roots")
    clean = [str(p) for p in validate_roots(ctx, roots)]
    with ctx.db.transaction() as s:
        cfg.put(s, INPUT_ROOTS, json.dumps(clean), actor.user_id, now_iso())
        hashes = [hashlib.sha256(p.encode("utf-8")).hexdigest()[:16] for p in clean]
        details = {"key": INPUT_ROOTS, "count": len(clean), "path_hashes": hashes}
        audit.append_event(s, "settings.changed", "setting", INPUT_ROOTS, details, actor.user_id)
    return clean


def set_sod(ctx: ServiceContext, actor: Actor, enabled: bool) -> bool:
    access.check(ctx, actor, "settings.technical", "settings.sod")
    with ctx.db.transaction() as s:
        before = sod_enabled(s)
        cfg.put(s, SOD, "true" if enabled else "false", actor.user_id, now_iso())
        audit.append_event(s, "sod.toggled", "setting", SOD, {"from": before, "to": enabled}, actor.user_id)
    return enabled


def view(ctx: ServiceContext, actor: Actor, configured: tuple[Path, ...]) -> dict[str, object]:
    access.check(ctx, actor, "settings.technical", "settings.view")
    with ctx.db.session() as s:
        sod = sod_enabled(s)
    return {"input_roots": [str(p) for p in input_roots(ctx, configured)], "sod_enabled": sod}
