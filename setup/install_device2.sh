#!/usr/bin/env bash
set -euo pipefail

VENV_PATH="/home/pi/venv_device2"
INSTALL_DIR="/home/pi/visitor-counter"
SERVICE_NAME="visitor-counter-device2"

echo "=== Installing Device 2 (Visitor Counter - Receiver & Dashboard) ==="

echo "--- Updating system packages ---"
sudo apt-get update -y
sudo apt-get install -y \
    python3-pip \
    python3-venv \
    git \
    libatlas-base-dev

echo "--- Creating Python virtual environment at $VENV_PATH ---"
python3 -m venv "$VENV_PATH"
source "$VENV_PATH/bin/activate"

echo "--- Installing Python dependencies ---"
pip install --upgrade pip
pip install \
    "gspread>=5.12.0" \
    "google-auth>=2.23.0" \
    "plotly>=5.17.0" \
    "dash>=2.14.0" \
    "dash-bootstrap-components>=1.5.0" \
    "pyyaml>=6.0" \
    "pandas>=2.1.0"

pip install spidev RPi.GPIO || echo "WARNING: spidev/RPi.GPIO install failed (may need system packages)"

echo "--- Creating required directories ---"
mkdir -p /home/pi/data
mkdir -p /home/pi/logs
mkdir -p /home/pi/config
chown -R pi:pi /home/pi/data /home/pi/logs /home/pi/config 2>/dev/null || true

echo "--- Creating systemd service ---"
sudo tee /etc/systemd/system/${SERVICE_NAME}.service > /dev/null << EOF
[Unit]
Description=Visitor Counter Device 2 (Receiver & Dashboard)
After=network.target
Wants=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=${INSTALL_DIR}
ExecStart=${VENV_PATH}/bin/python -m device2.main
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
echo "1. Set up Google Sheets service account:"
echo "   a) Go to https://console.cloud.google.com/"
echo "   b) Create a project and enable the Google Sheets API and Google Drive API"
echo "   c) Create a service account and download the JSON key file"
echo "   d) Place the key file at: /home/pi/config/service_account.json"
echo "   e) Share your Google Sheet with the service account email address"
echo ""
echo "2. Edit the config file:"
echo "   nano ${INSTALL_DIR}/device2/config.yaml"
echo "   - Set google_sheets.spreadsheet_id to your Google Sheet ID"
echo "   - Adjust lora settings to match your hardware wiring"
echo ""
echo "3. Start the service:"
echo "   sudo systemctl start ${SERVICE_NAME}"
echo "   sudo journalctl -u ${SERVICE_NAME} -f"
echo ""
echo "4. Access the dashboard at:"
echo "   http://$(hostname -I | awk '{print $1}'):8050"
