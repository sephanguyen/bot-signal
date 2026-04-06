"""Lấy dữ liệu OHLCV từ Binance REST API.

Tối ưu Pi: rate limiting, connection reuse, timeout ngắn.
"""

import logging
import time
import threading
import requests
import pandas as pd
from config import Config

logger = logging.getLogger(__name__)

INTERVAL_MAP = {
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1h", "4h": "4h", "1d": "1d", "1w": "1w",
}

# Rate limiter — Binance cho 1200 requests/min, ta giới hạn thấp hơn
_rate_lock = threading.Lock()
_last_request_time = 0.0
_MIN_INTERVAL = 0.15  # 150ms giữa các request (~400 req/min max)


def _rate_limit():
    """Simple rate limiter — thread-safe."""
    global _last_request_time
    with _rate_lock:
        now = time.monotonic()
        elapsed = now - _last_request_time
        if elapsed < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - elapsed)
        _last_request_time = time.monotonic()


class DataFetcher:

    def __init__(self):
        if Config.USE_FUTURES:
            self.base_url = Config.BINANCE_FUTURES_API + "/fapi/v1/klines"
        else:
            self.base_url = Config.BINANCE_API + "/api/v3/klines"
        # Reuse session — giảm TCP handshake overhead trên Pi
        self._session = requests.Session()
        self._session.headers.update({"Accept-Encoding": "gzip"})

    def fetch_ohlcv(self, symbol: str, timeframe: str) -> pd.DataFrame:
        """Lấy OHLCV từ Binance REST API."""
        _rate_limit()

        interval = INTERVAL_MAP.get(timeframe, timeframe)
        params = {
            "symbol": symbol,
            "interval": interval,
            "limit": Config.CANDLE_LIMIT,
        }

        resp = self._session.get(self.base_url, params=params, timeout=10)
        resp.raise_for_status()
        raw = resp.json()

        df = pd.DataFrame(raw, columns=[
            "timestamp", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades",
            "taker_buy_base", "taker_buy_quote", "ignore",
        ])

        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)

        df = df[["timestamp", "open", "high", "low", "close", "volume"]]
        df.set_index("timestamp", inplace=True)
        return df

    def fetch_all(self) -> dict:
        """Lấy dữ liệu cho tất cả symbols và timeframes."""
        data = {}
        for symbol in Config.SYMBOLS:
            display = Config.SYMBOL_DISPLAY.get(symbol, symbol)
            data[symbol] = {"display": display}

            for tf in Config.TIMEFRAMES:
                try:
                    df = self.fetch_ohlcv(symbol, tf)
                    data[symbol][tf] = df
                    logger.info(f"  ✓ {display} [{tf}] - {len(df)} candles")
                    print(f"  ✓ {display} [{tf}] - {len(df)} candles")
                except Exception as e:
                    logger.warning(f"  ✗ {display} [{tf}] - {e}")
                    print(f"  ✗ {display} [{tf}] - {e}")
                    data[symbol][tf] = None

        return data

    def fetch_price(self, symbol: str) -> float:
        """Lấy giá hiện tại — tự chọn Spot hoặc Futures API."""
        _rate_limit()
        if Config.USE_FUTURES:
            url = Config.BINANCE_FUTURES_API + "/fapi/v1/ticker/price"
        else:
            url = Config.BINANCE_API + "/api/v3/ticker/price"
        resp = self._session.get(url, params={"symbol": symbol}, timeout=5)
        resp.raise_for_status()
        return float(resp.json()["price"])

    def fetch_funding_rate(self, symbol: str) -> dict | None:
        """Lấy funding rate từ Binance Futures (nếu có).

        Funding rate cao + signal SHORT = confluence mạnh.
        Funding rate âm + signal LONG = confluence mạnh.
        """
        _rate_limit()
        try:
            url = Config.BINANCE_FUTURES_API + "/fapi/v1/fundingRate"
            resp = self._session.get(
                url, params={"symbol": symbol, "limit": 1}, timeout=5
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            if data:
                rate = float(data[0]["fundingRate"])
                return {
                    "rate": round(rate * 100, 4),  # Convert to percentage
                    "signal": "BEARISH" if rate > 0.01 else "BULLISH" if rate < -0.01 else "NEUTRAL",
                }
        except Exception:
            pass
        return None

    def fetch_long_short_ratio(self, symbol: str) -> dict | None:
        """Lấy long/short ratio từ Binance Futures."""
        _rate_limit()
        try:
            url = Config.BINANCE_FUTURES_API + "/futures/data/globalLongShortAccountRatio"
            resp = self._session.get(
                url, params={"symbol": symbol, "period": "1h", "limit": 1}, timeout=5
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            if data:
                ratio = float(data[0]["longShortRatio"])
                return {
                    "ratio": round(ratio, 3),
                    "long_pct": round(float(data[0]["longAccount"]) * 100, 1),
                    "short_pct": round(float(data[0]["shortAccount"]) * 100, 1),
                }
        except Exception:
            pass
        return None
