"""Loader for the version-controlled rule files in ``docs/schemas`` (TR-CC-01, TR-CC-02).

This module is the only place that reads the allowlist, exclusion rules and finding categories.
Code never repeats their contents (CLAUDE.md non-negotiable 7). Pure: stdlib only.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "docs" / "schemas"
_TAG_RE = re.compile(r"^\([0-9A-F]{4},[0-9A-F]{4}\)$")


class SchemaError(ValueError):
    """A rule file is missing or does not have the expected shape."""


@dataclass(frozen=True)
class Allowlist:
    version: str
    core: tuple[dict[str, str], ...]
    profiles: dict[str, tuple[dict[str, str], ...]]
    modality_to_profile: dict[str, str]
    generated: tuple[dict[str, str], ...]
    us_region_items: tuple[dict[str, str], ...]

    def tags_for(self, modality: str) -> frozenset[str]:
        """CORE ∪ PROFILE[modality] ∪ GENERATED as tag strings (SPEC §6.0)."""
        profile = self.profiles.get(self.modality_to_profile.get(modality.upper(), ""), ())
        return frozenset(e["tag"] for e in (*self.core, *profile, *self.generated))


def _read(name: str, schema_dir: Path) -> dict[str, Any]:
    path = schema_dir / name
    if not path.is_file():
        raise SchemaError(f"missing rule file {name}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SchemaError(f"{name}: top level must be an object")
    return data


def _entries(name: str, items: Any, expected: int | None) -> tuple[dict[str, str], ...]:
    if not isinstance(items, list):
        raise SchemaError(f"{name}: expected a list")
    for item in items:
        if not isinstance(item, dict) or not _TAG_RE.match(str(item.get("tag", ""))):
            raise SchemaError(f"{name}: every entry needs a tag like (0008,0060)")
    if expected is not None and len(items) != expected:
        raise SchemaError(f"{name}: expected {expected} entries, found {len(items)}")
    return tuple(items)


def load_allowlist(schema_dir: Path = SCHEMA_DIR) -> Allowlist:
    data = _read("dicom_allowlist.json", schema_dir)
    for key in ("version", "core", "profiles", "generated", "modality_to_profile"):
        if key not in data:
            raise SchemaError(f"dicom_allowlist.json: missing '{key}'")
    counts = data.get("profile_counts", {})
    profiles = {
        name: _entries(f"profiles.{name}", items, counts.get(name))
        for name, items in data["profiles"].items()
    }
    return Allowlist(
        version=str(data["version"]),
        core=_entries("core", data["core"], data.get("core_count")),
        profiles=profiles,
        modality_to_profile=dict(data["modality_to_profile"]),
        generated=_entries("generated", data["generated"], data.get("generated_count")),
        us_region_items=_entries("us_region_item_allowlist", data.get("us_region_item_allowlist", []), None),
    )


def load_exclusion_rules(schema_dir: Path = SCHEMA_DIR) -> dict[str, Any]:
    data = _read("exclusion_rules.json", schema_dir)
    if "rules_version" not in data or not isinstance(data.get("rules"), list):
        raise SchemaError("exclusion_rules.json: needs rules_version and a rules list")
    orders = [rule.get("order") for rule in data["rules"]]
    if orders != sorted(orders):
        raise SchemaError("exclusion_rules.json: rules must be in precedence order")
    return data


def load_finding_categories(schema_dir: Path = SCHEMA_DIR) -> dict[str, Any]:
    data = _read("finding_categories.json", schema_dir)
    codes = [c.get("code") for c in data.get("categories", [])]
    if not codes or len(codes) != len(set(codes)):
        raise SchemaError("finding_categories.json: categories need unique codes")
    unknown = set(data.get("prefill_keywords", {})) - set(codes)
    if unknown:
        raise SchemaError("finding_categories.json: prefill keywords for unknown categories")
    return data


@lru_cache(maxsize=1)
def allowlist() -> Allowlist:
    return load_allowlist()
