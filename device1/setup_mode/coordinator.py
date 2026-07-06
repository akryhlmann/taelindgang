"""
Setup mode coordinator.

Responsibilities:
  - Watch a GPIO button for a long press (default 3 s) to toggle setup mode
  - On activation: start WiFi hotspot + Flask web app
  - On deactivation / timeout: stop both cleanly
  - Auto-deactivate after timeout_seconds of web-UI inactivity

GPIO notes (Raspberry Pi 5):
  The 40-pin GPIO header is on gpiochip4 (kernel ≥ 6.6 / RPi5 default).
  Older Pi models use gpiochip0.  Make the chip number configurable via
  setup_mode.gpio_chip in config.yaml (default: 4).
"""
import logging
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)


class SetupModeCoordinator:
    def __init__(self, cfg: dict, shared_state) -> None:
        """
        cfg keys (all under setup_mode in config.yaml):
          gpio_button_pin    int   BCM pin number (default 26)
          gpio_chip          int   gpiochip number (default 4 for RPi5)
          hold_seconds       float seconds to hold for long-press (default 3)
          timeout_seconds    int   inactivity timeout in seconds (default 1200)
          hotspot_ssid       str   WiFi SSID (default "BornelandSetup")
          hotspot_password   str   WiFi password (default "borneland1")
          flask_port         int   port Flask listens on (default 8080)
          hotspot_ip         str   IP assigned to wlan0 (default "10.42.0.1")
        """
        self._state = shared_state
        self._pin          = cfg.get("gpio_button_pin", 26)
        self._chip         = cfg.get("gpio_chip", 4)
        self._hold_secs    = cfg.get("hold_seconds", 3.0)
        self._timeout_secs = cfg.get("timeout_seconds", 1200)
        self._ssid         = cfg.get("hotspot_ssid", "BornelandSetup")
        self._password     = cfg.get("hotspot_password", "borneland1")
        self._flask_port   = cfg.get("flask_port", 8080)
        self._hotspot_ip   = cfg.get("hotspot_ip", "10.42.0.1")

        self._active = False
        self._running = False
        self._server = None          # werkzeug WSGIServer instance
        self._server_thread: Optional[threading.Thread] = None
        self._timeout_thread: Optional[threading.Thread] = None
        self._button_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    # ── public API ───────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the coordinator (button watcher runs in background)."""
        self._running = True
        self._button_thread = threading.Thread(
            target=self._watch_button, name="setup-button", daemon=True
        )
        self._button_thread.start()
        logger.info(
            "SetupModeCoordinator started — GPIO pin %d (chip %d), hold %.1fs",
            self._pin, self._chip, self._hold_secs,
        )

    def stop(self) -> None:
        """Stop coordinator and deactivate setup mode if active."""
        self._running = False
        if self._active:
            self._deactivate()

    def is_active(self) -> bool:
        return self._active

    # ── button watcher ───────────────────────────────────────────────────────

    def _watch_button(self) -> None:
        chip_handle = None
        try:
            import lgpio
            # Try the configured chip; fall back to chip 0 if it fails
            for chip_num in (self._chip, 0):
                try:
                    chip_handle = lgpio.gpiochip_open(chip_num)
                    lgpio.gpio_claim_input(chip_handle, self._pin, lgpio.SET_PULL_UP)
                    logger.debug("GPIO button on chip %d pin %d", chip_num, self._pin)
                    break
                except Exception as exc:
                    logger.debug("gpiochip %d failed: %s", chip_num, exc)
                    chip_handle = None

            if chip_handle is None:
                logger.warning(
                    "Could not open GPIO chip — button disabled "
                    "(activate setup mode manually via: sudo python -c "
                    "\"from device1.setup_mode.coordinator import *\")"
                )
                return

            press_start: Optional[float] = None
            triggered = False  # Prevent re-triggering while held

            while self._running:
                val = lgpio.gpio_read(chip_handle, self._pin)
                pressed = (val == 0)  # Active-low: button connects pin to GND

                if pressed and press_start is None:
                    press_start = time.time()
                    triggered = False

                if not pressed:
                    press_start = None
                    triggered = False

                if pressed and press_start is not None and not triggered:
                    held = time.time() - press_start
                    if held >= self._hold_secs:
                        triggered = True
                        self._toggle()

                time.sleep(0.05)

        except ImportError:
            logger.warning("lgpio not available — GPIO button disabled")
        except Exception as exc:
            logger.error("Button watcher error: %s", exc)
        finally:
            if chip_handle is not None:
                try:
                    import lgpio
                    lgpio.gpiochip_close(chip_handle)
                except Exception:
                    pass

    # ── toggle / activate / deactivate ──────────────────────────────────────

    def _toggle(self) -> None:
        with self._lock:
            if self._active:
                logger.info("Button: deactivating setup mode")
                self._deactivate()
            else:
                logger.info("Button: activating setup mode")
                self._activate()

    def _activate(self) -> None:
        """Start hotspot and Flask app."""
        try:
            from device1.setup_mode import hotspot
            from device1.setup_mode.web_app import create_app
            from werkzeug.serving import make_server

            logger.info("Starting hotspot SSID=%r", self._ssid)
            hotspot.start(self._ssid, self._password)

            app = create_app(self._state, hotspot_ip=self._hotspot_ip)
            app.config["TIMEOUT_SECONDS"] = self._timeout_secs

            self._server = make_server("0.0.0.0", self._flask_port, app)
            self._server_thread = threading.Thread(
                target=self._server.serve_forever,
                name="setup-flask",
                daemon=True,
            )
            self._server_thread.start()
            logger.info("Flask setup app listening on port %d", self._flask_port)

            self._state.touch()  # Reset timeout clock
            self._active = True

            self._timeout_thread = threading.Thread(
                target=self._watch_timeout,
                name="setup-timeout",
                daemon=True,
            )
            self._timeout_thread.start()

        except Exception as exc:
            logger.error("Setup mode activation failed: %s", exc)
            self._deactivate()

    def _deactivate(self) -> None:
        """Stop Flask and hotspot."""
        self._active = False

        if self._server is not None:
            try:
                self._server.shutdown()
                logger.info("Flask setup app stopped")
            except Exception as exc:
                logger.warning("Flask shutdown error: %s", exc)
            self._server = None

        try:
            from device1.setup_mode import hotspot
            hotspot.stop()
        except Exception as exc:
            logger.warning("Hotspot stop error: %s", exc)

    # ── auto-timeout ─────────────────────────────────────────────────────────

    def _watch_timeout(self) -> None:
        """Deactivate setup mode after inactivity timeout."""
        while self._running and self._active:
            time.sleep(30)
            if not self._active:
                break
            if self._state.is_timed_out(self._timeout_secs):
                logger.info(
                    "Setup mode timed out after %ds inactivity — shutting down",
                    self._timeout_secs,
                )
                with self._lock:
                    if self._active:
                        self._deactivate()
                break
