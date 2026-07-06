import datetime
import logging
import os
import signal
import sys
import time
from collections import OrderedDict
from pathlib import Path
from typing import Optional

import yaml

_BASE = Path(__file__).parent
sys.path.insert(0, str(_BASE.parent))

from shared.protocol import MessageType, encode_message
from device1.camera.rtsp_capture import RTSPCapture, MockCamera
from device1.ai.hailo_detector import HailoDetector, MockDetector
from device1.counter.tracker import CentroidTracker
from device1.counter.line_counter import LineCounter
from device1.storage.local_storage import LocalStorage
from device1.lora.transmitter import LoRaTransmitter
import threading
from device1.setup_mode.shared_state import SharedState
from device1.setup_mode.web_app import create_app


def _setup_logging(cfg: dict) -> None:
    log_cfg = cfg.get("logging", {})
    level = getattr(logging, log_cfg.get("level", "INFO"))
    log_file = log_cfg.get("log_file", "/tmp/device1.log")
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_file),
    ]
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=handlers,
    )


def _expand_paths(obj):
    """Recursively expand ~ in string values."""
    if isinstance(obj, str):
        return os.path.expanduser(obj)
    if isinstance(obj, dict):
        return {k: _expand_paths(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_paths(i) for i in obj]
    return obj


def _load_config(path: str) -> dict:
    with open(path, "r") as f:
        return _expand_paths(yaml.safe_load(f))


def _event_window(schedule: dict) -> tuple:
    """Return (open_dt, close_dt) for today, or (None, None) if today is not in schedule."""
    day_name = datetime.datetime.now().strftime("%A").lower()
    day = schedule.get(day_name)
    if not day:
        return None, None
    now = datetime.datetime.now()
    open_h,  open_m  = map(int, day["open"].split(":"))
    close_h, close_m = map(int, day["close"].split(":"))
    open_dt  = now.replace(hour=open_h,  minute=open_m,  second=0, microsecond=0)
    close_dt = now.replace(hour=close_h, minute=close_m, second=0, microsecond=0)
    return open_dt, close_dt


def _persist_line_config(cfg: dict, line_update: dict) -> None:
    """Write updated line config back to config.yaml so it survives restarts."""
    try:
        config_path = os.path.join(os.path.dirname(__file__), "config.yaml")
        with open(config_path, "r") as f:
            raw = yaml.safe_load(f)
        raw.setdefault("counting", {}).setdefault("line", {})
        raw["counting"]["line"].update({
            "point1":       line_update["point1"],
            "point2":       line_update["point2"],
            "in_direction": line_update["in_direction"],
        })
        with open(config_path, "w") as f:
            yaml.dump(raw, f, allow_unicode=True, default_flow_style=False)
    except Exception as exc:
        logging.getLogger(__name__).warning("Could not persist line config: %s", exc)


def _is_event_active(schedule: dict) -> bool:
    open_dt, close_dt = _event_window(schedule)
    if open_dt is None:
        return False
    return open_dt <= datetime.datetime.now() <= close_dt


class Device1:
    def __init__(self, config_path: str):
        self._cfg = _load_config(config_path)
        _setup_logging(self._cfg)
        self._logger = logging.getLogger(__name__)
        self._running = False
        self._camera: Optional[RTSPCapture] = None
        self._detector: Optional[HailoDetector] = None
        self._tracker: Optional[CentroidTracker] = None
        self._line_counter: Optional[LineCounter] = None
        self._storage: Optional[LocalStorage] = None
        self._lora: Optional[LoRaTransmitter] = None
        self._shared_state = SharedState()
        self._setup_server = None

    def _init_components(self) -> None:
        cfg = self._cfg
        debug_cfg = cfg.get("debug", {})
        mock_camera = debug_cfg.get("mock_camera", False)

        if mock_camera:
            self._logger.warning("DEBUG MODE: using MockCamera and MockDetector (no RTSP/Hailo)")
            self._camera = MockCamera(
                fps_target=cfg["camera"].get("fps_target", 10),
                person_interval=debug_cfg.get("person_interval", 15.0),
            )
            self._detector = MockDetector()
        else:
            cam_cfg = cfg["camera"]
            rtsp_url = cam_cfg["rtsp_url"]
            username = cam_cfg.get("username", "")
            password = cam_cfg.get("password", "")
            if username and "@" not in rtsp_url:
                proto, rest = rtsp_url.split("://", 1)
                rtsp_url = f"{proto}://{username}:{password}@{rest}"
            self._camera = RTSPCapture(
                rtsp_url=rtsp_url,
                fps_target=cam_cfg["fps_target"],
                reconnect_interval=cam_cfg["reconnect_interval"],
            )
            ai_cfg = cfg["ai"]
            self._detector = HailoDetector(
                model_path=ai_cfg["model_path"],
                confidence_threshold=ai_cfg["confidence_threshold"],
                input_width=ai_cfg["input_width"],
                input_height=ai_cfg["input_height"],
            )

        tracker_cfg = cfg["counting"]["tracker"]
        self._tracker = CentroidTracker(
            max_disappeared=tracker_cfg["max_disappeared"],
            max_distance=tracker_cfg["max_distance"],
        )

        line_cfg = cfg["counting"]["line"]
        # Frame dimensions will be updated once first frame arrives
        self._line_cfg = line_cfg

        storage_cfg = cfg["storage"]
        self._storage = LocalStorage(
            csv_path=storage_cfg["csv_path"],
            db_path=storage_cfg["db_path"],
        )

        lora_cfg = cfg["lora"]
        self._lora = LoRaTransmitter(
            spi_bus=lora_cfg["spi_bus"],
            spi_device=lora_cfg["spi_device"],
            reset_pin=lora_cfg["reset_pin"],
            frequency=lora_cfg["frequency"],
            tx_power=lora_cfg["tx_power"],
            spreading_factor=lora_cfg["spreading_factor"],
            bandwidth=lora_cfg["bandwidth"],
        )
        if not self._lora.initialize():
            self._logger.error("LoRa initialization failed")

        setup_cfg = cfg.get("setup_mode", {})
        if setup_cfg.get("enabled", True):
            port = setup_cfg.get("port", 8080)
            from werkzeug.serving import make_server
            flask_app = create_app(self._shared_state)
            self._setup_server = make_server("0.0.0.0", port, flask_app, threaded=True)
            t = threading.Thread(
                target=self._setup_server.serve_forever,
                name="setup-web",
                daemon=True,
            )
            t.start()
            self._logger.info("Setup web app listening on port %d", port)

    def _send_lora_update(self, device_id: str, count_in: int, count_out: int) -> None:
        try:
            payload = encode_message(
                device_id=device_id,
                msg_type=MessageType.COUNT_UPDATE,
                count_in=count_in,
                count_out=count_out,
                timestamp=time.time(),
            )
            success = self._lora.send(payload)
            if success:
                self._logger.info("LoRa update sent: in=%d out=%d", count_in, count_out)
                self._shared_state.notify_lora_sent()
            else:
                self._logger.warning("LoRa send failed")
        except Exception as exc:
            self._logger.error("LoRa update error: %s", exc)

    def run(self) -> None:
        self._running = True
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

        self._init_components()
        self._camera.start()

        device_id = self._cfg["device"]["id"]
        lora_interval = self._cfg["lora"]["send_interval"]
        schedule = self._cfg.get("event", {}).get("schedule", {})
        last_lora_send = 0.0
        last_status_log = 0.0
        status_interval = 60.0

        prev_tracks: dict = {}
        line_counter: Optional[LineCounter] = None
        frame_dims_known = False
        last_reset_date = datetime.date.today()

        self._logger.info("Device1 main loop started (device_id=%s)", device_id)

        while self._running:
            # --- Midnight reset: nulstil tæller ved starten af ny dag ---
            today = datetime.date.today()
            if today != last_reset_date:
                if line_counter is not None:
                    line_counter.reset()
                    self._logger.info("Daily counter reset for %s", today.isoformat())
                last_reset_date = today
                last_lora_send = 0.0  # Send straks ved ny dag

            frame = self._camera.get_frame()
            if frame is None:
                time.sleep(0.05)
                continue

            if not frame_dims_known:
                h, w = frame.shape[:2]
                line_cfg = self._line_cfg
                line_counter = LineCounter(
                    point1=line_cfg["point1"],
                    point2=line_cfg["point2"],
                    in_direction=line_cfg["in_direction"],
                    frame_width=w,
                    frame_height=h,
                )
                self._line_counter = line_counter
                frame_dims_known = True
                self._logger.info("Frame dimensions detected: %dx%d", w, h)

            # Apply any line config update from the web UI
            line_update = self._shared_state.consume_line_update()
            if line_update is not None:
                line_counter = LineCounter(
                    point1=line_update["point1"],
                    point2=line_update["point2"],
                    in_direction=line_update["in_direction"],
                    frame_width=frame.shape[1],
                    frame_height=frame.shape[0],
                )
                self._line_counter = line_counter
                self._line_cfg = line_update
                _persist_line_config(self._cfg, line_update)
                self._logger.info(
                    "Line config updated from web UI: %s", line_update
                )

            detections = self._detector.detect(frame)
            tracks = self._tracker.update(detections)

            # Feed SharedState for setup mode MJPEG stream
            self._shared_state.update_frame(
                frame, detections,
                camera_ok=self._camera.is_connected(),
                line_cfg=self._line_cfg,
            )

            new_ins, new_outs = line_counter.process_tracks(tracks, prev_tracks)
            if new_ins > 0 or new_outs > 0:
                total_in, total_out = line_counter.get_totals()
                total_visitors = total_in - total_out
                self._storage.log_count(device_id, total_in, total_out, max(0, total_visitors))

            prev_tracks = {oid: dict(data) for oid, data in tracks.items()}

            now = time.time()
            # --- LoRa TX: send kun inden for event-åbningstider ---
            event_active = _is_event_active(schedule) if schedule else True
            if now - last_lora_send >= lora_interval:
                if event_active:
                    total_in, total_out = line_counter.get_totals()
                    self._send_lora_update(device_id, total_in, total_out)
                    last_lora_send = now
                else:
                    # Opdater timer alligevel for ikke at sende burst ved åbning
                    last_lora_send = now

            if now - last_status_log >= status_interval:
                total_in, total_out = line_counter.get_totals()
                self._logger.info(
                    "Status: tracks=%d in=%d out=%d camera_ok=%s event_active=%s",
                    len(tracks),
                    total_in,
                    total_out,
                    self._camera.is_connected(),
                    event_active,
                )
                last_status_log = now

        self._shutdown()

    def _handle_signal(self, signum: int, frame) -> None:
        self._logger.info("Signal %d received, shutting down", signum)
        self._running = False

    def _shutdown(self) -> None:
        self._logger.info("Shutting down Device1")
        if self._setup_server:
            self._setup_server.shutdown()
        if self._camera:
            self._camera.stop()
        if self._detector:
            self._detector.close()
        if self._lora:
            self._lora.close()
        self._logger.info("Device1 shutdown complete")


def main() -> None:
    config_path = os.path.join(os.path.dirname(__file__), "config.yaml")
    device = Device1(config_path)
    device.run()


if __name__ == "__main__":
    main()
