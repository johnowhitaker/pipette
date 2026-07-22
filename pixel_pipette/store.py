"""Small SQLite-backed store for calibration, colors, and the print queue."""

from __future__ import annotations

import copy
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


DEFAULT_CONFIG: dict[str, Any] = {
    "grid_sizes": [8, 10],
    "accepting_submissions": True,
    "learn_more_url": "https://johnowhitaker.dev",
    "queue": {
        "start_mode": "manual",
    },
    "devices": {
        "printer_port": "/dev/ttyUSB0",
        "printer_baud": 115200,
        "servo_port": "/dev/ttyACM0",
        "servo_baud": 1_000_000,
        "servo_id": 1,
    },
    "paper": {
        "bottom_right_x": None,
        "bottom_right_y": None,
        "deposit_z": 0.0,
    },
    "motion": {
        "travel_z": 30.0,
        "xy_feed": 1500,
        "z_feed": 300,
        "jog_feed": 600,
        "command_timeout_s": 20.0,
    },
    "servo": {
        "rest_position": None,
        "draw_position": None,
        "purge_position": None,
        "velocity": 20,
        "acceleration": 5,
        "settle_ms": 350,
    },
}

DEFAULT_COLORS = [
    {"id": "red", "name": "Red", "hex": "#ef476f", "x": 142.0, "y": 45.0},
    {"id": "green", "name": "Green", "hex": "#39b86b", "x": 167.0, "y": 45.0},
    {"id": "blue", "name": "Blue", "hex": "#3a86ff", "x": 192.0, "y": 45.0},
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def deep_merge(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=20)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _init_db(self) -> None:
        with self._lock, self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS colors (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    hex TEXT NOT NULL,
                    x REAL,
                    y REAL,
                    intake_z REAL,
                    purge_z REAL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    sort_order INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    artist_name TEXT NOT NULL DEFAULT '',
                    grid_size INTEGER NOT NULL,
                    pixels_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    submitted_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    progress_current INTEGER NOT NULL DEFAULT 0,
                    progress_total INTEGER NOT NULL DEFAULT 0,
                    error TEXT
                );
                CREATE INDEX IF NOT EXISTS jobs_status_id ON jobs(status, id);
                """
            )
            row = db.execute("SELECT value FROM settings WHERE key = 'config'").fetchone()
            if row is None:
                db.execute(
                    "INSERT INTO settings(key, value) VALUES('config', ?)",
                    (json.dumps(DEFAULT_CONFIG),),
                )
            count = db.execute("SELECT COUNT(*) AS n FROM colors").fetchone()["n"]
            if count == 0:
                for order, color in enumerate(DEFAULT_COLORS):
                    db.execute(
                        """INSERT INTO colors
                           (id, name, hex, x, y, intake_z, purge_z, enabled, sort_order)
                           VALUES (?, ?, ?, ?, ?, NULL, NULL, 1, ?)""",
                        (color["id"], color["name"], color["hex"], color["x"], color["y"], order),
                    )

    def get_config(self) -> dict[str, Any]:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key = 'config'").fetchone()
        stored = json.loads(row["value"]) if row else {}
        return deep_merge(DEFAULT_CONFIG, stored)

    def update_config(self, update: dict[str, Any]) -> dict[str, Any]:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key = 'config'").fetchone()
            current = deep_merge(DEFAULT_CONFIG, json.loads(row["value"]) if row else {})
            merged = deep_merge(current, update)
            db.execute(
                "INSERT INTO settings(key, value) VALUES('config', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (json.dumps(merged),),
            )
        return merged

    @staticmethod
    def _color_from_row(row: sqlite3.Row) -> dict[str, Any]:
        color = dict(row)
        color["enabled"] = bool(color["enabled"])
        return color

    def list_colors(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        query = "SELECT * FROM colors"
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY sort_order, name"
        with self._lock, self._connect() as db:
            rows = db.execute(query).fetchall()
        return [self._color_from_row(row) for row in rows]

    def get_color(self, color_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT * FROM colors WHERE id = ?", (color_id,)).fetchone()
        return self._color_from_row(row) if row else None

    def create_color(self, values: dict[str, Any]) -> dict[str, Any]:
        color_id = uuid4().hex[:10]
        with self._lock, self._connect() as db:
            order = db.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 AS n FROM colors").fetchone()["n"]
            db.execute(
                """INSERT INTO colors
                   (id, name, hex, x, y, intake_z, purge_z, enabled, sort_order)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    color_id,
                    values["name"],
                    values["hex"],
                    values.get("x"),
                    values.get("y"),
                    values.get("intake_z"),
                    values.get("purge_z"),
                    int(values.get("enabled", True)),
                    order,
                ),
            )
        return self.get_color(color_id) or {}

    def update_color(self, color_id: str, values: dict[str, Any]) -> dict[str, Any] | None:
        existing = self.get_color(color_id)
        if existing is None:
            return None
        updated = {**existing, **values}
        with self._lock, self._connect() as db:
            db.execute(
                """UPDATE colors SET name=?, hex=?, x=?, y=?, intake_z=?, purge_z=?, enabled=?
                   WHERE id=?""",
                (
                    updated["name"],
                    updated["hex"],
                    updated.get("x"),
                    updated.get("y"),
                    updated.get("intake_z"),
                    updated.get("purge_z"),
                    int(updated.get("enabled", True)),
                    color_id,
                ),
            )
        return self.get_color(color_id)

    def delete_color(self, color_id: str) -> bool:
        with self._lock, self._connect() as db:
            result = db.execute("DELETE FROM colors WHERE id = ?", (color_id,))
        return result.rowcount > 0

    @staticmethod
    def _job_from_row(row: sqlite3.Row) -> dict[str, Any]:
        job = dict(row)
        job["pixels"] = json.loads(job.pop("pixels_json"))
        return job

    def create_job(self, artist_name: str, grid_size: int, pixels: list[str | None]) -> dict[str, Any]:
        progress_total = sum(1 for pixel in pixels if pixel)
        with self._lock, self._connect() as db:
            cursor = db.execute(
                """INSERT INTO jobs
                   (artist_name, grid_size, pixels_json, status, submitted_at, progress_total)
                   VALUES (?, ?, ?, 'queued', ?, ?)""",
                (artist_name, grid_size, json.dumps(pixels), utc_now(), progress_total),
            )
            row = db.execute("SELECT * FROM jobs WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return self._job_from_row(row)

    def list_jobs(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock, self._connect() as db:
            rows = db.execute(
                "SELECT * FROM jobs ORDER BY "
                "CASE status WHEN 'waiting' THEN 0 WHEN 'printing' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END, "
                "CASE WHEN status IN ('waiting', 'printing', 'queued') THEN id END ASC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._job_from_row(row) for row in rows]

    def queue_count(self) -> int:
        with self._lock, self._connect() as db:
            return int(db.execute("SELECT COUNT(*) AS n FROM jobs WHERE status='queued'").fetchone()["n"])

    def claim_next_job(self, status: str = "printing") -> dict[str, Any] | None:
        if status not in {"waiting", "printing"}:
            raise ValueError(f"Invalid active job status: {status}")
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
            if row is None:
                db.rollback()
                return None
            db.execute(
                "UPDATE jobs SET status=?, started_at=?, error=NULL WHERE id=?",
                (status, utc_now(), row["id"]),
            )
            db.commit()
            updated = db.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone()
        return self._job_from_row(updated)

    def update_job(self, job_id: int, **values: Any) -> None:
        allowed = {"status", "started_at", "completed_at", "progress_current", "progress_total", "error"}
        clean = {key: value for key, value in values.items() if key in allowed}
        if not clean:
            return
        assignments = ", ".join(f"{key}=?" for key in clean)
        with self._lock, self._connect() as db:
            db.execute(f"UPDATE jobs SET {assignments} WHERE id=?", (*clean.values(), job_id))

    def remove_queued_job(self, job_id: int) -> bool:
        with self._lock, self._connect() as db:
            result = db.execute("DELETE FROM jobs WHERE id=? AND status='queued'", (job_id,))
        return result.rowcount > 0

    def recover_interrupted_jobs(self) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                """UPDATE jobs SET status='failed', completed_at=?,
                   error='The app restarted while this piece was active.'
                   WHERE status IN ('waiting', 'printing')""",
                (utc_now(),),
            )
