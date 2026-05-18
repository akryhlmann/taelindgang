# Tælindgang – Besøgende Tæller

System til automatisk tælling af besøgende ved udendørs events. En AI-drevet kamera-enhed tæller folk der går ind og ud, og sender data trådløst via LoRa til en modtager-enhed der viser et live dashboard og synkroniserer med Google Sheets.

## Systemarkitektur

```
[IP Kamera]
    │ RTSP
    ▼
┌─────────────────────────────────┐
│  Enhed 1: Raspberry Pi          │
│  + Hailo 8L AI-accelerator      │
│                                 │
│  • Person-detektion (YOLOv5)    │
│  • Linje-tæller (ind / ud)      │
│  • Lokal SQLite + CSV           │
└──────────────┬──────────────────┘
               │ LoRa 868 MHz (SX1276)
               │ hvert 5. minut
               ▼
┌─────────────────────────────────┐
│  Enhed 2: Raspberry Pi          │
│                                 │
│  • LoRa modtager                │
│  • Lokal SQLite                 │
│  • Google Sheets sync           │
│  • Dashboard  :8050             │
└─────────────────────────────────┘
```

## Funktioner

- **AI-baseret tælling** – YOLOv5 person-detektion via Hailo 8L accelerator
- **Ind/ud tælling** – konfigurerbar tællelinje med retningsdetektering
- **Trådløs dataoverførsel** – LoRa 868 MHz, rækkevidde op til ~2 km fri sigt
- **Offline-drift** – enhed 1 kræver ingen internetforbindelse under event
- **Live dashboard** – besøgstal og 15-minutters graf tilgængeligt lokalt på netværket
- **Google Sheets** – automatisk synkronisering når enhed 2 har internet
- **Skalerbar** – understøtter flere tælle-enheder til samme modtager
- **Mock-tilstand** – begge enheder kan køre og testes uden hardware

## Hardware

### Enhed 1
| Komponent | Specifikation |
|-----------|--------------|
| Raspberry Pi | 4B eller 5 |
| AI-accelerator | Hailo 8L (M.2 HAT) |
| Kamera | IP-kamera med RTSP-stream (Ethernet) |
| LoRa modul | SX1276 868 MHz (SPI) |

### Enhed 2
| Komponent | Specifikation |
|-----------|--------------|
| Raspberry Pi | 3B+, 4B eller 5 |
| LoRa modul | SX1276 868 MHz (SPI) |
| Netværk | LAN/WiFi til Google Sheets og dashboard-adgang |

### Waveshare SX1262 LoRaWAN Node Module → Raspberry Pi GPIO

Modulet er et HAT der stikkes direkte på Raspberry Pi's 40-pin GPIO-stik.
Aktivér **SPI0** på Raspberry Pi: `sudo raspi-config` → Interface Options → SPI → Enable

| SX1262 signal | RPi BCM | Fysisk pin | Funktion |
|---------------|---------|-----------|----------|
| MISO | GPIO 9 | Pin 21 | SPI0 Data ind |
| MOSI | GPIO 10 | Pin 19 | SPI0 Data ud |
| SCK | GPIO 11 | Pin 23 | SPI0 Clock |
| NSS/CS | GPIO 21 | Pin 40 | Chip Select (software-styret) |
| RESET | GPIO 18 | Pin 12 | Reset (100µs lav puls) |
| BUSY | GPIO 20 | Pin 38 | Optaget-indikator (aktiv høj) |
| DIO1 | GPIO 16 | Pin 36 | IRQ (TX done / RX done) |
| TXEN | GPIO 6 | Pin 31 | RF switch TX-enable |
| 3.3V | 3.3V | Pin 1/17 | Strøm |
| GND | GND | Pin 6/9/... | Stel |

> **BUSY-pin:** SX1262 kræver at BUSY er LAV før hver SPI-kommando — dette håndteres automatisk af driveren.

## Projektstruktur

