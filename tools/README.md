# Diagnostik-værktøjer

Standalone test-scripts til at debugge og verificere hardware.

---

## SX1276 LoRa-modul (868 MHz)

Bruges til at teste LoRa-kommunikation uafhængigt af Waveshare SX1262 HAT'en.

### Tilslutning til Raspberry Pi

Fjern Waveshare HAT'en inden tilslutning for at undgå MISO-konflikter.

| SX1276 pin | RPi GPIO    | RPi pin | Ledningsfarve |
|------------|-------------|---------|---------------|
| VCC        | 3.3V        | Pin 1   | Rød           |
| GND        | GND         | Pin 6   | Sort          |
| MISO       | GPIO 9      | Pin 21  | Blå           |
| MOSI       | GPIO 10     | Pin 19  | Violet        |
| RST        | GPIO 17     | Pin 11  | Brun          |
| NSS        | GPIO 8 (CE0)| Pin 24  | Hvid          |
| SCK        | GPIO 11     | Pin 23  | Grå           |
| DIO0       | GPIO 4      | Pin 7   | Orange        |

> **Bemærk:** NSS er forbundet til CE0 (GPIO 8) — spidev styrer chip-select automatisk,
> ingen software-CS nødvendig.

### Scripts

| Script               | Beskrivelse                                      |
|----------------------|--------------------------------------------------|
| `sx1276_tx_loop.py`  | Sender `SX1276_TEST_XXXX` pakker hvert 5. sekund |
| `sx1276_rx_test.py`  | Lytter efter pakker i kontinuerlig RX-tilstand   |

### Kørsel

```bash
# Stop systemd-service hvis den kører
sudo systemctl stop visitor-counter-device1   # eller device2

# Aktivér venv
source ~/venv_device1/bin/activate            # eller venv_device2

# Device 1 — sender:
python tools/sx1276_tx_loop.py

# Device 2 — modtager:
python tools/sx1276_rx_test.py
```

Det første output bekræfter om kablingen er korrekt:
```
[PASS] Chip version: 0x12 (SX1276 OK)    ← kabling OK
[FAIL] Expected version 0x12, got 0xFF   ← tjek ledningerne
```

### Hvad testen fortæller

| Resultat | Konklusion |
|----------|------------|
| SX1276 ↔ SX1276 virker | SX1262-modulerne er fysisk defekte |
| SX1276 TX → SX1262 RX virker | SX1262 kan modtage, men ikke sende |
| SX1262 TX → SX1276 RX virker | SX1262 kan sende, men ikke modtage |
| Intet virker | Fundamentalt SPI/software-problem |

---

## SX1262 Waveshare HAT (direkte SPI-tests)

| Script                     | Beskrivelse                                      |
|----------------------------|--------------------------------------------------|
| `lora_tx_loop.py`          | TX-loop med korrekt CS-styring og errata-fixes   |
| `lora_rx_test.py`          | RX-test med chip-mode heartbeat hvert 5. sek     |
| `lora_diagnostic.py`       | Samlet diagnostik (hardware-check + TX-test)     |
| `lora_waveshare_like_tx.py`| TX med Waveshare-identisk init-sekvens           |
| `lora_waveshare_like_rx.py`| RX med Waveshare-identisk init-sekvens           |
