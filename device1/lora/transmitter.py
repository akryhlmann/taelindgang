import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

# SX1262 opcodes
_CMD_SET_STANDBY = 0x80
_CMD_SET_TX = 0x83
_CMD_SET_RX = 0x82
_CMD_SET_PA_CONFIG = 0x95
_CMD_SET_REGULATOR_MODE = 0x96
_CMD_SET_DIO3_AS_TCXO_CTRL = 0x97
_CMD_SET_DIO2_AS_RF_SWITCH = 0x9D
_CMD_CALIBRATE = 0x89
_CMD_CALIBRATE_IMAGE = 0x98
_CMD_SET_PACKET_TYPE = 0x01
_CMD_SET_RF_FREQUENCY = 0x86
_CMD_SET_TX_PARAMS = 0x8E
_CMD_SET_BUFFER_BASE_ADDR = 0x8F
_CMD_SET_MODULATION_PARAMS = 0x8B
_CMD_SET_PACKET_PARAMS = 0x8C
_CMD_SET_DIO_IRQ_PARAMS = 0x08
_CMD_GET_IRQ_STATUS = 0x12
_CMD_CLEAR_IRQ_STATUS = 0x02
_CMD_WRITE_BUFFER = 0x0E
_CMD_WRITE_REGISTER = 0x0D

# SX1262 registers
_REG_LORA_SYNC_WORD_MSB = 0x0740
_REG_LORA_SYNC_WORD_LSB = 0x0741

# IRQ masks
_IRQ_TX_DONE = 0x0001
_IRQ_TIMEOUT = 0x0200

# LoRa bandwidth index
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


class LoRaTransmitter:
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
        tx_power: int = 14,
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
        self._tx_power = tx_power
        self._sf = spreading_factor
        self._bw = bandwidth
        self._spi = None
        self._GPIO = None
        self._mock_mode = False

        hw_ok, spidev_mod, gpio_mod = _try_import_hw()
        if not hw_ok:
            logger.warning("SPI/GPIO not available — LoRaTransmitter running in mock mode")
            self._mock_mode = True
        else:
            self._spidev = spidev_mod
            self._GPIO = gpio_mod

    def initialize(self) -> bool:
        if self._mock_mode:
            logger.info("LoRaTransmitter mock initialization OK")
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
            self._spi.no_cs = True  # CS controlled manually via cs_pin

            self._reset()
            self._cmd([_CMD_SET_STANDBY, 0x00])  # STDBY_RC

            # TCXO on DIO3 at 1.8V, 5ms startup delay
            self._cmd([_CMD_SET_DIO3_AS_TCXO_CTRL, 0x07, 0x00, 0x01, 0x40])
            # Calibrate all blocks after TCXO is stable
            self._cmd([_CMD_CALIBRATE, 0x7F])
            time.sleep(0.01)

            self._cmd([_CMD_SET_REGULATOR_MODE, 0x01])  # DC-DC
            self._cmd([_CMD_SET_PACKET_TYPE, 0x01])  # LoRa

            freq_raw = int(self._frequency / 32e6 * (1 << 25))
            self._cmd([
                _CMD_SET_RF_FREQUENCY,
                (freq_raw >> 24) & 0xFF,
                (freq_raw >> 16) & 0xFF,
                (freq_raw >> 8) & 0xFF,
                freq_raw & 0xFF,
            ])

            # PA config for SX1262 with PA_BOOST (up to +22 dBm)
            self._cmd([_CMD_SET_PA_CONFIG, 0x04, 0x07, 0x00, 0x01])
            clamped_power = max(-9, min(22, self._tx_power))
            self._cmd([_CMD_SET_TX_PARAMS, clamped_power & 0xFF, 0x04])  # ramp 200µs

            self._cmd([_CMD_SET_BUFFER_BASE_ADDR, 0x00, 0x00])

            bw_idx = _BW_MAP.get(self._bw, 0x04)
            self._cmd([_CMD_SET_MODULATION_PARAMS, self._sf, bw_idx, 0x01, 0x00])

            # Packet params: preamble=12, explicit header, max255, CRC on, IQ normal
            self._cmd([_CMD_SET_PACKET_PARAMS, 0x00, 0x0C, 0x00, 0xFF, 0x01, 0x00])

            # Private network sync word (0x1424)
            self._write_register(_REG_LORA_SYNC_WORD_MSB, 0x14)
            self._write_register(_REG_LORA_SYNC_WORD_LSB, 0x24)

            # IRQ: TX_DONE and TIMEOUT on DIO1
            self._cmd([
                _CMD_SET_DIO_IRQ_PARAMS,
                0x02, 0x01,  # IRQ mask (TX_DONE | TIMEOUT)
                0x02, 0x01,  # DIO1 mask
                0x00, 0x00,  # DIO2 mask
                0x00, 0x00,  # DIO3 mask
            ])

            logger.info("LoRaTransmitter (SX1262) initialized at %dMHz SF%d BW%dkHz",
                        self._frequency // 1_000_000, self._sf, self._bw // 1_000)
            return True
        except Exception as exc:
            logger.error("LoRa init failed: %s", exc)
            return False

    def send(self, data: bytes) -> bool:
        if self._mock_mode:
            logger.info("LoRa mock TX: %d bytes", len(data))
            return True
        try:
            self._cmd([_CMD_SET_STANDBY, 0x00])
            self._cmd([_CMD_CLEAR_IRQ_STATUS, 0xFF, 0xFF])

            payload = list(data)
            self._cmd([_CMD_WRITE_BUFFER, 0x00] + payload)

            # Update payload length in packet params
            self._cmd([_CMD_SET_PACKET_PARAMS, 0x00, 0x0C, 0x00, len(payload), 0x01, 0x00])

            self._GPIO.output(self._txen_pin, self._GPIO.HIGH)
            self._cmd([_CMD_SET_TX, 0x00, 0x00, 0x00])  # no timeout

            timeout = time.time() + 5.0
            while time.time() < timeout:
                irq = self._get_irq()
                if irq & _IRQ_TX_DONE:
                    self._cmd([_CMD_CLEAR_IRQ_STATUS, 0xFF, 0xFF])
                    self._GPIO.output(self._txen_pin, self._GPIO.LOW)
                    logger.debug("LoRa TX done, %d bytes", len(data))
                    return True
                if irq & _IRQ_TIMEOUT:
                    logger.warning("LoRa TX timeout (IRQ)")
                    break
                time.sleep(0.005)

            self._GPIO.output(self._txen_pin, self._GPIO.LOW)
            logger.warning("LoRa TX did not complete in time")
            return False
        except Exception as exc:
            logger.error("LoRa send error: %s", exc)
            if self._GPIO:
                self._GPIO.output(self._txen_pin, self._GPIO.LOW)
            return False

    def close(self) -> None:
        if self._mock_mode:
            return
        try:
            self._cmd([_CMD_SET_STANDBY, 0x00])
            if self._spi:
                self._spi.close()
            if self._GPIO:
                self._GPIO.cleanup()
        except Exception as exc:
            logger.warning("LoRa close error: %s", exc)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _reset(self) -> None:
        self._GPIO.output(self._reset_pin, self._GPIO.LOW)
        time.sleep(0.001)  # >100µs
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
