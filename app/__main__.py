"""Command line: ``python -m app <command>`` (SPEC §15.4)."""

from __future__ import annotations

import argparse
import sys

from app.config import ConfigError, load_settings


def _serve() -> int:
    import uvicorn

    from app.api.main import create_app

    settings = load_settings()
    uvicorn.run(
        create_app(settings=settings), host=settings.bind_address, port=settings.port, log_level="info"
    )
    return 0


def _doctor() -> int:
    settings = load_settings()
    print(f"PASS  python {sys.version_info.major}.{sys.version_info.minor}")
    print(f"PASS  bind {settings.bind_address}:{settings.port}")
    print("WARN  full self-check arrives in Phase 7 (SPEC §15.4)")
    return 0


def _reconcile() -> int:
    """Repair missing output rows/folders from the DB (SPEC §5.3 step 7). Prints counts only."""
    from app.services.startup import run_reconcile

    report = run_reconcile(load_settings())
    print(" ".join(f"{k}={v}" for k, v in report.as_dict().items()))
    for line in report.mismatches:
        print(f"MISMATCH {line}")
    return 1 if report.mismatches else 0


def _admin_reset(args: argparse.Namespace) -> int:
    """Unlock a user and set a temporary password (printed once). Audited as ``user.admin_reset`` by
    ``cli``. Whoever can run this already controls the app folder and the database file."""
    from app.auth.errors import AppError
    from app.services.auth.users import admin_reset
    from app.services.context import ServiceContext
    from app.services.startup import install_log_filter

    if not args.username:
        print("admin-reset needs --username", file=sys.stderr)
        return 2
    install_log_filter()
    ctx = ServiceContext.from_settings(load_settings())
    try:
        temporary = admin_reset(ctx, args.username, args.reset_totp)
    except AppError as exc:
        print(exc.message, file=sys.stderr)
        return 1
    finally:
        ctx.db.dispose()
    print("Account unlocked. Temporary password (shown once; must be changed at next login):")
    print(temporary)
    if args.reset_totp:
        print("The authenticator was removed; it must be enrolled again at the next login.")
    return 0


COMMANDS = {
    "serve": lambda _a: _serve(),
    "doctor": lambda _a: _doctor(),
    "reconcile": lambda _a: _reconcile(),
    "admin-reset": _admin_reset,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app")
    parser.add_argument("command", choices=sorted(COMMANDS))
    parser.add_argument("--username", help="admin-reset: the account to unlock")
    parser.add_argument(
        "--reset-totp", action="store_true", help="admin-reset: also remove the authenticator"
    )
    args = parser.parse_args(argv)
    try:
        return int(COMMANDS[args.command](args))
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
