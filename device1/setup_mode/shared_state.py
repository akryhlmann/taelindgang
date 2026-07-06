import threading
import time
from typing import Optional

import cv2
import numpy as np

_GREEN = (0, 220, 80)
_LINE_COLORS = {
    "top":    (0, 220, 80),
    "bottom": (0, 200, 255),
    "left":   (255, 200, 0),
    "right":  (200, 0, 255),
}


class SharedState:
    """
    Thread-safe bridge between the main detection loop and the setup web app.

    Main loop writes frames + detections; Flask reads them for MJPEG streaming
    and status. The web app writes line config updates; the main loop reads and
    applies them.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._frame: Optional[np.ndarray] = None
        self._camera_ok: bool = False
        self._det_history: list = []       # [(timestamp, count), ...]
        self._lora_last_sent: Optional[str] = None
        self._last_activity: float = time.time()
        self._line_pending: Optional[dict] = None

    # ── called from main detection loop ─────────────────────────────────────

    def update_frame(self, frame: np.ndarray, detections: list,
                     camera_ok: bool, line_cfg: dict) -> None:
        """Render bounding boxes and counting line on a frame copy for streaming."""
        out = frame.copy()

        for det in detections:
            x1, y1, x2, y2 = det["bbox"]
            cv2.rectangle(out, (x1, y1), (x2, y2), _GREEN, 2)
            cv2.putText(out, f"{det['confidence']:.2f}",
                        (x1, max(y1 - 4, 12)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, _GREEN, 1)

        p1 = line_cfg.get("point1")
        p2 = line_cfg.get("point2")
        in_dir = line_cfg.get("in_direction", "top")
        if p1 and p2:
            h, w = out.shape[:2]
            color = _LINE_COLORS.get(in_dir, _GREEN)
            pt1 = (int(p1[0] * w), int(p1[1] * h))
            pt2 = (int(p2[0] * w), int(p2[1] * h))
            cv2.line(out, pt1, pt2, color, 2)
            cv2.circle(out, pt1, 5, color, -1)
            cv2.circle(out, pt2, 5, color, -1)

        now = time.time()
        with self._lock:
            self._frame = out
            self._camera_ok = camera_ok
            self._det_history.append((now, len(detections)))
            cutoff = now - 5
            self._det_history = [(t, n) for t, n in self._det_history if t >= cutoff]

    def notify_lora_sent(self) -> None:
        with self._lock:
            self._lora_last_sent = time.strftime("%H:%M:%S")

    def consume_line_update(self) -> Optional[dict]:
        """Return a pending line config update and clear it; None if no update."""
        with self._lock:
            pending = self._line_pending
            self._line_pending = None
            return pending

    # ── called from web app ──────────────────────────────────────────────────

    def get_frame(self) -> Optional[np.ndarray]:
        with self._lock:
            return self._frame.copy() if self._frame is not None else None

    def get_frame_size(self):
        with self._lock:
            if self._frame is None:
                return 640, 360
            h, w = self._frame.shape[:2]
            return w, h

    def get_status(self) -> dict:
        with self._lock:
            now = time.time()
            if self._det_history:
                total = sum(n for _, n in self._det_history)
                span = max(now - self._det_history[0][0], 0.1)
                dps = total / span
            else:
                dps = 0.0
            return {
                "camera_ok": self._camera_ok,
                "detections_per_sec": round(dps, 1),
                "lora_last_sent": self._lora_last_sent,
            }

    def touch(self) -> None:
        """Record activity to reset the auto-timeout countdown."""
        self._last_activity = time.time()

    def timeout_remaining(self, timeout_seconds: int) -> int:
        elapsed = time.time() - self._last_activity
        return max(0, int(timeout_seconds - elapsed))

    def is_timed_out(self, timeout_seconds: int) -> bool:
        return (time.time() - self._last_activity) > timeout_seconds

    def request_line_update(self, point1, point2, in_direction: str) -> None:
        with self._lock:
            self._line_pending = {
                "point1": point1,
                "point2": point2,
                "in_direction": in_direction,
            }
