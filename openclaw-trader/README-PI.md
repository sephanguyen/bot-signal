# 🐾 OpenClaw Trader on Raspberry Pi

## Yêu cầu tối thiểu

| | Minimum | Recommended |
|---|---------|-------------|
| Model | Pi 3B+ | Pi 4 (2GB+) |
| OS | Raspberry Pi OS Lite (64-bit) | Raspberry Pi OS (64-bit) |
| RAM | 1GB | 2GB+ |
| Storage | 2GB free | 4GB free |
| Network | WiFi / Ethernet | Ethernet (ổn định hơn) |

## Quick Setup

```bash
cd ~
git clone <your-repo> openclaw-trader
cd openclaw-trader
chmod +x setup-pi.sh
./setup-pi.sh
nano .env  # Điền API keys
```

## Chạy thủ công

```bash
source venv/bin/activate

# Test kết nối
python main.py test

# Collect data (0 token cost)
python main.py collect

# Full scan (tốn token)
python main.py scan

# Auto scheduler
python main.py schedule
```

## Chạy như system service (24/7)

```bash
# Sửa đường dẫn trong file service nếu cần
nano openclaw-trader.service

# Install service
sudo cp openclaw-trader.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable openclaw-trader
sudo systemctl start openclaw-trader

# Xem logs
sudo journalctl -u openclaw-trader -f

# Stop / restart
sudo systemctl stop openclaw-trader
sudo systemctl restart openclaw-trader
```

## Tips cho Pi

- Dùng Ethernet thay WiFi để tránh mất kết nối
- Set timezone: `sudo timedatectl set-timezone Asia/Ho_Chi_Minh`
- Monitor RAM: `htop` hoặc `free -h`
- Nếu Pi 3B+ bị chậm khi install numpy, dùng `pip install --extra-index-url https://www.piwheels.org/simple numpy`
