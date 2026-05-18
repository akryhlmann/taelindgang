import logging
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# SX1262 opcodes
_CMD_SET_STANDBY = 0x80
_CMD_SET_RX = 0x82
_CMD_SET_PA_CONFIG = 0x95
_CMD_SET_REGULATOR_MODE = 0x96
_CMD_SET_DIO3_AS_TCXO_CTRL = 0x97
_CMD_CALIBRATE = 0x89
_CMD_SET_PACKET_TYPE = 0x01
_CMD_SET_RF_FREQUENCY = 0x86
_CMD_SET_TX_PARAMS = 0x8E
_CMD_SET_BUFFER_BASE_ADDR = 0x8F
_CMD_SET_MODULATION_PARAMS = 0x8B
_CMD_SET_PACKET_PARAMS = 0x8C
_CMD_SET_DIO_IRQ_PARAMS = 0x08
_CMD_GET_IRQ_STATUS = 0x12
_CMD_CLEAR_IRQ_STATUS = 0x02
_CMD_GET_RX_BUFFER_STATUS = 0x13
_CMD_READ_BUFFER = 0x1E
_CMD_GET_PACKET_STATUS = 0x14
_CMD_WRITE_REGISTER = 0x0D

_REG_LORA_SYNC_WORD_MSB = 0x0740
_REG_LORA_SYNC_WORD_LSB = 0x0741

_IRQ_RX_DONE = 0x0002
_IRQ_CRC_ERROR = 0x0040
_IRQ_HEADER_ERROR = 0x0020
_IRQ_TIMEOUT = 0x0200

