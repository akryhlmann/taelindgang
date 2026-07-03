import copy
import logging
import os
import sys
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np
import yaml

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "device1", "config.yaml")

DIRECTIONS = ["top", "bottom", "left", "right"]

DIRECTION_COLORS = {
    "top": (0, 255, 0),
    "bottom": (0, 200, 255),
    "left": (255, 200, 0),
    "right": (200, 0, 255),
}

DIRECTION_ARROWS = {
    "top": (0, -40),
    "bottom": (0, 40),
    "left": (-40, 0),
    "right": (40, 0),
}


def load_config(path: str) -> dict:
    with open(path, "r") as f:
        return yaml.safe_load(f)


def save_config(path: str, cfg: dict) -> None:
    with open(path, "w") as f:
        yaml.safe_dump(cfg, f, default_flow_style=False, allow_unicode=True)
    logger.info("Config saved to %s", path)


def to_normalized(point: Tuple[int, int], w: int, h: int) -> List[float]:
    return [round(point[0] / w, 4), round(point[1] / h, 4)]


def to_pixels(normalized: List[float], w: int, h: int) -> Tuple[int, int]:
    return (int(normalized[0] * w), int(normalized[1] * h))


def draw_overlay(
    frame: np.ndarray,
    points: List[Optional[Tuple[int, int]]],
    in_direction: str,
) -> np.ndarray:
    out = frame.copy()
    h, w = out.shape[:2]

    instructions = [
        "Click two points to define counting line",
        "I: toggle IN direction  |  R: reset  |  S: save  |  Q: quit",
        f"IN direction: {in_direction.upper()}",
    ]
    for i, line in enumerate(instructions):
        cv2.putText(out, line, (10, 25 + i * 28), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        cv2.putText(out, line, (10, 25 + i * 28), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 1)

    color = DIRECTION_COLORS[in_direction]

    if points[0] is not None:
        cv2.circle(out, points[0], 6, color, -1)
        cv2.putText(out, "P1", (points[0][0] + 8, points[0][1] - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

    if points[0] is not None and points[1] is not None:
        p1, p2 = points[0], points[1]
        cv2.line(out, p1, p2, color, 2)
        cv2.circle(out, p2, 6, color, -1)
        cv2.putText(out, "P2", (p2[0] + 8, p2[1] - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

        mid = ((p1[0] + p2[0]) // 2, (p1[1] + p2[1]) // 2)
        dx, dy = DIRECTION_ARROWS[in_direction]
        cv2.arrowedLine(out, mid, (mid[0] + dx, mid[1] + dy), color, 2, tipLength=0.4)

        norm_p1 = to_normalized(p1, w, h)
        norm_p2 = to_normalized(p2, w, h)
        cv2.putText(
            out,
            f"P1={norm_p1}  P2={norm_p2}",
            (10, h - 15),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (200, 200, 200),
            1,
        )

    return out


def main() -> None:
    config_path = os.path.abspath(CONFIG_PATH)
    if not os.path.exists(config_path):
        logger.error("Config not found: %s", config_path)
        sys.exit(1)

    cfg = load_config(config_path)
    cam_cfg = cfg["camera"]
    rtsp_url = cam_cfg["rtsp_url"]
    username = cam_cfg.get("username", "")
    password = cam_cfg.get("password", "")
    if username and "@" not in rtsp_url:
        proto, rest = rtsp_url.split("://", 1)
        rtsp_url = f"{proto}://{username}:{password}@{rest}"
    counting_cfg = cfg["counting"]["line"]

    in_direction = counting_cfg.get("in_direction", "top")
    direction_idx = DIRECTIONS.index(in_direction) if in_direction in DIRECTIONS else 0

    cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
    if not cap.isOpened():
        logger.warning("Could not open RTSP stream %s, using blank frame", rtsp_url)
        frame_w, frame_h = 1280, 720
        blank_frame = np.zeros((frame_h, frame_w, 3), dtype=np.uint8)
        cv2.putText(blank_frame, "No camera feed - click to set points",
                    (50, frame_h // 2), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (180, 180, 180), 2)
        static_frame = blank_frame
    else:
        ret, static_frame = cap.read()
        if not ret:
            logger.error("Failed to read frame from camera")
            cap.release()
            sys.exit(1)
        cap.release()
        frame_h, frame_w = static_frame.shape[:2]

    # Pre-populate from existing config
    p1_norm = counting_cfg.get("point1", [0.2, 0.5])
    p2_norm = counting_cfg.get("point2", [0.8, 0.5])
    points: List[Optional[Tuple[int, int]]] = [
        to_pixels(p1_norm, frame_w, frame_h),
        to_pixels(p2_norm, frame_w, frame_h),
    ]

    window_name = "Configure Counting Line"
    cv2.namedWindow(window_name)

    click_state = {"next": 0}

    def on_mouse(event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            idx = click_state["next"]
            points[idx] = (x, y)
            click_state["next"] = 1 - idx

    cv2.setMouseCallback(window_name, on_mouse)

    logger.info("Configure counting line — window opened")
    logger.info("Click two points on the image to define the line.")
    logger.info("Keys: I=toggle IN direction, R=reset, S=save, Q=quit")

    while True:
        current_direction = DIRECTIONS[direction_idx]
        display = draw_overlay(static_frame, points, current_direction)
        cv2.imshow(window_name, display)
        key = cv2.waitKey(30) & 0xFF

        if key == ord("q"):
            logger.info("Quit without saving")
            break
        elif key == ord("i"):
            direction_idx = (direction_idx + 1) % len(DIRECTIONS)
            logger.info("IN direction toggled to: %s", DIRECTIONS[direction_idx])
        elif key == ord("r"):
            points[0] = None
            points[1] = None
            click_state["next"] = 0
            logger.info("Points reset")
        elif key == ord("s"):
            if points[0] is None or points[1] is None:
                logger.warning("Both points must be set before saving")
                continue
            norm_p1 = to_normalized(points[0], frame_w, frame_h)
            norm_p2 = to_normalized(points[1], frame_w, frame_h)
            cfg["counting"]["line"]["point1"] = norm_p1
            cfg["counting"]["line"]["point2"] = norm_p2
            cfg["counting"]["line"]["in_direction"] = DIRECTIONS[direction_idx]
            save_config(config_path, cfg)
            logger.info("Saved: point1=%s point2=%s in_direction=%s",
                        norm_p1, norm_p2, DIRECTIONS[direction_idx])
            break

    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
