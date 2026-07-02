import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

# SX1276 register map
_REG_FIFO              = 0x00
_REG_OP_MODE           = 0x01
_REG_FR_MSB            = 0x06
_REG_FR_MID            = 0x07
_REG_FR_LSB            = 0x08
_REG_PA_CONFIG         = 0x09
_REG_PA_DAC            = 0x4D
_REG_FIFO_ADDR_PTR     = 0x0D
_REG_FIFO_TX_BASE_ADDR = 0x0E
_REG_IRQ_FLAGS         = 0x12
_REG_MODEM_CONFIG1     = 0x1D
_REG_MODEM_CONFIG2     = 0x1E
_REG_PREAMBLE_MSB      = 0x20
_REG_PREAMBLE_LSB      = 0x21
_REG_PAYLOAD_LENGTH    = 0x22
_REG_SYNC_WORD         = 0x39
_REG_VERSION           = 0x42

_MODE_SLEEP  = 0x00
_MODE_STDBY  = 0x01
_MODE_TX     = 0x03
_LORA_FLAG   = 0x80

_IRQ_TX_DONE = 0x08

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


class LoRaTransmitter:
    def __init__(
        self,
        spi_bus: int = 0,
        spi_device: int = 0,
        reset_pin: int = 17,
        frequency: int = 868_000_000,
        tx_power: int = 17,
        spreading_factor: int = 7,
        bandwidth: int = 125_000,
    ):
        self._spi_bus = spi_bus
        self._spi_device = spi_device
        self._reset_pin = reset_pin
        self._frequency = frequency
        self._tx_power = min(max(tx_power, 2), 20)
        self._sf = spreading_factor
        self._bw = bandwidth
        self._spi = None
        self._gpio_handle: Optional[int] = None
        self._mock_mode = False

        hw_ok, spidev_mod, lgpio_mod = _try_import_hw()
        if not hw_ok:
            logger.warning("spidev/lgpio not available — LoRaTransmitter running in mock mode")
            self._mock_mode = True
        else:
            self._spidev = spidev_mod
            self._lgpio = lgpio_mod

    def initialize(self) -> bool:
        if self._mock_mode:
            logger.info("LoRaTransmitter mock initialization OK")
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

            # PA config on PA_BOOST pin
            if self._tx_power > 17:
                # +20 dBm high-power mode
                self._write_reg(_REG_PA_CONFIG, 0x8F)
                self._write_reg(_REG_PA_DAC, 0x87)
            else:
                output_power = max(0, self._tx_power - 2)
                self._write_reg(_REG_PA_CONFIG, 0x80 | output_power)
                self._write_reg(_REG_PA_DAC, 0x84)

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

            # FIFO TX base at 0
            self._write_reg(_REG_FIFO_TX_BASE_ADDR, 0x00)

            logger.info("LoRaTransmitter (SX1276) initialized at %dMHz SF%d BW%dkHz +%ddBm",
                        self._frequency // 1_000_000, self._sf, self._bw // 1_000, self._tx_power)
            return True
        except Exception as exc:
            logger.error("LoRa init failed: %s", exc)
            return False

    def send(self, data: bytes) -> bool:
        if self._mock_mode:
            logger.info("LoRa mock TX: %d bytes", len(data))
            return True
        try:
            self._write_reg(_REG_OP_MODE, _LORA_FLAG | _MODE_STDBY)

            # Write payload to FIFO
            self._write_reg(_REG_FIFO_ADDR_PTR, 0x00)
            for b in data:
                self._write_reg(_REG_FIFO, b)
            self._write_reg(_REG_PAYLOAD_LENGTH, len(data))

            # Clear IRQ flags and start TX
            self._write_reg(_REG_IRQ_FLAGS, 0xFF)
            self._write_reg(_REG_OP_MODE, _LORA_FLAG | _MODE_TX)

            deadline = time.time() + 5.0
            while time.time() < deadline:
                if self._read_reg(_REG_IRQ_FLAGS) & _IRQ_TX_DONE:
                    self._write_reg(_REG_IRQ_FLAGS, 0xFF)
                    self._write_reg(_REG_OP_MODE, _LORA_FLAG | _MODE_STDBY)
                    logger.debug("LoRa TX done, %d bytes", len(data))
                    return True
                time.sleep(0.005)

            logger.warning("LoRa TX did not complete")
            self._write_reg(_REG_OP_MODE, _LORA_FLAG | _MODE_STDBY)
            return False
        except Exception as exc:
            logger.error("LoRa send error: %s", exc)
            return False

    def close(self) -> None:
        if self._mock_mode:
            return
        try:
            if self._spi:
                self._write_reg(_REG_OP_MODE, _MODE_SLEEP)
                self._spi.close()
            if self._gpio_handle is not None:
                self._lgpio.gpiochip_close(self._gpio_handle)
        except Exception as exc:
            logger.warning("LoRa close error: %s", exc)

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
