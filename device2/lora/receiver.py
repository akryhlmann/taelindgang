import logging
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# SX1276 register map
_REG_FIFO              = 0x00
_REG_OP_MODE           = 0x01
_REG_FR_MSB            = 0x06
_REG_FR_MID            = 0x07
_REG_FR_LSB            = 0x08
_REG_FIFO_ADDR_PTR     = 0x0D
_REG_FIFO_RX_BASE_ADDR = 0x0F
_REG_FIFO_RX_CURR_ADDR = 0x10
_REG_IRQ_FLAGS         = 0x12
_REG_RX_NB_BYTES       = 0x13
_REG_PKT_SNR_VALUE     = 0x19
_REG_PKT_RSSI_VALUE    = 0x1A
_REG_MODEM_CONFIG1     = 0x1D
_REG_MODEM_CONFIG2     = 0x1E
_REG_PREAMBLE_MSB      = 0x20
_REG_PREAMBLE_LSB      = 0x21
_REG_SYNC_WORD         = 0x39
_REG_VERSION           = 0x42

_MODE_SLEEP  = 0x00
_MODE_STDBY  = 0x01
_MODE_RXCONT = 0x05
_LORA_FLAG   = 0x80

_IRQ_RX_DONE     = 0x40
_IRQ_CRC_ERROR   = 0x20
_IRQ_RX_TIMEOUT  = 0x80

_BW_MAP = {
    7_800: 0x00, 10_400: 0x01, 15_600: 0x02, 20_800: 0x03,
    31_250: 0x04, 41_700: 0x05, 62_500: 0x06, 125_000: 0x07,
    250_000: 0x08, 500_000: 0x09,
}


def _try_import_hw():
    try:
        import spidev
        import lgpio
        return True, spidev, lgpio
    except (ImportError, RuntimeError):
        return False, None, None


