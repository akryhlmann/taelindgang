#!/usr/bin/env python3
"""
Hailo detection test — viser live kamerabillede med bounding boxes.

Bruger GStreamer pipeline via HailoDetector (samme kode som produktion).
Tryk ESC eller Q for at afslutte.

Usage:
    python tools/hailo_test.py [--config device1/config.yaml]
"""
import argparse
import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from device1.ai.hailo_detector import HailoDetector

COLOR_PERSON = (0, 220, 80)   # grøn


def load_config(path: str) -> dict:
    def expand(obj):
        if isinstance(obj, str):
            return os.path.expanduser(obj)
        if isinstance(obj, dict):
            return {k: expand(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [expand(i) for i in obj]
        return obj
    with open(path) as f:
        return expand(yaml.safe_load(f))


def build_rtsp_url(cam_cfg: dict) -> str:
    url = cam_cfg["rtsp_url"]
    username = cam_cfg.get("username", "")
    password = cam_cfg.get("password", "")
    if username and "@" not in url:
        proto, rest = url.split("://", 1)
        url = f"{proto}://{username}:{password}@{rest}"
    return url


def draw_detections(frame: np.ndarray, detections: list) -> np.ndarray:
    out = frame.copy()
    for det in detections:
        x1, y1, x2, y2 = det["bbox"]
        conf = det["confidence"]
        cv2.rectangle(out, (x1, y1), (x2, y2), COLOR_PERSON, 2)
        cv2.putText(out, f"person {conf:.2f}", (x1, max(y1 - 6, 12)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, COLOR_PERSON, 2)

    count = len(detections)
    cv2.putText(out, f"Detektioner: {count}", (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
    cv2.putText(out, f"Detektioner: {count}", (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 1)
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="device1/config.yaml")
    parser.add_argument("--video", default=None,
                        help="Path to video file instead of RTSP stream")
    args = parser.parse_args()

    cfg        = load_config(args.config)
    cam_cfg    = cfg["camera"]
    ai_cfg     = cfg["ai"]
    rtsp_url   = build_rtsp_url(cam_cfg)
    model_path = ai_cfg["model_path"]
    confidence = ai_cfg.get("confidence_threshold", 0.5)
    input_w    = ai_cfg.get("input_width",  640)
    input_h    = ai_cfg.get("input_height", 640)

    # Open the OpenCV window BEFORE initialising GStreamer so GTK is claimed first,
    # avoiding the GLib context conflict that causes a Pango crash.
    win = "Hailo detection test"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)

    print(f"Indlæser model: {model_path}")
    detector = HailoDetector(
        model_path=model_path,
        confidence_threshold=confidence,
        input_width=input_w,
        input_height=input_h,
    )
    if detector._mock_mode:
        print("ADVARSEL: Hailo/GStreamer ikke tilgængeligt — kører i mock-tilstand")
        print("  Tjek at hailo-all og hailo-tappas-core er installeret")
    else:
        print("Hailo GStreamer pipeline klar")

    source = args.video if args.video else rtsp_url
    print(f"Åbner kilde: {source}")
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        print(f"FEJL: Kunne ikke åbne {source}")
        detector.close()
        sys.exit(1)
    print("Forbundet. Tryk ESC eller Q for at afslutte.")

    fps_t   = time.time()
    fps_cnt = 0
    fps     = 0.0

    while True:
        ret, frame = cap.read()
        if not ret:
            if args.video:
                # Loop video file
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                continue
            print("Forbindelsen mistet — genforbinder...")
            cap.release()
            time.sleep(2)
            cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
            continue

        detections = detector.detect(frame)
        display = draw_detections(frame, detections)

        fps_cnt += 1
        elapsed = time.time() - fps_t
        if elapsed >= 1.0:
            fps = fps_cnt / elapsed
            fps_cnt = 0
            fps_t = time.time()

        fh, fw = display.shape[:2]
        cv2.putText(display, f"{fps:.1f} fps", (fw - 110, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)

        cv2.imshow(win, display)
        key = cv2.waitKey(1) & 0xFF
        if key in (27, ord("q")):
            break

    cap.release()
    cv2.destroyAllWindows()
    detector.close()
    print("Afsluttet.")


if __name__ == "__main__":
    main()
