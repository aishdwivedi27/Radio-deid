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


COMMANDS = {"serve": _serve, "doctor": _doctor, "reconcile": _reconcile}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app")
    parser.add_argument("command", choices=sorted(COMMANDS))
    args = parser.parse_args(argv)
    try:
        return COMMANDS[args.command]()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
