#!/usr/bin/env bash
# Konfigurerer direkte Ethernet-forbindelse mellem Raspberry Pi og IP-kamera.
# RPi tildeler kameraet en IP-adresse via DHCP (ingen router eller switch nødvendig).
#
# Resultat:
#   RPi eth0:   192.168.10.1
#   Kamera:     192.168.10.100  (eller anden ledig adresse i 192.168.10.x)
#
# Brug:
#   sudo bash setup/configure_camera_network.sh
#   sudo bash setup/configure_camera_network.sh --iface eth1   # alternativt interface
#   sudo bash setup/configure_camera_network.sh --subnet 10.0.0  # alternativt subnet

set -euo pipefail

# ── Standardværdier ──────────────────────────────────────────────────────────
IFACE="eth0"
SUBNET="192.168.10"
RPI_IP="${SUBNET}.1"
DHCP_START="${SUBNET}.100"
DHCP_END="${SUBNET}.200"
CON_NAME="camera-direct"
INSTALL_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# ── Argument-parsing ─────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --iface)  IFACE="$2"; shift 2 ;;
        --subnet) SUBNET="$2"; RPI_IP="${SUBNET}.1"; DHCP_START="${SUBNET}.100"; DHCP_END="${SUBNET}.200"; shift 2 ;;
        *) echo "Ukendt argument: $1"; exit 1 ;;
    esac
done

if [[ $EUID -ne 0 ]]; then
    echo "Kør scriptet med sudo: sudo bash setup/configure_camera_network.sh"
    exit 1
fi

echo "=== Konfigurerer direkte kamera-netværk ==="
echo "    Interface:  $IFACE"
echo "    RPi IP:     $RPI_IP/24"
echo "    DHCP range: $DHCP_START – $DHCP_END"
echo ""

# ── 1. Kontrollér at NetworkManager er tilgængeligt ──────────────────────────
if ! command -v nmcli &>/dev/null; then
    echo "NetworkManager ikke fundet — installerer..."
    apt-get install -y network-manager
fi

# ── 2. Fjern evt. eksisterende forbindelse med samme navn ────────────────────
if nmcli connection show "$CON_NAME" &>/dev/null; then
    echo "Fjerner eksisterende forbindelse '$CON_NAME'..."
    nmcli connection delete "$CON_NAME"
fi

# ── 3. Opret forbindelsen med delt IP (NetworkManager håndterer DHCP) ────────
echo "--- Opretter NetworkManager-forbindelse ---"
nmcli connection add \
    type ethernet \
    ifname "$IFACE" \
    con-name "$CON_NAME" \
    ipv4.method shared \
    ipv4.addresses "${RPI_IP}/24" \
    ipv6.method disabled \
    connection.autoconnect yes \
    connection.autoconnect-priority 100

# ── 4. Konfigurér DHCP-rækkevidde via dnsmasq-override ───────────────────────
# NetworkManager's shared mode bruger dnsmasq internt — vi kan styre rækkevidden
# via en override-fil.
DNSMASQ_DIR="/etc/NetworkManager/dnsmasq-shared.d"
mkdir -p "$DNSMASQ_DIR"

cat > "${DNSMASQ_DIR}/camera-range.conf" << EOF
# DHCP-rækkevidde for direkte kameraforbindelse på $IFACE
dhcp-range=${DHCP_START},${DHCP_END},255.255.255.0,12h
EOF

# ── 5. Aktivér forbindelsen ───────────────────────────────────────────────────
echo "--- Aktiverer forbindelsen ---"
nmcli connection up "$CON_NAME" || true   # fortsæt selvom kabel ikke er sat i

# ── 6. Vent på at kameraet dukker op ─────────────────────────────────────────
echo ""
echo "--- Venter på at kameraet forbinder (max 30 sek.) ---"
echo "    Sørg for at Ethernet-kablet er sat i kamera og RPi."
echo ""

CAMERA_IP=""
for i in $(seq 1 30); do
    sleep 1
    # Kig i DHCP-leasetabel
    LEASE_FILE="/var/lib/NetworkManager/dnsmasq-${IFACE}.leases"
    if [[ -f "$LEASE_FILE" ]]; then
        CAMERA_IP=$(awk '{print $3}' "$LEASE_FILE" | head -1)
        if [[ -n "$CAMERA_IP" ]]; then
            break
        fi
    fi
    printf "."
done
echo ""

# ── 7. Resultat ───────────────────────────────────────────────────────────────
echo ""
if [[ -n "$CAMERA_IP" ]]; then
    echo "✓ Kamera fundet med IP: $CAMERA_IP"
    RTSP_GUESS="rtsp://${CAMERA_IP}:554/stream"
    echo ""
    echo "Opdatér RTSP-URL i device1/config.yaml:"
    echo "    camera:"
    echo "      rtsp_url: \"${RTSP_GUESS}\""
    echo ""
    echo "OBS: Port og sti afhænger af kameraets model."
    echo "     Typiske RTSP-stier: /stream  /live  /h264  /cam/realmonitor?channel=1&subtype=0"
    echo ""
    # Opdatér automatisk config.yaml hvis det ikke allerede peger på denne IP
    CONFIG="${INSTALL_DIR}/device1/config.yaml"
    if [[ -f "$CONFIG" ]]; then
        if grep -q "rtsp_url" "$CONFIG"; then
            # Erstat kun adressen, bevar port og sti
            CURRENT=$(grep "rtsp_url" "$CONFIG" | sed "s/.*rtsp_url: *//;s/['\"]//g")
            NEW_URL=$(echo "$CURRENT" | sed "s|rtsp://[^/]*|rtsp://${CAMERA_IP}|")
            sed -i "s|rtsp_url:.*|rtsp_url: \"${NEW_URL}\"|" "$CONFIG"
            echo "✓ Opdaterede automatisk $CONFIG"
            echo "  rtsp_url: \"${NEW_URL}\""
            echo ""
            echo "  Tjek at sti og port er korrekte for dit kamera."
        fi
    fi
else
    echo "⚠  Intet kamera fundet inden for 30 sekunder."
    echo "   Mulige årsager:"
    echo "   - Ethernet-kablet er ikke sat i"
    echo "   - Kameraet bruger en fast IP i stedet for DHCP"
    echo "   - Kameraet er endnu ikke startet op"
    echo ""
    echo "   Prøv manuelt:"
    echo "     ip neigh show dev $IFACE"
    echo "     arp-scan --interface=$IFACE --localnet"
    echo ""
    echo "   Hvis kameraet har fast IP, sat denne statisk i config.yaml:"
    echo "     camera:"
    echo "       rtsp_url: \"rtsp://<KAMERA-IP>:554/stream\""
fi

echo ""
echo "=== Netværkskonfiguration fuldført ==="
echo ""
echo "Verifikation:"
echo "  ip addr show $IFACE"
echo "  ping -c 3 ${CAMERA_IP:-${SUBNET}.100}"
echo ""
echo "Forbindelsen starter automatisk ved genstart."
echo "For at fjerne konfigurationen:"
echo "  sudo nmcli connection delete $CON_NAME"
