import logging
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

REG_FIFO = 0x00
REG_OP_MODE = 0x01
REG_FR_MSB = 0x06
REG_FR_MID = 0x07
REG_FR_LSB = 0x08
REG_FIFO_ADDR_PTR = 0x0D
REG_FIFO_RX_BASE_ADDR = 0x0F
REG_FIFO_RX_CURRENT_ADDR = 0x10
REG_IRQ_FLAGS = 0x12
REG_RX_NB_BYTES = 0x13
REG_PKT_RSSI_VALUE = 0x1A
REG_MODEM_CONFIG_1 = 0x1D
REG_MODEM_CONFIG_2 = 0x1E
REG_SYNC_WORD = 0x39
REG_VERSION = 0x42

MODE_LONG_RANGE = 0x80
MODE_SLEEP = 0x00
MODE_STDBY = 0x01
MODE_RX_CONTINUOUS = 0x05

IRQ_RX_DONE = 0x40
IRQ_CRC_ERROR = 0x20
IRQ_VALID_HEADER = 0x10

FXOSC = 32_000_000
FSTEP = FXOSC / (2**19)


def _try_import_hw():
    try:
        import spidev
        import RPi.GPIO as GPIO
        return True, spidev, GPIO
    except (ImportError, RuntimeError):
        return False, None, None


