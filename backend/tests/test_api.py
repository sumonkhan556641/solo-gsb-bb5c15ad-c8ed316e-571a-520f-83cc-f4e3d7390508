"""FastAPI 端到端集成测试（使用临时 SQLite 库）。"""
import os
import tempfile
import pathlib

import pytest

os.environ.setdefault("LASER_STATIC_DIR", "/nonexistent-static")
_TMP = tempfile.mkdtemp(prefix="laser-db-")
os.environ["LASER_DB_PATH"] = os.path.join(_TMP, "test.db")

from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.main import app  # noqa: E402

SVG = b'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
<rect x="0" y="0" width="100" height="100"/>
<circle cx="50" cy="50" r="10"/>
</svg>'''


@pytest.fixture(scope="module")
def client():
    db.init_db()
    with TestClient(app) as c:
        yield c


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_upload_solve_and_fetch(client):
    r = client.post(
        "/api/jobs",
        files={"file": ("t.svg", SVG, "image/svg+xml")},
        data={"settings": '{"home_x": 0, "home_y": 0}'},
    )
    assert r.status_code == 200
    d = r.json()
    assert d["stats"]["contour_count"] == 2
    # 内圆先切
    assert d["cuts"][0]["contour_index"] == 1
    jid = d["id"]

    detail = client.get(f"/api/jobs/{jid}").json()
    assert detail["status"] == "solved"
    assert len(detail["contours"]) == 2

    g = client.get(f"/api/jobs/{jid}/gcode")
    assert g.status_code == 200
    assert "attachment" in g.headers["content-disposition"]
    assert "G21" in g.text

    assert any(j["id"] == jid for j in client.get("/api/jobs").json())


def test_bad_upload(client):
    r = client.post(
        "/api/jobs",
        files={"file": ("bad.svg", b"garbage", "image/svg+xml")},
    )
    assert r.status_code == 422


def test_missing_job(client):
    assert client.get("/api/jobs/999999").status_code == 404
    assert client.get("/api/jobs/999999/gcode").status_code == 404
