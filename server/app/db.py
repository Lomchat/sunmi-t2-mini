from __future__ import annotations

import json
import secrets
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any


class Database:
    def __init__(self, path: Path, lease_seconds: int, max_attempts: int):
        self.path = path
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self._lock = threading.RLock()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    def initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('queued','leased','printed','failed')),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    lease_token TEXT,
                    leased_at REAL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    completed_at REAL,
                    error TEXT
                );
                CREATE INDEX IF NOT EXISTS jobs_queue_idx
                    ON jobs(state, created_at);

                CREATE TABLE IF NOT EXISTS devices (
                    id TEXT PRIMARY KEY,
                    last_seen REAL NOT NULL,
                    app_version TEXT NOT NULL DEFAULT '',
                    device_model TEXT NOT NULL DEFAULT '',
                    printer_status TEXT NOT NULL DEFAULT 'unknown',
                    last_error TEXT NOT NULL DEFAULT '',
                    remote_ip TEXT NOT NULL DEFAULT ''
                );
                """
            )

    def create_job(self, payload: dict[str, Any]) -> str:
        now = time.time()
        job_id = str(uuid.uuid4())
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO jobs(id,payload,state,created_at,updated_at) VALUES(?,?, 'queued',?,?)",
                (job_id, json.dumps(payload, ensure_ascii=False), now, now),
            )
        return job_id

    def _requeue_expired(self, connection: sqlite3.Connection, now: float) -> None:
        deadline = now - self.lease_seconds
        connection.execute(
            """
            UPDATE jobs SET state='queued', lease_token=NULL, leased_at=NULL,
                updated_at=?, error='Le bail précédent a expiré; travail remis en file.'
            WHERE state='leased' AND leased_at < ? AND attempts < ?
            """,
            (now, deadline, self.max_attempts),
        )
        connection.execute(
            """
            UPDATE jobs SET state='failed', updated_at=?, completed_at=?,
                error='Nombre maximal de tentatives atteint.'
            WHERE state='leased' AND leased_at < ? AND attempts >= ?
            """,
            (now, now, deadline, self.max_attempts),
        )

    def lease_next_job(self) -> dict[str, Any] | None:
        now = time.time()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._requeue_expired(connection, now)
            row = connection.execute(
                "SELECT id,payload,attempts FROM jobs WHERE state='queued' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            lease_token = secrets.token_urlsafe(24)
            connection.execute(
                """
                UPDATE jobs SET state='leased', attempts=attempts+1, lease_token=?,
                    leased_at=?, updated_at=?, error=NULL WHERE id=? AND state='queued'
                """,
                (lease_token, now, now, row["id"]),
            )
            connection.commit()
            return {
                "id": row["id"],
                "lease_token": lease_token,
                "attempt": row["attempts"] + 1,
                "payload": json.loads(row["payload"]),
            }

    def acknowledge(self, job_id: str, lease_token: str, success: bool, error: str = "") -> bool:
        now = time.time()
        state = "printed" if success else "failed"
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs SET state=?, updated_at=?, completed_at=?, error=?,
                    lease_token=NULL, leased_at=NULL
                WHERE id=? AND state='leased' AND lease_token=?
                """,
                (state, now, now, error[:1000], job_id, lease_token),
            )
            return cursor.rowcount == 1

    def retry(self, job_id: str) -> bool:
        now = time.time()
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs SET state='queued', attempts=0, lease_token=NULL,
                    leased_at=NULL, completed_at=NULL, updated_at=?, error=NULL
                WHERE id=? AND state='failed'
                """,
                (now, job_id),
            )
            return cursor.rowcount == 1

    def list_jobs(self, limit: int = 100) -> list[dict[str, Any]]:
        now = time.time()
        with self._lock, self._connect() as connection:
            self._requeue_expired(connection, now)
            rows = connection.execute(
                """
                SELECT id,payload,state,attempts,created_at,updated_at,completed_at,error
                FROM jobs ORDER BY created_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
            result = []
            for row in rows:
                payload = json.loads(row["payload"])
                result.append({
                    "id": row["id"],
                    "state": row["state"],
                    "attempts": row["attempts"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "completed_at": row["completed_at"],
                    "error": row["error"] or "",
                    "summary": payload.get("summary", "Impression"),
                })
            return result

    def touch_device(
        self,
        device_id: str,
        app_version: str,
        device_model: str,
        printer_status: str,
        last_error: str,
        remote_ip: str,
    ) -> None:
        now = time.time()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO devices(id,last_seen,app_version,device_model,printer_status,last_error,remote_ip)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    last_seen=excluded.last_seen,
                    app_version=excluded.app_version,
                    device_model=excluded.device_model,
                    printer_status=excluded.printer_status,
                    last_error=excluded.last_error,
                    remote_ip=excluded.remote_ip
                """,
                (device_id, now, app_version[:80], device_model[:120], printer_status[:80], last_error[:500], remote_ip[:80]),
            )

    def device_status(self, device_id: str, online_window: int) -> dict[str, Any]:
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone()
            counts = dict(connection.execute("SELECT state,COUNT(*) count FROM jobs GROUP BY state").fetchall())
        if row is None:
            device = {
                "id": device_id,
                "online": False,
                "last_seen": None,
                "app_version": "",
                "device_model": "",
                "printer_status": "unknown",
                "last_error": "",
            }
        else:
            device = dict(row)
            device["online"] = time.time() - row["last_seen"] <= online_window
            device.pop("remote_ip", None)
        device["queue"] = {name: int(counts.get(name, 0)) for name in ("queued", "leased", "printed", "failed")}
        return device

