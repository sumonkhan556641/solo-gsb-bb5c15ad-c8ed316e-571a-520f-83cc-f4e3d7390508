"""Async SQLite persistence (aiosqlite)."""
from __future__ import annotations

import json
import os
from typing import Any

import aiosqlite

from .config import settings

DEFAULT_SETTINGS: dict[str, Any] = {
    "home_x": 0.0,
    "home_y": 0.0,
    "invert_y": True,
    "laser_power": 255,
    "travel_speed": 3000,
    "cut_speed": 600,
    "safe_z": 5.0,
    "preamble": "",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    svg           TEXT NOT NULL,
    view_box      TEXT,
    settings      TEXT NOT NULL,
    schedule      TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS contours (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    contour_index   INTEGER NOT NULL,
    kind            TEXT NOT NULL,
    points          TEXT NOT NULL,
    closed          INTEGER NOT NULL,
    valid           INTEGER NOT NULL,
    issues          TEXT NOT NULL,
    intersections   TEXT NOT NULL,
    parent          INTEGER,
    depth           INTEGER NOT NULL DEFAULT 0,
    cut_index       INTEGER
);
"""

_db: aiosqlite.Connection | None = None


async def connect() -> aiosqlite.Connection:
    global _db
    os.makedirs(os.path.dirname(settings.db_path) or ".", exist_ok=True)
    _db = await aiosqlite.connect(settings.db_path)
    _db.row_factory = aiosqlite.Row
    await _db.executescript(_SCHEMA)
    await _db.execute("PRAGMA foreign_keys = ON")
    await _db.commit()
    return _db


def db() -> aiosqlite.Connection:
    assert _db is not None, "database not initialised"
    return _db


async def close() -> None:
    global _db
    if _db is not None:
        await _db.close()
        _db = None


async def create_project(name: str, svg: str, view_box: tuple | None,
                         result) -> int:
    conn = db()
    settings_json = json.dumps(DEFAULT_SETTINGS)
    cur = await conn.execute(
        "INSERT INTO projects (name, svg, view_box, settings) VALUES (?, ?, ?, ?)",
        (name, svg, json.dumps(list(view_box)) if view_box else None, settings_json),
    )
    project_id = cur.lastrowid
    await _replace_contours(project_id, result)
    await conn.execute(
        "UPDATE projects SET schedule = ? WHERE id = ?",
        (json.dumps(_schedule_json(result)), project_id),
    )
    await conn.commit()
    return project_id


async def _replace_contours(project_id: int, result) -> None:
    conn = db()
    await conn.execute("DELETE FROM contours WHERE project_id = ?", (project_id,))
    for c in result.contours:
        await conn.execute(
            """INSERT INTO contours
               (project_id, contour_index, kind, points, closed, valid, issues,
                intersections, parent, depth, cut_index)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (project_id, c.id, c.kind,
             json.dumps(c.points), 1 if c.closed else 0, 1 if c.valid else 0,
             json.dumps(c.issues),
             json.dumps(c.self_intersections),
             c.parent, c.depth, c.cut_index),
        )


def _schedule_json(result) -> dict:
    return {
        "order": result.order,
        "segments": result.segments,
        "total_travel": result.total_travel,
        "total_cut": result.total_cut,
        "invalid_count": result.invalid_count,
    }


async def save_schedule(project_id: int, result) -> None:
    conn = db()
    await _replace_contours(project_id, result)
    await conn.execute(
        "UPDATE projects SET schedule = ? WHERE id = ?",
        (json.dumps(_schedule_json(result)), project_id),
    )
    await conn.commit()


async def list_projects() -> list[aiosqlite.Row]:
    cur = await db().execute(
        """SELECT p.id, p.name, p.created_at,
                  COUNT(c.id) AS contour_count,
                  SUM(CASE WHEN c.valid = 0 THEN 1 ELSE 0 END) AS invalid_count
           FROM projects p LEFT JOIN contours c ON c.project_id = p.id
           GROUP BY p.id ORDER BY p.id DESC"""
    )
    return list(await cur.fetchall())


async def get_project(project_id: int) -> aiosqlite.Row | None:
    cur = await db().execute(
        "SELECT * FROM projects WHERE id = ?", (project_id,))
    return await cur.fetchone()


async def get_contours(project_id: int) -> list[aiosqlite.Row]:
    cur = await db().execute(
        "SELECT * FROM contours WHERE project_id = ? ORDER BY contour_index",
        (project_id,))
    return list(await cur.fetchall())


async def update_settings(project_id: int, new_settings: dict) -> dict:
    row = await get_project(project_id)
    merged = {**json.loads(row["settings"]), **new_settings}
    await db().execute(
        "UPDATE projects SET settings = ? WHERE id = ?",
        (json.dumps(merged), project_id),
    )
    await db().commit()
    return merged


async def delete_project(project_id: int) -> None:
    conn = db()
    await conn.execute("DELETE FROM contours WHERE project_id = ?", (project_id,))
    await conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    await conn.commit()
