"""Quét tín hiệu kỹ thuật từ dữ liệu OHLCV."""

import logging
import pandas as pd
from datetime import datetime, timezone
from indicators import Indicators
from config import Config

logger = logging.getLogger(__name__)


def _get_session() -> str:
    """Xác định session hiện tại (Asia/London/NY)."""
    hour = datetime.now(timezone.utc).hour
    if 0 <= hour < 8:
        return "ASIA (00-08 UTC)"
    elif 8 <= hour < 15:
        return "LONDON (08-15 UTC)"
    elif 15 <= hour < 22:
        return "NEW YORK (15-22 UTC)"
    else:
        return "LATE NY / PRE-ASIA (22-00 UTC)"


class SignalScanner:

    def scan(self, df: pd.DataFrame, symbol: str, display: str, timeframe: str) -> dict | None:
        if df is None or len(df) < 52:
            return None

        df = Indicators.compute_all(df.copy(), Config.VOLUME_SPIKE_MULTIPLIER)
        sr = Indicators.support_resistance(df)
        fib = Indicators.fibonacci(df)
        last, prev = df.iloc[-1], df.iloc[-2]

        indicators = {}
        score = 0

        # 1. Ichimoku
        sa = last["senkou_a"] if pd.notna(last["senkou_a"]) else 0
        sb = last["senkou_b"] if pd.notna(last["senkou_b"]) else 0
        above = last["close"] > max(sa, sb)
        below = last["close"] < min(sa or float("inf"), sb or float("inf"))
        tk_kj = pd.notna(last["tenkan"]) and pd.notna(last["kijun"]) and last["tenkan"] > last["kijun"]

        if above and tk_kj:
            indicators["ichimoku"] = "LONG"
            score += 2
        elif below and not tk_kj:
            indicators["ichimoku"] = "SHORT"
            score -= 2
        else:
            indicators["ichimoku"] = "NEUTRAL"

        # 2. Volume
        if last["vol_spike"]:
            indicators["volume"] = "SPIKE"
            score += 1 if last["close"] > last["open"] else -1
        else:
            indicators["volume"] = "NORMAL"

        # 3. RSI
        if pd.notna(last["rsi"]):
            if last["rsi"] <= Config.RSI_OVERSOLD:
                indicators["rsi"] = f"OVERSOLD ({last['rsi']:.1f})"
                score += 1
            elif last["rsi"] >= Config.RSI_OVERBOUGHT:
                indicators["rsi"] = f"OVERBOUGHT ({last['rsi']:.1f})"
                score -= 1
            else:
                indicators["rsi"] = f"NEUTRAL ({last['rsi']:.1f})"

        # 3b. RSI Divergence — signal đảo chiều mạnh
        if last.get("rsi_bull_div"):
            indicators["rsi_divergence"] = "BULLISH DIV"
            score += 2
        elif last.get("rsi_bear_div"):
            indicators["rsi_divergence"] = "BEARISH DIV"
            score -= 2

        # 4. MACD
        if pd.notna(last["macd_hist"]) and pd.notna(prev["macd_hist"]):
            if last["macd_hist"] > 0 and prev["macd_hist"] <= 0:
                indicators["macd"] = "BULLISH CROSS"
                score += 2
            elif last["macd_hist"] < 0 and prev["macd_hist"] >= 0:
                indicators["macd"] = "BEARISH CROSS"
                score -= 2
            elif last["macd_hist"] > 0:
                indicators["macd"] = "BULLISH"
                score += 1
            else:
                indicators["macd"] = "BEARISH"
                score -= 1

        # 5. EMA Cross
        if last["ema_cross_up"]:
            indicators["ema"] = "GOLDEN CROSS (20>50)"
            score += 2
        elif last["ema_cross_down"]:
            indicators["ema"] = "DEATH CROSS (20<50)"
            score -= 2
        elif last["ema_bullish"]:
            indicators["ema"] = "BULLISH (20>50)"
            score += 1
        else:
            indicators["ema"] = "BEARISH (20<50)"
            score -= 1

        # 6. Market Structure (BOS/CHoCH)
        ms_trend = last.get("ms_trend", "UNKNOWN") if "ms_trend" in df.columns else "UNKNOWN"
        bos_bull = bool(last.get("bos_bullish", False))
        bos_bear = bool(last.get("bos_bearish", False))

        if bos_bull:
            indicators["market_structure"] = f"BOS BULLISH ({ms_trend})"
            score += 2
        elif bos_bear:
            indicators["market_structure"] = f"BOS BEARISH ({ms_trend})"
            score -= 2
        else:
            indicators["market_structure"] = ms_trend

        # 7. Fibonacci confluence — giá gần fib level = thêm điểm
        price = last["close"]
        fib_near = self._check_fib_confluence(price, fib)
        if fib_near:
            indicators["fibonacci"] = fib_near
            # Fib confluence thêm 1 điểm theo hướng
            if "support" in fib_near.lower():
                score += 1
            elif "resistance" in fib_near.lower():
                score -= 1

        # max score = 14 (ichimoku 2 + vol 1 + rsi 1 + rsi_div 2 + macd 2 + ema 2 + ms 2 + fib 1)
        confidence = min(abs(score) / 12, 1.0)
        direction = "LONG" if score > 0 else "SHORT" if score < 0 else "NEUTRAL"

        # Signal tier classification
        if confidence >= Config.TIER_STRONG_MIN:
            tier = "STRONG"
        elif confidence >= Config.TIER_MEDIUM_MIN:
            tier = "MEDIUM"
        elif confidence >= Config.TIER_WEAK_MIN:
            tier = "WEAK"
        else:
            return None  # Dưới WEAK → bỏ qua hoàn toàn

        # ATR data
        atr_val = round(last["atr"], 2) if pd.notna(last.get("atr")) else None
        atr_pct = round(last["atr_pct"], 2) if pd.notna(last.get("atr_pct")) else None

        # Invalidation level (mây Ichimoku edge)
        if direction == "LONG":
            invalidation = round(min(sa, sb), 2) if sa and sb else None
        elif direction == "SHORT":
            invalidation = round(max(sa, sb), 2) if sa and sb else None
        else:
            invalidation = None

        return {
            "symbol": display,
            "binance_symbol": symbol,
            "timeframe": timeframe,
            "direction": direction,
            "confidence": round(confidence, 2),
            "score": score,
            "tier": tier,
            "price": round(price, 2),
            "indicators": indicators,
            "support_resistance": sr,
            "fibonacci": fib,
            "volume_ratio": round(last["vol_ratio"], 2) if pd.notna(last["vol_ratio"]) else None,
            "cloud_bullish": bool(last["cloud_bullish"]) if pd.notna(last.get("cloud_bullish")) else None,
            "atr": atr_val,
            "atr_pct": atr_pct,
            "market_structure": ms_trend,
            "invalidation": invalidation,
            "session": _get_session(),
        }

    def _check_fib_confluence(self, price: float, fib: dict, tolerance_pct: float = 0.5) -> str | None:
        """Check xem giá có gần Fibonacci level nào không."""
        for level_name, level_price in fib.items():
            if level_name in ("trend",) or not isinstance(level_price, (int, float)):
                continue
            if level_price == 0:
                continue
            pct_diff = abs(price - level_price) / level_price * 100
            if pct_diff <= tolerance_pct:
                fib_pct = level_name.replace("fib_", "").replace("00", "0")
                if fib.get("trend") == "UP":
                    return f"Near Fib {fib_pct}% SUPPORT ({level_price})"
                else:
                    return f"Near Fib {fib_pct}% RESISTANCE ({level_price})"
        return None

    def scan_all(self, data: dict) -> list:
        results = []
        for symbol, sym_data in data.items():
            display = sym_data.get("display", symbol)
            for tf in Config.TIMEFRAMES:
                df = sym_data.get(tf)
                result = self.scan(df, symbol, display, tf)
                if result:
                    results.append(result)
        return results
