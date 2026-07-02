#!/usr/bin/env python3
"""
Sends LoRa test packets in a loop every 5 seconds.
Run this on Device 1 while lora_rx_test.py runs on Device 2.
"""
import time, sys

FREQUENCY  = 868_000_000
SF         = 7
BW         = 125_000
CS_PIN     = 21; RESET_PIN = 18; BUSY_PIN = 20; DIO1_PIN = 16; TXEN_PIN = 6
SPI_BUS    = 0;  SPI_DEVICE = 0

_BW_MAP = {125_000: 0x04, 250_000: 0x05, 500_000: 0x06}

def main():
    print("=== LoRa TX Loop ===")
    print(f"Sending on 868 MHz SF{SF} BW{BW//1000}kHz every 5s")
    print("Press Ctrl+C to stop\n")

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

    spi = spidev.SpiDev()
    spi.open(SPI_BUS, SPI_DEVICE)
    spi.max_speed_hz = 8_000_000
    spi.mode = 0

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

    # Reset + init
    lg.gpio_write(h, RESET_PIN, 0); time.sleep(0.001)
    lg.gpio_write(h, RESET_PIN, 1); time.sleep(0.02)
    busy()

    cmd([0x80, 0x00])           # SetStandby RC
    cmd([0x89, 0x1F])           # Calibrate (no XOSC)
    time.sleep(0.05)
    cmd([0x96, 0x01])           # SetRegulatorMode DC-DC
    cmd([0x01, 0x01])           # SetPacketType LoRa
    cmd([0x9D, 0x01])           # SetDio2AsRfSwitchCtrl
    cmd([0x98, 0xD7, 0xDB])    # CalibrateImage 863-870 MHz

    freq = int(FREQUENCY / 32e6 * (1 << 25))
    cmd([0x86, (freq>>24)&0xFF, (freq>>16)&0xFF, (freq>>8)&0xFF, freq&0xFF])

    cmd([0x95, 0x02, 0x02, 0x00, 0x01])  # SetPaConfig +14dBm
    cmd([0x8E, 0x16, 0x05])              # SetTxParams
    cmd([0x8F, 0x00, 0x00])             # SetBufferBase
    cmd([0x8B, SF, _BW_MAP[BW], 0x01, 0x00])  # SetModParams
    cmd([0x8C, 0x00, 0x0C, 0x00, 0xFF, 0x01, 0x00])  # SetPktParams

    wreg(0x0740, 0x14)   # Sync word (private 0x1424 = SX1262 default)
    wreg(0x0741, 0x24)
    cmd([0x08, 0x02, 0x01, 0x02, 0x01, 0x00, 0x00, 0x00, 0x00])  # SetDioIrq

    print("Init OK — starting TX loop\n")

    count = 0
    try:
        while True:
            count += 1
            payload = list(f"TEST_{count:04d}".encode())

            cmd([0x02, 0xFF, 0xFF])  # ClearIrq
            cmd([0x0E, 0x00] + payload)  # WriteBuffer
            cmd([0x8C, 0x00, 0x0C, 0x00, len(payload), 0x01, 0x00])

            # TXEN=LOW activates TX path on Waveshare module
            lg.gpio_write(h, TXEN_PIN, 0)
            cmd([0x83, 0x00, 0x00, 0x00])  # SetTx (no timeout)

            ok = False
            deadline = time.time() + 3.0
            while time.time() < deadline:
                r = spi.xfer2([0x12, 0x00, 0x00, 0x00])  # GetIrq (no busy wait)
                irq = (r[2] << 8) | r[3]
                if irq & 0x0001:
                    ok = True
                    break
                time.sleep(0.005)

            ts = time.strftime("%H:%M:%S")
            if ok:
                print(f"[{ts}] TX #{count}: '{payload}' — TX_DONE ✓")
            else:
                print(f"[{ts}] TX #{count}: TIMEOUT — no TX_DONE")

            time.sleep(5)

    except KeyboardInterrupt:
        print(f"\nStopped after {count} transmissions.")
    finally:
        cmd([0x80, 0x00])
        spi.close()
        lg.gpiochip_close(h)

if __name__ == "__main__":
    main()
