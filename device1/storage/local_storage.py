import csv
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import List, Optional

logger = logging.getLogger(__name__)


class LocalStorage:
    def __init__(self, csv_path: str, db_path: str):
        self._csv_path = csv_path
        self._db_path = db_path
        self._lock = threading.Lock()

        os.makedirs(os.path.dirname(csv_path), exist_ok=True)
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

        self._init_db()
        self._init_csv()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._get_conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    count_in INTEGER NOT NULL,
                    count_out INTEGER NOT NULL,
                    total_visitors INTEGER NOT NULL,
                    event_type TEXT NOT NULL DEFAULT 'count'
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp)"
            )
            conn.commit()
        logger.info("SQLite DB initialized at %s", self._db_path)

    def _init_csv(self) -> None:
        if not os.path.exists(self._csv_path):
            with open(self._csv_path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(
                    ["timestamp", "device_id", "count_in", "count_out", "total_visitors", "event_type"]
                )
        logger.info("CSV initialized at %s", self._csv_path)

    def log_count(
        self,
        device_id: str,
        count_in: int,
        count_out: int,
        total: int,
        event_type: str = "count",
    ) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        with self._lock:
            with self._get_conn() as conn:
                conn.execute(
                    """
                    INSERT INTO events (timestamp, device_id, count_in, count_out, total_visitors, event_type)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (ts, device_id, count_in, count_out, total, event_type),
                )
                conn.commit()

            with open(self._csv_path, "a", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([ts, device_id, count_in, count_out, total, event_type])

        logger.debug(
            "Logged count: device=%s in=%d out=%d total=%d", device_id, count_in, count_out, total
        )

    def get_hourly_summary(self) -> List[dict]:
        with self._lock:
            with self._get_conn() as conn:
                rows = conn.execute(
                    """
                    SELECT
                        strftime('%Y-%m-%dT%H:00:00', timestamp) AS hour,
                        device_id,
                        MAX(count_in) AS count_in,
                        MAX(count_out) AS count_out,
                        MAX(total_visitors) AS peak_total
                    FROM events
                    GROUP BY hour, device_id
                    ORDER BY hour
                    """
                ).fetchall()
        return [dict(r) for r in rows]

    def get_latest(self) -> Optional[dict]:
        with self._lock:
            with self._get_conn() as conn:
                row = conn.execute(
                    "SELECT * FROM events ORDER BY timestamp DESC LIMIT 1"
                ).fetchone()
        return dict(row) if row else None

    def export_csv(self, output_path: str) -> None:
        with self._lock:
            with self._get_conn() as conn:
                rows = conn.execute(
                    "SELECT timestamp, device_id, count_in, count_out, total_visitors, event_type FROM events ORDER BY timestamp"
                ).fetchall()

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                ["timestamp", "device_id", "count_in", "count_out", "total_visitors", "event_type"]
            )
            writer.writerows(rows)

        logger.info("Exported %d records to %s", len(rows), output_path)
