#!/usr/bin/env python3
"""
Hailo pipeline diagnostic — grabs one frame, pushes it through the
GStreamer/Hailo pipeline, and prints everything about the ROI output.

Usage:
    python tools/hailo_diag.py [--config device1/config.yaml]
"""
import argparse
import os
import sys
import time
import threading
from pathlib import Path

import cv2
import numpy as np
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))


def load_config(path):
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


def build_rtsp_url(cam_cfg):
    url = cam_cfg["rtsp_url"]
    username = cam_cfg.get("username", "")
    password = cam_cfg.get("password", "")
    if username and "@" not in url:
        proto, rest = url.split("://", 1)
        url = f"{proto}://{username}:{password}@{rest}"
    return url


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="device1/config.yaml")
    parser.add_argument("--video", default=None,
                        help="Path to video file (default: use RTSP from config)")
    parser.add_argument("--frames", type=int, default=10,
                        help="Number of frames to push through pipeline")
    args = parser.parse_args()

    cfg = load_config(args.config)
    cam_cfg = cfg["camera"]
    ai_cfg = cfg["ai"]
    rtsp_url = build_rtsp_url(cam_cfg)
    model_path = ai_cfg["model_path"]
    input_w = ai_cfg.get("input_width", 640)
    input_h = ai_cfg.get("input_height", 640)

    # ---- GStreamer setup ----
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst
    import hailo

    Gst.init(None)

    from device1.ai.hailo_detector import _find_postproc_lib
    postproc = _find_postproc_lib()
    if not postproc:
        print("FEJL: libyolo_hailortpp_post.so ikke fundet")
        sys.exit(1)
    print(f"Post-processing library: {postproc}")
    print(f"Model: {model_path}")

    caps = f"video/x-raw,format=BGR,width={input_w},height={input_h},framerate=10/1"
    pipeline_str = (
        f'appsrc name=src format=time is-live=true block=true caps="{caps}" ! '
        f"videoconvert ! video/x-raw,format=RGB ! "
        f"hailonet hef-path={model_path} ! "
        f'hailofilter so-path="{postproc}" function-name=yolov8s qos=false ! '
        f"appsink name=sink emit-signals=true drop=false max-buffers=4 sync=false"
    )
    print(f"\nPipeline:\n  {pipeline_str}\n")

    pipeline = Gst.parse_launch(pipeline_str)
    appsrc = pipeline.get_by_name("src")
    appsink = pipeline.get_by_name("sink")

    callback_count = [0]
    total_objects = [0]
    received = threading.Event()

    def on_new_sample(sink):
        sample = sink.emit("pull-sample")
        if not sample:
            return Gst.FlowReturn.OK

        buf = sample.get_buffer()
        callback_count[0] += 1
        n = callback_count[0]

        try:
            roi = hailo.get_roi_from_buffer(buf)
            all_objs = roi.get_objects()
            dets = roi.get_objects_typed(hailo.HAILO_DETECTION)

            print(f"\n[Frame {n}] ROI objects total: {len(all_objs)}  "
                  f"HAILO_DETECTION: {len(dets)}")

            for i, obj in enumerate(all_objs):
                obj_type = type(obj).__name__
                print(f"  obj[{i}] type={obj_type}")

            for i, det in enumerate(dets):
                bbox = det.get_bbox()
                total_objects[0] += 1
                print(f"  det[{i}] class_id={det.get_class_id()} "
                      f"label={det.get_label()!r} "
                      f"conf={det.get_confidence():.3f} "
                      f"bbox=({bbox.xmin():.3f},{bbox.ymin():.3f},"
                      f"{bbox.xmin()+bbox.width():.3f},{bbox.ymin()+bbox.height():.3f})")

        except Exception as exc:
            print(f"[Frame {n}] ROI error: {exc}")

        received.set()
        return Gst.FlowReturn.OK

    appsink.connect("new-sample", on_new_sample)

    # Bus error watcher
    def bus_watcher():
        bus = pipeline.get_bus()
        while True:
            msg = bus.timed_pop_filtered(
                200 * Gst.MSECOND,
                Gst.MessageType.ERROR | Gst.MessageType.EOS | Gst.MessageType.WARNING,
            )
            if msg is None:
                continue
            if msg.type == Gst.MessageType.ERROR:
                err, debug = msg.parse_error()
                print(f"GStreamer ERROR: {err} — {debug}")
            elif msg.type == Gst.MessageType.WARNING:
                w, debug = msg.parse_warning()
                print(f"GStreamer WARNING: {w} — {debug}")
            elif msg.type == Gst.MessageType.EOS:
                print("GStreamer EOS")

    threading.Thread(target=bus_watcher, daemon=True).start()

    ret = pipeline.set_state(Gst.State.PLAYING)
    if ret == Gst.StateChangeReturn.FAILURE:
        print("FEJL: Pipeline kunne ikke starte")
        sys.exit(1)
    print("Pipeline startet — venter på PLAYING state...")
    pipeline.get_state(5 * Gst.SECOND)
    print("Pipeline klar\n")

    # ---- Grab frames from video or camera ----
    source = args.video if args.video else rtsp_url
    print(f"Kilde: {source}")
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        print(f"FEJL: Kunne ikke åbne {source}")
        sys.exit(1)

    if not args.video:
        # Discard a few frames to let camera stabilise
        for _ in range(5):
            cap.read()

    ret_f, frame = cap.read()
    cap.release()
    if not ret_f:
        print("FEJL: Kunne ikke læse ramme")
        sys.exit(1)

    print(f"Ramme hentet: {frame.shape[1]}x{frame.shape[0]}")

    # Save the frame so you can inspect what the camera sees
    cv2.imwrite("/tmp/hailo_diag_frame.jpg", frame)
    print("Ramme gemt til /tmp/hailo_diag_frame.jpg — tjek at der er personer i billedet!")

    # Resize to model input
    resized = cv2.resize(frame, (input_w, input_h))

    # Push the same frame multiple times to prime the pipeline
    print(f"\nPusher {args.frames} rammer igennem pipeline...")
    pts = 0
    for i in range(args.frames):
        buf = Gst.Buffer.new_wrapped(resized.tobytes())
        buf.pts = pts
        buf.duration = Gst.SECOND // 10
        pts += buf.duration

        received.clear()
        flow = appsrc.emit("push-buffer", buf)
        if flow != Gst.FlowReturn.OK:
            print(f"  push-buffer fejl: {flow}")
            continue

        # Wait up to 3 seconds for callback
        if not received.wait(timeout=3.0):
            print(f"  [Frame {i+1}] TIMEOUT — callback ikke kaldt!")
        time.sleep(0.05)

    pipeline.set_state(Gst.State.NULL)

    print(f"\n=== Resultat ===")
    print(f"Callbacks modtaget: {callback_count[0]}")
    print(f"Detektioner i alt: {total_objects[0]}")
    if callback_count[0] == 0:
        print("PROBLEM: appsink callback blev aldrig kaldt — pipeline processerer ikke rammer")
    elif total_objects[0] == 0:
        print("PROBLEM: Pipeline kørte men fandt ingen objekter — tjek /tmp/hailo_diag_frame.jpg")
        print("  Er der faktisk personer i billedet? Prøv at sænke confidence til 0.1 i config")
    else:
        print("SUCCESS: Pipeline detekterede objekter!")


if __name__ == "__main__":
    main()
