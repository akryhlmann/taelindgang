#!/usr/bin/env python3
"""
Standalone diagnostics for Waveshare SX1262 LoRaWAN Node Module.

Run directly on the Raspberry Pi:
    python3 tools/lora_diagnostic.py

Tests SPI communication, SX1262 initialization, and sends a test packet.
"""
import sys
import time

# --- Pin config (BCM) — matches device1/config.yaml ---
CS_PIN   = 21
RESET_PIN = 18
BUSY_PIN  = 20
DIO1_PIN  = 16
TXEN_PIN  =  6
SPI_BUS   =  0
SPI_DEV   =  0

# --- SX1262 opcodes ---
CMD_GET_STATUS          = 0xC0
CMD_SET_STANDBY         = 0x80
CMD_SET_PACKET_TYPE     = 0x01
CMD_SET_RF_FREQUENCY    = 0x86
CMD_SET_PA_CONFIG       = 0x95
CMD_SET_REGULATOR_MODE  = 0x96
CMD_SET_DIO3_TCXO       = 0x97
CMD_CALIBRATE_IMAGE     = 0x98
CMD_SET_DIO2_RF_SWITCH  = 0x9D
CMD_CALIBRATE           = 0x89
CMD_SET_TX_PARAMS       = 0x8E
CMD_SET_BUFFER_BASE     = 0x8F
CMD_SET_MOD_PARAMS      = 0x8B
CMD_SET_PKT_PARAMS      = 0x8C
CMD_SET_DIO_IRQ         = 0x08
CMD_CLEAR_IRQ           = 0x02
CMD_GET_IRQ             = 0x12
CMD_WRITE_BUFFER        = 0x0E
CMD_SET_TX              = 0x83
CMD_WRITE_REGISTER      = 0x0D

IRQ_TX_DONE  = 0x0001
IRQ_TIMEOUT  = 0x0200

PASS = "\033[92m[PASS]\033[0m"
FAIL = "\033[91m[FAIL]\033[0m"
INFO = "\033[94m[INFO]\033[0m"
WARN = "\033[93m[WARN]\033[0m"


