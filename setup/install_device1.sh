#!/usr/bin/env bash
set -euo pipefail

CURRENT_USER="${SUDO_USER:-$USER}"
HOME_DIR="$(eval echo ~"$CURRENT_USER")"
VENV_PATH="${HOME_DIR}/venv_device1"
INSTALL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE_NAME="visitor-counter-device1"

echo "=== Installing Device 1 (Visitor Counter - AI Edge) ==="

echo "--- Updating system packages ---"
sudo apt-get update -y
sudo apt-get install -y \
    python3-pip \
    python3-venv \
    libopencv-dev \
    python3-opencv \
    git \
    libopenblas0

echo "--- Creating Python virtual environment at $VENV_PATH ---"
python3 -m venv "$VENV_PATH"
source "$VENV_PATH/bin/activate"

echo "--- Installing Python dependencies ---"
pip install --upgrade pip
pip install \
    "opencv-python>=4.8.0" \
    "numpy>=1.24.0" \
    "pyyaml>=6.0" \
    "scipy>=1.11.0"

# spidev and RPi.GPIO are hardware-specific
pip install spidev lgpio || echo "WARNING: spidev/lgpio install failed (may need system packages)"

echo "--- Creating required directories ---"
mkdir -p "${HOME_DIR}/data"
mkdir -p "${HOME_DIR}/logs"
mkdir -p "${HOME_DIR}/models"
mkdir -p "${HOME_DIR}/config"
chown -R "${CURRENT_USER}:${CURRENT_USER}" "${HOME_DIR}/data" "${HOME_DIR}/logs" "${HOME_DIR}/models" "${HOME_DIR}/config" 2>/dev/null || true

echo "--- Creating systemd service ---"
sudo tee /etc/systemd/system/${SERVICE_NAME}.service > /dev/null << EOF
[Unit]
Description=Visitor Counter Device 1 (AI Edge)
After=network.target
Wants=network.target

[Service]
Type=simple
User=${CURRENT_USER}
WorkingDirectory=${INSTALL_DIR}
ExecStart=${VENV_PATH}/bin/python -m device1.main
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
Environment=PYTHONPATH=${INSTALL_DIR}

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable ${SERVICE_NAME}.service

echo ""
echo "=== Installation complete ==="
echo ""
echo "NEXT STEPS:"
echo ""
echo "1. Konfigurér kamera-netværk (direkte Ethernet-forbindelse):"
echo "   sudo bash setup/configure_camera_network.sh"
echo "   Scriptet tildeler kameraet en IP og opdaterer config.yaml automatisk."
echo ""
echo "2. Install HailoRT SDK:"
echo "   - Download from https://hailo.ai/developer-zone/"
echo "   - Follow the official HailoRT installation guide for Raspberry Pi"
echo "   - Install hailo_platform Python wheel into $VENV_PATH"
echo "     Example: $VENV_PATH/bin/pip install hailo_platform-*.whl"
echo ""
echo "3. Download YOLOv8s person detection model (.hef):"
echo "   mkdir -p ~/models"
echo "   curl -L -o ~/models/yolov8s.hef \\"
echo "     https://hailo-model-zoo.s3.eu-west-2.amazonaws.com/ModelZoo/Compiled/v2.13.0/hailo8l/yolov8s.hef"
echo ""
echo "4. Edit the config file:"
echo "   nano ${INSTALL_DIR}/device1/config.yaml"
echo "   - Adjust event schedule (open/close times per day)"
echo "   - Set debug.mock_camera: false when camera is ready"
echo ""
echo "5. Configure the counting line (requires display or VNC):"
echo "   source $VENV_PATH/bin/activate"
echo "   python ${INSTALL_DIR}/tools/configure_line.py"
echo ""
echo "6. Start the service:"
echo "   sudo systemctl start ${SERVICE_NAME}"
echo "   sudo journalctl -u ${SERVICE_NAME} -f"
