import glob
import logging
import os
import random
import threading
from typing import List, Optional

import numpy as np

logger = logging.getLogger(__name__)

PERSON_CLASS_ID = 0

# Hailo YOLO post-processing library locations.
# tappas 5.x: libyolo_hailortpp_post.so (note _post suffix, new path)
# tappas 4.x: libyolo_hailortpp.so
_POSTPROC_CANDIDATES = [
    "/usr/lib/aarch64-linux-gnu/hailo/tappas/post_processes/libyolo_hailortpp_post.so",
    "/usr/lib/aarch64-linux-gnu/hailo/tappas/post_processes/libyolo_hailortpp.so",
    "/usr/lib/hailo/post_proc/libyolo_hailortpp.so",
    "/usr/lib/aarch64-linux-gnu/hailo/post_proc/libyolo_hailortpp.so",
    "/usr/local/lib/hailo/post_proc/libyolo_hailortpp.so",
]


def _find_postproc_lib() -> Optional[str]:
    for path in _POSTPROC_CANDIDATES:
        if os.path.exists(path):
            return path
    # Fallback: recursive search
    found = (
        glob.glob("/usr/**/libyolo_hailortpp.so", recursive=True)
        + glob.glob("/opt/**/libyolo_hailortpp.so", recursive=True)
    )
    return found[0] if found else None