def main():
    print("\n=== SX1262 LoRa Diagnostic ===\n")

    # ------------------------------------------------------------------
    # 1. Import hardware libraries
    # ------------------------------------------------------------------
    print(f"{INFO} Importing spidev and lgpio...")
    try:
        import spidev
        import lgpio
        print(f"{PASS} Libraries imported")
    except ImportError as e:
        print(f"{FAIL} {e}")
        print("       Run: pip install spidev lgpio")
        sys.exit(1)

    # ------------------------------------------------------------------
    # 2. Setup GPIO
    # ------------------------------------------------------------------
    print(f"\n{INFO} Setting up GPIO via lgpio...")
    try:
        h = lgpio.gpiochip_open(0)
        lgpio.gpio_claim_output(h, CS_PIN,    1)
        lgpio.gpio_claim_output(h, RESET_PIN, 1)
        lgpio.gpio_claim_input(h,  BUSY_PIN)
        lgpio.gpio_claim_input(h,  DIO1_PIN)
        lgpio.gpio_claim_output(h, TXEN_PIN,  0)
        print(f"{PASS} GPIO configured: CS={CS_PIN} RST={RESET_PIN} BUSY={BUSY_PIN} DIO1={DIO1_PIN} TXEN={TXEN_PIN}")
    except Exception as e:
        print(f"{FAIL} GPIO setup failed: {e}")
        sys.exit(1)


    # ------------------------------------------------------------------
    # 3. Open SPI
    # ------------------------------------------------------------------
    print(f"\n{INFO} Opening SPI...")
    spi = None
    spi_bus_used, spi_dev_used = 0, 0
    for try_bus, try_dev in [(0, 0), (0, 1), (10, 0)]:
        try:
            spi = spidev.SpiDev()
            spi.open(try_bus, try_dev)
            spi.max_speed_hz = 1_000_000
            spi.mode = 0
            spi_bus_used, spi_dev_used = try_bus, try_dev
            print(f"{PASS} SPI opened (/dev/spidev{try_bus}.{try_dev})")
            break
        except Exception as e:
            print(f"{WARN} /dev/spidev{try_bus}.{try_dev} failed: {e}")
            spi = None
    if spi is None:
        print(f"{FAIL} Could not open any SPI device")
        lgpio.gpiochip_close(h)
        sys.exit(1)

    def wait_busy(label="", timeout=2.0):
        deadline = time.time() + timeout
        while lgpio.gpio_read(h, BUSY_PIN) == 1:
            if time.time() > deadline:
                print(f"{FAIL} BUSY timeout ({label}) — SX1262 is stuck busy")
                print("       Check wiring: BUSY=GPIO20, RESET=GPIO18")
                return False
            time.sleep(0.0001)
        return True

    def cmd(data, label=""):
        if not wait_busy(label):
            return None
        lgpio.gpio_write(h, CS_PIN, 0)
        result = spi.xfer2(data)
        lgpio.gpio_write(h, CS_PIN, 1)
        return result

    def write_reg(addr, value):
        cmd([CMD_WRITE_REGISTER, (addr >> 8) & 0xFF, addr & 0xFF, value], "write_reg")

    def get_irq():
        if not wait_busy("get_irq"):
            return None
        lgpio.gpio_write(h, CS_PIN, 0)
        r = spi.xfer2([CMD_GET_IRQ, 0x00, 0x00, 0x00])
        lgpio.gpio_write(h, CS_PIN, 1)
        return (r[2] << 8) | r[3]

    # ------------------------------------------------------------------
    # 4. Hardware reset
    # ------------------------------------------------------------------
    print(f"\n{INFO} Hardware reset...")
    lgpio.gpio_write(h, RESET_PIN, 0)
    time.sleep(0.002)
    lgpio.gpio_write(h, RESET_PIN, 1)
    time.sleep(0.020)

    busy_after_reset = lgpio.gpio_read(h, BUSY_PIN)
    print(f"{INFO} BUSY after reset: {'HIGH (chip starting up)' if busy_after_reset else 'LOW (ready)'}")

    if not wait_busy("post-reset", timeout=3.0):
        print(f"{FAIL} Chip never became ready after reset")
        print("       Possible causes:")
        print("       - RESET pin wiring issue (should be GPIO18)")
        print("       - No 3.3V power to module")
        lgpio.gpiochip_close(h)
        sys.exit(1)
    print(f"{PASS} Chip ready after reset")

    # ------------------------------------------------------------------
    # 5. Get chip status
    # ------------------------------------------------------------------
    print(f"\n{INFO} Reading chip status...")
    status_resp = cmd([CMD_GET_STATUS, 0x00], "get_status")
    if status_resp is None:
        print(f"{FAIL} No response to GetStatus")
        lgpio.gpiochip_close(h)
        sys.exit(1)

    status_byte = status_resp[1]
    chip_mode   = (status_byte >> 4) & 0x07
    cmd_status  = (status_byte >> 1) & 0x07

    mode_names = {2: "STDBY_RC", 3: "STDBY_XOSC", 4: "FS", 5: "RX", 6: "TX"}
    cmd_names  = {0: "Reserved", 1: "RFU", 2: "Data available", 3: "Timeout",
                  4: "Error", 5: "Fail to execute", 6: "TX done"}

    print(f"{INFO} Status byte: 0x{status_byte:02X}")
    print(f"{INFO} Chip mode:   {chip_mode} = {mode_names.get(chip_mode, 'Unknown')}")
    print(f"{INFO} Cmd status:  {cmd_status} = {cmd_names.get(cmd_status, 'Unknown')}")

    if chip_mode in (2, 3):
        print(f"{PASS} Chip is in standby — SPI communication working")
    elif chip_mode == 0:
        print(f"{WARN} Chip mode=0 — may indicate SPI MISO wiring issue (all zeros)")
        print("       Check: MISO=GPIO9 (pin 21), MOSI=GPIO10 (pin 19), CLK=GPIO11 (pin 23)")
    else:
        print(f"{WARN} Unexpected chip mode {chip_mode}")

    # ------------------------------------------------------------------
    # 6. Initialize SX1262
    # ------------------------------------------------------------------
    print(f"\n{INFO} Initializing SX1262...")

    cmd([CMD_SET_STANDBY, 0x00], "SetStandby RC")
    print(f"{INFO} SetStandby(RC) sent")

    # --- Strategy A: DIO3-controlled TCXO at each voltage ---
    tcxo_used = None

    def hw_reset():
        lgpio.gpio_write(h, RESET_PIN, 0)
        time.sleep(0.002)
        lgpio.gpio_write(h, RESET_PIN, 1)
        time.sleep(0.020)
        wait_busy("reset")

    voltages = [(0x07,"3.3V"),(0x06,"3.0V"),(0x05,"2.7V"),(0x04,"2.4V"),
                (0x03,"2.2V"),(0x02,"1.8V"),(0x01,"1.7V"),(0x00,"1.6V")]
    for v, label in voltages:
        hw_reset()
        cmd([CMD_SET_STANDBY, 0x00])
        cmd([CMD_SET_DIO3_TCXO, v, 0x00, 0x1F, 0x40])  # ~500ms timeout
        cmd([CMD_CALIBRATE, 0x7F])
        time.sleep(0.2)
        err = cmd([0x17, 0x00, 0x00, 0x00])
        err_val = ((err[2] << 8) | err[3]) if err else 0xFFFF
        cmd([0x07, 0x00, 0x00])  # ClearDeviceErrors
        if err_val == 0:
            print(f"{PASS} Strategy A: TCXO via DIO3 at {label} — no errors")
            tcxo_used = f"DIO3 {label}"
            break
        else:
            print(f"{WARN} DIO3 {label}: errors 0x{err_val:04X}")

    # --- Strategy B: always-on TCXO, skip XOSC calibration ---
    if tcxo_used is None:
        print(f"{INFO} Trying Strategy B: always-on TCXO (skip XOSC calib)...")
        hw_reset()
        cmd([CMD_SET_STANDBY, 0x00])
        # Calibrate everything EXCEPT XOSC startup (bit 5): mask = 0x1F
        cmd([CMD_CALIBRATE, 0x1F])
        time.sleep(0.1)
        err = cmd([0x17, 0x00, 0x00, 0x00])
        err_val = ((err[2] << 8) | err[3]) if err else 0xFFFF
        cmd([0x07, 0x00, 0x00])
        if err_val == 0:
            print(f"{PASS} Strategy B: calibration OK without XOSC")
            tcxo_used = "always-on (no DIO3 ctrl)"
        else:
            print(f"{WARN} Strategy B errors: 0x{err_val:04X}")

    if tcxo_used is None:
        print(f"{WARN} All strategies failed — continuing anyway")
        tcxo_used = "none"

    print(f"{INFO} Calibration done — using: {tcxo_used}")

    cmd([CMD_SET_REGULATOR_MODE, 0x01], "SetRegulatorMode DC-DC")
    cmd([CMD_SET_PACKET_TYPE, 0x01], "SetPacketType LoRa")
    print(f"{INFO} LoRa mode set")

    # DIO2 as RF switch — required on Waveshare module for antenna switching
    cmd([CMD_SET_DIO2_RF_SWITCH, 0x01], "SetDio2AsRfSwitch")
    print(f"{INFO} DIO2 configured as RF antenna switch")

    # Image calibration for 863-870 MHz before setting frequency
    cmd([CMD_CALIBRATE_IMAGE, 0xD7, 0xDB], "CalibrateImage 868MHz")

    freq = 868_000_000
    freq_raw = int(freq / 32e6 * (1 << 25))
    cmd([CMD_SET_RF_FREQUENCY,
         (freq_raw >> 24) & 0xFF, (freq_raw >> 16) & 0xFF,
         (freq_raw >> 8) & 0xFF, freq_raw & 0xFF], "SetRfFrequency")
    print(f"{INFO} Frequency set to 868 MHz")

    # PA config + TX params for +14 dBm on SX1262 (per Waveshare/Semtech datasheet)
    cmd([CMD_SET_PA_CONFIG, 0x02, 0x02, 0x00, 0x01], "SetPaConfig +14dBm")
    cmd([CMD_SET_TX_PARAMS, 0x16, 0x05], "SetTxParams +14dBm ramp800us")
    cmd([CMD_SET_BUFFER_BASE, 0x00, 0x00], "SetBufferBase")
    cmd([CMD_SET_MOD_PARAMS, 7, 0x04, 0x01, 0x00], "SetModulationParams SF7 BW125")
    cmd([CMD_SET_PKT_PARAMS, 0x00, 0x0C, 0x00, 0x05, 0x01, 0x00], "SetPacketParams")

    # Private network sync word 0x1424
    write_reg(0x0740, 0x14)
    write_reg(0x0741, 0x24)
    print(f"{INFO} Sync word set (0x1424 private)")

    cmd([CMD_SET_DIO_IRQ,
         0x02, 0x01, 0x02, 0x01, 0x00, 0x00, 0x00, 0x00], "SetDioIrqParams")
    cmd([CMD_CLEAR_IRQ, 0xFF, 0xFF], "ClearIrq")

    # Check status after init
    status_resp2 = cmd([CMD_GET_STATUS, 0x00], "get_status post-init")
    chip_mode2 = (status_resp2[1] >> 4) & 0x07 if status_resp2 else -1
    if chip_mode2 in (2, 3):
        print(f"{PASS} Init complete — chip in standby")
    else:
        print(f"{WARN} Unexpected mode after init: {chip_mode2}")

    # ------------------------------------------------------------------
    # 7. Send a test packet
    # ------------------------------------------------------------------
    print(f"\n{INFO} Sending test packet...")

    payload = list(b"LORA_TEST_DIAG_01")
    cmd([CMD_WRITE_BUFFER, 0x00] + payload, "WriteBuffer")
    cmd([CMD_SET_PKT_PARAMS, 0x00, 0x0C, 0x00, len(payload), 0x01, 0x00], "SetPacketParams")
    cmd([CMD_CLEAR_IRQ, 0xFF, 0xFF], "ClearIrq pre-TX")

    lgpio.gpio_write(h, TXEN_PIN, 1)
    # SetTx with 2-second hardware timeout (0x000C80 = 800 * 15.625µs ≈ 12.5ms...
    # actually use 0x013880 = 80000 * 15.625µs = 1.25s hardware timeout)
    cmd([CMD_SET_TX, 0x01, 0x38, 0x80], "SetTx with 1.25s timeout")

    # Read chip mode immediately after SetTx
    time.sleep(0.005)
    st = cmd([CMD_GET_STATUS, 0x00], "GetStatus after SetTx")
    if st:
        mode_after = (st[1] >> 4) & 0x07
        mode_names = {2: "STDBY_RC", 3: "STDBY_XOSC", 4: "FS", 5: "RX", 6: "TX"}
        print(f"{INFO} Chip mode after SetTx: {mode_after} = {mode_names.get(mode_after, 'Unknown')}")
        if mode_after == 6:
            print(f"{PASS} Chip is transmitting")
        elif mode_after == 2:
            print(f"{FAIL} Chip stayed in standby — SetTx was ignored (likely TCXO/PLL issue)")
        else:
            print(f"{WARN} Unexpected mode {mode_after}")

    print(f"{INFO} Watching for TX_DONE or TIMEOUT IRQ (max 5s)...")
    tx_ok = False
    deadline = time.time() + 5.0
    while time.time() < deadline:
        irq = get_irq()
        if irq is None:
            break
        if irq & IRQ_TX_DONE:
            tx_ok = True
            break
        if irq & IRQ_TIMEOUT:
            print(f"{FAIL} TX timeout IRQ (0x{irq:04X}) — chip tried to TX but failed")
            break
        time.sleep(0.01)

    lgpio.gpio_write(h, TXEN_PIN, 0)
    cmd([CMD_CLEAR_IRQ, 0xFF, 0xFF], "ClearIrq post-TX")

    # Check device errors after TX attempt
    err2 = cmd([0x17, 0x00, 0x00, 0x00], "GetDeviceErrors post-TX")
    if err2:
        err_val2 = (err2[2] << 8) | err2[3]
        if err_val2:
            print(f"{WARN} Device errors after TX: 0x{err_val2:04X}")
            if err_val2 & 0x0020: print("       - XoscStart failed (XOSC did not start)")
            if err_val2 & 0x0010: print("       - PLL calibration failed")
            if err_val2 & 0x0001: print("       - RC64k calibration failed")
            if err_val2 & 0x0002: print("       - RC13M calibration failed")
            if err_val2 & 0x0004: print("       - PLL calibration failed")
            if err_val2 & 0x0008: print("       - ADC calibration failed")
            if err_val2 & 0x0100: print("       - PA ramp failed")

    if tx_ok:
        print(f"{PASS} TX_DONE received — packet sent successfully!")
        print(f"       TX LED should have blinked. TCXO setting that worked: {tcxo_used}")
    else:
        irq_final = get_irq()
        print(f"{FAIL} TX_DONE not received (IRQ=0x{irq_final or 0:04X})")

    # ------------------------------------------------------------------
    # 8. Summary
    # ------------------------------------------------------------------
    print("\n=== Summary ===")
    print(f"  SPI communication:  {'OK' if chip_mode != 0 else 'PROBLEM'}")
    print(f"  Chip responds:      {'OK' if chip_mode in (2,3) else 'PROBLEM'}")
    print(f"  TX test:            {'OK' if tx_ok else 'FAILED'}")
    print()

    spi.close()
    lgpio.gpiochip_close(h)


if __name__ == "__main__":
    main()
