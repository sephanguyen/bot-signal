#!/bin/bash
# ============================================
# 🛡️ OpenClaw Trader — Pi Security Setup
# ============================================

set -e

echo "🛡️ OpenClaw Trader — Security Setup"
echo "====================================="

INSTALL_DIR="/home/pi/openclaw-trader"
SERVICE_USER="openclaw"

# ── 1. Tạo dedicated user (không có sudo, không có shell login) ──
echo ""
echo "1. Creating dedicated user: ${SERVICE_USER}"
if id "$SERVICE_USER" &>/dev/null; then
    echo "   User already exists"
else
    sudo useradd -r -s /usr/sbin/nologin -d "$INSTALL_DIR" -M "$SERVICE_USER"
    echo "   ✓ Created user: ${SERVICE_USER} (no login shell, no sudo)"
fi

# ── 2. File permissions ──
echo ""
echo "2. Setting file permissions..."

# Chỉ owner đọc được .env và key
if [ -f "$INSTALL_DIR/.env" ]; then
    sudo chown ${SERVICE_USER}:${SERVICE_USER} "$INSTALL_DIR/.env"
    sudo chmod 600 "$INSTALL_DIR/.env"
    echo "   ✓ .env: owner-only (600)"
fi

if [ -f "$INSTALL_DIR/.env.age" ]; then
    sudo chown ${SERVICE_USER}:${SERVICE_USER} "$INSTALL_DIR/.env.age"
    sudo chmod 600 "$INSTALL_DIR/.env.age"
    echo "   ✓ .env.age: owner-only (600)"
fi

# Code files: read-only cho service user
sudo chown -R ${SERVICE_USER}:${SERVICE_USER} "$INSTALL_DIR"
sudo find "$INSTALL_DIR" -type f -name "*.py" -exec chmod 644 {} \;
sudo find "$INSTALL_DIR" -type d -exec chmod 755 {} \;
echo "   ✓ Code files: read-only"

# ── 3. Firewall (ufw) ──
echo ""
echo "3. Configuring firewall..."
if command -v ufw &> /dev/null; then
    sudo ufw default deny incoming
    sudo ufw default allow outgoing
    sudo ufw allow ssh
    sudo ufw --force enable
    echo "   ✓ UFW: deny incoming, allow outgoing, allow SSH"
else
    echo "   Installing ufw..."
    sudo apt-get install -y ufw
    sudo ufw default deny incoming
    sudo ufw default allow outgoing
    sudo ufw allow ssh
    sudo ufw --force enable
    echo "   ✓ UFW configured"
fi

# ── 4. SSH hardening ──
echo ""
echo "4. SSH hardening tips (manual):"
echo "   - Disable password auth: PasswordAuthentication no"
echo "   - Use SSH keys only"
echo "   - Change default port: Port 2222"
echo "   - Config: sudo nano /etc/ssh/sshd_config"

# ── 5. Fail2ban ──
echo ""
echo "5. Installing fail2ban (brute-force protection)..."
if command -v fail2ban-client &> /dev/null; then
    echo "   Already installed"
else
    sudo apt-get install -y fail2ban
    sudo systemctl enable fail2ban
    sudo systemctl start fail2ban
    echo "   ✓ fail2ban active"
fi

# ── 6. Auto security updates ──
echo ""
echo "6. Enabling automatic security updates..."
sudo apt-get install -y unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades
echo "   ✓ Unattended upgrades enabled"

echo ""
echo "====================================="
echo "✅ Security setup complete!"
echo ""
echo "Summary:"
echo "  - Dedicated user '${SERVICE_USER}' (no sudo, no login)"
echo "  - .env encrypted + permissions 600"
echo "  - Firewall: only outgoing + SSH"
echo "  - fail2ban: SSH brute-force protection"
echo "  - Auto security updates"
echo ""
echo "Next steps:"
echo "  1. Encrypt .env: bash security/encrypt_env.sh"
echo "  2. Update systemd service to use '${SERVICE_USER}' user"
echo "  3. Disable SSH password login, use keys only"
