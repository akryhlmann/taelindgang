#!/usr/bin/env python3
"""
Hailo detection test — viser live kamerabillede med bounding boxes.

Trykker ESC eller Q for at afslutte.

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

PERSON_CLASS_ID = 0
COLOR = (0, 220, 80)   # grøn


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


def init_hailo(model_path: str, confidence: float):
    try:
        from hailo_platform import (
            HEF, VDevice, HailoStreamInterface,
            InferVStreams, ConfigureParams,
            InputVStreamParams, OutputVStreamParams, FormatType,
        )
    except ImportError:
        print("FEJL: hailo_platform ikke tilgængeligt")
        sys.exit(1)

    print(f"Indlæser model: {model_path}")
    hef    = HEF(model_path)
    device = VDevice()
    params = ConfigureParams.create_from_hef(hef, interface=HailoStreamInterface.PCIe)
    ng     = device.configure(hef, params)[0]

    in_params  = InputVStreamParams.make_from_network_group(ng, quantized=False, format_type=FormatType.FLOAT32)
    out_params = OutputVStreamParams.make_from_network_group(ng, quantized=False, format_type=FormatType.FLOAT32)
    input_name = list(in_params.keys())[0]
    print(f"Hailo klar — input stream: {input_name}")

    return device, ng, in_params, out_params, input_name, confidence


def preprocess(frame: np.ndarray, w: int, h: int) -> np.ndarray:
    resized = cv2.resize(frame, (w, h))
    rgb     = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
    return np.expand_dims(rgb.astype(np.float32) / 255.0, axis=0)


def infer(ng, in_params, out_params, input_name: str, data: np.ndarray):
    from hailo_platform import InferVStreams
    with ng.activate():
        with InferVStreams(ng, in_params, out_params) as pipeline:
            output = pipeline.infer({input_name: data})
    return list(output.values())[0][0]


def draw_boxes(frame: np.ndarray, detections: list, confidence: float) -> np.ndarray:
    out = frame.copy()
    fh, fw = out.shape[:2]
    count = 0
    for det in detections:
        if len(det) < 6:
            continue
        y1n, x1n, y2n, x2n, conf, cls = det[:6]
        if int(cls) != PERSON_CLASS_ID or conf < confidence:
            continue
        x1, y1 = int(x1n * fw), int(y1n * fh)
        x2, y2 = int(x2n * fw), int(y2n * fh)
        cv2.rectangle(out, (x1, y1), (x2, y2), COLOR, 2)
        cv2.putText(out, f"person {conf:.2f}", (x1, max(y1 - 6, 12)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, COLOR, 2)
        count += 1
    cv2.putText(out, f"Detektioner: {count}", (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
    cv2.putText(out, f"Detektioner: {count}", (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 1)
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="device1/config.yaml")
    args = parser.parse_args()

    cfg        = load_config(args.config)
    cam_cfg    = cfg["camera"]
    ai_cfg     = cfg["ai"]
    rtsp_url   = build_rtsp_url(cam_cfg)
    model_path = ai_cfg["model_path"]
    confidence = ai_cfg.get("confidence_threshold", 0.5)
    input_w    = ai_cfg.get("input_width",  640)
    input_h    = ai_cfg.get("input_height", 640)

    device, ng, in_params, out_params, input_name, confidence = \
        init_hailo(model_path, confidence)

    print(f"Åbner kamera: {rtsp_url}")
    cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
    if not cap.isOpened():
        print("FEJL: Kunne ikke åbne RTSP-stream")
        sys.exit(1)
    print("Kamera forbundet. Tryk ESC eller Q for at afslutte.")

    win = "Hailo detection test"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)

    fps_t   = time.time()
    fps_cnt = 0
    fps     = 0.0

    while True:
        ret, frame = cap.read()
        if not ret:
            print("Forbindelsen mistet — genforbinder...")
            cap.release()
            time.sleep(2)
            cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
            continue

        data = preprocess(frame, input_w, input_h)
        try:
            raw = infer(ng, in_params, out_params, input_name, data)
            display = draw_boxes(frame, raw, confidence)
        except Exception as exc:
            print(f"Inference fejl: {exc}")
            display = frame.copy()

        # FPS overlay
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
    device.release()
    print("Afsluttet.")


if __name__ == "__main__":
    main()
