# Tælindgang – Besøgende Tæller

System til automatisk tælling af besøgende ved udendørs events. En AI-drevet kamera-enhed tæller folk der går ind og ud, og sender data trådløst via LoRa til en modtager-enhed der viser et live dashboard og synkroniserer med Google Sheets.

## Systemarkitektur

```
[IP Kamera]
    │ RTSP
    ▼
┌─────────────────────────────────┐
│  Enhed 1: Raspberry Pi 5        │
│  + Hailo 8L AI-accelerator      │
│                                 │
│  • Person-detektion (YOLOv8s)   │
│  • Linje-tæller (ind / ud)      │
│  • Setup-tilstand (WiFi hotspot)│
│  • Lokal SQLite + CSV           │
└──────────────┬──────────────────┘
               │ LoRa 868 MHz (SX1272)
               │ hvert 5. minut
               ▼
┌─────────────────────────────────┐
│  Enhed 2: Raspberry Pi 5        │
│                                 │
│  • LoRa modtager                │
│  • Lokal SQLite                 │
│  • Google Sheets sync           │
│  • Dashboard  :8050             │
└─────────────────────────────────┘
```

## Funktioner

- **AI-baseret tælling** – YOLOv8s person-detektion via Hailo 8L accelerator (GStreamer pipeline)
- **Ind/ud tælling** – konfigurerbar tællelinje med retningsdetektering
- **Setup-tilstand** – hold en fysisk knap i 3 sek. for at aktivere et WiFi-hotspot med et webbaseret konfigurationsinterface; LED indikerer aktiv portal
- **Trådløs dataoverførsel** – LoRa 868 MHz, rækkevidde op til ~2 km fri sigt
- **Offline-drift** – enhed 1 kræver ingen internetforbindelse under event
- **Live dashboard** – besøgstal og 15-minutters graf tilgængeligt lokalt på netværket
- **Google Sheets** – automatisk synkronisering når enhed 2 har internet
- **Skalerbar** – understøtter flere tælle-enheder til samme modtager
- **Debug-tilstand** – kør og test hele systemet uden kamera eller Hailo-hardware

## Hardware

### Enhed 1
| Komponent | Specifikation |
|-----------|--------------|
| Raspberry Pi | 5 |
| AI-accelerator | Hailo 8L (M.2 HAT) |
| Kamera | IP-kamera med RTSP-stream (Ethernet) |
| LoRa modul | SX1272 868 MHz bare module |
| Setup-knap | Momentary push-button (GPIO26 → GND) |
| Setup-LED | LED + 330 Ω modstand (GPIO24 → LED → modstand → GND) |

### Enhed 2
| Komponent | Specifikation |
|-----------|--------------|
| Raspberry Pi | 5 |
| LoRa modul | SX1272 868 MHz bare module |
| Netværk | LAN/WiFi til Google Sheets og dashboard-adgang |

### SX1272 LoRa modul → Raspberry Pi GPIO

Modulet tilsluttes via SPI0. Aktivér **SPI0** inden brug:
`sudo raspi-config` → Interface Options → SPI → Enable

| SX1272 signal | RPi BCM | Fysisk pin | Funktion |
|---------------|---------|-----------|----------|
| VCC | 3.3V | Pin 1 | Strøm |
| GND | GND | Pin 6 | Stel |
| MISO | GPIO 9 | Pin 21 | SPI0 Data ind |
| MOSI | GPIO 10 | Pin 19 | SPI0 Data ud |
| SCK | GPIO 11 | Pin 23 | SPI0 Clock |
| NSS | GPIO 8 (CE0) | Pin 24 | Chip Select (hardware-styret) |
| RST | GPIO 17 | Pin 11 | Reset |
| DIO0 | GPIO 4 | Pin 7 | TX/RX done IRQ (valgfri — polling bruges) |

> **Polling-tilstand:** Driveren aflæser IRQ-flaget direkte via SPI i stedet for at bruge DIO0-interrupt. DIO0 behøver derfor ikke forbindes, men kan bruges til fremtidig interrupt-baseret RX.

> **Spændingsniveau:** SX1272 arbejder på 3.3V. Brug **ikke** 5V — det beskadiger modulet.

### Setup-knap og LED (Enhed 1)

