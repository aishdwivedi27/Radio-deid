"""A running station for job tests (Phase 4): setup done, one signed-in client per role, an inbox input root.

The worker thread is off by default; ``site.run()`` runs queued jobs in the test thread so results are
deterministic. ``live_site`` starts the real worker thread. OCR and NER are off unless a test turns them on
(the sample-inbox test does). Synthetic data only (SYNTHETIC-DEMO / DEMO-).
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import build_settings
from app.server import build_app
from app.services.context import ServiceContext
from app.services.jobs import run
from app.worker.runner import Worker
from tests.api.conftest import BASE, ROLE_USERS, Station, run_setup


@dataclass
class Site:
    station: Station
    clients: dict[str, TestClient]
    data: Path
    inbox: Path
    outside: Path
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def ctx(self) -> ServiceContext:
        return self.station.ctx

    def run(self) -> int:
        return Worker(self.ctx).run_pending()


def multipart(files: list[tuple[str, bytes]]) -> tuple[bytes, str]:
    """``path`` then ``file`` part for each file, in order (the upload contract)."""
    boundary = "deid" + secrets.token_hex(8)
    out = bytearray()
    for rel, content in files:
        out += f'--{boundary}\r\nContent-Disposition: form-data; name="path"\r\n\r\n'.encode()
        out += rel.encode("utf-8") + b"\r\n"
        out += f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="f"\r\n'.encode()
        out += b"Content-Type: application/octet-stream\r\n\r\n" + content + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def folder_files(folder: Path) -> list[tuple[str, bytes]]:
    return [
        (p.relative_to(folder).as_posix(), p.read_bytes()) for p in sorted(folder.rglob("*")) if p.is_file()
    ]


def upload(client: TestClient, files: list[tuple[str, bytes]]) -> Any:
    body, ctype = multipart(files)
    return client.post("/api/jobs/upload", content=body, headers={"Content-Type": ctype})


def sse(client: TestClient, job_id: str, last: int = 0) -> list[tuple[str, dict[str, Any], str | None]]:
    """Every message of the job's event stream until it ends: (type, data, id)."""
    out: list[tuple[str, dict[str, Any], str | None]] = []
    headers = {"Last-Event-ID": str(last)} if last else {}
    with client.stream("GET", f"/api/jobs/{job_id}/events", headers=headers) as r:
        assert r.status_code == 200, r.read()
        kind, ident = "", None
        for line in r.iter_lines():
            if line.startswith("id: "):
                ident = line[4:]
            elif line.startswith("event: "):
                kind = line[7:]
            elif line.startswith("data: "):
                out.append((kind, json.loads(line[6:]), ident))
                if kind in ("finished", "cancelled", "error"):
                    break
                kind, ident = "", None
    return out


def _site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, worker: bool) -> Iterator[Site]:
    monkeypatch.setattr(run, "OCR_ENABLED", False)
    monkeypatch.setattr(run, "NER_ENABLED", False)
    data, outside = tmp_path / "data", tmp_path / "outside"
    inbox = data / "inbox"
    inbox.mkdir(parents=True)
    outside.mkdir()
    settings = build_settings({"data_root": str(data), "port": 8765})
    app = build_app(settings, web_dist=tmp_path / "no-web", worker=worker)
    with TestClient(app, base_url=BASE) as client:
        st = Station(app, client, data)
        run_setup(st)
        admin = st.login("admin1")
        for role in ("operator", "reviewer", "auditor"):
            name = ROLE_USERS[role]
            r = admin.post("/api/users", json={"username": name, "roles": [role]})
            assert r.status_code == 200, r.text
            st.passwords[name], st.ids[name] = r.json()["temporary_password"], r.json()["user"]["id"]
        clients = {"admin": admin, "custodian": st.login("custodian1")}
        for role in ("operator", "reviewer", "auditor"):
            clients[role] = st.login(ROLE_USERS[role])
        yield Site(st, clients, data, inbox, outside)


@pytest.fixture
def site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Site]:
    yield from _site(tmp_path, monkeypatch, worker=False)


@pytest.fixture
def live_site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Site]:
    yield from _site(tmp_path, monkeypatch, worker=True)
