#!/usr/bin/env bash
# Sætter Chromium op i kiosktilstand på den lokale skærm.
# Chromium åbner automatisk http://localhost:8050 (dashboard) ved login.
#
# Brug:
#   bash setup/setup_kiosk.sh
#   bash setup/setup_kiosk.sh --url http://localhost:8050  # alternativ URL
#   bash setup/setup_kiosk.sh --display :0               # alternativt display

set -euo pipefail

CURRENT_USER="${SUDO_USER:-$USER}"
HOME_DIR="$(eval echo ~"$CURRENT_USER")"
DASHBOARD_URL="http://localhost:8050"
DISPLAY_ENV=":0"

# ── Argument-parsing ──────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --url)     DASHBOARD_URL="$2"; shift 2 ;;
        --display) DISPLAY_ENV="$2";   shift 2 ;;
        *) echo "Ukendt argument: $1"; exit 1 ;;
    esac
done

echo "=== Kiosk-opsætning til dashboard ==="
echo "    Bruger:  $CURRENT_USER"
echo "    URL:     $DASHBOARD_URL"
echo "    Display: $DISPLAY_ENV"
echo ""

# ── 1. Installér Chromium hvis ikke fundet ────────────────────────────────────
if ! command -v chromium-browser &>/dev/null && ! command -v chromium &>/dev/null; then
    echo "--- Installerer chromium-browser ---"
    sudo apt-get update -y
    sudo apt-get install -y chromium-browser
fi

CHROMIUM_BIN="chromium-browser"
if ! command -v chromium-browser &>/dev/null; then
    CHROMIUM_BIN="chromium"
fi

echo "--- Chromium fundet: $(command -v $CHROMIUM_BIN) ---"

# ── 2. Deaktivér skærm-blanking og screensaver ────────────────────────────────
echo "--- Konfigurerer skærm-blanking til aldrig ---"
mkdir -p "${HOME_DIR}/.config/autostart"

# xset-kommandoer via autostart
cat > "${HOME_DIR}/.config/autostart/disable-screensaver.desktop" << EOF
[Desktop Entry]
Type=Application
Name=Deaktivér screensaver
Exec=bash -c "xset s off; xset s noblank; xset -dpms"
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
EOF

# ── 3. Opret autostart-fil for Chromium ──────────────────────────────────────
echo "--- Opretter Chromium autostart-fil ---"
cat > "${HOME_DIR}/.config/autostart/dashboard-kiosk.desktop" << EOF
[Desktop Entry]
Type=Application
Name=Dashboard Kiosk
Comment=Åbner visitor-counter dashboard i kiosktilstand
Exec=bash -c "sleep 10 && DISPLAY=${DISPLAY_ENV} ${CHROMIUM_BIN} --kiosk --noerrdialogs --disable-infobars --no-first-run --disable-session-crashed-bubble --disable-restore-session-state --check-for-update-interval=31536000 ${DASHBOARD_URL}"
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
EOF

chown "${CURRENT_USER}:${CURRENT_USER}" \
    "${HOME_DIR}/.config/autostart/dashboard-kiosk.desktop" \
    "${HOME_DIR}/.config/autostart/disable-screensaver.desktop"

echo "--- Autostart-filer oprettet ---"

# ── 4. Opret systemd-brugerservice som alternativ til autostart ───────────────
# Bruges hvis der ikke er et desktop-miljø (bare Wayfire/Openbox/ingen WM)
echo "--- Opretter systemd-brugerservice til Chromium ---"

mkdir -p "${HOME_DIR}/.config/systemd/user"
cat > "${HOME_DIR}/.config/systemd/user/dashboard-kiosk.service" << EOF
[Unit]
Description=Dashboard Kiosk (Chromium)
After=graphical-session.target visitor-counter-device2.service
Wants=graphical-session.target

[Service]
Type=simple
ExecStartPre=/bin/sleep 15
ExecStart=${CHROMIUM_BIN} --kiosk --noerrdialogs --disable-infobars --no-first-run --disable-session-crashed-bubble --disable-restore-session-state --check-for-update-interval=31536000 ${DASHBOARD_URL}
Restart=on-failure
RestartSec=10
Environment=DISPLAY=${DISPLAY_ENV}
Environment=XAUTHORITY=${HOME_DIR}/.Xauthority

[Install]
WantedBy=graphical-session.target
EOF

chown "${CURRENT_USER}:${CURRENT_USER}" \
    "${HOME_DIR}/.config/systemd/user/dashboard-kiosk.service"

# Aktivér brugerservice hvis systemd --user er tilgængeligt
if sudo -u "${CURRENT_USER}" systemctl --user daemon-reload 2>/dev/null; then
    sudo -u "${CURRENT_USER}" systemctl --user enable dashboard-kiosk.service || true
    echo "--- Brugerservice aktiveret ---"
else
    echo "--- systemd --user ikke tilgængeligt (autostart .desktop bruges i stedet) ---"
fi

# ── 5. Aktivér auto-login hvis muligt (så kiosk starter uden login-prompt) ────
echo ""
echo "--- Tjekker auto-login konfiguration ---"

# Raspberry Pi OS bruger lightdm
LIGHTDM_CONF="/etc/lightdm/lightdm.conf"
if [[ -f "$LIGHTDM_CONF" ]]; then
    # Sæt autologin hvis det ikke allerede er sat
    if ! grep -q "autologin-user=${CURRENT_USER}" "$LIGHTDM_CONF" 2>/dev/null; then
        echo ""
        echo "BEMÆRK: Auto-login er ikke konfigureret. For at dashboardet starter automatisk ved opstart"
        echo "  uden login-prompt, kør:"
        echo ""
        echo "  sudo raspi-config"
        echo "  → System Options → Boot / Auto Login → Desktop Autologin"
        echo ""
        echo "  Eller manuelt:"
        echo "  sudo sed -i '/^#autologin-user=/a autologin-user=${CURRENT_USER}' $LIGHTDM_CONF"
        echo "  sudo sed -i 's/^#autologin-user-timeout=.*/autologin-user-timeout=0/' $LIGHTDM_CONF"
    else
        echo "--- Auto-login er allerede konfigureret til ${CURRENT_USER} ---"
    fi
fi

echo ""
echo "=== Kiosk-opsætning fuldført ==="
echo ""
echo "Hvad der sker ved næste login/genstart:"
echo "  - Skærm-blanking og screensaver deaktiveret"
echo "  - Chromium åbner automatisk ${DASHBOARD_URL} i kiosktilstand"
echo "  - 10-15 sek. forsinkelse giver dashboardet tid til at starte"
echo ""
echo "Test nu uden at genstarte:"
echo "  DISPLAY=${DISPLAY_ENV} ${CHROMIUM_BIN} --kiosk ${DASHBOARD_URL} &"
echo ""
echo "Nyttige kommandoer:"
echo "  # Afslut kiosktilstand (Alt+F4 eller):"
echo "  pkill chromium"
echo ""
echo "  # Tjek dashboard kører:"
echo "  curl -s http://localhost:8050 | head -3"
echo ""
echo "  # Genstart dashboard-service:"
echo "  sudo systemctl restart visitor-counter-device2"
