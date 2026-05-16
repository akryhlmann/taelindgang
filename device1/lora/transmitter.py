import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

# SX1276 Register addresses
REG_FIFO = 0x00
REG_OP_MODE = 0x01
REG_FR_MSB = 0x06
REG_FR_MID = 0x07
REG_FR_LSB = 0x08
REG_PA_CONFIG = 0x09
REG_FIFO_ADDR_PTR = 0x0D
REG_FIFO_TX_BASE_ADDR = 0x0E
REG_FIFO_RX_BASE_ADDR = 0x0F
REG_FIFO_RX_CURRENT_ADDR = 0x10
REG_IRQ_FLAGS = 0x12
REG_PAYLOAD_LENGTH = 0x22
REG_MODEM_CONFIG_1 = 0x1D
REG_MODEM_CONFIG_2 = 0x1E
REG_SYNC_WORD = 0x39
REG_DIO_MAPPING_1 = 0x40
REG_VERSION = 0x42

MODE_LONG_RANGE = 0x80
MODE_SLEEP = 0x00
MODE_STDBY = 0x01
MODE_TX = 0x03
MODE_RX_CONTINUOUS = 0x05

PA_BOOST = 0x80
IRQ_TX_DONE = 0x08

FXOSC = 32_000_000
FSTEP = FXOSC / (2**19)


def _try_import_hw():
    try:
        import spidev
        import RPi.GPIO as GPIO
        return True, spidev, GPIO
    except (ImportError, RuntimeError):
        return False, None, None


class LoRaTransmitter:
    def __init__(
        self,
        spi_bus: int = 0,
        spi_device: int = 0,
        reset_pin: int = 22,
        dio0_pin: int = 18,
        frequency: int = 868_000_000,
        tx_power: int = 14,
        spreading_factor: int = 7,
        bandwidth: int = 125_000,
    ):
        self._spi_bus = spi_bus
        self._spi_device = spi_device
        self._reset_pin = reset_pin
        self._dio0_pin = dio0_pin
        self._frequency = frequency
        self._tx_power = tx_power
        self._sf = spreading_factor
        self._bw = bandwidth
        self._spi = None
        self._GPIO = None
        self._mock_mode = False

        hw_ok, spidev_mod, gpio_mod = _try_import_hw()
        if not hw_ok:
            logger.warning("SPI/GPIO not available, LoRaTransmitter running in mock mode")
            self._mock_mode = True
        else:
            self._spidev = spidev_mod
            self._GPIO = gpio_mod

    def initialize(self) -> bool:
        if self._mock_mode:
            logger.info("LoRaTransmitter mock initialization OK")
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

            if self._tx_power > 17:
                self._write_register(REG_PA_CONFIG, PA_BOOST | (self._tx_power - 2))
            else:
                self._write_register(REG_PA_CONFIG, 0x70 | (self._tx_power - 2))

            bw_index = {
                7_800: 0,
                10_400: 1,
                15_600: 2,
                20_800: 3,
                31_250: 4,
                41_700: 5,
                62_500: 6,
                125_000: 7,
                250_000: 8,
                500_000: 9,
            }.get(self._bw, 7)
            # CR=4/5, explicit header
            modem_cfg1 = (bw_index << 4) | (0x01 << 1) | 0x00
            self._write_register(REG_MODEM_CONFIG_1, modem_cfg1)

            # SF, TX continuous=off, CRC=on
            modem_cfg2 = (self._sf << 4) | 0x04
            self._write_register(REG_MODEM_CONFIG_2, modem_cfg2)

            self._write_register(REG_SYNC_WORD, 0x12)
            self._write_register(REG_FIFO_TX_BASE_ADDR, 0x00)
            self._write_register(REG_FIFO_ADDR_PTR, 0x00)

            self._write_register(REG_OP_MODE, MODE_LONG_RANGE | MODE_STDBY)
            logger.info("LoRaTransmitter initialized at %dMHz", self._frequency // 1_000_000)
            return True
        except Exception as exc:
            logger.error("LoRa init failed: %s", exc)
            return False

    def send(self, data: bytes) -> bool:
        if self._mock_mode:
            logger.info("LoRa mock TX: %d bytes", len(data))
            return True
        try:
            self._write_register(REG_OP_MODE, MODE_LONG_RANGE | MODE_STDBY)
            self._write_register(REG_FIFO_ADDR_PTR, 0x00)
            self._write_register(REG_PAYLOAD_LENGTH, len(data))

            for byte in data:
                self._write_register(REG_FIFO, byte)

            self._write_register(REG_OP_MODE, MODE_LONG_RANGE | MODE_TX)

            timeout = time.time() + 5.0
            while time.time() < timeout:
                irq = self._read_register(REG_IRQ_FLAGS)
                if irq & IRQ_TX_DONE:
                    self._write_register(REG_IRQ_FLAGS, IRQ_TX_DONE)
                    logger.debug("LoRa TX done, %d bytes", len(data))
                    return True
                time.sleep(0.01)

            logger.warning("LoRa TX timeout")
            return False
        except Exception as exc:
            logger.error("LoRa send error: %s", exc)
            return False

    def close(self) -> None:
        if self._mock_mode:
            return
        try:
            if self._spi:
                self._write_register(REG_OP_MODE, MODE_LONG_RANGE | MODE_SLEEP)
                self._spi.close()
            if self._GPIO:
                self._GPIO.cleanup()
        except Exception as exc:
            logger.warning("LoRa close error: %s", exc)

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
