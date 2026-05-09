#!/usr/bin/env bash
# Bitana VPS Setup Script — Ubuntu 22.04+
set -euo pipefail

echo "=== Bitana VPS Setup ==="

# System packages
sudo apt-get update
sudo apt-get install -y python3.11 python3.11-venv python3.11-dev \
    git curl build-essential

# Create user
if ! id -u bitana &>/dev/null; then
    sudo useradd -r -m -d /opt/bitana -s /bin/bash bitana
    echo "Created user 'bitana'"
fi

# Setup directory
INSTALL_DIR=/opt/bitana
sudo mkdir -p "$INSTALL_DIR"/{data,logs}

# Copy project files (assumes running from repo root)
sudo cp -r . "$INSTALL_DIR/"
sudo chown -R bitana:bitana "$INSTALL_DIR"

# Create venv and install deps
sudo -u bitana bash -c "
    cd $INSTALL_DIR
    python3.11 -m venv venv
    source venv/bin/activate
    pip install --upgrade pip
    pip install -r requirements.txt
"

# Setup .env (if not exists)
if [ ! -f "$INSTALL_DIR/.env" ]; then
    sudo cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
    sudo chown bitana:bitana "$INSTALL_DIR/.env"
    sudo chmod 600 "$INSTALL_DIR/.env"
    echo ">>> IMPORTANT: Edit /opt/bitana/.env with your API keys <<<"
fi

# Install systemd service
sudo cp "$INSTALL_DIR/deploy/bitana.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable bitana

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo "  1. Edit /opt/bitana/.env with Binance + Telegram credentials"
echo "  2. Review /opt/bitana/config/settings.yaml"
echo "  3. Start: sudo systemctl start bitana"
echo "  4. Logs:  sudo journalctl -u bitana -f"
echo "  5. Health: curl http://localhost:8080/health"
echo ""
echo "For live mode, edit bitana.service: --mode live"
