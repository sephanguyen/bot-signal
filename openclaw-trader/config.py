import os
import subprocess
from pathlib import Path
from dotenv import load_dotenv


def _decrypt_env_if_needed():
    """Tự động decrypt .env.age nếu .env không tồn tại."""
    env_path = Path(".env")
    age_path = Path(".env.age")
    key_path = Path.home() / ".openclaw-key.txt"

    if env_path.exists():
        return
    if age_path.exists() and key_path.exists():
        try:
            subprocess.run(
                ["age", "-d", "-i", str(key_path), "-o", str(env_path), str(age_path)],
                check=True, capture_output=True,
            )
            env_path.chmod(0o600)
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass


_decrypt_env_if_needed()
load_dotenv()


class Config:
    # Claude API — gọi trực tiếp Anthropic
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
    CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")

    # Telegram
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

    # Binance symbols
    SYMBOLS = os.getenv("SYMBOLS", "BTCUSDT,XAUUSDT,CLUSDT").split(",")
    TIMEFRAMES = os.getenv("TIMEFRAMES", "1d,4h,1h").split(",")

    # Thresholds
    VOLUME_SPIKE_MULTIPLIER = float(os.getenv("VOLUME_SPIKE_MULTIPLIER", "2.0"))
    RSI_OVERBOUGHT = float(os.getenv("RSI_OVERBOUGHT", "70"))
    RSI_OVERSOLD = float(os.getenv("RSI_OVERSOLD", "30"))
    SIGNAL_CONFIDENCE_MIN = float(os.getenv("SIGNAL_CONFIDENCE_MIN", "0.6"))

    # Signal Tier thresholds
    TIER_STRONG_MIN = float(os.getenv("TIER_STRONG_MIN", "0.6"))    # Full AI + track
    TIER_MEDIUM_MIN = float(os.getenv("TIER_MEDIUM_MIN", "0.35"))   # AI analyze, user decides
    TIER_WEAK_MIN = float(os.getenv("TIER_WEAK_MIN", "0.2"))        # Summary only, no AI
    TIER_NOTIFY_ALL = os.getenv("TIER_NOTIFY_ALL", "true").lower() == "true"  # Gửi tất cả tiers

    CANDLE_LIMIT = 200
    COLLECT_INTERVAL = int(os.getenv("COLLECT_INTERVAL", "60"))

    SYMBOL_DISPLAY = {
        "BTCUSDT": "BTC/USDT",
        "XAUUSDT": "XAU/USDT (Gold)",
        "CLUSDT": "OIL/USDT (Crude)",
    }

        # API rate limiting (free tier: 5 req/min, paid: 50+)
    AI_MAX_CONCURRENT = int(os.getenv("AI_MAX_CONCURRENT", "2"))  # Max parallel AI calls
    AI_CALL_DELAY = float(os.getenv("AI_CALL_DELAY", "15"))       # Seconds between calls


    BINANCE_API = "https://api.binance.com"
    BINANCE_FUTURES_API = "https://fapi.binance.com"
    USE_FUTURES = os.getenv("USE_FUTURES", "false").lower() == "true"

    # AI Backend: "anthropic" (direct API) or "zeroclaw" (local agent)
    AI_BACKEND = os.getenv("AI_BACKEND", "anthropic")

    # ZeroClaw config
    ZEROCLAW_BIN = os.getenv("ZEROCLAW_BIN", "zeroclaw")
    ZEROCLAW_MODE = os.getenv("ZEROCLAW_MODE", "cli")  # "cli" or "gateway"
    ZEROCLAW_GATEWAY_URL = os.getenv("ZEROCLAW_GATEWAY_URL", "http://127.0.0.1:3000")
    ZEROCLAW_MODEL = os.getenv("ZEROCLAW_MODEL", "")  # e.g. "openrouter/auto"
    ZEROCLAW_TIMEOUT = int(os.getenv("ZEROCLAW_TIMEOUT", "120"))
    ZEROCLAW_WORKDIR = os.getenv("ZEROCLAW_WORKDIR", "")  # optional working dir
