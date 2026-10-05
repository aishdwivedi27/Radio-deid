"""CI must not depend on git-ignored files (CLAUDE.md, Git and checks) and must cover all three OSes."""

import subprocess
from pathlib import Path

CI = Path(".github/workflows/ci.yml")
IGNORED_DOCS = [
    "docs/SPEC",
    "docs/TRS",
    "SPEC.md",
    "CLAUDE.md",
    "README.md",
    "prompts/",
    "docs/AI_DEV",
    "docs/PROGRESS",
    "docs/ARCHITECTURE",
    "docs/DECISIONS",
    "docs/DEV_SETUP",
    "docs/INSTALL",
]


def test_ci_covers_three_os(repo: Path) -> None:
    text = (repo / CI).read_text(encoding="utf-8")
    for os_name in ("windows-latest", "macos-latest", "ubuntu-latest"):
        assert os_name in text
    assert 'python-version: "3.12"' in text
    assert "--require-hashes" in text


def test_ci_reads_no_ignored_file(repo: Path) -> None:
    text = (repo / CI).read_text(encoding="utf-8")
    assert not [d for d in IGNORED_DOCS if d in text]
    assert "check_all.ps1 -CI" in text and "check_all.sh --ci" in text


def test_ci_scripts_and_locks_are_tracked(repo: Path) -> None:
    needed = [
        CI,
        Path("tools/check_all.sh"),
        Path("tools/check_all.ps1"),
        Path("requirements-dev.lock"),
        Path("web/package-lock.json"),
    ]
    for path in needed:
        result = subprocess.run(["git", "check-ignore", "-q", str(path)], cwd=repo)
        assert result.returncode == 1, f"{path} is git-ignored but CI needs it"
