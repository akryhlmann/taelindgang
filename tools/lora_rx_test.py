#!/usr/bin/env python3
"""
Standalone LoRa RX test for Device 2.

Initializes SX1262 in continuous RX mode and prints any received packets.
Run this on Device 2 while running lora_diagnostic.py on Device 1.

Usage:
    sudo systemctl stop visitor-counter-device2
    source ~/venv_device2/bin/activate
    python tools/lora_rx_test.py
"""
import time
import sys

FREQUENCY   = 868_000_000
SF          = 7
BW          = 125_000
CS_PIN      = 21
RESET_PIN   = 18
BUSY_PIN    = 20
DIO1_PIN    = 16
TXEN_PIN    = 6
SPI_BUS     = 0
SPI_DEVICE  = 0

_BW_MAP = {
    7_800: 0x00, 10_400: 0x08, 15_600: 0x01, 20_800: 0x09,
    31_250: 0x02, 41_700: 0x0A, 62_500: 0x03, 125_000: 0x04,
    250_000: 0x05, 500_000: 0x06,
}

def info(msg):  print(f"[INFO] {msg}", flush=True)
def ok(msg):    print(f"[PASS] {msg}", flush=True)
def warn(msg):  print(f"[WARN] {msg}", flush=True)
def err(msg):   print(f"[FAIL] {msg}", flush=True)


