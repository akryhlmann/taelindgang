import csv
import logging
import os
import sqlite3
import time
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class LocalStorage:
    def __init__(self, db_path: str, csv_path: str):
        self._db_path = db_path
        self._csv_path = csv_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        os.makedirs(os.path.dirname(csv_path), exist_ok=True)
        self._init_db()
        self._init_csv()

    def _init_db(self) -> None:
        with self._get_conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS devices (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id TEXT UNIQUE NOT NULL,
                    location TEXT,
                    first_seen REAL,
                    last_seen REAL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS count_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    device_id TEXT NOT NULL,
                    count_in INTEGER NOT NULL,
                    count_out INTEGER NOT NULL,
                    total INTEGER NOT NULL,
                    rssi INTEGER
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ce_timestamp ON count_events(timestamp)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ce_device ON count_events(device_id)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS hourly_summary (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    hour_timestamp REAL NOT NULL,
                    device_id TEXT NOT NULL,
                    count_in INTEGER NOT NULL,
                    count_out INTEGER NOT NULL,
                    peak_total INTEGER NOT NULL,
                    UNIQUE(hour_timestamp, device_id)
                )
                """
            )

    def _init_csv(self) -> None:
        if not os.path.exists(self._csv_path):
            with open(self._csv_path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(
                    ["timestamp", "device_id", "count_in", "count_out", "total", "rssi"]
                )

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def store_event(
        self,
        device_id: str,
        count_in: int,
        count_out: int,
        total: int,
        rssi: Optional[int] = None,
    ) -> None:
        ts = time.time()
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO devices (device_id, first_seen, last_seen)
                VALUES (?, ?, ?)
                """,
                (device_id, ts, ts),
            )
            conn.execute(
                """
                UPDATE devices SET last_seen = ? WHERE device_id = ?
                """,
                (ts, device_id),
            )
            conn.execute(
                """
                INSERT INTO count_events (timestamp, device_id, count_in, count_out, total, rssi)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (ts, device_id, count_in, count_out, total, rssi),
            )
            self._update_hourly_summary(conn, ts, device_id, count_in, count_out, total)

        try:
            with open(self._csv_path, "a", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([ts, device_id, count_in, count_out, total, rssi])
        except OSError as exc:
            logger.warning("CSV append failed: %s", exc)

    def _update_hourly_summary(
        self,
        conn: sqlite3.Connection,
        ts: float,
        device_id: str,
        count_in: int,
        count_out: int,
        total: int,
    ) -> None:
        import math
        hour_ts = math.floor(ts / 3600) * 3600
        conn.execute(
            """
            INSERT INTO hourly_summary (hour_timestamp, device_id, count_in, count_out, peak_total)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(hour_timestamp, device_id) DO UPDATE SET
                count_in = MAX(count_in, excluded.count_in),
                count_out = MAX(count_out, excluded.count_out),
                peak_total = MAX(peak_total, excluded.peak_total)
            """,
            (hour_ts, device_id, count_in, count_out, total),
        )

    def get_current_total(self, device_id: Optional[str] = None) -> int:
        with self._get_conn() as conn:
            if device_id:
                row = conn.execute(
                    """
                    SELECT total FROM count_events
                    WHERE device_id = ?
                    ORDER BY timestamp DESC LIMIT 1
                    """,
                    (device_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT SUM(latest_total) FROM (
                        SELECT device_id, total AS latest_total
                        FROM count_events
                        WHERE id IN (
                            SELECT MAX(id) FROM count_events GROUP BY device_id
                        )
                    )
                    """
                ).fetchone()
        if row and row[0] is not None:
            return int(row[0])
        return 0

    def get_timeseries(self, hours: int = 24, interval_minutes: int = 15) -> List[dict]:
        since = time.time() - hours * 3600
        interval_sec = interval_minutes * 60
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT
                    CAST(timestamp / ? AS INTEGER) * ? AS bucket,
                    device_id,
                    MAX(total) AS peak_total,
                    MAX(count_in) AS count_in,
                    MAX(count_out) AS count_out
                FROM count_events
                WHERE timestamp >= ?
                GROUP BY bucket, device_id
                ORDER BY bucket
                """,
                (interval_sec, interval_sec, since),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_all_devices(self) -> List[str]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT device_id FROM devices ORDER BY first_seen"
            ).fetchall()
        return [row["device_id"] for row in rows]

    def get_device_lora_stats(self) -> List[dict]:
        """Return latest RSSI, last packet time, and recent avg RSSI per device."""
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT
                    d.device_id,
                    d.last_seen,
                    ce.rssi AS latest_rssi,
                    (
                        SELECT AVG(rssi) FROM (
                            SELECT rssi FROM count_events
                            WHERE device_id = d.device_id AND rssi IS NOT NULL
                            ORDER BY timestamp DESC LIMIT 10
                        )
                    ) AS avg_rssi
                FROM devices d
                LEFT JOIN count_events ce ON ce.id = (
                    SELECT MAX(id) FROM count_events WHERE device_id = d.device_id
                )
                ORDER BY d.first_seen
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def get_stats(self, since_hours: int = 24) -> dict:
        since = time.time() - since_hours * 3600
        with self._get_conn() as conn:
            row = conn.execute(
                """
                SELECT
                    MAX(total) AS peak,
                    MAX(count_in) AS total_in,
                    MAX(count_out) AS total_out
                FROM count_events
                WHERE timestamp >= ?
                """,
                (since,),
            ).fetchone()

            latest_row = conn.execute(
                """
                SELECT SUM(latest_total) FROM (
                    SELECT total AS latest_total
                    FROM count_events
                    WHERE id IN (SELECT MAX(id) FROM count_events GROUP BY device_id)
                )
                """
            ).fetchone()

        current = int(latest_row[0]) if latest_row and latest_row[0] else 0
        if row:
            return {
                "current": current,
                "peak": int(row["peak"] or 0),
                "total_in": int(row["total_in"] or 0),
                "total_out": int(row["total_out"] or 0),
            }
        return {"current": current, "peak": 0, "total_in": 0, "total_out": 0}
