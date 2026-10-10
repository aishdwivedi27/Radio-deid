"""Input roots and folder inputs (SPEC §6.5, §7). TR-ING-01, TR-ING-05.

- Each approved root keeps the identity of its volume (serial / UUID) and its path relative to the volume
  root. At job start it is found again by that identity, so a drive that comes back under another letter
  or mount path still works and a different drive at the old letter is refused (T34).
- A folder input must resolve (symlinks followed) to a place inside a root, and never inside or around
  ``output_root`` or ``app_data``. Errors carry a reason code, never the path.
- The walk never follows directory links or junctions and drops any file whose real path leaves the root.
  Inputs are only ever read.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from app.auth.errors import AppError
from app.services.context import ServiceContext, now_iso
from app.services.jobs.paths import has_dotdot
from app.store.repos import settings as cfg

INPUT_ROOTS = "input_roots"
ROOT_IDS = "input_root_ids"
DRIVE_MISSING = "Input drive not found — connect the approved drive."


@dataclass(frozen=True)
class RootIdentity:
    volume_id: str
    label: str
    rel: str  # path of the root relative to its volume's mount point (posix)
    removable: bool


@dataclass(frozen=True)
class Resolved:
    approved: Path  # as approved by the admin
    current: Path | None  # where it is now; None if the drive is not connected


@dataclass(frozen=True)
class FolderInput:
    root: Path  # the re-resolved root that holds the folder
    folder: Path  # the real path of the folder


def path_invalid(reason: str) -> AppError:
    return AppError(400, "path_invalid", f"The folder cannot be used ({reason}).")


def drive_missing() -> AppError:
    return AppError(409, "input_drive_missing", DRIVE_MISSING)


def _inside(child: Path, parent: Path) -> bool:
    return child == parent or parent in child.parents


def approved_roots(ctx: ServiceContext) -> list[Path]:
    with ctx.db.session() as s:
        stored = cfg.get(s, INPUT_ROOTS)
    if stored:
        return [Path(p) for p in json.loads(stored)]
    return list(ctx.settings.input_roots) if ctx.settings else []


def _identities(ctx: ServiceContext) -> dict[str, RootIdentity]:
    with ctx.db.session() as s:
        raw = cfg.get(s, ROOT_IDS)
    return {k: RootIdentity(**v) for k, v in json.loads(raw or "{}").items()}


def identity_of(ctx: ServiceContext, root: Path) -> RootIdentity | None:
    vol = ctx.volumes.volume_of(root)
    if vol is None or not vol.volume_id:
        return None
    try:
        rel = root.resolve().relative_to(vol.mount.resolve()).as_posix()
    except ValueError:
        return None
    return RootIdentity(vol.volume_id, vol.label, "" if rel == "." else rel, vol.removable)


def record_identities(ctx: ServiceContext, roots: list[Path], changed_by: str) -> None:
    """Store the volume identity of each root that is present now (admin approval, or first use)."""
    known = _identities(ctx)
    for root in roots:
        ident = identity_of(ctx, root) if root.is_dir() else None
        if ident is not None:
            known[str(root)] = ident
    with ctx.db.transaction() as s:
        body = json.dumps({k: asdict(v) for k, v in known.items()}, sort_keys=True)
        cfg.put(s, ROOT_IDS, body, changed_by, now_iso())


def _find(ctx: ServiceContext, approved: Path, ident: RootIdentity | None) -> Path | None:
    if ident is None:
        return approved.resolve() if approved.is_dir() else None
    if approved.is_dir():
        vol = ctx.volumes.volume_of(approved)
        if vol is not None and vol.volume_id == ident.volume_id:
            return approved.resolve()
    for vol in ctx.volumes.mounted():
        if vol.volume_id == ident.volume_id:
            candidate = vol.mount / ident.rel if ident.rel else vol.mount
            if candidate.is_dir():
                return candidate.resolve()
    return None


def resolve_roots(ctx: ServiceContext) -> list[Resolved]:
    """Every approved root, found again now (SPEC §6.5: re-resolved at job start)."""
    roots = approved_roots(ctx)
    idents = _identities(ctx)
    unknown = [r for r in roots if str(r) not in idents and r.is_dir()]
    if unknown:  # roots from settings.toml get their identity the first time they are seen
        record_identities(ctx, unknown, "system")
        idents = _identities(ctx)
    return [Resolved(r, _find(ctx, r, idents.get(str(r)))) for r in roots]


def _blocked(ctx: ServiceContext, real: Path) -> None:
    for base, reason in (
        (ctx.paths.output_root, "inside_output"),
        (ctx.paths.app_data_dir, "inside_app_data"),
    ):
        b = base.resolve()
        if _inside(real, b) or _inside(b, real):
            raise path_invalid(reason)


def _lexical(raw: str) -> Path:
    return Path(os.path.abspath(raw))


def validate_folder(ctx: ServiceContext, raw: str) -> FolderInput:
    """SPEC §7 folder input: see the module docstring for the order of the checks."""
    raw = (raw or "").strip()
    if not raw or not Path(raw).is_absolute():
        raise path_invalid("not_absolute")
    if has_dotdot(raw):
        raise path_invalid("dotdot")
    roots = resolve_roots(ctx)
    lexical = _lexical(raw)
    present = [r.current for r in roots if r.current is not None]
    under_present = [c for c in present if _inside(lexical, c)]
    if not under_present and any(r.current is None and _inside(lexical, r.approved) for r in roots):
        raise drive_missing()
    try:
        real = Path(raw).resolve(strict=True)
    except (OSError, RuntimeError):
        raise path_invalid("not_found") from None
    if not real.is_dir():
        raise path_invalid("not_folder")
    _blocked(ctx, real)
    holder = next((c for c in present if _inside(real, c)), None)
    if holder is None:
        raise path_invalid("symlink_escape" if under_present else "outside_roots")
    return FolderInput(holder, real)


def find_root(ctx: ServiceContext, root_index: int) -> Path:
    """The current location of approved root ``root_index``; ``drive_missing`` if it is not connected."""
    roots = resolve_roots(ctx)
    if not 0 <= root_index < len(roots):
        raise drive_missing()
    current = roots[root_index].current
    if current is None:
        raise drive_missing()
    return current


def root_index_of(ctx: ServiceContext, root: Path) -> int:
    for i, r in enumerate(resolve_roots(ctx)):
        if r.current == root:
            return i
    raise drive_missing()


def is_link(path: Path) -> bool:
    return os.path.islink(path) or os.path.isjunction(path)


@dataclass
class Walk:
    files: list[Path]
    links_outside: int = 0


def walk(folder: Path, root: Path) -> Walk:
    """Regular files under ``folder``; directory links/junctions are not entered; a linked file is kept only
    if its real path stays inside ``root``."""
    out = Walk([])
    real_root = root.resolve()
    for dirpath, dirnames, filenames in os.walk(folder, followlinks=False):
        here = Path(dirpath)
        dirnames[:] = sorted(d for d in dirnames if not is_link(here / d))
        for name in sorted(filenames):
            path = here / name
            if os.path.islink(path):
                try:
                    target = path.resolve(strict=True)
                except (OSError, RuntimeError):
                    out.links_outside += 1
                    continue
                if not _inside(target, real_root) or not target.is_file():
                    out.links_outside += 1
                    continue
            elif not path.is_file():
                continue
            out.files.append(path)
    return out
