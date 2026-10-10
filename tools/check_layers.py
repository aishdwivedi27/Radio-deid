"""Layer import rules (CLAUDE.md, Architecture; docs/ARCHITECTURE.md §2).

api → services → deid | store; worker → services; auth is cross-cutting. ``deid`` is pure: no web,
DB or network libraries. A violation fails the build.

Usage: python tools/check_layers.py [app_dir]
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

# Packages under app/ that each layer may import (its own package is always allowed).
ALLOWED: dict[str, set[str]] = {
    "api": {"services", "auth", "config"},
    "services": {"deid", "store", "auth", "config"},
    "deid": set(),
    "store": {"config"},
    "worker": {"services", "config"},
    "auth": {"store", "config"},
}
_WEB = {"fastapi", "starlette", "uvicorn"}
_DB = {"sqlalchemy", "alembic", "sqlite3"}
_NET = {"httpx", "requests", "urllib3", "aiohttp", "socket"}
# Third-party top-level modules each layer must never import.
FORBIDDEN_EXTERNAL: dict[str, set[str]] = {
    "api": _DB,
    "services": _WEB,
    "deid": _WEB | _DB | _NET,
    "store": _WEB | _NET,
    "worker": _WEB | _DB,
    "auth": _WEB | _NET,
}


def _module_name(app_dir: Path, path: Path) -> list[str]:
    rel = path.relative_to(app_dir.parent).with_suffix("")
    parts = list(rel.parts)
    return parts[:-1] if parts[-1] == "__init__" else parts


def _imports(tree: ast.AST, module: list[str], is_package: bool) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend((alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = module if is_package else module[:-1]
                base = base[: len(base) - (node.level - 1)]
                name = ".".join([*base, node.module] if node.module else base)
            else:
                name = node.module or ""
            # "from app import services" imports a package, so record each name.
            if name == "app":
                out.extend((f"app.{alias.name}", node.lineno) for alias in node.names)
            else:
                out.append((name, node.lineno))
    return out


def check(app_dir: Path) -> list[str]:
    violations: list[str] = []
    for path in sorted(app_dir.rglob("*.py")):
        module = _module_name(app_dir, path)
        if len(module) < 2 or module[1] not in ALLOWED:
            continue  # app/__init__, app/__main__, app/config, app/server: composition root
        layer = module[1]
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = path.relative_to(app_dir.parent).as_posix()
        for name, line in _imports(tree, module, path.name == "__init__.py"):
            parts = name.split(".")
            if parts[0] == "app":
                if len(parts) < 2 or parts[1] in {layer, "__version__"}:
                    continue
                if parts[1] not in ALLOWED[layer]:
                    violations.append(f"{rel}:{line}: layer '{layer}' must not import '{name}'")
            elif parts[0] in FORBIDDEN_EXTERNAL[layer]:
                violations.append(f"{rel}:{line}: layer '{layer}' must not import '{parts[0]}'")
    return violations


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    app_dir = Path(args[0]) if args else Path(__file__).resolve().parents[1] / "app"
    violations = check(app_dir)
    for v in violations:
        print(f"FAIL  {v}")
    print(f"layer imports: {len(violations)} violation(s)")
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