```
GPIO26 ──── [Knap] ──── GND        (aktiv-lav med intern pull-up)
GPIO24 ──── [LED annode] ──── [330 Ω] ──── GND
```

| Signal | BCM pin | Fysisk pin |
|--------|---------|-----------|
| Knap | GPIO 26 | Pin 37 |
| LED | GPIO 24 | Pin 18 |

> Brug `gpio_chip: 4` i `config.yaml` for Raspberry Pi 5. Brug `gpio_chip: 0` for RPi 4 og ældre.

## Projektstruktur

```
taelindgang/
├── shared/
│   └── protocol.py              # LoRa besked-protokol med CRC16
├── device1/                     # Kamera + AI + tæller
│   ├── config.yaml              # Konfiguration (RTSP, tællelinje, LoRa, setup-tilstand)
│   ├── main.py                  # Hoved-loop
│   ├── camera/rtsp_capture.py   # RTSPCapture + MockCamera (debug-tilstand)
│   ├── ai/hailo_detector.py     # HailoDetector (GStreamer pipeline) + MockDetector
│   ├── counter/
│   │   ├── tracker.py           # Centroid-baseret person-tracker
│   │   └── line_counter.py      # Linje-krydsnings logik
│   ├── storage/local_storage.py # SQLite + CSV
│   ├── lora/transmitter.py      # SX1272/SX1276 SPI driver (TX)
│   └── setup_mode/              # WiFi hotspot + web-konfigurationsinterface
│       ├── shared_state.py      # Trådsikker bro mellem hoved-loop og web app
│       ├── hotspot.py           # nmcli hotspot + iptables + dnsmasq (captive portal)
│       ├── web_app.py           # Flask app: MJPEG stream + linje-konfigurator
│       └── coordinator.py       # GPIO knap-lytter + LED + livscyklus-styring
├── device2/                     # Modtager + dashboard
│   ├── config.yaml
│   ├── main.py
│   ├── lora/receiver.py         # SX1272/SX1276 SPI driver (RX)
│   ├── storage/local_storage.py # SQLite med per-enhed historik
│   ├── sync/google_sheets.py    # Google Sheets via service account
│   └── dashboard/app.py         # Plotly Dash dashboard
├── tools/
│   ├── configure_line.py        # Visuelt tællelinje-konfigurationsværktøj (desktop)
│   ├── hailo_test.py            # Live kamera + Hailo detektion med bounding boxes
│   ├── hailo_diag.py            # Dyb GStreamer/Hailo pipeline-diagnostik
│   └── lora_diagnostic.py       # SX1272 hardware-diagnostik
├── setup/
│   ├── install_device1.sh       # Installer + systemd service (enhed 1)
│   └── install_device2.sh       # Installer + systemd service (enhed 2)
├── requirements_device1.txt
└── requirements_device2.txt
```

## Installation

### Forudsætninger (begge enheder)

```bash
# Aktiver SPI
sudo raspi-config   # Interface Options → SPI → Enable

# Installer lgpio (kræves på Raspberry Pi 5)
sudo apt install -y python3-lgpio

# Klon til din hjemmemappe
git clone https://github.com/akryhlmann/taelindgang.git ~/taelindgang
cd ~/taelindgang
git checkout claude/visitor-counter-system-zvhjv
```

---

### Enhed 1 – Kamera + AI

**1. Kør installationsscriptet**

```bash
bash setup/install_device1.sh
```

**2. Konfigurér direkte kamera-netværk (Ethernet uden router)**

Hvis IP-kameraet er forbundet direkte til RPi'ens Ethernet-port uden router eller switch:

```bash
sudo bash setup/configure_camera_network.sh
```

Scriptet konfigurerer `eth0` med statisk IP `192.168.10.1` og starter en DHCP-server der automatisk tildeler kameraet en adresse i `192.168.10.100–200`. Hvis kameraet registreres, opdateres `device1/config.yaml` automatisk med den tildelte IP.

```
RPi eth0:  192.168.10.1
Kamera:    192.168.10.100  (eller anden ledig adresse)
```

> **TP-Link Tapo / Vigi RTSP-URL:**
> ```
> rtsp://brugernavn:adgangskode@192.168.10.100:554/stream1
> ```
> `brugernavn` og `adgangskode` er **Camera Account** — ikke din Tapo-app-konto.
> Sættes i Tapo-appen: **Kamera → Indstillinger → Avanceret → Camera Account**
>
> - `/stream1` = HD hovedstream (anbefalet til detektion)
> - `/stream2` = SD substream (lavere CPU-belastning)