class LoRaReceiver:
    def __init__(
        self,
        spi_bus: int = 0,
        spi_device: int = 0,
        reset_pin: int = 17,
        frequency: int = 868_000_000,
        spreading_factor: int = 7,
        bandwidth: int = 125_000,
        force_mock: bool = False,
    ):
        self._spi_bus = spi_bus
        self._spi_device = spi_device
        self._reset_pin = reset_pin
        self._frequency = frequency
        self._sf = spreading_factor
        self._bw = bandwidth
        self._spi = None
        self._gpio_handle: Optional[int] = None
        self._mock_mode = False
        self._running = False
        self._rx_thread: Optional[threading.Thread] = None

        if force_mock:
            logger.warning("LoRaReceiver: mock mode forced by config — no real LoRa hardware used")
            self._mock_mode = True
            return

        hw_ok, spidev_mod, lgpio_mod = _try_import_hw()
        if not hw_ok:
            logger.warning(
                "LoRaReceiver: spidev/lgpio not available — falling back to mock mode. "
                "Install with: sudo apt install python3-lgpio && pip install spidev"
            )
            self._mock_mode = True
        else:
            self._spidev = spidev_mod
            self._lgpio = lgpio_mod

    def initialize(self) -> bool:
        if self._mock_mode:
            logger.info("LoRaReceiver mock initialization OK")
            return True
        try:
            lg = self._lgpio
            self._gpio_handle = lg.gpiochip_open(0)
            lg.gpio_claim_output(self._gpio_handle, self._reset_pin, 1)

            self._spi = self._spidev.SpiDev()
            self._spi.open(self._spi_bus, self._spi_device)
            self._spi.max_speed_hz = 8_000_000
            self._spi.mode = 0

            self._reset()

            ver = self._read_reg(_REG_VERSION)
            if ver != 0x12:
                logger.error("SX1276 version check failed: got 0x%02X (expected 0x12)", ver)
                return False

            # Enter LoRa mode via sleep
            self._write_reg(_REG_OP_MODE, _MODE_SLEEP)
            time.sleep(0.01)
            self._write_reg(_REG_OP_MODE, _LORA_FLAG | _MODE_SLEEP)
            time.sleep(0.01)
            self._write_reg(_REG_OP_MODE, _LORA_FLAG | _MODE_STDBY)
            time.sleep(0.01)

            # Frequency
            frf = int(self._frequency / 32e6 * (1 << 19))
            self._write_reg(_REG_FR_MSB, (frf >> 16) & 0xFF)
            self._write_reg(_REG_FR_MID, (frf >>  8) & 0xFF)
            self._write_reg(_REG_FR_LSB,  frf        & 0xFF)

            # BW + CR 4/5 + explicit header
            bw_bits = _BW_MAP.get(self._bw, 0x07)
            self._write_reg(_REG_MODEM_CONFIG1, (bw_bits << 4) | 0x02)
            # SF + CRC on
            self._write_reg(_REG_MODEM_CONFIG2, (self._sf << 4) | 0x04)

            # Preamble = 8 symbols
            self._write_reg(_REG_PREAMBLE_MSB, 0x00)
            self._write_reg(_REG_PREAMBLE_LSB, 0x08)

            # Private network sync word
            self._write_reg(_REG_SYNC_WORD, 0x12)

            # FIFO RX base at 0
            self._write_reg(_REG_FIFO_RX_BASE_ADDR, 0x00)
            self._write_reg(_REG_FIFO_ADDR_PTR, 0x00)

            logger.info("LoRaReceiver (SX1276) initialized at %dMHz SF%d BW%dkHz",
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
                self._write_reg(_REG_OP_MODE, _MODE_SLEEP)
                if self._spi:
                    self._spi.close()
                if self._gpio_handle is not None:
                    self._lgpio.gpiochip_close(self._gpio_handle)
            except Exception as exc:
                logger.warning("LoRa receiver close error: %s", exc)
        logger.info("LoRaReceiver stopped")

    # ------------------------------------------------------------------

    def _rx_loop(self, callback: Callable[[bytes, int], None]) -> None:
        if self._mock_mode:
            self._mock_rx_loop(callback)
            return

        # Enter continuous RX
        self._write_reg(_REG_IRQ_FLAGS, 0xFF)
        self._write_reg(_REG_OP_MODE, _LORA_FLAG | _MODE_RXCONT)
        time.sleep(0.01)
        logger.info("Listening for LoRa packets (continuous RX)...")

        while self._running:
            flags = self._read_reg(_REG_IRQ_FLAGS)
            if flags & _IRQ_RX_DONE:
                self._write_reg(_REG_IRQ_FLAGS, 0xFF)
                if flags & _IRQ_CRC_ERROR:
                    logger.warning("LoRa CRC error (IRQ=0x%02X)", flags)
                    continue
                data, rssi = self._read_packet()
                logger.debug("Received %d bytes, RSSI=%ddBm", len(data), rssi)
                try:
                    callback(data, rssi)
                except Exception as exc:
                    logger.error("RX callback error: %s", exc)
            time.sleep(0.005)

    def _read_packet(self) -> tuple:
        nb = self._read_reg(_REG_RX_NB_BYTES)
        rx_addr = self._read_reg(_REG_FIFO_RX_CURR_ADDR)
        self._write_reg(_REG_FIFO_ADDR_PTR, rx_addr)
        data = bytes(self._read_reg(_REG_FIFO) for _ in range(nb))
        rssi_raw = self._read_reg(_REG_PKT_RSSI_VALUE)
        rssi = rssi_raw - 157
        return data, rssi

    def _mock_rx_loop(self, callback: Callable[[bytes, int], None]) -> None:
        import os, sys, random
        sys.path.insert(0, str(os.path.join(os.path.dirname(__file__), "..", "..")))
        from shared.protocol import encode_message, MessageType
        count_in = count_out = 0
        logger.warning(
            "*** LoRaReceiver MOCK MODE ACTIVE — generating fake packets every 30s. "
            "No real LoRa hardware is used. Set debug.mock_lora: false in config "
            "and ensure spidev/lgpio are installed. ***"
        )
        while self._running:
            time.sleep(30)
            if not self._running:
                break
            count_in  += random.randint(0, 3)
            count_out += random.randint(0, 2)
            data = encode_message("DEV001", MessageType.COUNT_UPDATE,
                                  count_in, count_out, time.time())
            rssi = random.randint(-100, -60)
            logger.warning("*** MOCK RX (fake data): in=%d out=%d rssi=%d ***",
                           count_in, count_out, rssi)
            try:
                callback(data, rssi)
            except Exception as exc:
                logger.error("Mock RX callback error: %s", exc)

    # ------------------------------------------------------------------

    def _reset(self) -> None:
        lg = self._lgpio
        h = self._gpio_handle
        lg.gpio_write(h, self._reset_pin, 0)
        time.sleep(0.01)
        lg.gpio_write(h, self._reset_pin, 1)
        time.sleep(0.01)

    def _write_reg(self, reg: int, val: int) -> None:
        self._spi.xfer2([reg | 0x80, val])

    def _read_reg(self, reg: int) -> int:
        r = self._spi.xfer2([reg & 0x7F, 0x00])
        return r[1]
