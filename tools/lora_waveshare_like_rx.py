#!/usr/bin/env python3
"""
RX test that mirrors the Waveshare LoRaRF library init sequence
using lgpio instead of RPi.GPIO.

Run this on Device 2 while lora_waveshare_like_tx.py runs on Device 1.
"""
import time, sys

FREQUENCY  = 868_000_000
SF         = 7
BW         = 125_000
CS_PIN     = 21; RESET_PIN = 18; BUSY_PIN = 20; DIO1_PIN = 16; TXEN_PIN = 6
SPI_BUS    = 0;  SPI_DEVICE = 0

SYNC_WORD  = 0x3444   # Must match lora_waveshare_like_tx.py

_BW_MAP = {125_000: 0x04, 250_000: 0x05, 500_000: 0x06}


def main():
    print("\n=== SX1262 LoRa RX (Waveshare-like init) ===\n")

    try:
        import spidev, lgpio
    except ImportError as e:
        print(f"Missing: {e}"); sys.exit(1)

    lg = lgpio
    h = lg.gpiochip_open(0)
    lg.gpio_claim_output(h, CS_PIN,    1)
    lg.gpio_claim_output(h, RESET_PIN, 1)
    lg.gpio_claim_input(h,  BUSY_PIN)
    lg.gpio_claim_input(h,  DIO1_PIN)
    lg.gpio_claim_output(h, TXEN_PIN,  0)
    print("[PASS] GPIO configured")

    spi = spidev.SpiDev()
    spi.open(SPI_BUS, SPI_DEVICE)
    spi.max_speed_hz = 8_000_000
    spi.mode = 0
    print("[PASS] SPI opened")

    def busy(timeout=2.0):
        t = time.time() + timeout
        while lg.gpio_read(h, BUSY_PIN):
            if time.time() > t: raise TimeoutError("BUSY")
            time.sleep(0.0001)

    def cmd(data):
        busy()
        lg.gpio_write(h, CS_PIN, 0)
        r = spi.xfer2(data)
        lg.gpio_write(h, CS_PIN, 1)
        return r

    def wreg(addr, val):
        cmd([0x0D, (addr>>8)&0xFF, addr&0xFF, val])

    def rreg(addr):
        r = cmd([0x1D, (addr>>8)&0xFF, addr&0xFF, 0x00, 0x00])
        return r[4]

    def get_irq():
        busy()
        lg.gpio_write(h, CS_PIN, 0)
        r = spi.xfer2([0x12, 0x00, 0x00, 0x00])
        lg.gpio_write(h, CS_PIN, 1)
        return (r[2] << 8) | r[3]

    # --- Reset ---
    lg.gpio_write(h, RESET_PIN, 0); time.sleep(0.001)
    lg.gpio_write(h, RESET_PIN, 1); time.sleep(0.02)
    busy()

    r = cmd([0xC0, 0x00])
    mode = (r[1] >> 4) & 0x07
    print(f"[INFO] Chip mode after reset: {mode} ({'STDBY_RC' if mode == 2 else mode})")

    # --- begin() equivalent ---
    cmd([0x80, 0x00])           # SetStandby RC
    cmd([0x01, 0x01])           # SetPacketType LoRa
    wreg(0x08D8, rreg(0x08D8) | 0x1E)  # _fixResistanceAntenna

    # --- setDio2RfSwitch ---
    cmd([0x9D, 0x01])

    # --- setFrequency (includes calibrateImage) ---
    cmd([0x98, 0xD7, 0xDB])    # CalibrateImage 863-870 MHz
    freq = int(FREQUENCY / 32e6 * (1 << 25))
    cmd([0x86, (freq>>24)&0xFF, (freq>>16)&0xFF, (freq>>8)&0xFF, freq&0xFF])

    # --- setRxGain power saving (default) ---
    wreg(0x08AC, 0x94)

    # --- setLoRaModulation: SF7 BW125 CR4/5 LDRO off ---
    bw_idx = _BW_MAP[BW]
    cmd([0x8B, SF, bw_idx, 0x01, 0x00])

    # --- setLoRaPacket: explicit header, 12 preamble, CRC on, IQ standard ---
    cmd([0x8C, 0x00, 0x0C, 0x00, 0xFF, 0x01, 0x00])
    wreg(0x0736, rreg(0x0736) & 0xFB)  # _fixInvertedIq(False)

    # --- setSyncWord ---
    wreg(0x0740, (SYNC_WORD >> 8) & 0xFF)
    wreg(0x0741, SYNC_WORD & 0xFF)

    # --- SetBufferBase TX=0 RX=0 ---
    cmd([0x8F, 0x00, 0x00])

    # --- SetDioIrq: RX_DONE | PREAMBLE | SYNC | HEADER | CRC_ERR | TIMEOUT on DIO1 ---
    cmd([0x08, 0x02, 0x7E, 0x02, 0x7E, 0x00, 0x00, 0x00, 0x00])

    print(f"[PASS] Initialized: 868 MHz, SF{SF}, BW{BW//1000}kHz, sync=0x{SYNC_WORD:04X}")

    # TXEN=HIGH enables RX path on Waveshare module
    lg.gpio_write(h, TXEN_PIN, 1)

    # Enter continuous RX
    cmd([0x82, 0xFF, 0xFF, 0xFF])
    r = cmd([0xC0, 0x00])
    mode = (r[1] >> 4) & 0x07
    print(f"[INFO] Chip mode after SetRx: {mode} (5=RX expected)")
    if mode == 5:
        print("[PASS] Chip is in RX mode — listening...")
    else:
        print(f"[WARN] Unexpected chip mode {mode}")

    print(f"\nWaiting for packets on 868 MHz SF{SF} BW{BW//1000}kHz sync=0x{SYNC_WORD:04X}...")
    print("Press Ctrl+C to stop.\n")

    packets = 0
    start = time.time()
    last_hb = time.time()

    try:
        while True:
            now = time.time()
            if now - last_hb >= 5.0:
                elapsed = int(now - start)
                r = cmd([0xC0, 0x00])
                mode = (r[1] >> 4) & 0x07 if r else -1
                raw_irq = get_irq()
                irq_info = f"IRQ=0x{raw_irq:04X}"
                if raw_irq & 0x0004: irq_info += " PREAMBLE"
                if raw_irq & 0x0008: irq_info += " SYNC"
                print(f"  [{elapsed:3d}s] mode={mode} (5=RX) pkts={packets} {irq_info}", flush=True)
                last_hb = now

            irq = get_irq()

            if irq & 0x0002:  # RX_DONE
                cmd([0x02, 0xFF, 0xFF])
                elapsed = time.time() - start

                if irq & (0x0040 | 0x0020):
                    print(f"  [WARN] Packet error (IRQ=0x{irq:04X}) at t+{elapsed:.1f}s")
                    cmd([0x82, 0xFF, 0xFF, 0xFF])
                    continue

                st = cmd([0x13, 0x00, 0x00, 0x00])
                payload_len = st[2]; rx_start = st[3]
                raw = cmd([0x1E, rx_start, 0x00] + [0x00] * payload_len)
                data = bytes(raw[3:])

                pkt = cmd([0x14, 0x00, 0x00, 0x00, 0x00])
                rssi = -(pkt[2] // 2)
                snr  = pkt[3] / 4 if pkt[3] < 128 else (pkt[3] - 256) / 4

                packets += 1
                print(f"[PASS] Packet #{packets} at t+{elapsed:.1f}s: "
                      f"{payload_len}B RSSI={rssi}dBm SNR={snr:.1f}dB")
                try:
                    print(f"  Text: {data.decode('ascii', errors='replace')!r}")
                except Exception:
                    pass
                print(f"  Hex:  {data.hex()}")

                cmd([0x82, 0xFF, 0xFF, 0xFF])

            elif irq & 0x0200:  # TIMEOUT (shouldn't happen in continuous RX)
                cmd([0x02, 0xFF, 0xFF])
                cmd([0x82, 0xFF, 0xFF, 0xFF])

            time.sleep(0.005)

    except KeyboardInterrupt:
        print(f"\n--- Stopped. Received {packets} packet(s). ---")
    finally:
        cmd([0x80, 0x00])
        spi.close()
        lg.gpiochip_close(h)


if __name__ == "__main__":
    main()
