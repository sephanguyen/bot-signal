"""Binance WebSocket price stream.

Dùng cho:
1. Realtime SL/TP tracking (tick-by-tick)
2. Volume spike detection ngay khi xảy ra
3. Kline close event → trigger scan chính xác khi nến đóng

Không thay thế REST API cho historical klines, chỉ bổ sung realtime.
"""

import json
import threading
import time
from datetime import datetime, timezone

import websocket

from config import Config


class PriceStream:
    """Binance WebSocket multi-stream cho nhiều symbols."""

    def __init__(self, on_price=None, on_kline_close=None):
        self._on_price = on_price
        self._on_kline_close = on_kline_close
        self._ws = None
        self._running = False
        self._thread = None
        self._prices: dict[str, float] = {}
        self._reconnect_delay = 5
        self._max_reconnect_delay = 60

    @property
    def prices(self) -> dict[str, float]:
        """Giá realtime hiện tại."""
        return self._prices.copy()

    def get_price(self, symbol: str) -> float | None:
        return self._prices.get(symbol.lower())

    def start(self):
        """Start WebSocket stream trong background thread."""
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        print("📡 WebSocket price stream started")

    def stop(self):
        """Stop stream."""
        self._running = False
        if self._ws:
            self._ws.close()
        print("📡 WebSocket stream stopped")

    def _build_url(self) -> str:
        """Build combined stream URL — tự chọn Spot hoặc Futures WebSocket."""
        streams = []
        for symbol in Config.SYMBOLS:
            s = symbol.lower()
            streams.append(f"{s}@miniTicker")
            for tf in Config.TIMEFRAMES:
                streams.append(f"{s}@kline_{tf}")

        stream_path = "/".join(streams)

        if Config.USE_FUTURES:
            return f"wss://fstream.binance.com/stream?streams={stream_path}"
        return f"wss://stream.binance.com:9443/stream?streams={stream_path}"

    def _run(self):
        """Main WebSocket loop với exponential backoff reconnect."""
        delay = self._reconnect_delay
        while self._running:
            try:
                url = self._build_url()
                self._ws = websocket.WebSocketApp(
                    url,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                    on_open=self._on_open,
                )
                self._ws.run_forever(ping_interval=30, ping_timeout=10)
                delay = self._reconnect_delay  # Reset on clean disconnect
            except Exception as e:
                print(f"  📡 WS error: {e}")

            if self._running:
                print(f"  📡 Reconnecting in {delay}s...")
                time.sleep(delay)
                delay = min(delay * 2, self._max_reconnect_delay)  # Exponential backoff

    def _on_open(self, ws):
        symbols = [s.upper() for s in Config.SYMBOLS]
        print(f"  📡 Connected: {symbols}")
        self._reconnect_delay = 5  # Reset backoff on successful connect

    def _on_close(self, ws, close_status, close_msg):
        if self._running:
            print(f"  📡 Disconnected: {close_msg}")

    def _on_error(self, ws, error):
        print(f"  📡 WS error: {error}")

    def _on_message(self, ws, message):
        """Handle incoming WebSocket messages."""
        try:
            data = json.loads(message)
            stream = data.get("stream", "")
            payload = data.get("data", {})

            if "@miniTicker" in stream:
                self._handle_ticker(payload)
            elif "@kline_" in stream:
                self._handle_kline(payload)

        except Exception as e:
            pass  # Silently ignore parse errors

    def _handle_ticker(self, data):
        """Update realtime price."""
        symbol = data.get("s", "").lower()
        price = float(data.get("c", 0))
        if symbol and price:
            self._prices[symbol] = price
            if self._on_price:
                self._on_price(symbol.upper(), price)

    def _handle_kline(self, data):
        """Detect kline close → trigger scan."""
        kline = data.get("k", {})
        is_closed = kline.get("x", False)  # True khi nến đóng

        if is_closed and self._on_kline_close:
            symbol = kline.get("s", "")
            interval = kline.get("i", "")
            close_price = float(kline.get("c", 0))
            volume = float(kline.get("v", 0))

            self._on_kline_close(
                symbol=symbol,
                timeframe=interval,
                close_price=close_price,
                volume=volume,
            )