class HailoDetector:
    """
    Person detector using Hailo-8L via GStreamer pipeline.

    Pipeline: appsrc (BGR frames) → videoconvert (RGB) → hailonet → hailofilter
              (YOLO post-processing) → appsink (ROI callbacks)

    Falls back to mock mode if GStreamer/Hailo is not available.
    """

    def __init__(
        self,
        model_path: str,
        confidence_threshold: float = 0.5,
        input_width: int = 640,
        input_height: int = 640,
    ):
        self._model_path = model_path
        self._confidence_threshold = confidence_threshold
        self._input_width = input_width
        self._input_height = input_height
        self._mock_mode = False

        self._latest_detections: List[dict] = []
        self._lock = threading.Lock()
        self._pipeline = None
        self._appsrc = None
        self._Gst = None
        self._loop = None
        self._loop_thread = None
        self._pts = 0
        self._hailo_mod = None

        if not self._init_gstreamer():
            logger.warning("GStreamer/Hailo not available — running in mock mode")
            self._mock_mode = True

    def _init_gstreamer(self) -> bool:
        try:
            import gi
            gi.require_version("Gst", "1.0")
            from gi.repository import Gst, GLib
            import hailo as hailo_mod
        except (ImportError, ValueError) as exc:
            logger.warning("GStreamer/hailo import failed: %s", exc)
            return False

        self._hailo_mod = hailo_mod
        Gst.init(None)

        postproc = _find_postproc_lib()
        if not postproc:
            logger.warning(
                "libyolo_hailortpp_post.so not found — "
                "expected at /usr/lib/aarch64-linux-gnu/hailo/tappas/post_processes/"
            )
            return False

        logger.info("Post-processing library: %s", postproc)

        w, h = self._input_width, self._input_height
        caps = f"video/x-raw,format=BGR,width={w},height={h},framerate=10/1"
        pipeline_str = (
            f'appsrc name=src format=time is-live=true block=true caps="{caps}" ! '
            f"videoconvert ! video/x-raw,format=RGB ! "
            f"hailonet hef-path={self._model_path} ! "
            f'hailofilter so-path="{postproc}" function-name=yolov8 qos=false ! '
            f"appsink name=sink emit-signals=true drop=true max-buffers=1"
        )

        try:
            pipeline = Gst.parse_launch(pipeline_str)
        except Exception as exc:
            logger.warning("GStreamer pipeline parse failed: %s", exc)
            return False

        appsrc = pipeline.get_by_name("src")
        appsink = pipeline.get_by_name("sink")
        if appsrc is None or appsink is None:
            logger.warning("Could not get appsrc/appsink elements from pipeline")
            return False

        appsink.connect("new-sample", self._on_new_sample)

        bus = pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", self._on_gst_error)

        ret = pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            logger.warning("GStreamer pipeline failed to transition to PLAYING")
            pipeline.set_state(Gst.State.NULL)
            return False

        self._pipeline = pipeline
        self._appsrc = appsrc
        self._Gst = Gst

        # GLib main loop in daemon thread — needed for bus signals and appsink callbacks
        self._loop = GLib.MainLoop()
        self._loop_thread = threading.Thread(target=self._loop.run, daemon=True, name="gst-loop")
        self._loop_thread.start()

        logger.info("HailoDetector ready (GStreamer, model=%s)", self._model_path)
        return True

    def _on_new_sample(self, sink):
        sample = sink.emit("pull-sample")
        if not sample:
            return self._Gst.FlowReturn.OK

        buf = sample.get_buffer()
        try:
            roi = self._hailo_mod.get_roi_from_buffer(buf)
            dets = []
            for obj in roi.get_objects_typed(self._hailo_mod.HAILO_DETECTION):
                if obj.get_class_id() != PERSON_CLASS_ID:
                    continue
                conf = obj.get_confidence()
                if conf < self._confidence_threshold:
                    continue
                bbox = obj.get_bbox()
                dets.append({
                    "xmin_n": float(bbox.xmin()),
                    "ymin_n": float(bbox.ymin()),
                    "xmax_n": float(bbox.xmin() + bbox.width()),
                    "ymax_n": float(bbox.ymin() + bbox.height()),
                    "confidence": float(conf),
                    "class_id": int(obj.get_class_id()),
                })
            with self._lock:
                self._latest_detections = dets
        except Exception as exc:
            logger.debug("Detection callback error: %s", exc)

        return self._Gst.FlowReturn.OK

    def _on_gst_error(self, bus, msg):
        err, debug = msg.parse_error()
        logger.error("GStreamer pipeline error: %s — %s", err, debug)

    def detect(self, frame: np.ndarray) -> List[dict]:
        if self._mock_mode:
            return self._mock_detect(frame)
        return self._gst_detect(frame)

    def _gst_detect(self, frame: np.ndarray) -> List[dict]:
        import cv2
        frame_h, frame_w = frame.shape[:2]

        if frame_w != self._input_width or frame_h != self._input_height:
            resized = cv2.resize(frame, (self._input_width, self._input_height))
        else:
            resized = frame

        buf = self._Gst.Buffer.new_wrapped(resized.tobytes())
        buf.pts = self._pts
        buf.duration = self._Gst.SECOND // 10
        self._pts += buf.duration

        ret = self._appsrc.emit("push-buffer", buf)
        if ret != self._Gst.FlowReturn.OK:
            logger.warning("push-buffer returned: %s", ret)
            return []

        with self._lock:
            raw = list(self._latest_detections)

        results = []
        for d in raw:
            x1 = int(d["xmin_n"] * frame_w)
            y1 = int(d["ymin_n"] * frame_h)
            x2 = int(d["xmax_n"] * frame_w)
            y2 = int(d["ymax_n"] * frame_h)
            results.append({
                "bbox": (x1, y1, x2, y2),
                "confidence": d["confidence"],
                "class_id": d["class_id"],
                "centroid": ((x1 + x2) // 2, (y1 + y2) // 2),
            })
        return results

    def _mock_detect(self, frame: np.ndarray) -> List[dict]:
        frame_h, frame_w = frame.shape[:2]
        detections = []
        num = random.choices([0, 1, 2], weights=[0.5, 0.35, 0.15])[0]
        for _ in range(num):
            x1 = random.randint(0, frame_w - 80)
            y1 = random.randint(0, frame_h - 160)
            x2 = x1 + random.randint(40, 80)
            y2 = y1 + random.randint(80, 160)
            x2 = min(x2, frame_w - 1)
            y2 = min(y2, frame_h - 1)
            detections.append({
                "bbox": (x1, y1, x2, y2),
                "confidence": round(random.uniform(self._confidence_threshold, 1.0), 3),
                "class_id": PERSON_CLASS_ID,
                "centroid": ((x1 + x2) // 2, (y1 + y2) // 2),
            })
        return detections

    def close(self) -> None:
        if self._pipeline is not None:
            try:
                self._pipeline.set_state(self._Gst.State.NULL)
            except Exception as exc:
                logger.warning("Error stopping GStreamer pipeline: %s", exc)
        if self._loop is not None:
            try:
                self._loop.quit()
            except Exception:
                pass
        logger.info("HailoDetector closed")


class MockDetector:
    """Detects orange blobs rendered by MockCamera via HSV color thresholding."""

    def detect(self, frame: np.ndarray) -> List[dict]:
        import cv2
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv,
                           np.array([5,  80,  80]),
                           np.array([25, 255, 255]))
        mask = cv2.dilate(mask, None, iterations=2)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        detections = []
        for cnt in contours:
            if cv2.contourArea(cnt) < 200:
                continue
            x, y, w, h = cv2.boundingRect(cnt)
            detections.append({
                "bbox": (x, y, x + w, y + h),
                "confidence": 0.99,
                "class_id": PERSON_CLASS_ID,
                "centroid": (x + w // 2, y + h // 2),
            })
        return detections

    def close(self) -> None:
        pass
