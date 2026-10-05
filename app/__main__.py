"""Command line: ``python -m app <command>`` (SPEC §15.4)."""

from __future__ import annotations

import argparse
import sys

from app.config import ConfigError, load_settings


def _serve() -> int:
    import uvicorn

    from app.api.main import create_app

    settings = load_settings()
    uvicorn.run(create_app(), host=settings.bind_address, port=settings.port, log_level="info")
    return 0


def _doctor() -> int:
    settings = load_settings()
    print(f"PASS  python {sys.version_info.major}.{sys.version_info.minor}")
    print(f"PASS  bind {settings.bind_address}:{settings.port}")
    print("WARN  full self-check arrives in Phase 7 (SPEC §15.4)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app")
    parser.add_argument("command", choices=["serve", "doctor"])
    args = parser.parse_args(argv)
    try:
        return _serve() if args.command == "serve" else _doctor()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
