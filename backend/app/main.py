"""FastAPI application: project upload, schedule recompute and G-code export."""
from __future__ import annotations

import json
import os
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db
from .config import settings
from .gcode import render_gcode
from .scheduler import ContourInput, ScheduleResult, solve
from .svg_parser import SVGParseError, parse_svg

STATIC_DIR = os.getenv("STATIC_DIR", "/app/static")

app = FastAPI(title="Laser Cut Path Scheduler", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _startup() -> None:
    await db.connect()


@app.on_event("shutdown")
async def _shutdown() -> None:
    await db.close()


# --------------------------------------------------------------------------- schemas
class SettingsPatch(BaseModel):
    home_x: float | None = None
    home_y: float | None = None
    invert_y: bool | None = None
    laser_power: int = Field(default=None, ge=0, le=1000)
    travel_speed: int = Field(default=None, ge=0, le=100000)
    cut_speed: int = Field(default=None, ge=0, le=100000)
    safe_z: float = Field(default=None, ge=0, le=1000)
    preamble: str | None = None


# --------------------------------------------------------------------------- helpers
def _compute_from_svg(svg_text: str) -> tuple[list, ScheduleResult, tuple | None]:
    try:
        raw_contours, view_box = parse_svg(svg_text)
    except SVGParseError as exc:
        raise HTTPException(status_code=400, detail=f"SVG parse error: {exc}") from exc
    inputs = []
    for rc in raw_contours:
        from .geometry import find_self_intersections
        hits = find_self_intersections(rc.points, closed=rc.closed)
        inputs.append(ContourInput(
            id=rc.element_id, kind=rc.kind, points=rc.points,
            closed=rc.closed, self_intersections=hits))
    result = solve(inputs, home=(0.0, 0.0))
    return raw_contours, result, view_box


def _reschedule(svg_text: str, project_settings: dict) -> ScheduleResult:
    try:
        raw_contours, _ = parse_svg(svg_text)
    except SVGParseError as exc:
        raise HTTPException(status_code=400, detail=f"SVG parse error: {exc}") from exc
    from .geometry import find_self_intersections
    inputs = [
        ContourInput(
            id=rc.element_id, kind=rc.kind, points=rc.points,
            closed=rc.closed,
            self_intersections=find_self_intersections(rc.points, closed=rc.closed))
        for rc in raw_contours
    ]
    return solve(inputs, home=(project_settings["home_x"], project_settings["home_y"]))


def _contour_payload(row: Any, travel: dict[int, float] | None = None) -> dict:
    return {
        "id": row["contour_index"],
        "kind": row["kind"],
        "points": json.loads(row["points"]),
        "closed": bool(row["closed"]),
        "valid": bool(row["valid"]),
        "issues": json.loads(row["issues"]),
        "intersections": json.loads(row["intersections"]),
        "parent": row["parent"],
        "depth": row["depth"],
        "cut_index": row["cut_index"],
        "distance_from_previous": (travel or {}).get(row["contour_index"], 0.0),
    }


def _project_payload(row: Any, contour_rows: list[Any]) -> dict:
    schedule = json.loads(row["schedule"]) if row["schedule"] else None
    children_map: dict[int, list[int]] = {}
    travel: dict[int, float] = {}
    if schedule:
        for r in contour_rows:
            if r["parent"] is not None:
                children_map.setdefault(r["parent"], []).append(r["contour_index"])
        for seg in schedule["segments"]:
            if seg["type"] == "travel" and seg.get("contour_id") is not None:
                travel[seg["contour_id"]] = seg["length"]
    payloads = []
    for r in contour_rows:
        payload = _contour_payload(r, travel)
        payload["children"] = sorted(children_map.get(r["contour_index"], []))
        payloads.append(payload)
    return {
        "id": row["id"],
        "name": row["name"],
        "created_at": row["created_at"],
        "view_box": json.loads(row["view_box"]) if row["view_box"] else None,
        "settings": json.loads(row["settings"]),
        "contours": payloads,
        "schedule": schedule,
    }


# --------------------------------------------------------------------------- routes
@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/api/projects", status_code=201)
async def create_project(
    file: UploadFile = File(...),
    name: str | None = Form(default=None),
) -> dict:
    raw = await file.read()
    if len(raw) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="SVG file too large")
    svg_text = raw.decode("utf-8", errors="strict")
    default_name = name or os.path.splitext(file.filename or "drawing.svg")[0]
    _, result, view_box = _compute_from_svg(svg_text)
    project_id = await db.create_project(default_name, svg_text, view_box, result)
    row = await db.get_project(project_id)
    contour_rows = await db.get_contours(project_id)
    return _project_payload(row, contour_rows)


