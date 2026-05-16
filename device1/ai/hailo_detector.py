import logging
import random
from typing import List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

try:
    from hailo_platform import (
        HEF,
        ConfigureParams,
        FormatType,
        HailoSchedulingAlgorithm,
        HailoStreamInterface,
        InferVStreams,
        InputVStreamParams,
        OutputVStreamParams,
        VDevice,
    )
    HAILO_AVAILABLE = True
    logger.info("hailo_platform imported successfully")
except ImportError:
    HAILO_AVAILABLE = False
    logger.warning("hailo_platform not available, running in mock mode")


COCO_PERSON_CLASS_ID = 0


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
        self._mock_mode = not HAILO_AVAILABLE

        self._device: Optional[object] = None
        self._network_group: Optional[object] = None
        self._input_vstream_params = None
        self._output_vstream_params = None

        if not self._mock_mode:
            self._initialize_hailo()

    def _initialize_hailo(self) -> None:
        try:
            self._hef = HEF(self._model_path)
            self._device = VDevice()

            configure_params = ConfigureParams.create_from_hef(
                self._hef, interface=HailoStreamInterface.PCIe
            )
            network_groups = self._device.configure(self._hef, configure_params)
            self._network_group = network_groups[0]

            self._input_vstream_params = InputVStreamParams.make_from_network_group(
                self._network_group, quantized=False, format_type=FormatType.FLOAT32
            )
            self._output_vstream_params = OutputVStreamParams.make_from_network_group(
                self._network_group, quantized=False, format_type=FormatType.FLOAT32
            )
            logger.info("Hailo device initialized with model: %s", self._model_path)
        except Exception as exc:
            logger.error("Hailo initialization failed: %s", exc)
            self._mock_mode = True
            logger.warning("Falling back to mock mode")

    def _preprocess(self, frame: np.ndarray) -> np.ndarray:
        resized = cv2.resize(frame, (self._input_width, self._input_height))
        normalized = resized.astype(np.float32) / 255.0
        return np.expand_dims(normalized, axis=0)

    def _postprocess(
        self, raw_output: np.ndarray, orig_h: int, orig_w: int
    ) -> List[dict]:
        detections = []
        if raw_output.ndim == 3:
            raw_output = raw_output[0]

        for row in raw_output:
            if len(row) < 6:
                continue
            x_center, y_center, w, h, obj_conf, *class_scores = row
            class_id = int(np.argmax(class_scores))
            confidence = float(obj_conf) * float(class_scores[class_id])

            if class_id != COCO_PERSON_CLASS_ID:
                continue
            if confidence < self._confidence_threshold:
                continue

            x1 = int((x_center - w / 2) * orig_w)
            y1 = int((y_center - h / 2) * orig_h)
            x2 = int((x_center + w / 2) * orig_w)
            y2 = int((y_center + h / 2) * orig_h)

            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(orig_w, x2), min(orig_h, y2)

            cx = (x1 + x2) // 2
            cy = (y1 + y2) // 2

            detections.append(
                {
                    "bbox": (x1, y1, x2, y2),
                    "confidence": confidence,
                    "class_id": class_id,
                    "centroid": (cx, cy),
                }
            )

        return detections

    def _mock_detect(self, frame: np.ndarray) -> List[dict]:
        h, w = frame.shape[:2]
        detections = []
        num_persons = random.randint(0, 3)
        for _ in range(num_persons):
            x1 = random.randint(0, w - 100)
            y1 = random.randint(0, h - 150)
            x2 = x1 + random.randint(40, 100)
            y2 = y1 + random.randint(80, 150)
            x2, y2 = min(x2, w), min(y2, h)
            cx = (x1 + x2) // 2
            cy = (y1 + y2) // 2
            detections.append(
                {
                    "bbox": (x1, y1, x2, y2),
                    "confidence": round(random.uniform(0.5, 0.99), 3),
                    "class_id": COCO_PERSON_CLASS_ID,
                    "centroid": (cx, cy),
                }
            )
        return detections

    def detect(self, frame: np.ndarray) -> List[dict]:
        if self._mock_mode:
            return self._mock_detect(frame)

        orig_h, orig_w = frame.shape[:2]
        preprocessed = self._preprocess(frame)

        try:
            with InferVStreams(
                self._network_group,
                self._input_vstream_params,
                self._output_vstream_params,
            ) as pipeline:
                input_data = {
                    pipeline.get_input_vstreams()[0].name: preprocessed
                }
                with self._network_group.activate():
                    raw_results = pipeline.infer(input_data)

            output_name = list(raw_results.keys())[0]
            raw_output = raw_results[output_name]
            return self._postprocess(raw_output, orig_h, orig_w)

        except Exception as exc:
            logger.error("Inference error: %s", exc)
            return []

    def close(self) -> None:
        if self._device is not None:
            try:
                self._device.release()
            except Exception as exc:
                logger.warning("Error releasing Hailo device: %s", exc)


try:
    import cv2
except ImportError:
    pass
