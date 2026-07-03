import logging
import random
from typing import List, Optional

import numpy as np

logger = logging.getLogger(__name__)

PERSON_CLASS_ID = 0


def _try_import_hailo():
    try:
        from hailo_platform import (
            HEF,
            VDevice,
            HailoStreamInterface,
            InferVStreams,
            ConfigureParams,
            InputVStreamParams,
            OutputVStreamParams,
            FormatType,
        )
        return True, {
            "HEF": HEF,
            "VDevice": VDevice,
            "HailoStreamInterface": HailoStreamInterface,
            "InferVStreams": InferVStreams,
            "ConfigureParams": ConfigureParams,
            "InputVStreamParams": InputVStreamParams,
            "OutputVStreamParams": OutputVStreamParams,
            "FormatType": FormatType,
        }
    except ImportError:
        return False, {}


class HailoDetector:
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
        self._device = None
        self._network_group = None
        self._infer_pipeline = None

        hailo_available, self._hailo = _try_import_hailo()
        if not hailo_available:
            logger.warning("hailo_platform not available, running in mock mode")
            self._mock_mode = True
        else:
            self._initialize_hailo()

    def _initialize_hailo(self) -> None:
        try:
            hef = self._hailo["HEF"](self._model_path)
            self._device = self._hailo["VDevice"]()
            configure_params = self._hailo["ConfigureParams"].create_from_hef(
                hef, interface=self._hailo["HailoStreamInterface"].PCIe
            )
            network_groups = self._device.configure(hef, configure_params)
            self._network_group = network_groups[0]

            input_vstreams_params = self._hailo["InputVStreamParams"].make_from_network_group(
                self._network_group,
                quantized=False,
                format_type=self._hailo["FormatType"].FLOAT32,
            )
            output_vstreams_params = self._hailo["OutputVStreamParams"].make_from_network_group(
                self._network_group,
                quantized=False,
                format_type=self._hailo["FormatType"].FLOAT32,
            )
            # Store input stream name — keys of InputVStreamParams dict (HailoRT 4.x)
            self._input_name = list(input_vstreams_params.keys())[0]
            self._input_vstreams_params = input_vstreams_params
            self._output_vstreams_params = output_vstreams_params

            logger.info("HailoDetector initialized with model: %s", self._model_path)
        except Exception as exc:
            logger.error("Failed to initialize Hailo device: %s — switching to mock mode", exc)
            self._mock_mode = True

    def detect(self, frame: np.ndarray) -> List[dict]:
        if self._mock_mode:
            return self._mock_detect(frame)
        return self._hailo_detect(frame)

    def _preprocess(self, frame: np.ndarray) -> np.ndarray:
        import cv2
        resized = cv2.resize(frame, (self._input_width, self._input_height))
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
        normalized = rgb.astype(np.float32) / 255.0
        return np.expand_dims(normalized, axis=0)

    def _hailo_detect(self, frame: np.ndarray) -> List[dict]:
        frame_h, frame_w = frame.shape[:2]
        input_data = self._preprocess(frame)
        results = []
        try:
            with self._network_group.activate():
                with self._hailo["InferVStreams"](
                    self._network_group,
                    self._input_vstreams_params,
                    self._output_vstreams_params,
                ) as pipeline:
                    output = pipeline.infer({self._input_name: input_data})
            raw_detections = list(output.values())[0][0]
            for det in raw_detections:
                if len(det) < 6:
                    continue
                y1_n, x1_n, y2_n, x2_n, confidence, class_id = det[:6]
                if int(class_id) != PERSON_CLASS_ID:
                    continue
                if confidence < self._confidence_threshold:
                    continue
                x1 = int(x1_n * frame_w)
                y1 = int(y1_n * frame_h)
                x2 = int(x2_n * frame_w)
                y2 = int(y2_n * frame_h)
                cx = (x1 + x2) // 2
                cy = (y1 + y2) // 2
                results.append({
                    "bbox": (x1, y1, x2, y2),
                    "confidence": float(confidence),
                    "class_id": int(class_id),
                    "centroid": (cx, cy),
                })
        except Exception as exc:
            logger.error("Hailo inference error: %s", exc)
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
        if self._device is not None:
            try:
                self._device.release()
            except Exception as exc:
                logger.warning("Error releasing Hailo device: %s", exc)
        logger.info("HailoDetector closed")


class MockDetector:
    """Detects the orange blobs rendered by MockCamera via HSV color thresholding.
    Used in debug mode so the tracker and line counter see realistic, consistent
    detections tied to the actual blob positions in the frame."""

    def detect(self, frame: np.ndarray) -> List[dict]:
        import cv2
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        # BGR (0, 120, 255) is orange: HSV hue ~15°, broad saturation/value range
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