_BW_MAP = {
    7_800: 0x00, 10_400: 0x08, 15_600: 0x01, 20_800: 0x09,
    31_250: 0x02, 41_700: 0x0A, 62_500: 0x03, 125_000: 0x04,
    250_000: 0x05, 500_000: 0x06,
}


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
        cs_pin: int = 21,
        reset_pin: int = 18,
        busy_pin: int = 20,
        dio1_pin: int = 16,
        txen_pin: int = 6,
        frequency: int = 868_000_000,
        spreading_factor: int = 7,
        bandwidth: int = 125_000,
    ):
        self._spi_bus = spi_bus
        self._spi_device = spi_device
        self._cs_pin = cs_pin
        self._reset_pin = reset_pin
        self._busy_pin = busy_pin
        self._dio1_pin = dio1_pin
        self._txen_pin = txen_pin
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
            logger.warning("SPI/GPIO not available — LoRaReceiver running in mock mode")
            self._mock_mode = True
        else:
            self._spidev = spidev_mod
            self._GPIO = gpio_mod

    def initialize(self) -> bool:
        if self._mock_mode:
            logger.info("LoRaReceiver mock initialization OK")
            return True
        try:
            GPIO = self._GPIO
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self._cs_pin, GPIO.OUT, initial=GPIO.HIGH)
            GPIO.setup(self._reset_pin, GPIO.OUT, initial=GPIO.HIGH)
            GPIO.setup(self._busy_pin, GPIO.IN)
            GPIO.setup(self._dio1_pin, GPIO.IN)
            GPIO.setup(self._txen_pin, GPIO.OUT, initial=GPIO.LOW)

            self._spi = self._spidev.SpiDev()
            self._spi.open(self._spi_bus, self._spi_device)
            self._spi.max_speed_hz = 8_000_000
            self._spi.mode = 0
            self._spi.no_cs = True

            self._reset()
            self._cmd([_CMD_SET_STANDBY, 0x00])

            self._cmd([_CMD_SET_DIO3_AS_TCXO_CTRL, 0x07, 0x00, 0x01, 0x40])
            self._cmd([_CMD_CALIBRATE, 0x7F])
            time.sleep(0.01)

            self._cmd([_CMD_SET_REGULATOR_MODE, 0x01])
            self._cmd([_CMD_SET_PACKET_TYPE, 0x01])

            freq_raw = int(self._frequency / 32e6 * (1 << 25))
            self._cmd([
                _CMD_SET_RF_FREQUENCY,
                (freq_raw >> 24) & 0xFF,
                (freq_raw >> 16) & 0xFF,
                (freq_raw >> 8) & 0xFF,
                freq_raw & 0xFF,
            ])

            self._cmd([_CMD_SET_BUFFER_BASE_ADDR, 0x00, 0x00])

            bw_idx = _BW_MAP.get(self._bw, 0x04)
            self._cmd([_CMD_SET_MODULATION_PARAMS, self._sf, bw_idx, 0x01, 0x00])
            self._cmd([_CMD_SET_PACKET_PARAMS, 0x00, 0x0C, 0x00, 0xFF, 0x01, 0x00])

            self._write_register(_REG_LORA_SYNC_WORD_MSB, 0x14)
            self._write_register(_REG_LORA_SYNC_WORD_LSB, 0x24)

            # IRQ: RX_DONE, CRC_ERROR, HEADER_ERROR, TIMEOUT on DIO1
            self._cmd([
                _CMD_SET_DIO_IRQ_PARAMS,
                0x02, 0x62,  # mask: RX_DONE | CRC_ERROR | HEADER_ERROR | TIMEOUT
                0x02, 0x62,  # DIO1
                0x00, 0x00,
                0x00, 0x00,
            ])

            logger.info("LoRaReceiver (SX1262) initialized at %dMHz SF%d BW%dkHz",
                        self._frequency // 1_000_000, self._sf, self._bw // 1_000)
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
                self._cmd([_CMD_SET_STANDBY, 0x00])
                if self._spi:
                    self._spi.close()
                if self._GPIO:
                    self._GPIO.cleanup()
            except Exception as exc:
                logger.warning("LoRa receiver close error: %s", exc)
        logger.info("LoRaReceiver stopped")

    # ------------------------------------------------------------------
    # RX loop
    # ------------------------------------------------------------------

    def _rx_loop(self, callback: Callable[[bytes, int], None]) -> None:
        if self._mock_mode:
            self._mock_rx_loop(callback)
            return

        # Continuous RX (timeout = 0x000000 means continuous)
        self._cmd([_CMD_SET_RX, 0xFF, 0xFF, 0xFF])
        logger.info("Listening for LoRa packets (continuous RX)...")

        while self._running:
            irq = self._get_irq()
            if irq & _IRQ_RX_DONE:
                self._cmd([_CMD_CLEAR_IRQ_STATUS, 0xFF, 0xFF])

                if irq & (_IRQ_CRC_ERROR | _IRQ_HEADER_ERROR):
                    logger.warning("LoRa packet error (IRQ=0x%04X)", irq)
                    self._cmd([_CMD_SET_RX, 0xFF, 0xFF, 0xFF])
                    continue

                data, rssi = self._read_packet()
                logger.debug("Received %d bytes, RSSI=%ddBm", len(data), rssi)
                try:
                    callback(data, rssi)
                except Exception as exc:
                    logger.error("RX callback error: %s", exc)

                # Re-enter continuous RX
                self._cmd([_CMD_SET_RX, 0xFF, 0xFF, 0xFF])

            time.sleep(0.005)

    def _read_packet(self) -> tuple:
        status = self._cmd([_CMD_GET_RX_BUFFER_STATUS, 0x00, 0x00, 0x00])
        payload_len = status[2]
        rx_start = status[3]

        raw = self._cmd([_CMD_READ_BUFFER, rx_start, 0x00] + [0x00] * payload_len)
        data = bytes(raw[3:])  # skip opcode, offset, status bytes

        pkt = self._cmd([_CMD_GET_PACKET_STATUS, 0x00, 0x00, 0x00, 0x00])
        rssi = -(pkt[2] // 2)
        return data, rssi

    def _mock_rx_loop(self, callback: Callable[[bytes, int], None]) -> None:
        import os
        import sys
        import random
        sys.path.insert(0, str(os.path.join(os.path.dirname(__file__), "..", "..")))
        from shared.protocol import encode_message, MessageType

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
            logger.info("Mock RX: in=%d out=%d rssi=%d", count_in, count_out, rssi)
            try:
                callback(data, rssi)
            except Exception as exc:
                logger.error("Mock RX callback error: %s", exc)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _reset(self) -> None:
        self._GPIO.output(self._reset_pin, self._GPIO.LOW)
        time.sleep(0.001)
        self._GPIO.output(self._reset_pin, self._GPIO.HIGH)
        time.sleep(0.01)
        self._wait_busy()

    def _wait_busy(self, timeout: float = 1.0) -> None:
        deadline = time.time() + timeout
        while self._GPIO.input(self._busy_pin) == self._GPIO.HIGH:
            if time.time() > deadline:
                raise TimeoutError("SX1262 BUSY timeout")
            time.sleep(0.0001)

    def _cmd(self, data: list) -> list:
        self._wait_busy()
        self._GPIO.output(self._cs_pin, self._GPIO.LOW)
        result = self._spi.xfer2(data)
        self._GPIO.output(self._cs_pin, self._GPIO.HIGH)
        return result

    def _write_register(self, address: int, value: int) -> None:
        self._cmd([
            _CMD_WRITE_REGISTER,
            (address >> 8) & 0xFF,
            address & 0xFF,
            value,
        ])

    def _get_irq(self) -> int:
        self._wait_busy()
        self._GPIO.output(self._cs_pin, self._GPIO.LOW)
        result = self._spi.xfer2([_CMD_GET_IRQ_STATUS, 0x00, 0x00, 0x00])
        self._GPIO.output(self._cs_pin, self._GPIO.HIGH)
        return (result[2] << 8) | result[3]
