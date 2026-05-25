import logging
import threading
import time
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)


class RTSPCapture:
    def __init__(self, rtsp_url: str, fps_target: int = 10, reconnect_interval: int = 5):
        self._url = rtsp_url
        self._fps_target = fps_target
        self._reconnect_interval = reconnect_interval
        self._cap: Optional[cv2.VideoCapture] = None
        self._frame: Optional[np.ndarray] = None
        self._lock = threading.Lock()
        self._connected = False
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        logger.info("RTSPCapture started for %s", self._url)

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=5)
        if self._cap is not None:
            self._cap.release()
        logger.info("RTSPCapture stopped")

    def is_connected(self) -> bool:
        return self._connected

    def get_frame(self) -> Optional[np.ndarray]:
        with self._lock:
            return self._frame.copy() if self._frame is not None else None

    def _connect(self) -> bool:
        if self._cap is not None:
            self._cap.release()
        self._cap = cv2.VideoCapture(self._url, cv2.CAP_FFMPEG)
        self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if self._cap.isOpened():
            logger.info("Connected to RTSP stream: %s", self._url)
            return True
        logger.warning("Failed to connect to RTSP stream: %s", self._url)
        return False

    def _capture_loop(self) -> None:
        frame_interval = 1.0 / self._fps_target
        while self._running:
            if not self._connected:
                if self._connect():
                    self._connected = True
                else:
                    time.sleep(self._reconnect_interval)
                    continue

            loop_start = time.monotonic()
            ret, frame = self._cap.read()
            if not ret or frame is None:
                logger.warning("Frame read failed, reconnecting in %ds", self._reconnect_interval)
                self._connected = False
                time.sleep(self._reconnect_interval)
                continue

            with self._lock:
                self._frame = frame

            elapsed = time.monotonic() - loop_start
            sleep_time = frame_interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)


class MockCamera:
    """Synthetic camera for debug mode. Generates frames with simulated person blobs
    that cross the counting line, allowing the full pipeline to be tested without
    a real RTSP stream or Hailo hardware."""

    WIDTH  = 640
    HEIGHT = 480

    def __init__(self, fps_target: int = 10, person_interval: float = 15.0):
        self._fps_target = fps_target
        self._person_interval = person_interval  # seconds between new simulated persons
        self._frame: Optional[np.ndarray] = None
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._persons: list = []
        self._next_person_id = 0
        self._next_person_time = 0.0

    def start(self) -> None:
        self._running = True
        self._next_person_time = time.time() + 2.0
        self._thread = threading.Thread(target=self._generate_loop, daemon=True)
        self._thread.start()
        logger.info("MockCamera started (%dx%d, person every %.0fs)",
                    self.WIDTH, self.HEIGHT, self._person_interval)

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=5)

    def is_connected(self) -> bool:
        return self._running

    def get_frame(self) -> Optional[np.ndarray]:
        with self._lock:
            return self._frame.copy() if self._frame is not None else None

    def _generate_loop(self) -> None:
        interval = 1.0 / self._fps_target
        while self._running:
            t0 = time.monotonic()
            now = time.time()

            # Spawn a new simulated person periodically; alternates in/out direction
            if now >= self._next_person_time:
                pid = self._next_person_id
                self._next_person_id += 1
                # Even IDs move top→bottom (counted as "in"), odd IDs bottom→top ("out")
                x = int(self.WIDTH * (0.2 + 0.6 * (pid % 5) / 4))
                if pid % 2 == 0:
                    self._persons.append({"id": pid, "x": x, "y": 20, "vx": 0, "vy": 4})
                else:
                    self._persons.append({"id": pid, "x": x, "y": self.HEIGHT - 20, "vx": 0, "vy": -4})
                self._next_person_time = now + self._person_interval
                logger.debug("MockCamera: spawned person %d", pid)

            # Move persons and remove those that left the frame
            active = []
            for p in self._persons:
                p["x"] += p["vx"]
                p["y"] += p["vy"]
                if 0 <= p["y"] <= self.HEIGHT:
                    active.append(p)
            self._persons = active

            # Render frame
            frame = np.zeros((self.HEIGHT, self.WIDTH, 3), dtype=np.uint8)
            frame[:] = (30, 30, 30)
            line_y = self.HEIGHT // 2
            cv2.line(frame, (0, line_y), (self.WIDTH, line_y), (0, 200, 0), 2)
            cv2.putText(frame, "DEBUG MODE - MockCamera", (10, 20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 200), 1)
            for p in self._persons:
                cx, cy = int(p["x"]), int(p["y"])
                cv2.ellipse(frame, (cx, cy), (18, 30), 0, 0, 360, (0, 120, 255), -1)
                cv2.circle(frame, (cx, cy - 38), 12, (0, 120, 255), -1)

            with self._lock:
                self._frame = frame

            elapsed = time.monotonic() - t0
            sleep = interval - elapsed
            if sleep > 0:
                time.sleep(sleep)
