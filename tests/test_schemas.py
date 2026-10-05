"""Rule files load from docs/schemas only, with the expected shape (TR-CC-01, TR-CC-02; SPEC §6.0, §6.4)."""

import json
import shutil
from pathlib import Path

import pytest

from app.deid.schemas import (
    SCHEMA_DIR,
    SchemaError,
    load_allowlist,
    load_exclusion_rules,
    load_finding_categories,
)


def test_allowlist_counts() -> None:  # TR-CC-01
    a = load_allowlist()
    assert len(a.core) == 54 and len(a.generated) == 15
    assert {k: len(v) for k, v in a.profiles.items()} == {"CT": 4, "MR": 14, "US": 4, "XR": 3}
    assert len(a.us_region_items) == 13


def test_tags_for_modality_profile() -> None:
    a = load_allowlist()
    assert len(a.tags_for("CT")) == 54 + 4 + 15
    assert len(a.tags_for("DX")) == 54 + 3 + 15
    assert len(a.tags_for("MG")) == 54 + 15  # D7 open: core only
    us_regions = a.profiles["US"][-1]["tag"]
    assert us_regions in a.tags_for("US") and us_regions not in a.tags_for("CT")


def test_exclusion_rules_and_categories_load() -> None:  # TR-CC-02
    rules = load_exclusion_rules()
    assert rules["rules_version"] and rules["rules"][0]["reason"] == "PRE_APPROVAL_REAL_DATA"
    cats = load_finding_categories()
    assert any(c["code"] == "NORMAL" for c in cats["categories"])


def test_wrong_count_rejected(tmp_path: Path) -> None:
    shutil.copytree(SCHEMA_DIR, tmp_path, dirs_exist_ok=True)
    path = tmp_path / "dicom_allowlist.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["core"].append(dict(data["core"][0]))
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(SchemaError):
        load_allowlist(tmp_path)


def test_missing_file_rejected(tmp_path: Path) -> None:
    with pytest.raises(SchemaError):
        load_exclusion_rules(tmp_path)
