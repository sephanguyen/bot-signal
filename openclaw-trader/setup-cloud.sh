#!/bin/bash
# ============================================
# 🐾 OpenClaw Trader — Cloud VM Setup
# Works on: GCP, Oracle Cloud, AWS, any Debian/Ubuntu VM
# Tối ưu cho e2-micro (1 vCPU, 1GB RAM)
# ============================================

set -e

echo "🐾 OpenClaw Trader — Cloud VM Setup"
echo "====================================="

INSTALL_DIR="${1:-$(pwd)}"
CURRENT_USER=$(whoami)

# 1. Swap (quan trọng cho VM 1GB RAM)
echo ""
echo "💾 Setting up swap..."
if [ ! -f /swapfile ]; then
    sudo fallocate -l 1G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
    echo "   ✓ 1GB swap created"
else
    echo "   ✓ Swap already exists"
fi

# 2. System deps
echo ""
echo "📦 Installing system dependencies..."
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-pip python3-venv git

# 3. Venv
echo ""
echo "🐍 Creating virtual environment..."
python3 -m venv "$INSTALL_DIR/venv"
source "$INSTALL_DIR/venv/bin/activate"
pip install --upgrade pip setuptools wheel -q

# 4. Install deps
echo ""
echo "� SInstalling Python dependencies..."
pip install -r "$INSTALL_DIR/requirements.txt" -q

# 5. Setup .env
if [ ! -f "$INSTALL_DIR/.env" ]; then
    echo ""
    echo "⚙️  Creating .env..."
    cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
    chmod 600 "$INSTALL_DIR/.env"
    echo "   ⚠️  Edit .env: nano $INSTALL_DIR/.env"
fi

# 6. Create data directories
mkdir -p "$INSTALL_DIR/data/logs"
mkdir -p "$INSTALL_DIR/data/archive"

# 7. Setup systemd service
echo ""
echo "🔧 Setting up systemd service..."
SERVICE_FILE="/etc/systemd/system/openclaw-trader.service"

sudo tee "$SERVICE_FILE" > /dev/null << EOF
[Unit]
Description=OpenClaw Trader - AI Trading Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/venv/bin/python main.py bot
Restart=always
RestartSec=30
StandardOutput=journal
StandardError=journal

# Security
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=$INSTALL_DIR/data
PrivateDevices=true

# Resource limits (tối ưu cho e2-micro)
MemoryMax=512M
CPUQuota=80%

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable openclaw-trader
echo "   ✓ Service installed (bot mode)"

# 8. Test
echo ""
echo "🧪 Testing..."
python main.py test

echo ""
echo "✅ Setup complete!"
echo ""
echo "Next steps:"
echo "  1. Edit .env:     nano .env"
echo "  2. Test:          source venv/bin/activate && python main.py test"
echo "  3. Single scan:   python main.py scan"
echo "  4. Start 24/7:    sudo systemctl start openclaw-trader"
echo "  5. View logs:     sudo journalctl -u openclaw-trader -f"
echo "  6. Stop:          sudo systemctl stop openclaw-trader"
echo "  7. Stats:         python main.py stats"
