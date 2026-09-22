"""End-to-end API checks using Starlette TestClient + a temp SQLite file."""
import io
import os
import sys
import tempfile

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_PATH"] = os.path.join(_tmp, "test.db")
os.environ["STATIC_DIR"] = "/nonexistent"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

SVG = b"""<?xml version="1.0"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 300">
  <rect x="0" y="0" width="200" height="200"/>
  <circle cx="100" cy="100" r="50"/>
  <circle cx="100" cy="100" r="20"/>
  <polygon points="250,10 290,90 250,90 290,10"/>
  <path d="M10,250 L50,280"/>
</svg>
"""


def main() -> int:
    with TestClient(app) as client:
        r = client.get("/api/health")
        assert r.status_code == 200 and r.json()["status"] == "ok"

        r = client.post(
            "/api/projects",
            files={"file": ("plate.svg", io.BytesIO(SVG), "image/svg+xml")},
            data={"name": "plate"},
        )
        assert r.status_code == 201, r.text
        p = r.json()
        pid = p["id"]
        assert p["name"] == "plate"
        assert len(p["contours"]) == 5
        invalids = [c for c in p["contours"] if not c["valid"]]
        assert len(invalids) == 2, invalids
        assert any("self-intersecting" in i for c in invalids for i in c["issues"])
        assert any("not closed" in i for c in invalids for i in c["issues"])

        # nesting: inner circles before outer rect
        order = p["schedule"]["order"]
        assert len(order) == 3
        by_id = {c["id"]: c for c in p["contours"]}
        pos = {cid: k for k, cid in enumerate(order)}
        assert pos[2] < pos[1] < pos[0]
        assert by_id[2]["children"] == []
        assert by_id[0]["children"] == [1]

        # children field + travel distance present on payload
        assert "children" in by_id[0]
        assert isinstance(by_id[1]["distance_from_previous"], float)

        # list endpoint
        r = client.get("/api/projects")
        assert r.status_code == 200 and r.json()[0]["id"] == pid
        assert r.json()[0]["invalid_count"] == 2

        # settings patch changes home -> recomputed schedule
        r = client.patch(f"/api/projects/{pid}/settings",
                         json={"home_x": 100, "home_y": 100, "laser_power": 180})
        assert r.status_code == 200, r.text
        assert r.json()["settings"]["laser_power"] == 180
        # nearest contour from (100,100) is inner circle -> it goes first
        assert r.json()["schedule"]["order"][0] == 2

        # validation error on bad power
        r = client.patch(f"/api/projects/{pid}/settings",
                         json={"laser_power": 9999})
        assert r.status_code == 422

        # gcode export reflects settings
        r = client.get(f"/api/projects/{pid}/gcode")
        assert r.status_code == 200
        assert r.headers["content-disposition"].startswith("attachment")
        body = r.text
        assert "M4 S180" in body
        assert "plate" in body
        assert body.strip().endswith("$H")

        # reschedule endpoint
        r = client.post(f"/api/projects/{pid}/schedule")
        assert r.status_code == 200 and r.json()["schedule"]

        # bad svg
        r = client.post("/api/projects",
                        files={"file": ("x.svg", io.BytesIO(b"<html/>"),
                                        "image/svg+xml")})
        assert r.status_code == 400

        r = client.post("/api/projects",
                        files={"file": ("x.svg", io.BytesIO(b"not xml"),
                                        "image/svg+xml")})
        assert r.status_code == 400

        # 404 paths
        assert client.get("/api/projects/9999").status_code == 404
        assert client.get("/api/projects/9999/gcode").status_code == 404
        assert client.delete("/api/projects/9999").status_code == 404

        # delete
        assert client.delete(f"/api/projects/{pid}").status_code == 204
        assert client.get(f"/api/projects/{pid}").status_code == 404

    print("ALL API TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
