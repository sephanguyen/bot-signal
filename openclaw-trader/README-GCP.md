# 🐾 OpenClaw Trader — Google Cloud Setup

Hướng dẫn deploy bot lên Google Cloud e2-micro (Free Tier, $0/tháng).

## Yêu cầu

- Tài khoản Google Cloud (có billing enabled, nhưng e2-micro miễn phí)
- `gcloud` CLI đã cài và đăng nhập
- API keys: Anthropic + Telegram Bot

## 1. Tạo VM

```bash
# Tạo instance e2-micro (Free Tier)
gcloud compute instances create openclaw-trader \
  --machine-type=e2-micro \
  --zone=asia-southeast1-b \
  --image-family=debian-12 \
  --image-project=debian-cloud \
  --boot-disk-size=10GB \
  --boot-disk-type=pd-standard \
  --tags=openclaw

# Nếu muốn region US (cũng free):
# --zone=us-central1-a
```

> **Region**: `asia-southeast1` (Singapore) cho latency thấp tới Binance (~20ms).
> `us-central1` cũng free nhưng latency cao hơn (~200ms).

## 2. SSH vào VM

```bash
gcloud compute ssh openclaw-trader --zone=asia-southeast1-b
```

## 3. Setup swap (quan trọng cho e2-micro 1GB RAM)

```bash
sudo fallocate -l 1G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# Verify
free -h
```

## 4. Cài dependencies

```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv git
```

## 5. Clone project

```bash
cd ~
git clone <your-repo-url> openclaw-trader
cd openclaw-trader
```

## 6. Setup Python environment

```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## 7. Cấu hình .env

```bash
cp .env.example .env
chmod 600 .env
nano .env
```

Điền các giá trị:

```env
# Bắt buộc
ANTHROPIC_API_KEY=sk-ant-xxx
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id

# Tùy chọn
AI_BACKEND=anthropic
SYMBOLS=BTCUSDT,XAUUSDT,CLUSDT
TIMEFRAMES=1d,4h,1h

# Signal Tier (giữ mặc định hoặc tùy chỉnh)
TIER_STRONG_MIN=0.6
TIER_MEDIUM_MIN=0.35
TIER_WEAK_MIN=0.2
TIER_NOTIFY_ALL=true
```

## 8. Test kết nối

```bash
source venv/bin/activate
python main.py test
```

Kết quả mong đợi:

```
🧪 Testing...
  ✓ BTC/USDT: 200 candles, price: 84500.00
  ✓ XAU/USDT (Gold): 200 candles, price: 3100.50
  ✓ OIL/USDT (Crude): 200 candles, price: 68.20
  AI Backend: anthropic
  Anthropic: ✓ Key set
  Telegram: ✓ Configured
✅ Done.
```

## 9. Chạy scan thử

```bash
python main.py scan
```

## 10. Setup systemd service (chạy 24/7)

```bash
# Tạo service file
sudo tee /etc/systemd/system/openclaw-trader.service > /dev/null << 'EOF'
[Unit]
Description=OpenClaw Trader - AI Trading Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=/home/$USER/openclaw-trader
ExecStart=/home/$USER/openclaw-trader/venv/bin/python main.py bot
Restart=always
RestartSec=30
StandardOutput=journal
StandardError=journal

# Security
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/home/$USER/openclaw-trader/data
PrivateDevices=true

# Resource limits (tối ưu cho e2-micro)
MemoryMax=512M
CPUQuota=80%

[Install]
WantedBy=multi-user.target
EOF
```

Thay `$USER` bằng username thực:

```bash
sudo sed -i "s/\$USER/$(whoami)/g" /etc/systemd/system/openclaw-trader.service
```

Kích hoạt:

```bash
sudo systemctl daemon-reload
sudo systemctl enable openclaw-trader
sudo systemctl start openclaw-trader
```

## 11. Kiểm tra bot đang chạy

```bash
# Status
sudo systemctl status openclaw-trader

# Logs realtime
sudo journalctl -u openclaw-trader -f

# Logs 1 giờ gần nhất
sudo journalctl -u openclaw-trader --since "1 hour ago"
```

## Quản lý hàng ngày

```bash
# Restart bot
sudo systemctl restart openclaw-trader

# Stop bot
sudo systemctl stop openclaw-trader

# Xem stats (SSH vào VM)
cd ~/openclaw-trader
source venv/bin/activate
python main.py stats

# Chạy backtest
python main.py backtest
python main.py backtest --walk-forward

# Update code
cd ~/openclaw-trader
git pull
sudo systemctl restart openclaw-trader
```

## Monitoring

Bot tự gửi lên Telegram:
- **Daily heartbeat** lúc 08:00 UTC — xác nhận bot còn sống
- **Weekly report** Chủ Nhật 08:30 UTC — tổng kết tuần
- **SL/TP alerts** — ngay khi hit

Nếu không nhận heartbeat → SSH vào check logs.

## Chi phí ước tính

| Hạng mục | Chi phí/tháng |
|----------|---------------|
| GCP e2-micro | $0 (Free Tier) |
| Disk 10GB | $0 (Free Tier) |
| Network egress | $0 (< 1GB/tháng) |
| Anthropic API | $5-10 |
| **Tổng** | **$5-10/tháng** |

## Troubleshooting

**Bot không start:**
```bash
sudo journalctl -u openclaw-trader -n 50
# Thường do thiếu .env hoặc sai API key
```

**Out of memory:**
```bash
# Check swap
free -h
# Nếu chưa có swap → quay lại bước 3
```

**WebSocket disconnect liên tục:**
```bash
# Check network
curl -s https://api.binance.com/api/v3/ping
# Nếu timeout → check firewall rules trên GCP Console
```

**Update dependencies:**
```bash
cd ~/openclaw-trader
source venv/bin/activate
pip install -r requirements.txt --upgrade
sudo systemctl restart openclaw-trader
```
