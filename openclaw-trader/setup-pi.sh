#!/bin/bash
# ============================================
# 🐾 OpenClaw Trader — Raspberry Pi Setup
# Tested on: Pi 3B+, Pi 4, Pi 5 (Raspberry Pi OS)
# ============================================

set -e

echo "🐾 OpenClaw Trader — Raspberry Pi Setup"
echo "========================================="

# 1. System dependencies
echo ""
echo "📦 Installing system dependencies..."
sudo apt-get update
sudo apt-get install -y python3 python3-pip python3-venv libatlas-base-dev libopenblas-dev

# 2. Create venv
echo ""
echo "🐍 Creating Python virtual environment..."
python3 -m venv venv
source venv/bin/activate

# 3. Upgrade pip
pip install --upgrade pip setuptools wheel

# 4. Install numpy first (ARM build can be slow)
echo ""
echo "📊 Installing numpy (may take a few minutes on Pi)..."
pip install numpy

# 5. Install remaining dependencies
echo ""
echo "📦 Installing dependencies..."
pip install -r requirements.txt

# 6. Setup .env
if [ ! -f .env ]; then
    echo ""
    echo "⚙️  Creating .env from template..."
    cp .env.example .env
    echo "   ⚠️  Edit .env with your API keys: nano .env"
fi

# 7. Test
echo ""
echo "🧪 Testing..."
python main.py test

echo ""
echo "✅ Setup complete!"
echo ""
echo "Commands:"
echo "  source venv/bin/activate"
echo "  python main.py test        # Test connections"
echo "  python main.py collect     # Collect data (no token cost)"
echo "  python main.py scan        # Full AI scan"
echo "  python main.py schedule    # Auto scheduler"
echo ""
echo "To run as background service:"
echo "  sudo cp openclaw-trader.service /etc/systemd/system/"
echo "  sudo systemctl enable openclaw-trader"
echo "  sudo systemctl start openclaw-trader"
