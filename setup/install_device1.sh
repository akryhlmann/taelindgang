#!/usr/bin/env bash
set -euo pipefail

VENV_PATH="/home/pi/venv_device1"
INSTALL_DIR="/home/pi/visitor-counter"
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
    libatlas-base-dev \
    libhdf5-dev

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
pip install spidev RPi.GPIO || echo "WARNING: spidev/RPi.GPIO install failed (may need system packages)"

echo "--- Creating required directories ---"
mkdir -p /home/pi/data
mkdir -p /home/pi/logs
mkdir -p /home/pi/models
mkdir -p /home/pi/config
chown -R pi:pi /home/pi/data /home/pi/logs /home/pi/models /home/pi/config 2>/dev/null || true

echo "--- Creating systemd service ---"
sudo tee /etc/systemd/system/${SERVICE_NAME}.service > /dev/null << EOF
[Unit]
Description=Visitor Counter Device 1 (AI Edge)
After=network.target
Wants=network.target

[Service]
Type=simple
User=pi
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
echo "1. Install HailoRT SDK:"
echo "   - Download from https://hailo.ai/developer-zone/"
echo "   - Follow the official HailoRT installation guide for Raspberry Pi"
echo "   - Install hailo_platform Python wheel into $VENV_PATH"
echo "     Example: $VENV_PATH/bin/pip install hailo_platform-*.whl"
echo ""
echo "2. Download YOLOv5m person detection model (.hef):"
echo "   - Get it from the Hailo Model Zoo or convert your own"
echo "   - Place at: /home/pi/models/yolov5m_person.hef"
echo "   - Hailo Model Zoo: https://github.com/hailo-ai/hailo_model_zoo"
echo ""
echo "3. Edit the config file:"
echo "   nano ${INSTALL_DIR}/device1/config.yaml"
echo "   - Set camera.rtsp_url to your IP camera address"
echo "   - Adjust lora settings to match your hardware wiring"
echo ""
echo "4. Configure the counting line (requires display or VNC):"
echo "   source $VENV_PATH/bin/activate"
echo "   python ${INSTALL_DIR}/tools/configure_line.py"
echo ""
echo "5. Start the service:"
echo "   sudo systemctl start ${SERVICE_NAME}"
echo "   sudo journalctl -u ${SERVICE_NAME} -f"
