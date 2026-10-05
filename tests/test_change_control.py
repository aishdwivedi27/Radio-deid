"""Change control for the rule files (TR-CC-01, TR-CC-02, TR-CC-03; SPEC §6.0, §6.4, §13.4).

The hashes below pin docs/schemas/dicom_allowlist.json and exclusion_rules.json. If this test fails, the
file was changed: record the change in docs/ALLOWLIST_CHANGES.md or docs/RULES_CHANGES.md, get reviewer
sign-off, bump pipeline_version / rules_version, add an ec_amendment_ref when the change adds a tag or
loosens a rule, and only then update the pinned hash here.
"""

import hashlib
import json
import re
from pathlib import Path

from app.deid.schemas import SCHEMA_DIR

PINNED = {
    "dicom_allowlist.json": "e0e5e59c8eb6d40b3a90207e93ce7e75eaabc72b42468c76768e81333572f39b",
    "exclusion_rules.json": "af64bbdada5e5e9772fc7ee1e97859a6c402a7135dbe4971ba28e58863dd3abf",
}
LOADER = Path("app/deid/schemas.py")
TAG_LITERAL = re.compile(r"\(\s*[0-9A-Fa-f]{4}\s*,\s*[0-9A-Fa-f]{4}\s*\)|0x[0-9A-Fa-f]{8}\b")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_allowlist_pinned() -> None:  # TR-CC-01
    assert _sha256(SCHEMA_DIR / "dicom_allowlist.json") == PINNED["dicom_allowlist.json"], __doc__


def test_exclusion_rules_pinned() -> None:  # TR-CC-02
    assert _sha256(SCHEMA_DIR / "exclusion_rules.json") == PINNED["exclusion_rules.json"], __doc__


def test_no_second_copy_of_allowlist_in_code(repo: Path) -> None:  # TR-CC-01, CLAUDE.md rule 7
    offenders = [
        p.relative_to(repo).as_posix()
        for p in (repo / "app").rglob("*.py")
        if p.relative_to(repo) != LOADER and TAG_LITERAL.search(p.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"DICOM tag literals outside the schema loader: {offenders}"


def test_manifest_carries_rule_versions() -> None:  # TR-CC-03
    schema = json.loads((SCHEMA_DIR / "manifest.schema.json").read_text(encoding="utf-8"))
    assert {"allowlist_version", "rules_version", "pipeline_version"} <= set(schema["required"])
