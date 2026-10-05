"""Traceability report (AI_DEVELOPMENT_LIFECYCLE §3; DECISIONS D-009). Local only: it reads git-ignored docs.

For every TR in the TRS (.docx), list its SPEC sections, code locations and tests. A TR's phase is the first
phase whose prompt lists it under "Implements". ``--phase N`` fails when a TR of phase <= N has no test;
``--strict`` fails when any TR has no test. Skips cleanly (exit 0) when the docs are absent, as in CI.

Usage: python tools/trace_report.py --phase 0 [--strict] [--verbose]
"""

from __future__ import annotations

import argparse
import re
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TR_RE = re.compile(r"TR-[A-Z]+(?:-NF)?-\d{2}(?:\.\.\d{2})?")
TAG_RE = re.compile(r"<[^>]+>")


@dataclass
class Trace:
    tr: str
    phase: int | None = None
    spec: set[str] = field(default_factory=set)
    code: set[str] = field(default_factory=set)
    tests: set[str] = field(default_factory=set)


def expand(token: str) -> list[str]:
    """TR-QA-01..03 -> TR-QA-01, TR-QA-02, TR-QA-03."""
    if ".." not in token:
        return [token]
    head, end = token.split("..")
    prefix, start = head.rsplit("-", 1)
    return [f"{prefix}-{n:02d}" for n in range(int(start), int(end) + 1)]


def find_trs(text: str) -> set[str]:
    return {tr for token in TR_RE.findall(text) for tr in expand(token)}


def trs_from_docx(path: Path) -> set[str]:
    with zipfile.ZipFile(path) as zf:
        xml = zf.read("word/document.xml").decode("utf-8")
    return find_trs(TAG_RE.sub(" ", xml))


def spec_sections(spec: Path) -> dict[str, set[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    heading = "(top)"
    for line in spec.read_text(encoding="utf-8").splitlines():
        title = line.lstrip("#").strip()
        if line.startswith("#") and title[:1].isdigit():
            heading = title.split(" ")[0].rstrip(".")
        for tr in find_trs(line):
            out[tr].add(heading)
    return out


def phase_map(prompts: Path) -> dict[str, int]:
    out: dict[str, int] = {}
    for path in sorted(prompts.glob("phase-*.md")):
        match = re.match(r"phase-(\d+)", path.name)
        if not match:
            continue
        phase = int(match.group(1))
        for line in path.read_text(encoding="utf-8").splitlines():
            if "Implements" in line:
                for tr in find_trs(line):
                    out[tr] = min(out.get(tr, phase), phase)
    return out


def scan(paths: list[Path], root: Path) -> dict[str, set[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    for path in paths:
        for tr in find_trs(path.read_text(encoding="utf-8", errors="replace")):
            out[tr].add(path.relative_to(root).as_posix())
    return out


def build(root: Path) -> list[Trace] | None:
    docs = root / "docs"
    trs_docs = sorted(docs.glob("TRS-RAD-001_*.docx"))
    spec = docs / "SPEC.md"
    if not trs_docs or not spec.is_file():
        return None
    all_trs = trs_from_docx(trs_docs[-1])
    sections = spec_sections(spec)
    phases = phase_map(root / "prompts")
    test_files = [*(root / "tests").rglob("*.py"), *(root / "web").glob("src/**/*.test.ts*")]
    tests = scan(test_files, root)
    code = scan(list((root / "app").rglob("*.py")), root)
    return [
        Trace(tr, phases.get(tr), sections.get(tr, set()), code.get(tr, set()), tests.get(tr, set()))
        for tr in sorted(all_trs)
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", type=int, default=0, help="last phase built; its TRs must have tests")
    parser.add_argument("--strict", action="store_true", help="fail on any TR without a test")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--root", type=Path, default=REPO)
    args = parser.parse_args(argv)
    traces = build(args.root)
    if traces is None:
        print("trace report: SKIPPED (TRS/SPEC not present; local-only check)")
        return 0
    missing: list[Trace] = []
    pending: list[Trace] = []
    covered: list[Trace] = []
    for t in traces:
        due = args.strict or (t.phase is not None and t.phase <= args.phase)
        (covered if t.tests else missing if due else pending).append(t)
    for t in covered + missing + (pending if args.verbose else []):
        status = "OK  " if t.tests else ("FAIL" if t in missing else "PEND")
        print(
            f"{status}  {t.tr:<14} phase {t.phase if t.phase is not None else '-'}  "
            f"spec {','.join(sorted(t.spec)) or '-'}  tests {','.join(sorted(t.tests)) or '-'}"
        )
    print(
        f"trace report: {len(traces)} TRs, {len(covered)} tested, {len(missing)} missing (due by phase "
        f"{args.phase}{', strict' if args.strict else ''}), {len(pending)} pending"
    )
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
