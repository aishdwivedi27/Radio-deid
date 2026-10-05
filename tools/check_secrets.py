"""Secret scan (lifecycle §4; DECISIONS D-005). Fails on any secret not already reviewed in .secrets.baseline.

Scans every git-tracked file (lockfile hashes excluded). Prints file, line and detector only, never the value.

Usage: python tools/check_secrets.py            (check)
       python tools/check_secrets.py --update   (rewrite the baseline after a human review)
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
BASELINE = REPO / ".secrets.baseline"
EXCLUDE = r"(^|[/\\])(requirements(-dev)?\.lock|package-lock\.json|\.secrets\.baseline)$"


def scan(repo: Path = REPO) -> dict[str, Any]:
    out = subprocess.run(
        [sys.executable, "-m", "detect_secrets", "scan", "--exclude-files", EXCLUDE],
        cwd=repo,
        capture_output=True,
        check=True,
    ).stdout
    result: dict[str, Any] = json.loads(out)
    # POSIX paths so the baseline is identical on Windows, macOS and Linux.
    result["results"] = {name.replace("\\", "/"): items for name, items in result.get("results", {}).items()}
    for items in result["results"].values():
        for item in items:
            item["filename"] = item["filename"].replace("\\", "/")
    result.pop("generated_at", None)
    return result


def new_findings(found: dict[str, Any], baseline: dict[str, Any]) -> list[str]:
    known = {
        (name, item["hashed_secret"]) for name, items in baseline.get("results", {}).items() for item in items
    }
    return [
        f"{name}:{item.get('line_number', '?')}: {item['type']}"
        for name, items in found.get("results", {}).items()
        for item in items
        if (name, item["hashed_secret"]) not in known
    ]


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    found = scan()
    if "--update" in args:
        BASELINE.write_text(json.dumps(found, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(f"secret scan: baseline rewritten ({sum(len(v) for v in found['results'].values())} entries)")
        return 0
    baseline = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.is_file() else {}
    problems = new_findings(found, baseline)
    for p in problems:
        print(f"FAIL  {p}")
    print(f"secret scan: {len(problems)} new finding(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
