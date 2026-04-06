"""Backtest mode — chạy signal scanner trên dữ liệu quá khứ.

Không tốn token AI, chỉ test phần technical analysis.
Thêm: slippage + fees, walk-forward validation.
"""

import logging
import pandas as pd
from data_fetcher import DataFetcher
from signal_scanner import SignalScanner
from config import Config

logger = logging.getLogger(__name__)

# Trading costs
TRADING_FEE_PCT = 0.1    # Binance spot fee 0.1%
SLIPPAGE_PCT = 0.05       # Estimated slippage 0.05%
TOTAL_COST_PCT = (TRADING_FEE_PCT + SLIPPAGE_PCT) * 2  # Entry + exit


class Backtester:

    def __init__(self):
        self.fetcher = DataFetcher()
        self.scanner = SignalScanner()

    def run(self, symbol: str, timeframe: str, lookback: int = 100) -> dict:
        """Backtest signal scanner trên dữ liệu quá khứ."""
        print(f"\n📊 Backtesting {symbol} [{timeframe}] - {lookback} candles...")

        df = self.fetcher.fetch_ohlcv(symbol, timeframe)
        if len(df) < lookback + 52:
            print("  ✗ Không đủ dữ liệu")
            return {}

        display = Config.SYMBOL_DISPLAY.get(symbol, symbol)
        results = []

        for i in range(52, len(df) - 10):
            window = df.iloc[:i + 1].copy()
            signal = self.scanner.scan(window, symbol, display, timeframe)

            if not signal:
                continue

            future = df.iloc[i + 1: i + 11]
            outcome = self._check_outcome(signal, future)
            results.append({**signal, **outcome})

        return self._summarize(results, symbol, timeframe)

    def run_walk_forward(self, symbol: str, timeframe: str,
                         train_pct: float = 0.7, step: int = 20) -> dict:
        """Walk-forward validation — train/test rolling windows.

        Realistic hơn backtest thường vì tránh look-ahead bias.
        """
        print(f"\n📊 Walk-Forward: {symbol} [{timeframe}]...")

        df = self.fetcher.fetch_ohlcv(symbol, timeframe)
        if len(df) < 100:
            print("  ✗ Không đủ dữ liệu")
            return {}

        display = Config.SYMBOL_DISPLAY.get(symbol, symbol)
        total_len = len(df)
        train_size = int(total_len * train_pct)
        results = []

        # Rolling window: train trên train_size, test trên step candles tiếp theo
        start = 52
        while start + train_size + step <= total_len:
            test_start = start + train_size
            test_end = min(test_start + step, total_len - 10)

            for i in range(test_start, test_end):
                window = df.iloc[start:i + 1].copy()
                signal = self.scanner.scan(window, symbol, display, timeframe)

                if not signal:
                    continue

                future = df.iloc[i + 1: i + 11]
                outcome = self._check_outcome(signal, future)
                results.append({**signal, **outcome})

            start += step  # Slide forward

        summary = self._summarize(results, symbol, timeframe)
        summary["mode"] = "walk_forward"
        return summary

    def _check_outcome(self, signal: dict, future: pd.DataFrame) -> dict:
        """Check tín hiệu với slippage + fees."""
        if future.empty:
            return {"outcome": "NO_DATA", "pnl_pct": 0}

        entry = signal["price"]
        sr = signal["support_resistance"]
        is_long = signal["direction"] == "LONG"

        if is_long:
            tp1 = sr["r1"]
            sl = sr["s1"]
        else:
            tp1 = sr["s1"]
            sl = sr["r1"]

        for _, candle in future.iterrows():
            if is_long and candle["low"] <= sl:
                pnl = round((sl - entry) / entry * 100 - TOTAL_COST_PCT, 2)
                return {"outcome": "SL", "pnl_pct": pnl, "exit_price": sl}
            if not is_long and candle["high"] >= sl:
                pnl = round((entry - sl) / entry * 100 - TOTAL_COST_PCT, 2)
                return {"outcome": "SL", "pnl_pct": pnl, "exit_price": sl}

            if is_long and candle["high"] >= tp1:
                pnl = round((tp1 - entry) / entry * 100 - TOTAL_COST_PCT, 2)
                return {"outcome": "TP1", "pnl_pct": pnl, "exit_price": tp1}
            if not is_long and candle["low"] <= tp1:
                pnl = round((entry - tp1) / entry * 100 - TOTAL_COST_PCT, 2)
                return {"outcome": "TP1", "pnl_pct": pnl, "exit_price": tp1}

        last_close = future["close"].iloc[-1]
        if is_long:
            pnl = round((last_close - entry) / entry * 100 - TOTAL_COST_PCT, 2)
        else:
            pnl = round((entry - last_close) / entry * 100 - TOTAL_COST_PCT, 2)
        return {"outcome": "EXPIRED", "pnl_pct": pnl, "exit_price": last_close}

    def _summarize(self, results: list, symbol: str, timeframe: str) -> dict:
        """Tổng kết backtest."""
        if not results:
            return {"symbol": symbol, "timeframe": timeframe, "total_signals": 0}

        total = len(results)
        wins = [r for r in results if r["pnl_pct"] > 0]
        losses = [r for r in results if r["pnl_pct"] <= 0]
        tp_hits = [r for r in results if r["outcome"] == "TP1"]
        sl_hits = [r for r in results if r["outcome"] == "SL"]

        pnls = [r["pnl_pct"] for r in results]
        avg_pnl = round(sum(pnls) / len(pnls), 2)
        total_pnl = round(sum(pnls), 2)
        win_rate = round(len(wins) / total * 100, 1)

        cumulative = 0
        max_dd = 0
        for pnl in pnls:
            cumulative += pnl
            if cumulative < max_dd:
                max_dd = cumulative

        gross_profit = sum(r["pnl_pct"] for r in wins) if wins else 0
        gross_loss = abs(sum(r["pnl_pct"] for r in losses)) if losses else 1
        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss else 0

        summary = {
            "symbol": symbol,
            "timeframe": timeframe,
            "total_signals": total,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": win_rate,
            "avg_pnl": avg_pnl,
            "total_pnl": total_pnl,
            "max_drawdown": round(max_dd, 2),
            "profit_factor": profit_factor,
            "tp_hits": len(tp_hits),
            "sl_hits": len(sl_hits),
            "expired": total - len(tp_hits) - len(sl_hits),
            "total_fees_pct": round(total * TOTAL_COST_PCT, 2),
        }

        print(f"\n{'='*40}")
        print(f"📊 Backtest: {symbol} [{timeframe}]")
        print(f"{'='*40}")
        print(f"  Signals:       {total}")
        print(f"  Win Rate:      {win_rate}%")
        print(f"  Avg PnL:       {avg_pnl}% (after fees)")
        print(f"  Total PnL:     {total_pnl}%")
        print(f"  Max Drawdown:  {max_dd}%")
        print(f"  Profit Factor: {profit_factor}")
        print(f"  Fees paid:     {summary['total_fees_pct']}%")
        print(f"  TP: {len(tp_hits)} | SL: {len(sl_hits)} | Expired: {summary['expired']}")
        print(f"{'='*40}")

        return summary