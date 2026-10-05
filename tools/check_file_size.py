"""File-size check (CLAUDE.md, File size): warn over 400 lines, fail over 1,000.

Also warns on Python functions over 60 lines. Exempt: generated files, lockfiles, Alembic migrations,
JSON schemas, test fixtures and sample data.

Usage: python tools/check_file_size.py [root]
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

WARN_LINES = 400
FAIL_LINES = 1000
FUNC_WARN_LINES = 60
CODE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".ps1", ".sh", ".css"}
SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    "sample_inbox",
}
# Exempt paths (POSIX, relative to root). Prefix match.
EXEMPT = (
    "app/store/migrations/versions/",
    "docs/",
    "tests/fixtures/",
    "web/package-lock.json",
    "requirements.lock",
    "requirements-dev.lock",
)


def iter_code_files(root: Path) -> list[Path]:
    files = []
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts) or not path.is_file():
            continue
        if path.suffix in CODE_SUFFIXES and not rel.as_posix().startswith(EXEMPT):
            files.append(path)
    return sorted(files)


def long_functions(path: Path) -> list[tuple[str, int]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.end_lineno:
            length = node.end_lineno - node.lineno + 1
            if length > FUNC_WARN_LINES:
                out.append((node.name, length))
    return out


def check(root: Path) -> tuple[list[str], list[str]]:
    """Return (failures, warnings)."""
    failures: list[str] = []
    warnings: list[str] = []
    for path in iter_code_files(root):
        rel = path.relative_to(root).as_posix()
        lines = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
        if lines > FAIL_LINES:
            failures.append(f"{rel}: {lines} lines (hard cap {FAIL_LINES})")
        elif lines > WARN_LINES:
            warnings.append(f"{rel}: {lines} lines (target {WARN_LINES}; split before adding code)")
        if path.suffix == ".py":
            for name, length in long_functions(path):
                warnings.append(f"{rel}: function {name} is {length} lines (target {FUNC_WARN_LINES})")
    return failures, warnings


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    root = Path(args[0]) if args else Path(__file__).resolve().parents[1]
    failures, warnings = check(root)
    for w in warnings:
        print(f"WARN  {w}")
    for f in failures:
        print(f"FAIL  {f}")
    print(f"file size: {len(failures)} failure(s), {len(warnings)} warning(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
