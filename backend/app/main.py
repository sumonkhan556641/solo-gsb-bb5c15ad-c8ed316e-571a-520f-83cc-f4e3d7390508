"""FastAPI 入口：SVG 上传/解析、排程求解、预览与 G-code 下载。

启动后同时托管 /api 与前端静态文件（容器内单服务，端口 8000）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db
from .gcode import GcodeSettings, build_gcode
from .geometry import Contour, build_nesting, parse_svg
from .scheduler import solve_order

STATIC_DIR = Path(os.environ.get("LASER_STATIC_DIR", "/app/static"))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="激光切割路径排程", version="1.0.0", lifespan=lifespan)


# ---------------------------------------------------------------- 模型

class SolveSettings(BaseModel):
    home_x: float = 0.0
    home_y: float = 0.0
    return_home: bool = True
    flip_y: bool = False
    curve_tolerance: float = Field(0.5, gt=0.0, le=10.0)
    # G-code
    rapid_feed: float = Field(3000.0, gt=0.0)
    cut_feed: float = Field(800.0, gt=0.0)
    power: int = Field(255, ge=0, le=1000)


# ---------------------------------------------------------------- 序列化

def contour_to_dict(c: Contour) -> dict[str, Any]:
    return {
        "index": c.index,
        "source": c.source,
        "points": [[round(x, 4), round(y, 4)] for x, y in c.points],
        "closed": c.closed,
        "valid": c.valid,
        "self_intersecting": c.self_intersecting,
        "issues": c.issues,
        "area": round(c.area, 4),
        "length": round(c.length, 4),
        "bbox": [round(v, 4) for v in c.bbox],
        "parent": c.parent,
        "depth": c.depth,
        "order": c.order,
        "start": [round(c.start[0], 4), round(c.start[1], 4)] if c.start else None,
        "end": [round(c.end[0], 4), round(c.end[1], 4)] if c.end else None,
        "reversed": c.reversed,
    }


def run_pipeline(svg: bytes, settings: SolveSettings):
    contours, meta = parse_svg(
        svg, tolerance=settings.curve_tolerance, flip_y=settings.flip_y)
    build_nesting(contours)
    plan = solve_order(
        contours,
        home=(settings.home_x, settings.home_y),
        return_home=settings.return_home,
    )
    gsettings = GcodeSettings(
        rapid_feed=settings.rapid_feed,
        cut_feed=settings.cut_feed,
        power=settings.power,
        return_home=settings.return_home,
    )
    gcode = build_gcode(
        contours, plan, gsettings,
        meta_lines=(f"job contours {len(contours)}",
                    f"curve_tolerance={settings.curve_tolerance} mm"
                    + (" y-flipped" if settings.flip_y else "")),
    )
    result = {
        "contours": [contour_to_dict(c) for c in contours],
        "cuts": [vars(cut) for cut in plan.cuts],
        "stats": {
            "contour_count": len(contours),
            "valid_count": sum(1 for c in contours if c.valid),
            "invalid_count": sum(1 for c in contours if not c.valid),
            "total_air_travel": round(plan.total_air_travel, 4),
            "total_cut_length": round(plan.total_cut_length, 4),
            "home_return_travel": round(plan.home_return_travel, 4),
            "skipped": plan.skipped,
        },
        "home": list(plan.home),
    }
    meta_out = {
        "viewbox": [round(v, 4) for v in meta.viewbox],
        "width": round(meta.width, 4),
        "height": round(meta.height, 4),
        "element_count": meta.element_count,
    }
    return result, meta_out, gcode


# ---------------------------------------------------------------- 路由

@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/jobs")
async def create_and_solve_job(
    file: UploadFile = File(...),
    name: Optional[str] = Form(None),
    settings: str = Form("{}"),
):
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="上传文件为空")
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="SVG 文件不能超过 5 MB")
    try:
        s = SolveSettings(**json.loads(settings))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"设置参数无效: {exc}")

    job_name = name or Path(file.filename or "job.svg").stem
    job_id = db.create_job(job_name, file.filename or "upload.svg", raw, s.model_dump())

    try:
        result, meta, gcode = run_pipeline(raw, s)
    except ValueError as exc:
        db.save_result(job_id, "error", {}, {}, None, error=str(exc))
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:  # noqa: BLE001 - 兜底，把意外错误返回前端
        db.save_result(job_id, "error", {}, {}, None, error=f"内部错误: {exc}")
        raise HTTPException(status_code=500, detail=f"求解失败: {exc}")

    db.save_result(job_id, "solved", meta, result, gcode)
    return {"id": job_id, "name": job_name, "meta": meta, **result}


@app.get("/api/jobs")
def jobs_list():
    rows = db.list_jobs()
    return [dict(r) for r in rows]


@app.get("/api/jobs/{job_id}")
def job_detail(job_id: int):
    row = db.get_job(job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="作业不存在")
    out = {
        "id": row["id"],
        "name": row["name"],
        "filename": row["filename"],
        "status": row["status"],
        "error": row["error"],
        "settings": json.loads(row["settings_json"]),
        "created_at": row["created_at"],
        "solved_at": row["solved_at"],
    }
    if row["meta_json"]:
        out["meta"] = json.loads(row["meta_json"])
    if row["result_json"]:
        out.update(json.loads(row["result_json"]))
    return out


@app.get("/api/jobs/{job_id}/gcode")
def job_gcode(job_id: int):
    row = db.get_job(job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="作业不存在")
    if not row["gcode"]:
        raise HTTPException(status_code=409, detail="该作业没有可用的 G-code")
    fname = f"{(row['name'] or 'job')}.gcode"
    return Response(
        content=row["gcode"],
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.get("/api/jobs/{job_id}/svg")
def job_svg(job_id: int):
    row = db.get_job(job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="作业不存在")
    return Response(content=row["svg"], media_type="image/svg+xml")


# ---------------------------------------------------------------- 静态前端

if STATIC_DIR.is_dir():
    app.mount(
        "/assets",
        StaticFiles(directory=STATIC_DIR / "assets"),
        name="assets",
    )

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")
