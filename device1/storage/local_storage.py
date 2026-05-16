import csv
import logging
import os
import sqlite3
import time
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class LocalStorage:
    def __init__(self, csv_path: str, db_path: str):
        self._csv_path = csv_path
        self._db_path = db_path
        os.makedirs(os.path.dirname(csv_path), exist_ok=True)
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()
        self._init_csv()

    def _init_db(self) -> None:
        with self._get_conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    device_id TEXT NOT NULL,
                    count_in INTEGER NOT NULL,
                    count_out INTEGER NOT NULL,
                    total_visitors INTEGER NOT NULL,
                    event_type TEXT NOT NULL DEFAULT 'count_update'
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON events(timestamp)")

    def _init_csv(self) -> None:
        if not os.path.exists(self._csv_path):
            with open(self._csv_path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(
                    ["id", "timestamp", "device_id", "count_in", "count_out", "total_visitors", "event_type"]
                )

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def log_count(
        self,
        device_id: str,
        count_in: int,
        count_out: int,
        total: int,
        event_type: str = "count_update",
    ) -> None:
        ts = time.time()
        with self._get_conn() as conn:
            cursor = conn.execute(
                """
                INSERT INTO events (timestamp, device_id, count_in, count_out, total_visitors, event_type)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (ts, device_id, count_in, count_out, total, event_type),
            )
            row_id = cursor.lastrowid

        try:
            with open(self._csv_path, "a", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([row_id, ts, device_id, count_in, count_out, total, event_type])
        except OSError as exc:
            logger.warning("CSV write failed: %s", exc)

    def get_hourly_summary(self) -> List[dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT
                    strftime('%Y-%m-%d %H:00:00', datetime(timestamp, 'unixepoch')) AS hour,
                    device_id,
                    MAX(count_in) AS count_in,
                    MAX(count_out) AS count_out,
                    MAX(total_visitors) AS peak_total
                FROM events
                GROUP BY hour, device_id
                ORDER BY hour
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def get_latest(self) -> Optional[dict]:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM events ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def export_csv(self, output_path: str) -> None:
        with self._get_conn() as conn:
            rows = conn.execute("SELECT * FROM events ORDER BY timestamp").fetchall()
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                ["id", "timestamp", "device_id", "count_in", "count_out", "total_visitors", "event_type"]
            )
            for row in rows:
                writer.writerow(list(row))
        logger.info("Exported %d rows to %s", len(rows), output_path)
