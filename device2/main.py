import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Optional

import yaml

_BASE = Path(__file__).parent
sys.path.insert(0, str(_BASE.parent))

from shared.protocol import decode_message
from device2.lora.receiver import LoRaReceiver
from device2.storage.local_storage import LocalStorage
from device2.sync.google_sheets import GoogleSheetsSync
from device2.dashboard.app import DashApp


def _setup_logging(cfg: dict) -> None:
    log_cfg = cfg.get("logging", {})
    level = getattr(logging, log_cfg.get("level", "INFO"))
    log_file = log_cfg.get("log_file", "/tmp/device2.log")
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


class Device2:
    def __init__(self, config_path: str):
        self._cfg = _load_config(config_path)
        _setup_logging(self._cfg)
        self._logger = logging.getLogger(__name__)
        self._running = False
        self._receiver: Optional[LoRaReceiver] = None
        self._storage: Optional[LocalStorage] = None
        self._sheets: Optional[GoogleSheetsSync] = None
        self._dash_app: Optional[DashApp] = None
        self._last_sync_data = []
        self._sync_lock = threading.Lock()

    def _on_lora_packet(self, data: bytes, rssi: int) -> None:
        try:
            msg = decode_message(data)
            self._logger.info(
                "LoRa RX from %s: in=%d out=%d rssi=%ddBm",
                msg["device_id"],
                msg["count_in"],
                msg["count_out"],
                rssi,
            )
            total = max(0, msg["count_in"] - msg["count_out"])
            self._storage.store_event(
                device_id=msg["device_id"],
                count_in=msg["count_in"],
                count_out=msg["count_out"],
                total=total,
                rssi=rssi,
            )
            with self._sync_lock:
                self._last_sync_data.append({
                    "timestamp": msg["timestamp"],
                    "device_id": msg["device_id"],
                    "count_in": msg["count_in"],
                    "count_out": msg["count_out"],
                    "total": total,
                })
        except Exception as exc:
            self._logger.error("Error processing LoRa packet: %s", exc)

    def _sync_loop(self) -> None:
        sheets_cfg = self._cfg.get("google_sheets", {})
        sync_interval = sheets_cfg.get("sync_interval", 60)
        self._logger.info("Google Sheets sync loop started (interval=%ds)", sync_interval)
        while self._running:
            time.sleep(sync_interval)
            if not self._running:
                break
            try:
                with self._sync_lock:
                    data_to_sync = list(self._last_sync_data)
                    self._last_sync_data.clear()

                if data_to_sync:
                    self._sheets.sync(data_to_sync)

                stats = self._storage.get_stats(since_hours=24)
                self._sheets.update_summary_row(
                    current_total=stats["current"],
                    peak=stats["peak"],
                    total_in=stats["total_in"],
                    total_out=stats["total_out"],
                )
            except Exception as exc:
                self._logger.error("Sync loop error: %s", exc)

    def run(self) -> None:
        self._running = True
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

        cfg = self._cfg
        storage_cfg = cfg["storage"]
        self._storage = LocalStorage(
            db_path=storage_cfg["db_path"],
            csv_path=storage_cfg["csv_path"],
        )

        sheets_cfg = cfg["google_sheets"]
        self._sheets = GoogleSheetsSync(
            credentials_file=sheets_cfg["credentials_file"],
            spreadsheet_id=sheets_cfg["spreadsheet_id"],
            worksheet_name=sheets_cfg["worksheet_name"],
        )

        lora_cfg = cfg["lora"]
        debug_cfg = cfg.get("debug", {})
        force_mock = debug_cfg.get("mock_lora", False)

        self._receiver = LoRaReceiver(
            spi_bus=lora_cfg["spi_bus"],
            spi_device=lora_cfg["spi_device"],
            reset_pin=lora_cfg["reset_pin"],
            frequency=lora_cfg["frequency"],
            spreading_factor=lora_cfg["spreading_factor"],
            bandwidth=lora_cfg["bandwidth"],
            force_mock=force_mock,
        )
        if not self._receiver.initialize():
            self._logger.error(
                "LoRa receiver initialization failed — hardware may not be connected. "
                "Set debug.mock_lora: true in config to use mock mode explicitly."
            )
            return
        self._receiver.start_receiving(self._on_lora_packet)

        sync_thread = threading.Thread(target=self._sync_loop, daemon=True)
        sync_thread.start()

        self._dash_app = DashApp(storage=self._storage, config=cfg, sheets=self._sheets)
        dash_thread = threading.Thread(
            target=self._dash_app.run,
            kwargs={"debug": False},
            daemon=True,
        )
        dash_thread.start()
        self._logger.info(
            "Dashboard started at http://%s:%d",
            cfg["dashboard"].get("host", "0.0.0.0"),
            cfg["dashboard"].get("port", 8050),
        )

        self._logger.info("Device2 running, waiting for LoRa packets...")
        while self._running:
            time.sleep(1)

        self._shutdown()

    def _handle_signal(self, signum: int, frame) -> None:
        self._logger.info("Signal %d received, shutting down", signum)
        self._running = False

    def _shutdown(self) -> None:
        self._logger.info("Shutting down Device2")
        if self._receiver:
            self._receiver.stop()
        self._logger.info("Device2 shutdown complete")


def main() -> None:
    config_path = os.path.join(os.path.dirname(__file__), "config.yaml")
    device = Device2(config_path)
    device.run()


if __name__ == "__main__":
    main()
