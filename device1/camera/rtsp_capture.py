import logging
import threading
import time
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)


class RTSPCapture:
    def __init__(self, rtsp_url: str, fps_target: int = 10, reconnect_interval: int = 5):
        self._rtsp_url = rtsp_url
        self._fps_target = fps_target
        self._reconnect_interval = reconnect_interval

        self._cap: Optional[cv2.VideoCapture] = None
        self._frame: Optional[np.ndarray] = None
        self._frame_lock = threading.Lock()
        self._connected = False
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        logger.info("RTSPCapture started for %s", self._rtsp_url)

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        if self._cap is not None:
            self._cap.release()
        logger.info("RTSPCapture stopped")

    def is_connected(self) -> bool:
        return self._connected

    def get_frame(self) -> Optional[np.ndarray]:
        with self._frame_lock:
            if self._frame is None:
                return None
            return self._frame.copy()

    def _connect(self) -> bool:
        if self._cap is not None:
            self._cap.release()

        self._cap = cv2.VideoCapture(self._rtsp_url, cv2.CAP_FFMPEG)
        self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        if not self._cap.isOpened():
            logger.warning("Failed to open RTSP stream: %s", self._rtsp_url)
            return False

        logger.info("Connected to RTSP stream: %s", self._rtsp_url)
        return True

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
                logger.warning("Frame read failed, reconnecting...")
                self._connected = False
                time.sleep(self._reconnect_interval)
                continue

            with self._frame_lock:
                self._frame = frame

            elapsed = time.monotonic() - loop_start
            sleep_time = frame_interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)