```
taelindgang/
├── shared/
│   └── protocol.py              # LoRa besked-protokol med CRC16
├── device1/                     # Kamera + AI + tæller
│   ├── config.yaml              # Konfiguration (RTSP, tællelinje, LoRa)
│   ├── main.py                  # Hoved-loop
│   ├── camera/rtsp_capture.py   # RTSP kamera (baggrundstråd, auto-reconnect)
│   ├── ai/hailo_detector.py     # Hailo 8L person-detektion
│   ├── counter/
│   │   ├── tracker.py           # Centroid-baseret person-tracker
│   │   └── line_counter.py      # Linje-krydsnings logik
│   ├── storage/local_storage.py # SQLite + CSV
│   └── lora/transmitter.py      # SX1276 SPI driver (TX)
├── device2/                     # Modtager + dashboard
│   ├── config.yaml
│   ├── main.py
│   ├── lora/receiver.py         # SX1276 SPI driver (RX)
│   ├── storage/local_storage.py # SQLite med per-enhed historik
│   ├── sync/google_sheets.py    # Google Sheets via service account
│   └── dashboard/app.py         # Plotly Dash dashboard
├── tools/
│   └── configure_line.py        # Visuelt tællelinje-konfigurationsværktøj
├── setup/
│   ├── install_device1.sh       # Installer + systemd service (enhed 1)
│   └── install_device2.sh       # Installer + systemd service (enhed 2)
├── requirements_device1.txt
└── requirements_device2.txt
```

## Installation

### Forudsætninger (begge enheder)

```bash
git clone https://github.com/akryhlmann/taelindgang.git /home/pi/taelindgang
cd /home/pi/taelindgang
```

---

### Enhed 1 – Kamera + AI

**1. Kør installationsscriptet**

```bash
bash setup/install_device1.sh
```

Scriptet installerer Python-afhængigheder og opretter en systemd-service der starter automatisk ved boot.

**2. Installer Hailo SDK og model**

