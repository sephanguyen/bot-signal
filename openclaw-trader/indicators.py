"""Tính toán các chỉ báo kỹ thuật trên DataFrame OHLCV.

Tối ưu cho Raspberry Pi: tránh copy DataFrame không cần thiết,
dùng numpy vectorized operations thay vì loop.
"""

import logging
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


class Indicators:

    @staticmethod
    def ichimoku(df: pd.DataFrame) -> pd.DataFrame:
        high, low, close = df["high"], df["low"], df["close"]
        df["tenkan"] = (high.rolling(9).max() + low.rolling(9).min()) / 2
        df["kijun"] = (high.rolling(26).max() + low.rolling(26).min()) / 2
        df["senkou_a"] = ((df["tenkan"] + df["kijun"]) / 2).shift(26)
        df["senkou_b"] = ((high.rolling(52).max() + low.rolling(52).min()) / 2).shift(26)
        df["chikou"] = close.shift(-26)
        df["cloud_bullish"] = df["senkou_a"] > df["senkou_b"]
        return df

    @staticmethod
    def volume_spike(df: pd.DataFrame, multiplier: float = 2.0) -> pd.DataFrame:
        df["vol_sma20"] = df["volume"].rolling(20).mean()
        df["vol_ratio"] = df["volume"] / df["vol_sma20"]
        df["vol_spike"] = df["vol_ratio"] >= multiplier
        return df

    @staticmethod
    def rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        delta = df["close"].diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        rs = gain.rolling(period).mean() / loss.rolling(period).mean()
        df["rsi"] = 100 - (100 / (1 + rs))
        return df

    @staticmethod
    def rsi_divergence(df: pd.DataFrame, lookback: int = 14) -> pd.DataFrame:
        """RSI Divergence — detect phân kỳ giá vs RSI.

        Bullish divergence: giá tạo Lower Low nhưng RSI tạo Higher Low
        Bearish divergence: giá tạo Higher High nhưng RSI tạo Lower High
        Vectorized: dùng rolling min/max thay vì loop.
        """
        close = df["close"]
        rsi = df["rsi"]

        # Rolling lows/highs cho price và RSI
        price_low = close.rolling(lookback).min()
        price_high = close.rolling(lookback).max()
        rsi_low = rsi.rolling(lookback).min()
        rsi_high = rsi.rolling(lookback).max()

        prev_price_low = price_low.shift(lookback)
        prev_price_high = price_high.shift(lookback)
        prev_rsi_low = rsi_low.shift(lookback)
        prev_rsi_high = rsi_high.shift(lookback)

        # Bullish: price lower low + RSI higher low
        df["rsi_bull_div"] = (
            (price_low < prev_price_low) &
            (rsi_low > prev_rsi_low) &
            (rsi < 40)  # Chỉ detect ở vùng thấp
        ).fillna(False)

        # Bearish: price higher high + RSI lower high
        df["rsi_bear_div"] = (
            (price_high > prev_price_high) &
            (rsi_high < prev_rsi_high) &
            (rsi > 60)  # Chỉ detect ở vùng cao
        ).fillna(False)

        return df

    @staticmethod
    def fibonacci(df: pd.DataFrame, window: int = 50) -> dict:
        """Fibonacci Retracement levels từ swing high/low gần nhất.

        Nhẹ: chỉ tính 1 lần trên window cuối, không tạo columns mới.
        """
        recent = df.tail(window)
        high = recent["high"].max()
        low = recent["low"].min()
        diff = high - low

        if diff == 0:
            return {"fib_0": high, "fib_236": high, "fib_382": high,
                    "fib_500": high, "fib_618": high, "fib_786": high, "fib_1": high}

        # Detect trend direction từ vị trí high vs low
        high_idx = recent["high"].idxmax()
        low_idx = recent["low"].idxmin()
        is_uptrend = low_idx < high_idx  # Low trước High = uptrend

        if is_uptrend:
            # Retracement từ trên xuống
            return {
                "fib_0": round(high, 2),
                "fib_236": round(high - diff * 0.236, 2),
                "fib_382": round(high - diff * 0.382, 2),
                "fib_500": round(high - diff * 0.5, 2),
                "fib_618": round(high - diff * 0.618, 2),
                "fib_786": round(high - diff * 0.786, 2),
                "fib_1": round(low, 2),
                "trend": "UP",
            }
        else:
            # Retracement từ dưới lên
            return {
                "fib_0": round(low, 2),
                "fib_236": round(low + diff * 0.236, 2),
                "fib_382": round(low + diff * 0.382, 2),
                "fib_500": round(low + diff * 0.5, 2),
                "fib_618": round(low + diff * 0.618, 2),
                "fib_786": round(low + diff * 0.786, 2),
                "fib_1": round(high, 2),
                "trend": "DOWN",
            }

    @staticmethod
    def macd(df: pd.DataFrame) -> pd.DataFrame:
        ema12 = df["close"].ewm(span=12, adjust=False).mean()
        ema26 = df["close"].ewm(span=26, adjust=False).mean()
        df["macd_line"] = ema12 - ema26
        df["macd_signal"] = df["macd_line"].ewm(span=9, adjust=False).mean()
        df["macd_hist"] = df["macd_line"] - df["macd_signal"]
        return df

    @staticmethod
    def ema_cross(df: pd.DataFrame) -> pd.DataFrame:
        df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
        df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
        df["ema_bullish"] = df["ema20"] > df["ema50"]
        df["ema_cross_up"] = (df["ema20"] > df["ema50"]) & (
            df["ema20"].shift(1) <= df["ema50"].shift(1)
        )
        df["ema_cross_down"] = (df["ema20"] < df["ema50"]) & (
            df["ema20"].shift(1) >= df["ema50"].shift(1)
        )
        return df

    @staticmethod
    def atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        """Average True Range — đo volatility."""
        high, low, close = df["high"], df["low"], df["close"]
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        df["true_range"] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        df["atr"] = df["true_range"].rolling(period).mean()
        df["atr_pct"] = (df["atr"] / close) * 100
        return df

    @staticmethod
    def market_structure(df: pd.DataFrame, lookback: int = 5) -> pd.DataFrame:
        """Market Structure — BOS & CHoCH detection."""
        high, low, close = df["high"], df["low"], df["close"]

        df["swing_high"] = high[
            (high == high.rolling(lookback * 2 + 1, center=True).max())
        ]
        df["swing_low"] = low[
            (low == low.rolling(lookback * 2 + 1, center=True).min())
        ]

        df["last_swing_high"] = df["swing_high"].ffill()
        df["last_swing_low"] = df["swing_low"].ffill()

        df["bos_bullish"] = (close > df["last_swing_high"].shift(1)) & (
            close.shift(1) <= df["last_swing_high"].shift(1)
        )
        df["bos_bearish"] = (close < df["last_swing_low"].shift(1)) & (
            close.shift(1) >= df["last_swing_low"].shift(1)
        )

        recent_highs = df["swing_high"].dropna().tail(3)
        recent_lows = df["swing_low"].dropna().tail(3)

        if len(recent_highs) >= 2:
            df["hh"] = recent_highs.iloc[-1] > recent_highs.iloc[-2]
        else:
            df["hh"] = False

        if len(recent_lows) >= 2:
            df["ll"] = recent_lows.iloc[-1] < recent_lows.iloc[-2]
        else:
            df["ll"] = False

        if len(recent_highs) >= 2 and len(recent_lows) >= 2:
            hh = recent_highs.iloc[-1] > recent_highs.iloc[-2]
            hl = recent_lows.iloc[-1] > recent_lows.iloc[-2]
            lh = recent_highs.iloc[-1] < recent_highs.iloc[-2]
            ll = recent_lows.iloc[-1] < recent_lows.iloc[-2]

            if hh and hl:
                df["ms_trend"] = "BULLISH"
            elif lh and ll:
                df["ms_trend"] = "BEARISH"
            else:
                df["ms_trend"] = "RANGING"
        else:
            df["ms_trend"] = "UNKNOWN"

        return df

    @staticmethod
    def support_resistance(df: pd.DataFrame, window: int = 20) -> dict:
        recent = df.tail(window)
        h, l, c = recent["high"].max(), recent["low"].min(), recent["close"].iloc[-1]
        pivot = (h + l + c) / 3
        return {
            "pivot": round(pivot, 2),
            "r1": round(2 * pivot - l, 2),
            "r2": round(pivot + (h - l), 2),
            "s1": round(2 * pivot - h, 2),
            "s2": round(pivot - (h - l), 2),
        }

    @staticmethod
    def compute_all(df: pd.DataFrame, vol_multiplier: float = 2.0) -> pd.DataFrame:
        df = Indicators.ichimoku(df)
        df = Indicators.volume_spike(df, vol_multiplier)
        df = Indicators.rsi(df)
        df = Indicators.rsi_divergence(df)
        df = Indicators.macd(df)
        df = Indicators.ema_cross(df)
        df = Indicators.atr(df)
        df = Indicators.market_structure(df)
        return df
