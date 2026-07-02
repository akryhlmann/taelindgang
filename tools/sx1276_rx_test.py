#!/usr/bin/env python3
"""
SX1276 LoRa RX test — listens for packets continuously.

Wiring (RPi GPIO header):
  VCC  -> 3.3V  (pin 1)
  GND  -> GND   (pin 6)
  SCK  -> GPIO 11 (pin 23)
  MISO -> GPIO 9  (pin 21)
  MOSI -> GPIO 10 (pin 19)
  NSS  -> GPIO 8  (pin 24)   <- hardware CE0, managed by spidev
  RST  -> GPIO 17 (pin 11)

Usage:
  sudo systemctl stop visitor-counter-device2
  source ~/venv_device2/bin/activate
  python tools/sx1276_rx_test.py

Run this on Device 2 while sx1276_tx_loop.py runs on Device 1.
"""
import time, sys

FREQUENCY  = 868_000_000
SF         = 7
RST_PIN    = 17    # BCM
SPI_BUS    = 0
SPI_DEVICE = 0     # CE0 = GPIO 8

REG_FIFO              = 0x00
REG_OP_MODE           = 0x01
REG_FR_MSB            = 0x06
REG_FR_MID            = 0x07
REG_FR_LSB            = 0x08
REG_PA_CONFIG         = 0x09
REG_FIFO_ADDR_PTR     = 0x0D
REG_FIFO_TX_BASE_ADDR = 0x0E
REG_FIFO_RX_BASE_ADDR = 0x0F
REG_FIFO_RX_CURR_ADDR = 0x10
REG_IRQ_FLAGS         = 0x12
REG_RX_NB_BYTES       = 0x13
REG_PKT_RSSI_VALUE    = 0x1A
REG_PKT_SNR_VALUE     = 0x19
REG_MODEM_CONFIG1     = 0x1D
REG_MODEM_CONFIG2     = 0x1E
REG_PREAMBLE_MSB      = 0x20
REG_PREAMBLE_LSB      = 0x21
REG_PAYLOAD_LENGTH    = 0x22
REG_SYNC_WORD         = 0x39
REG_VERSION           = 0x42

MODE_SLEEP  = 0x00
MODE_STDBY  = 0x01
MODE_RXCONT = 0x05
LORA_FLAG   = 0x80

IRQ_RX_DONE      = 0x40
IRQ_PAYLOAD_CRC  = 0x20
IRQ_RX_TIMEOUT   = 0x80

SYNC_WORD = 0x12  # must match TX


def main():
    print("\n=== SX1276 LoRa RX Test ===\n")

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

    ver = read_reg(REG_VERSION)
    if ver != 0x12:
        print(f"[FAIL] Expected version 0x12, got 0x{ver:02X} — wrong chip or wiring?")
        sys.exit(1)
    print(f"[PASS] Chip version: 0x{ver:02X} (SX1276 OK)")

    # Switch to LoRa mode via sleep
    write_reg(REG_OP_MODE, MODE_SLEEP)
    time.sleep(0.01)
    write_reg(REG_OP_MODE, LORA_FLAG | MODE_SLEEP)
    time.sleep(0.01)
    write_reg(REG_OP_MODE, LORA_FLAG | MODE_STDBY)
    time.sleep(0.01)

    # Frequency
    frf = int(FREQUENCY / 32e6 * (1 << 19))
    write_reg(REG_FR_MSB, (frf >> 16) & 0xFF)
    write_reg(REG_FR_MID, (frf >>  8) & 0xFF)
    write_reg(REG_FR_LSB,  frf        & 0xFF)

    # Modem config: BW 125kHz, CR 4/5, explicit header
    write_reg(REG_MODEM_CONFIG1, 0x72)
    # SF7, CRC on
    write_reg(REG_MODEM_CONFIG2, (SF << 4) | 0x04)

    # Preamble
    write_reg(REG_PREAMBLE_MSB, 0x00)
    write_reg(REG_PREAMBLE_LSB, 0x08)

    # Sync word
    write_reg(REG_SYNC_WORD, SYNC_WORD)

    # FIFO RX base = 0
    write_reg(REG_FIFO_RX_BASE_ADDR, 0x00)
    write_reg(REG_FIFO_ADDR_PTR, 0x00)

    print(f"[PASS] Initialized: 868 MHz SF{SF} BW125kHz sync=0x{SYNC_WORD:02X}")

    # Enter continuous RX
    write_reg(REG_IRQ_FLAGS, 0xFF)
    write_reg(REG_OP_MODE, LORA_FLAG | MODE_RXCONT)
    time.sleep(0.01)   # allow mode transition

    mode = read_reg(REG_OP_MODE)
    print(f"[INFO] OpMode after SetRx: 0x{mode:02X} (0x85=RxCont+LoRa expected)")
    if (mode & 0x07) == MODE_RXCONT and (mode & LORA_FLAG):
        print("[PASS] Chip in RxContinuous mode — listening...")
    else:
        print(f"[WARN] Unexpected mode 0x{mode:02X}")

    print(f"\nWaiting for packets on 868 MHz SF{SF} BW125kHz ...")
    print("Press Ctrl+C to stop.\n")

    packets = 0
    start = time.time()
    last_hb = time.time()

    try:
        while True:
            now = time.time()
            if now - last_hb >= 5.0:
                elapsed = int(now - start)
                mode = read_reg(REG_OP_MODE)
                flags = read_reg(REG_IRQ_FLAGS)
                mode_ok = "RxCont" if (mode & 0x07) == MODE_RXCONT else f"0x{mode:02X}"
                print(f"  [{elapsed:3d}s] mode={mode_ok} pkts={packets} IRQ=0x{flags:02X}", flush=True)
                last_hb = now

            flags = read_reg(REG_IRQ_FLAGS)

            if flags & IRQ_RX_DONE:
                elapsed = time.time() - start
                write_reg(REG_IRQ_FLAGS, 0xFF)

                if flags & IRQ_PAYLOAD_CRC:
                    print(f"  [WARN] CRC error (IRQ=0x{flags:02X}) at t+{elapsed:.1f}s")
                    continue

                nb = read_reg(REG_RX_NB_BYTES)
                rx_addr = read_reg(REG_FIFO_RX_CURR_ADDR)
                write_reg(REG_FIFO_ADDR_PTR, rx_addr)

                data = bytes(read_reg(REG_FIFO) for _ in range(nb))

                rssi = read_reg(REG_PKT_RSSI_VALUE) - 157
                snr_raw = read_reg(REG_PKT_SNR_VALUE)
                snr = snr_raw / 4 if snr_raw < 128 else (snr_raw - 256) / 4

                packets += 1
                print(f"[PASS] Packet #{packets} at t+{elapsed:.1f}s: "
                      f"{nb}B RSSI={rssi}dBm SNR={snr:.1f}dB")
                try:
                    print(f"  Text: {data.decode('ascii', errors='replace')!r}")
                except Exception:
                    pass
                print(f"  Hex:  {data.hex()}")

            time.sleep(0.005)

    except KeyboardInterrupt:
        print(f"\n--- Stopped. Received {packets} packet(s). ---")
    finally:
        write_reg(REG_OP_MODE, MODE_SLEEP)
        spi.close()
        lg.gpiochip_close(h)


if __name__ == "__main__":
    main()
