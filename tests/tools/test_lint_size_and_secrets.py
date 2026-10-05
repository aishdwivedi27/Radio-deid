"""pylint max-module-lines and the secret scan fail on planted violations (CLAUDE.md; lifecycle §4)."""

import json
import subprocess
import sys
from pathlib import Path

PYLINT_ARGS = ["--disable=all", "--enable=too-many-lines", "--max-module-lines=1000", "--score=n"]


def _pylint(path: Path) -> int:
    cmd = [sys.executable, "-m", "pylint", *PYLINT_ARGS, str(path)]
    return subprocess.run(cmd, capture_output=True).returncode


def test_pylint_fails_over_1000_lines(tmp_path: Path) -> None:
    big = tmp_path / "big.py"
    big.write_text("".join(f"x{i} = {i}\n" for i in range(1001)), encoding="utf-8")
    assert _pylint(big) != 0


def test_pylint_passes_small_module(tmp_path: Path) -> None:
    small = tmp_path / "small.py"
    small.write_text("x = 1\n", encoding="utf-8")
    assert _pylint(small) == 0


def test_secret_scan_finds_planted_key(tmp_path: Path) -> None:
    planted = tmp_path / "config.py"
    # Planted credentials, built at runtime so this test file itself holds no secret-shaped literal.
    key = "AKIA" + "Z7Q4M2K9" + "W3X8B5N1"
    planted.write_text(f'aws_access_key_id = "{key}"\n', encoding="utf-8")
    out = subprocess.run(
        [sys.executable, "-m", "detect_secrets", "scan", "--all-files", planted.name],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    ).stdout
    assert json.loads(out)["results"], "detect-secrets did not flag a planted AWS key"


def test_new_secret_not_in_baseline_fails() -> None:
    from tools.check_secrets import new_findings

    item = {"type": "AWS Access Key", "hashed_secret": "abc", "line_number": 1}
    found = {"results": {"app/x.py": [item]}}
    assert new_findings(found, {"results": {}}) == ["app/x.py:1: AWS Access Key"]
    assert new_findings(found, {"results": {"app/x.py": [item]}}) == []