**3. Installer Hailo SDK og GStreamer-elementer**

```bash
# Installer hailo-all — inkluderer HailoRT, tappas GStreamer-elementer og YOLOv8s-modellen
sudo apt install hailo-all

# Verificér at modellen er på plads
ls /usr/share/hailo-models/yolov8s_h8l.hef
```

`hailo-all` placerer den kompilerede model direkte på enheden — ingen separat download nødvendig. Standardstien i `config.yaml` er `/usr/share/hailo-models/yolov8s_h8l.hef`.

> **Pakke-kilde:** Hailo leverer en APT-kilde der installeres som del af HailoRT-opsætningen.
> Se [Hailo Developer Zone](https://hailo.ai/developer-zone/) for den seneste installationsvejledning.

**4. Installer Python-afhængigheder**

```bash
source ~/venv_device1/bin/activate
pip install -r requirements_device1.txt
```

**5. Konfigurér tællelinjen**

Der er to måder at konfigurere tællelinjen på:

**Mulighed A — Desktop-værktøj** (kræver skærm tilsluttet RPi):
```bash
source ~/venv_device1/bin/activate
python tools/configure_line.py --config device1/config.yaml
```

| Tast | Funktion |
|------|----------|
| Klik | Sæt linjepunkt (2 klik = én linje) |
| `I` | Skift ind-retning (top / bottom / left / right) |
| `S` | Gem til config.yaml |
| `R` | Nulstil punkter |
| `Q` | Afslut uden at gemme |

**Mulighed B — Setup-tilstand via WiFi-hotspot** (se afsnit nedenfor):
Hold knappen i 3 sek., forbind til `BornelandSetup`-netværket, og brug det webbaserede interface til at klikke tællelinjen ind direkte på kamera-billedet.

**6. Rediger config.yaml**

```bash
nano device1/config.yaml
```

De vigtigste værdier at tilpasse:

```yaml
device:
  id: "Hovedindgang"    # Unikt ID — vigtigt hvis du bruger flere enheder

camera:
  rtsp_url: "rtsp://192.168.10.13/stream1"
  username: "brugernavn"
  password: "adgangskode"

ai:
  model_path: "/usr/share/hailo-models/yolov8s_h8l.hef"
  confidence_threshold: 0.5

lora:
  send_interval: 300    # Sekunder mellem LoRa-transmissioner (300 = 5 min)

setup_mode:
  gpio_button_pin: 26   # BCM-pin for setup-knap
  gpio_led_pin: 24      # BCM-pin for status-LED (null for at deaktivere)
  gpio_chip: 4          # 4 for RPi5, 0 for RPi4
  timeout_seconds: 1200 # Auto-luk efter 20 min inaktivitet
  hotspot_ssid: "BornelandSetup"
  hotspot_password: "borneland1"

debug:
  mock_camera: false    # Sæt til true for at teste uden kamera og Hailo
```

**7. Start tjenesten**

```bash
sudo systemctl start visitor-counter-device1
sudo systemctl status visitor-counter-device1
sudo journalctl -u visitor-counter-device1 -f    # Live log
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
   mkdir -p ~/config
   cp service_account.json ~/config/
   ```
5. Opret et Google Sheet og **del det** med service accountens email-adresse (giv redaktøradgang)
6. Kopiér spreadsheet-ID'et fra URL'en: `https://docs.google.com/spreadsheets/d/`**`DETTE_ER_ID'ET`**`/edit`

**3. Rediger config.yaml**

```bash
nano device2/config.yaml
```

```yaml
device:
  id: "RECEIVER_01"

google_sheets:
  credentials_file: "~/config/service_account.json"
  spreadsheet_id: "INDSÆT_DIT_SPREADSHEET_ID_HER"
  worksheet_name: "Visitor Counts"

dashboard:
  port: 8050
```

**4. Start tjenesten**

```bash
sudo systemctl start visitor-counter-device2
sudo journalctl -u visitor-counter-device2 -f
```

Dashboard er tilgængeligt på: `http://<enhed2-ip>:8050`

---

## Setup-tilstand

Setup-tilstand giver dig mulighed for at konfigurere tællelinjen og overvåge detektionen direkte fra en telefon eller laptop — uden at kameraet eller RPi'en behøver at være forbundet til et netværk.

### Aktivering

1. **Hold setup-knappen (GPIO26) nede i 3 sekunder**
2. LED'en (GPIO24) blinker 3 gange hurtigt og lyser derefter konstant
3. Et WiFi-netværk ved navn **BornelandSetup** (adgangskode: `borneland1`) vises på din enhed
4. Forbind til netværket — et captive portal-vindue åbner automatisk

### Web-interface

Åbn `http://10.42.0.1` hvis captive portal-vinduet ikke åbner af sig selv.

| Funktion | Beskrivelse |
|----------|-------------|
| **Live stream** | MJPEG-stream fra kameraet med bounding boxes og tællelinje |
| **Linje-konfigurator** | Klik to punkter på billedet for at sætte tællelinjen; vælg ind-retning |
| **Gem linje** | Aktiverer ændringen øjeblikkeligt og gemmer den i `config.yaml` |
| **Status-panel** | Kamera OK, detektioner/sek, sidst sendt LoRa, nedtælling til auto-luk |

### LED-adfærd

| Signal | Betydning |
|--------|-----------|
| 3 korte blink | Portal starter |
| Konstant lys | Portal er aktiv — hotspot kører |
| 2 langsomme blink | Portal lukker ned |
| Slukket | Normal drift |

### Deaktivering

- **Hold knappen i 3 sekunder** igen — LED slukker og hotspot lukkes ned
- **Automatisk** efter 20 minutters inaktivitet på web-interfacet

### WiFi-note

Når setup-tilstand aktiveres, afbryder RPi'en sin eventuelle WiFi-klientforbindelse (et enkelt WiFi-radio kan ikke være klient og adgangspunkt samtidig). NetworkManager genopretter automatisk forbindelsen når setup-tilstand lukkes ned. LoRa og Ethernet-forbindelsen til kameraet påvirkes ikke.

---

## Debug-tilstand (uden kamera og Hailo)

Når kameraet ikke er tilgængeligt (eller du vil teste LoRa og tælle-logikken isoleret), kan enhed 1 køre med syntetisk kamera-input:

```bash
nano device1/config.yaml
# Sæt:  debug.mock_camera: true
sudo systemctl restart visitor-counter-device1
```

I debug-tilstand genererer `MockCamera` frames med orange person-figurer der bevæger sig hen over tællelinjen (skiftevis ind og ud, én person hvert 15. sekund). `MockDetector` finder figurerne via farvedetektion – resten af pipeline (tracker, linje-tæller, LoRa TX) kører uændret.

For at skifte tilbage til rigtig RTSP/Hailo: sæt `mock_camera: false` og genstart tjenesten.

---

## Hailo-diagnostik

To scripts til at verificere at GStreamer/Hailo-pipeline virker korrekt:

### hailo_test.py — live visning med bounding boxes

```bash
source ~/venv_device1/bin/activate

# Med RTSP-kamera (fra config.yaml)
python tools/hailo_test.py

# Med en videofil (løkke automatisk)
python tools/hailo_test.py --video tools/test_video.mp4
```

Viser kamera-billedet i et vindue med bounding boxes og FPS. Tryk `Q` eller `ESC` for at afslutte.

### hailo_diag.py — dyb pipeline-diagnostik

```bash
source ~/venv_device1/bin/activate

# Med RTSP-kamera
python tools/hailo_diag.py

# Med en videofil
python tools/hailo_diag.py --video tools/test_video.mp4 --frames 20
```

Udskriver detaljeret information om alle objekter Hailo returnerer: type, class_id, label, confidence og bounding box koordinater. Gemmer én ramme som `/tmp/hailo_diag_frame.jpg` til visuel verifikation.

---

## Dashboard

Dashboardet viser i realtid:

- **Nuværende besøgende** – beregnet som (ind − ud)
- **Ind i dag / Ud i dag** – totaler siden midnat
- **Graf** – tilstedeværende + indkomne per 15-minutters interval (seneste 24 timer)
- **Enhedstabel** – opdeling per tælle-enhed

Opdateres automatisk hvert 30. sekund.

## Udvid med flere tælle-enheder

Systemet understøtter flere Enhed 1-instanser til samme modtager:

1. Konfigurér en ny Enhed 1 med unikt `device.id` (fx `"Sideindgang"`)
2. Sørg for at alle enheder bruger samme LoRa-frekvens og spreading factor
3. Enhed 2 identificerer automatisk nye enheder via `device_id` i pakkerne
4. Dashboardet viser automatisk alle registrerede enheder i tabellen

## LoRa-protokol

Pakkeformat (20 bytes total):

```
[ Version (1B) | Device ID (8B) | Msg Type (1B) | Count In (2B) | Count Out (2B) | Timestamp (4B) | CRC16 (2B) ]
```

| Parameter | Værdi |
|-----------|-------|
| Frekvens | 868 MHz (EU) |
| Spreading Factor | 7 |
| Båndbredde | 125 kHz |
| Sync word | 0x12 (privat netværk) |
| Chip | SX1272 |
| Max payload | 255 bytes |

## LoRa diagnostik

Brug det medfølgende diagnostikscript til at verificere LoRa-hardware, inden du starter systemet:

```bash
sudo python3 tools/lora_diagnostic.py
```

Scriptet tester SPI-kommunikation, chip-initialisering og sender en testpakke. Output viser `[PASS]`/`[FAIL]` for hvert trin og rapporterer chip-fejlkoder hvis noget går galt.

> **SX1272 vs SX1276:** Driveren er skrevet til SX1276 (version register `0x12`). SX1272 returnerer `0x22` fra version-registeret og bruger forskellig båndbredde-encoding. Hvis du ser fejlen `SX1276 version check failed: got 0x22`, er driveren nødt til at opdateres til SX1272-registerkortet. Kontakt projektet hvis dette er tilfældet.

## Fejlfinding

**Kamera forbinder ikke**
```bash
# Test RTSP-stream manuelt
ffplay rtsp://brugernavn:adgangskode@192.168.10.100:554/stream1

# Alternativt: brug debug-tilstand mens kamera-problemet løses
# Sæt debug.mock_camera: true i device1/config.yaml
```

**Hailo finder ingen personer**
```bash
# Kør diagnostik — udskriver alle objekter pipeline returnerer
source ~/venv_device1/bin/activate
python tools/hailo_diag.py --video tools/test_video.mp4

# Verificér at GStreamer-elementerne er installeret
gst-inspect-1.0 hailonet
gst-inspect-1.0 hailofilter

# Verificér at post-processing biblioteket findes
ls /usr/lib/aarch64-linux-gnu/hailo/tappas/post_processes/libyolo_hailortpp_post.so
```

**Setup-knap reagerer ikke**
```bash
# Verificér at lgpio er tilgængeligt
python3 -c "import lgpio; print('OK')"

# Tjek GPIO-chip nummeret (RPi5 bruger typisk chip 4)
ls /dev/gpiochip*

# Se log for fejlmeddelelser fra koordinatoren
sudo journalctl -u visitor-counter-device1 -f | grep setup
```

**Hotspot vises ikke / captive portal åbner ikke**
```bash
# Verificér at NetworkManager er installeret og kører
systemctl status NetworkManager

# Verificér at iptables er tilgængeligt
which iptables

# Tjek at wlan0 eksisterer
ip link show wlan0
```

**LoRa TX sender ikke**
```bash
# Kør diagnostikscript
sudo python3 tools/lora_diagnostic.py

# Verificér SPI er aktiveret
ls /dev/spidev*   # Skal vise /dev/spidev0.0

# Verificér kabling: NSS=GPIO8 (Pin 24), RST=GPIO17 (Pin 11)
# Verificér 3.3V strøm til modulet (ikke 5V)

# Verificér lgpio er installeret
python3 -c "import lgpio; print('OK')"
# Hvis fejl: sudo apt install -y python3-lgpio
```

**Google Sheets opdateres ikke**
- Verificér at service accountens email har redaktøradgang til sheetet
- Tjek at `spreadsheet_id` er korrekt i `device2/config.yaml`
- Se log: `sudo journalctl -u visitor-counter-device2 -f`

**Hent seneste ændringer fra GitHub**
```bash
cd ~/taelindgang
git pull origin claude/visitor-counter-system-zvhjv
```

## Licens

MIT