@app.get("/api/projects")
async def list_projects() -> list[dict]:
    rows = await db.list_projects()
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "created_at": r["created_at"],
            "contour_count": r["contour_count"],
            "invalid_count": r["invalid_count"] or 0,
        }
        for r in rows
    ]


@app.get("/api/projects/{project_id}")
async def get_project(project_id: int) -> dict:
    row = await db.get_project(project_id)
    if row is None:
        raise HTTPException(status_code=404, detail="project not found")
    return _project_payload(row, await db.get_contours(project_id))


@app.post("/api/projects/{project_id}/schedule")
async def reschedule(project_id: int) -> dict:
    row = await db.get_project(project_id)
    if row is None:
        raise HTTPException(status_code=404, detail="project not found")
    project_settings = json.loads(row["settings"])
    result = _reschedule(row["svg"], project_settings)
    await db.save_schedule(project_id, result)
    row = await db.get_project(project_id)
    return _project_payload(row, await db.get_contours(project_id))


@app.patch("/api/projects/{project_id}/settings")
async def patch_settings(project_id: int, patch: SettingsPatch) -> dict:
    row = await db.get_project(project_id)
    if row is None:
        raise HTTPException(status_code=404, detail="project not found")
    new_values = {k: v for k, v in patch.model_dump().items() if v is not None}
    merged = await db.update_settings(project_id, new_values)
    # Home point affects the schedule; recompute deterministically.
    result = _reschedule(row["svg"], merged)
    await db.save_schedule(project_id, result)
    row = await db.get_project(project_id)
    return _project_payload(row, await db.get_contours(project_id))


@app.get("/api/projects/{project_id}/gcode")
async def export_gcode(project_id: int) -> Response:
    row = await db.get_project(project_id)
    if row is None:
        raise HTTPException(status_code=404, detail="project not found")
    project_settings = json.loads(row["settings"])
    result = _reschedule(row["svg"], project_settings)
    code = render_gcode(
        result,
        project_name=row["name"],
        invert_y=project_settings["invert_y"],
        laser_power=project_settings["laser_power"],
        travel_speed=project_settings["travel_speed"],
        cut_speed=project_settings["cut_speed"],
        safe_z=project_settings["safe_z"],
        preamble=project_settings["preamble"],
    )
    safe_name = "".join(ch if ch.isalnum() or ch in "-_" else "_"
                        for ch in row["name"]) or f"project_{project_id}"
    return Response(
        content=code,
        media_type="text/x-gcode",
        headers={"Content-Disposition":
                 f'attachment; filename="{safe_name}.gcode"'},
    )


@app.delete("/api/projects/{project_id}", status_code=204)
async def delete_project(project_id: int) -> Response:
    row = await db.get_project(project_id)
    if row is None:
        raise HTTPException(status_code=404, detail="project not found")
    await db.delete_project(project_id)
    return Response(status_code=204)


# --------------------------------------------------------------------------- static SPA
if os.path.isdir(STATIC_DIR):
    app.mount("/assets",
              StaticFiles(directory=os.path.join(STATIC_DIR, "assets")),
              name="assets")

    @app.get("/")
    async def index() -> Response:
        with open(os.path.join(STATIC_DIR, "index.html"), "rb") as fh:
            return Response(content=fh.read(), media_type="text/html")

    @app.get("/{path:path}")
    async def spa_fallback(path: str) -> Response:
        # Never shadow API routes; serve existing static files, else index.
        if path.startswith("api/"):
            raise HTTPException(status_code=404)
        candidate = os.path.join(STATIC_DIR, path)
        if path and os.path.isfile(candidate):
            with open(candidate, "rb") as fh:
                return Response(content=fh.read())
        with open(os.path.join(STATIC_DIR, "index.html"), "rb") as fh:
            return Response(content=fh.read(), media_type="text/html")
