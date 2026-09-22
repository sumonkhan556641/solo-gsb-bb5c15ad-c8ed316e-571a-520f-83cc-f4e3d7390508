"""SQLite 持久化层。

存储上传的 SVG 原文、作业设置与求解结果（轮廓/路径以 JSON 保存）。
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Optional

DB_PATH = os.environ.get("LASER_DB_PATH", "/data/laser.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    filename        TEXT NOT NULL,
    svg             BLOB NOT NULL,
    settings_json   TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'created',
    error           TEXT,
    meta_json       TEXT,
    result_json     TEXT,
    gcode           TEXT,
    created_at      TEXT NOT NULL,
    solved_at       TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_db(path: Optional[str] = None) -> None:
    p = path or DB_PATH
    os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
    with sqlite3.connect(p) as con:
        con.execute("PRAGMA foreign_keys = ON")
        con.executescript(_SCHEMA)


@contextmanager
def connect(path: Optional[str] = None):
    p = path or DB_PATH
    con = sqlite3.connect(p)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def create_job(name: str, filename: str, svg: bytes, settings: dict[str, Any],
               path: Optional[str] = None) -> int:
    with connect(path) as con:
        cur = con.execute(
            "INSERT INTO jobs(name, filename, svg, settings_json, created_at)"
            " VALUES (?,?,?,?,?)",
            (name, filename, sqlite3.Binary(svg),
             json.dumps(settings, ensure_ascii=False), _now()),
        )
        return int(cur.lastrowid)


def get_job(job_id: int, path: Optional[str] = None) -> Optional[sqlite3.Row]:
    with connect(path) as con:
        row = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return row


def list_jobs(path: Optional[str] = None) -> list[sqlite3.Row]:
    with connect(path) as con:
        return list(con.execute(
            "SELECT id, name, filename, status, error, created_at, solved_at "
            "FROM jobs ORDER BY id DESC"))


def save_result(job_id: int, status: str, meta: dict[str, Any],
                result: dict[str, Any], gcode: Optional[str],
                error: Optional[str] = None,
                path: Optional[str] = None) -> None:
    with connect(path) as con:
        con.execute(
            "UPDATE jobs SET status=?, error=?, meta_json=?, result_json=?, "
            "gcode=?, solved_at=? WHERE id=?",
            (status, error,
             json.dumps(meta, ensure_ascii=False),
             json.dumps(result, ensure_ascii=False),
             gcode, _now(), job_id),
        )
