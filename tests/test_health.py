"""Phase 0 acceptance: GET /api/health returns {"status":"ok"}. TR-PLAT-01."""

from pathlib import Path

from fastapi.testclient import TestClient

from app.api.main import create_app


def test_health_ok() -> None:
    client = TestClient(create_app())
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_placeholder_home_when_frontend_not_built(tmp_path: Path) -> None:
    client = TestClient(create_app(web_dist=tmp_path))
    response = client.get("/")
    assert response.status_code == 200
    assert "De-identification Station" in response.text


def test_serves_built_frontend(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<html>built-ui</html>", encoding="utf-8")
    client = TestClient(create_app(web_dist=tmp_path))
    assert "built-ui" in client.get("/").text
    assert client.get("/api/health").json() == {"status": "ok"}
