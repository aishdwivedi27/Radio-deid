"""tools/check_layers.py fails on planted layer violations (CLAUDE.md, Architecture)."""

from pathlib import Path

import pytest

from tools import check_layers


def _app(tmp_path: Path, files: dict[str, str]) -> Path:
    app = tmp_path / "app"
    for layer in check_layers.ALLOWED:
        (app / layer).mkdir(parents=True, exist_ok=True)
        (app / layer / "__init__.py").write_text("", encoding="utf-8")
    (app / "__init__.py").write_text("", encoding="utf-8")
    for rel, code in files.items():
        (app / rel).write_text(code, encoding="utf-8")
    return app


@pytest.mark.parametrize(
    ("rel", "code"),
    [
        ("deid/bad.py", "import fastapi\n"),
        ("deid/bad.py", "from sqlalchemy import select\n"),
        ("deid/bad.py", "from app.store import repo\n"),
        ("deid/bad.py", "from ..services import jobs\n"),
        ("deid/bad.py", "import requests\n"),
        ("api/bad.py", "from app.store.repo import Repo\n"),
        ("api/bad.py", "import sqlalchemy\n"),
        ("store/bad.py", "from app.services import jobs\n"),
        ("worker/bad.py", "from app.deid import rebuild\n"),
        ("services/bad.py", "from app import api\n"),
        ("services/bad.py", "from fastapi import HTTPException\n"),
    ],
)
def test_planted_violation_fails(tmp_path: Path, rel: str, code: str) -> None:
    app = _app(tmp_path, {rel: code})
    assert check_layers.check(app), f"not caught: {rel}: {code!r}"
    assert check_layers.main([str(app)]) == 1


@pytest.mark.parametrize(
    ("rel", "code"),
    [
        (
            "api/ok.py",
            "from fastapi import APIRouter\nfrom app.services import jobs\nfrom app.auth import roles\n",
        ),
        ("services/ok.py", "from app.deid import rebuild\nfrom app.store import repo\nfrom . import other\n"),
        ("deid/ok.py", "import json\nimport hashlib\nfrom .schemas import load_allowlist\n"),
        ("store/ok.py", "import sqlalchemy\nfrom app.config import Settings\n"),
        ("worker/ok.py", "import threading\nfrom app.services import jobs\n"),
    ],
)
def test_allowed_imports_pass(tmp_path: Path, rel: str, code: str) -> None:
    assert check_layers.check(_app(tmp_path, {rel: code})) == []


def test_repo_passes() -> None:
    assert check_layers.main([]) == 0