def main():
    print("\n=== SX1262 LoRa RX Test ===\n")

    try:
        import spidev
        import lgpio
    except ImportError as e:
        err(f"Missing library: {e}")
        sys.exit(1)

    # GPIO setup
    lg = lgpio
    h = lg.gpiochip_open(0)
    lg.gpio_claim_output(h, CS_PIN,    1)
    lg.gpio_claim_output(h, RESET_PIN, 1)
    lg.gpio_claim_input(h,  BUSY_PIN)
    lg.gpio_claim_input(h,  DIO1_PIN)
    lg.gpio_claim_output(h, TXEN_PIN,  0)
    ok("GPIO configured")

    # SPI setup
    spi = spidev.SpiDev()
    spi.open(SPI_BUS, SPI_DEVICE)
    spi.max_speed_hz = 8_000_000
    spi.mode = 0
    ok("SPI opened")

    def wait_busy(timeout=2.0):
        deadline = time.time() + timeout
        while lg.gpio_read(h, BUSY_PIN) == 1:
            if time.time() > deadline:
                raise TimeoutError("BUSY timeout")
            time.sleep(0.0001)

    def cmd(data):
        wait_busy()
        lg.gpio_write(h, CS_PIN, 0)
        result = spi.xfer2(data)
        lg.gpio_write(h, CS_PIN, 1)
        return result

    def write_reg(addr, val):
        cmd([0x0D, (addr >> 8) & 0xFF, addr & 0xFF, val])

    def get_irq():
        wait_busy()
        lg.gpio_write(h, CS_PIN, 0)
        r = spi.xfer2([0x12, 0x00, 0x00, 0x00])
        lg.gpio_write(h, CS_PIN, 1)
        return (r[2] << 8) | r[3]

    # Reset
    lg.gpio_write(h, RESET_PIN, 0); time.sleep(0.001)
    lg.gpio_write(h, RESET_PIN, 1); time.sleep(0.02)
    wait_busy()

    # Check chip status
    r = cmd([0xC0, 0x00])
    mode = (r[1] >> 4) & 0x07
    info(f"Chip mode after reset: {mode} ({'STDBY_RC' if mode == 2 else mode})")

    # Init sequence (matches receiver.py exactly)
    cmd([0x80, 0x00])                          # SetStandby RC
    cmd([0x89, 0x1F])                          # Calibrate (no XOSC)
    time.sleep(0.05)
    cmd([0x96, 0x01])                          # SetRegulatorMode DC-DC
    cmd([0x01, 0x01])                          # SetPacketType LoRa
    cmd([0x9D, 0x01])                          # SetDio2AsRfSwitchCtrl
    cmd([0x98, 0xD7, 0xDB])                    # CalibrateImage 863-870 MHz

    freq_raw = int(FREQUENCY / 32e6 * (1 << 25))
    cmd([0x86, (freq_raw>>24)&0xFF, (freq_raw>>16)&0xFF,
               (freq_raw>>8)&0xFF, freq_raw&0xFF])

    cmd([0x8F, 0x00, 0x00])                    # SetBufferBase TX=0 RX=0
    bw_idx = _BW_MAP.get(BW, 0x04)
    cmd([0x8B, SF, bw_idx, 0x01, 0x00])       # SetModParams SF BW CR LDRO
    cmd([0x8C, 0x00, 0x0C, 0x00, 0xFF, 0x01, 0x00])  # SetPktParams

    write_reg(0x0740, 0x14)                    # Sync word MSB
    write_reg(0x0741, 0x24)                    # Sync word LSB

    # IRQ: RX_DONE | CRC_ERROR | HEADER_ERROR | TIMEOUT on DIO1
    cmd([0x08, 0x02, 0x62, 0x02, 0x62, 0x00, 0x00, 0x00, 0x00])

    ok(f"Initialized: 868 MHz, SF{SF}, BW{BW//1000}kHz, sync=0x1424")

    # Set TXEN HIGH — on Waveshare module this enables the RF switch path
    # (needed for both TX and RX, not just TX)
    lg.gpio_write(h, TXEN_PIN, 1)

    # Enter continuous RX
    cmd([0x82, 0xFF, 0xFF, 0xFF])
    r = cmd([0xC0, 0x00])
    mode = (r[1] >> 4) & 0x07
    info(f"Chip mode after SetRx: {mode} (5=RX expected)")
    if mode == 5:
        ok("Chip is in RX mode — listening...")
    else:
        warn(f"Unexpected chip mode {mode} — RX may not be active")

    print(f"\nWaiting for packets on 868 MHz SF{SF} BW{BW//1000}kHz ...")
    print("(Run lora_diagnostic.py on the OTHER device to send a test packet)\n")
    print("Press Ctrl+C to stop.\n")

    packets = 0
    start = time.time()
    last_heartbeat = time.time()
    try:
        while True:
            now = time.time()
            if now - last_heartbeat >= 5.0:
                elapsed = int(now - start)
                r = cmd([0xC0, 0x00])
                mode = (r[1] >> 4) & 0x07 if r else -1
                print(f"  [{elapsed:3d}s] Still listening... chip mode={mode} (5=RX OK), packets={packets}", flush=True)
                last_heartbeat = now

            irq = get_irq()

            if irq & 0x0002:  # RX_DONE
                cmd([0x02, 0xFF, 0xFF])  # ClearIrq all
                elapsed = time.time() - start

                if irq & (0x0040 | 0x0020):  # CRC_ERROR | HEADER_ERROR
                    warn(f"Packet error (IRQ=0x{irq:04X}) at t+{elapsed:.1f}s")
                    cmd([0x82, 0xFF, 0xFF, 0xFF])  # back to RX
                    continue

                # Read buffer status
                st = cmd([0x13, 0x00, 0x00, 0x00])
                payload_len = st[2]
                rx_start    = st[3]

                # Read payload
                raw = cmd([0x1E, rx_start, 0x00] + [0x00] * payload_len)
                data = bytes(raw[3:])

                # Packet status (RSSI, SNR)
                pkt = cmd([0x14, 0x00, 0x00, 0x00, 0x00])
                rssi = -(pkt[2] // 2)
                snr  = pkt[3] / 4 if pkt[3] < 128 else (pkt[3] - 256) / 4

                packets += 1
                ok(f"Packet #{packets} received at t+{elapsed:.1f}s: "
                   f"{payload_len} bytes, RSSI={rssi}dBm, SNR={snr:.1f}dB")
                info(f"  Raw hex: {data.hex()}")

                # Try to decode as visitor counter protocol
                if len(data) >= 12:
                    try:
                        device_id = data[1:9].rstrip(b'\x00').decode('ascii', errors='replace')
                        msg_type  = data[9]
                        count_in  = int.from_bytes(data[10:12], 'big')
                        count_out = int.from_bytes(data[12:14], 'big') if len(data) >= 14 else 0
                        info(f"  Decoded: device={device_id!r} type={msg_type} in={count_in} out={count_out}")
                    except Exception:
                        pass

                cmd([0x82, 0xFF, 0xFF, 0xFF])  # back to RX

            elif irq & 0x0200:  # TIMEOUT
                cmd([0x02, 0xFF, 0xFF])
                cmd([0x82, 0xFF, 0xFF, 0xFF])

            time.sleep(0.005)

    except KeyboardInterrupt:
        print(f"\n--- Test stopped. Received {packets} packet(s). ---")
    finally:
        cmd([0x80, 0x00])  # standby
        spi.close()
        lg.gpiochip_close(h)


if __name__ == "__main__":
    main()