Download og installer HailoRT SDK fra [hailo.ai/developer-zone](https://hailo.ai/developer-zone/):

```bash
# Eksempel – brug den version der passer til din RPi OS
sudo dpkg -i hailort_*.deb
pip install hailo_platform-*.whl
```

Download person-detektionsmodellen fra [Hailo Model Zoo](https://github.com/hailo-ai/hailo_model_zoo):

```bash
mkdir -p /home/pi/models
# Placer yolov5m_wo_spp_60p.hef (eller tilsvarende) her:
cp yolov5m_person.hef /home/pi/models/
```

**3. Konfigurer tællelinjen**

Kør det visuelle konfigurationsværktøj – det åbner et vindue med kamera-billedet:

```bash
python tools/configure_line.py --config device1/config.yaml
```

| Tast | Funktion |
|------|----------|
| Klik | Sæt linjepunkt (2 klik = én linje) |
| `I` | Skift ind-retning (top / bottom / left / right) |
| `S` | Gem til config.yaml |
| `R` | Nulstil punkter |
| `Q` | Afslut uden at gemme |

Pilen på linjen viser hvilken retning der tæller som "ind".

**4. Rediger config.yaml**

```bash
nano device1/config.yaml
```

```yaml
device:
  id: "DEV001"          # Unikt ID – vigtigt hvis du bruger flere enheder

camera:
  rtsp_url: "rtsp://192.168.1.100:554/stream"   # Din kameras RTSP-adresse

ai:
  model_path: "/home/pi/models/yolov5m_person.hef"
  confidence_threshold: 0.5

lora:
  frequency: 868000000
  send_interval: 300    # Sekunder mellem LoRa-transmissioner (300 = 5 min)
```

**5. Start tjenesten**

```bash
sudo systemctl start visitor-counter-device1
sudo systemctl status visitor-counter-device1
journalctl -u visitor-counter-device1 -f    # Live log
```

---

### Enhed 2 – Modtager + Dashboard

**1. Kør installationsscriptet**

```bash
bash setup/install_device2.sh
```

**2. Opsæt Google Sheets**

1. Gå til [Google Cloud Console](https://console.cloud.google.com/) og opret et projekt
2. Aktivér **Google Sheets API** og **Google Drive API**
3. Opret en **Service Account** og download JSON-nøglefilen
4. Placer nøglefilen på enheden:
   ```bash
   mkdir -p /home/pi/config
   cp service_account.json /home/pi/config/
   ```
5. Opret et Google Sheet og **del det** med service accountens email-adresse (giv redaktøradgang)
6. Kopiér spreadsheet-ID'et fra URL'en: `https://docs.google.com/spreadsheets/d/`**`DETTE_ER_ID'ET`**`/edit`

**3. Rediger config.yaml**

```bash
nano device2/config.yaml
```

```yaml
device:
  event_name: "Mit Event 2025"   # Vises i dashboard-titlen

google_sheets:
  credentials_file: "/home/pi/config/service_account.json"
  spreadsheet_id: "INDSÆT_DIT_SPREADSHEET_ID_HER"
  worksheet_name: "Visitor Counts"

dashboard:
  port: 8050
```

**4. Start tjenesten**

```bash
sudo systemctl start visitor-counter-device2
```

Dashboard er tilgængeligt på: `http://<enhed2-ip>:8050`

---

## Dashboard

Dashboardet viser i realtid:

- **Nuværende besøgende** – beregnet som (ind − ud)
- **Ind i dag / Ud i dag** – totaler siden midnat
- **Graf** – tilstedeværende + indkomne per 15-minutter interval (seneste 24 timer)
- **Enhedstabel** – opdeling per tælle-enhed

Opdateres automatisk hvert 30. sekund.

## Udvid med flere tælle-enheder

Systemet understøtter flere Enhed 1-instanser til samme modtager:

1. Konfigurér en ny Enhed 1 med unikt `device.id` (fx `"DEV002"`)
2. Sørg for at alle enheder bruger samme LoRa-frekvens og spreading factor
3. Enhed 2 identificerer automatisk nye enheder via `device_id` i pakkerne
4. Dashboardet viser automatisk alle registrerede enheder i tabellen

## Udvikling uden hardware (mock-tilstand)

Alle moduler kører automatisk i mock-tilstand når hardware ikke er tilgængeligt:

- **Hailo-detector** – genererer tilfældige person-detektioner
- **LoRa TX/RX** – logger til konsollen i stedet for SPI
- **LoRa modtager** – sender mock-pakker hvert 30. sekund

```bash
# Kør enhed 1 lokalt (ingen Raspberry Pi nødvendig)
pip install -r requirements_device1.txt
python -m device1.main

# Kør enhed 2 lokalt
pip install -r requirements_device2.txt
python -m device2.main
```

## LoRa-protokol

Pakkeformat (20 bytes total):

```
[ Version (1B) | Device ID (8B) | Msg Type (1B) | Count In (2B) | Count Out (2B) | Timestamp (4B) | CRC16 (2B) ]
```

- Frekvens: 868 MHz (EU)
- Spreading Factor: 7
- Båndbredde: 125 kHz
- Sync word: 0x12 (privat netværk)
- Max payload: 200 bytes

## Fejlfinding

**Kamera forbinder ikke**
```bash
# Test RTSP-stream manuelt
ffplay rtsp://192.168.1.100:554/stream
```

**LoRa sender/modtager ikke**
```bash
# Verificér SPI er aktiveret
ls /dev/spidev*   # Skal vise /dev/spidev0.0

# Tjek GPIO-forbindelser med multimeter
```

**Google Sheets opdateres ikke**
- Verificér at service accountens email har redaktøradgang til sheetet
- Tjek at `spreadsheet_id` er korrekt i config.yaml
- Se log: `journalctl -u visitor-counter-device2 -f`

**Hailo-modellen kan ikke loades**
- Verificér at HailoRT-versionen matcher modellens version
- Prøv med `hailortcli scan` for at se om enheden registreres

## Licens

MIT
