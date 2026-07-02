#!/usr/bin/env python3
"""
SX1276 LoRa TX test — sends a packet every 5 seconds.

Wiring (RPi GPIO header):
  VCC  -> 3.3V  (pin 1)
  GND  -> GND   (pin 6)
  SCK  -> GPIO 11 (pin 23)
  MISO -> GPIO 9  (pin 21)
  MOSI -> GPIO 10 (pin 19)
  NSS  -> GPIO 8  (pin 24)   <- hardware CE0, managed by spidev
  RST  -> GPIO 17 (pin 11)

Usage:
  sudo systemctl stop visitor-counter-device1
  source ~/venv_device1/bin/activate
  python tools/sx1276_tx_loop.py
"""
import time, sys

FREQUENCY  = 868_000_000
SF         = 7
RST_PIN    = 17    # BCM
SPI_BUS    = 0
SPI_DEVICE = 0     # CE0 = GPIO 8

# SX1276 LoRa register map
REG_FIFO              = 0x00
REG_OP_MODE           = 0x01
REG_FR_MSB            = 0x06
REG_FR_MID            = 0x07
REG_FR_LSB            = 0x08
REG_PA_CONFIG         = 0x09
REG_FIFO_ADDR_PTR     = 0x0D
REG_FIFO_TX_BASE_ADDR = 0x0E
REG_IRQ_FLAGS         = 0x12
REG_MODEM_CONFIG1     = 0x1D  # BW + CR + header
REG_MODEM_CONFIG2     = 0x1E  # SF + CRC
REG_PREAMBLE_MSB      = 0x20
REG_PREAMBLE_LSB      = 0x21
REG_PAYLOAD_LENGTH    = 0x22
REG_SYNC_WORD         = 0x39
REG_VERSION           = 0x42  # should read 0x12 for SX1276

# OpMode values (LoRa flag = bit 7)
MODE_SLEEP   = 0x00
MODE_STDBY   = 0x01
MODE_TX      = 0x03
MODE_RXCONT  = 0x05
LORA_FLAG    = 0x80

IRQ_TX_DONE = 0x08
SYNC_WORD   = 0x12   # private network


def main():
    print("=== SX1276 LoRa TX Loop ===")
    print(f"868 MHz SF{SF} BW125kHz every 5s")
    print("Press Ctrl+C to stop\n")

    try:
        import spidev, lgpio
    except ImportError as e:
        print(f"Missing: {e}"); sys.exit(1)

    lg = lgpio
    h = lg.gpiochip_open(0)
    lg.gpio_claim_output(h, RST_PIN, 1)

    spi = spidev.SpiDev()
    spi.open(SPI_BUS, SPI_DEVICE)
    spi.max_speed_hz = 8_000_000
    spi.mode = 0

    def write_reg(reg, val):
        spi.xfer2([reg | 0x80, val])

    def read_reg(reg):
        r = spi.xfer2([reg & 0x7F, 0x00])
        return r[1]

    # Hardware reset
    lg.gpio_write(h, RST_PIN, 0); time.sleep(0.01)
    lg.gpio_write(h, RST_PIN, 1); time.sleep(0.01)

    # Verify chip version
    ver = read_reg(REG_VERSION)
    if ver != 0x12:
        print(f"ERROR: Expected version 0x12, got 0x{ver:02X} — wrong chip or wiring?")
        sys.exit(1)
    print(f"Chip version: 0x{ver:02X} (SX1276 OK)")

    # Switch to LoRa mode (must go through sleep)
    write_reg(REG_OP_MODE, MODE_SLEEP)
    time.sleep(0.01)
    write_reg(REG_OP_MODE, LORA_FLAG | MODE_SLEEP)
    time.sleep(0.01)
    write_reg(REG_OP_MODE, LORA_FLAG | MODE_STDBY)
    time.sleep(0.01)

    # Set frequency: Frf = Freq * 2^19 / 32e6
    frf = int(FREQUENCY / 32e6 * (1 << 19))
    write_reg(REG_FR_MSB, (frf >> 16) & 0xFF)
    write_reg(REG_FR_MID, (frf >>  8) & 0xFF)
    write_reg(REG_FR_LSB,  frf        & 0xFF)

    # PA config: PA_BOOST, max power (+20 dBm)
    write_reg(REG_PA_CONFIG, 0xFF)

    # Modem config: BW 125kHz, CR 4/5, explicit header
    write_reg(REG_MODEM_CONFIG1, 0x72)
    # SF7, CRC on
    write_reg(REG_MODEM_CONFIG2, (SF << 4) | 0x04)

    # Preamble length = 8
    write_reg(REG_PREAMBLE_MSB, 0x00)
    write_reg(REG_PREAMBLE_LSB, 0x08)

    # Sync word
    write_reg(REG_SYNC_WORD, SYNC_WORD)

    # FIFO TX base address = 0
    write_reg(REG_FIFO_TX_BASE_ADDR, 0x00)

    print(f"Init OK (sync=0x{SYNC_WORD:02X}) — starting TX loop\n")

    count = 0
    try:
        while True:
            count += 1
            payload = f"SX1276_TEST_{count:04d}".encode()

            # Set FIFO pointer to TX base
            write_reg(REG_FIFO_ADDR_PTR, 0x00)

            # Write payload to FIFO
            for b in payload:
                write_reg(REG_FIFO, b)
            write_reg(REG_PAYLOAD_LENGTH, len(payload))

            # Clear IRQ flags
            write_reg(REG_IRQ_FLAGS, 0xFF)

            # Start TX
            write_reg(REG_OP_MODE, LORA_FLAG | MODE_TX)

            # Wait for TX_DONE
            ok = False
            deadline = time.time() + 5.0
            while time.time() < deadline:
                flags = read_reg(REG_IRQ_FLAGS)
                if flags & IRQ_TX_DONE:
                    write_reg(REG_IRQ_FLAGS, 0xFF)
                    ok = True
                    break
                time.sleep(0.005)

            # Return to standby
            write_reg(REG_OP_MODE, LORA_FLAG | MODE_STDBY)

            ts = time.strftime("%H:%M:%S")
            status = "TX_DONE ✓" if ok else "TIMEOUT ✗"
            print(f"[{ts}] TX #{count}: '{payload.decode()}' — {status}")

            time.sleep(5)

    except KeyboardInterrupt:
        print(f"\nStopped after {count} transmissions.")
    finally:
        write_reg(REG_OP_MODE, MODE_SLEEP)
        spi.close()
        lg.gpiochip_close(h)


if __name__ == "__main__":
    main()