class LoRaReceiver:
    def __init__(
        self,
        spi_bus: int = 0,
        spi_device: int = 0,
        reset_pin: int = 22,
        dio0_pin: int = 18,
        frequency: int = 868_000_000,
        spreading_factor: int = 7,
        bandwidth: int = 125_000,
    ):
        self._spi_bus = spi_bus
        self._spi_device = spi_device
        self._reset_pin = reset_pin
        self._dio0_pin = dio0_pin
        self._frequency = frequency
        self._sf = spreading_factor
        self._bw = bandwidth
        self._spi = None
        self._GPIO = None
        self._mock_mode = False
        self._running = False
        self._rx_thread: Optional[threading.Thread] = None

        hw_ok, spidev_mod, gpio_mod = _try_import_hw()
        if not hw_ok:
            logger.warning("SPI/GPIO not available, LoRaReceiver running in mock mode")
            self._mock_mode = True
        else:
            self._spidev = spidev_mod
            self._GPIO = gpio_mod

    def initialize(self) -> bool:
        if self._mock_mode:
            logger.info("LoRaReceiver mock initialization OK")
            return True
        try:
            self._GPIO.setmode(self._GPIO.BCM)
            self._GPIO.setup(self._reset_pin, self._GPIO.OUT)
            self._GPIO.setup(self._dio0_pin, self._GPIO.IN)

            self._spi = self._spidev.SpiDev()
            self._spi.open(self._spi_bus, self._spi_device)
            self._spi.max_speed_hz = 5_000_000

            self._reset()

            version = self._read_register(REG_VERSION)
            if version != 0x12:
                logger.error("SX1276 not found (version=0x%02X)", version)
                return False

            self._write_register(REG_OP_MODE, MODE_LONG_RANGE | MODE_SLEEP)
            time.sleep(0.01)

            frf = int(self._frequency / FSTEP)
            self._write_register(REG_FR_MSB, (frf >> 16) & 0xFF)
            self._write_register(REG_FR_MID, (frf >> 8) & 0xFF)
            self._write_register(REG_FR_LSB, frf & 0xFF)

            bw_index = {
                7_800: 0, 10_400: 1, 15_600: 2, 20_800: 3,
                31_250: 4, 41_700: 5, 62_500: 6, 125_000: 7,
                250_000: 8, 500_000: 9,
            }.get(self._bw, 7)
            modem_cfg1 = (bw_index << 4) | (0x01 << 1) | 0x00
            self._write_register(REG_MODEM_CONFIG_1, modem_cfg1)

            modem_cfg2 = (self._sf << 4) | 0x04
            self._write_register(REG_MODEM_CONFIG_2, modem_cfg2)

            self._write_register(REG_SYNC_WORD, 0x12)
            self._write_register(REG_FIFO_RX_BASE_ADDR, 0x00)

            self._write_register(REG_OP_MODE, MODE_LONG_RANGE | MODE_STDBY)
            logger.info("LoRaReceiver initialized at %dMHz", self._frequency // 1_000_000)
            return True
        except Exception as exc:
            logger.error("LoRa receiver init failed: %s", exc)
            return False

    def start_receiving(self, callback: Callable[[bytes, int], None]) -> None:
        self._running = True
        self._rx_thread = threading.Thread(
            target=self._rx_loop, args=(callback,), daemon=True
        )
        self._rx_thread.start()
        logger.info("LoRaReceiver RX loop started")

    def stop(self) -> None:
        self._running = False
        if self._rx_thread:
            self._rx_thread.join(timeout=5)
        if not self._mock_mode:
            try:
                if self._spi:
                    self._write_register(REG_OP_MODE, MODE_LONG_RANGE | MODE_SLEEP)
                    self._spi.close()
                if self._GPIO:
                    self._GPIO.cleanup()
            except Exception as exc:
                logger.warning("LoRa receiver close error: %s", exc)
        logger.info("LoRaReceiver stopped")

    def _rx_loop(self, callback: Callable[[bytes, int], None]) -> None:
        if self._mock_mode:
            self._mock_rx_loop(callback)
            return

        self._write_register(REG_OP_MODE, MODE_LONG_RANGE | MODE_RX_CONTINUOUS)
        logger.info("Listening for LoRa packets...")

        while self._running:
            irq = self._read_register(REG_IRQ_FLAGS)
            if irq & IRQ_RX_DONE:
                self._write_register(REG_IRQ_FLAGS, 0xFF)
                if irq & IRQ_CRC_ERROR:
                    logger.warning("LoRa packet CRC error")
                    time.sleep(0.01)
                    continue

                nb_bytes = self._read_register(REG_RX_NB_BYTES)
                rx_addr = self._read_register(REG_FIFO_RX_CURRENT_ADDR)
                self._write_register(REG_FIFO_ADDR_PTR, rx_addr)
                data = bytes(self._spi.xfer2([0x00] * (nb_bytes + 1))[1:])
                rssi = self._read_register(REG_PKT_RSSI_VALUE) - 164
                logger.debug("Received %d bytes, RSSI=%ddBm", len(data), rssi)
                try:
                    callback(data, rssi)
                except Exception as exc:
                    logger.error("RX callback error: %s", exc)
            time.sleep(0.01)

    def _mock_rx_loop(self, callback: Callable[[bytes, int], None]) -> None:
        import sys
        import os
        sys.path.insert(0, str(os.path.join(os.path.dirname(__file__), "..", "..")))
        from shared.protocol import encode_message, MessageType
        import random

        count_in = 0
        count_out = 0
        logger.info("LoRaReceiver mock RX loop started (packets every 30s)")
        while self._running:
            time.sleep(30)
            if not self._running:
                break
            count_in += random.randint(0, 3)
            count_out += random.randint(0, 2)
            data = encode_message(
                device_id="DEV001",
                msg_type=MessageType.COUNT_UPDATE,
                count_in=count_in,
                count_out=count_out,
                timestamp=time.time(),
            )
            rssi = random.randint(-100, -60)
            logger.info("Mock RX packet: in=%d out=%d rssi=%d", count_in, count_out, rssi)
            try:
                callback(data, rssi)
            except Exception as exc:
                logger.error("Mock RX callback error: %s", exc)

    def _reset(self) -> None:
        self._GPIO.output(self._reset_pin, self._GPIO.LOW)
        time.sleep(0.01)
        self._GPIO.output(self._reset_pin, self._GPIO.HIGH)
        time.sleep(0.01)

    def _write_register(self, address: int, value: int) -> None:
        self._spi.xfer2([address | 0x80, value])

    def _read_register(self, address: int) -> int:
        result = self._spi.xfer2([address & 0x7F, 0x00])
        return result[1]
