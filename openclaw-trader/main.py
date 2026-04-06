#!/usr/bin/env python3
"""
🐾 OpenClaw Trader — Personal AI Trading Assistant

Usage:
  python main.py scan                     # Full scan
  python main.py scan -tf 4h 1h           # Scan specific timeframes
  python main.py collect                  # Collect data ($0 token)
  python main.py backtest                 # Backtest all pairs
  python main.py backtest -s BTCUSDT      # Backtest specific symbol
  python main.py backtest --walk-forward  # Walk-forward validation
  python main.py bot                      # Telegram 2-way bot + scheduler
  python main.py schedule                 # Scheduler only
  python main.py stats                    # Performance stats
  python main.py test                     # Test connections
"""

import asyncio
import argparse
import logging
import time
from datetime import datetime
from pathlib import Path

from config import Config
from data_fetcher import DataFetcher
from signal_scanner import SignalScanner
from signal_history import SignalHistory
from openclaw_trader import OpenClawTrader
from notifier import TelegramNotifier
from cooldown import check_cooldown, record_signal, resolve_conflicts
from scheduler import setup_scheduler


# ── Structured logging ────────────────────────────────────────────────

def _setup_logging():
    """Setup logging với file rotation — tối ưu cho Pi."""
    from logging.handlers import RotatingFileHandler

    log_dir = Path("data/logs")
    log_dir.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # File handler — max 5MB, giữ 3 files
    file_handler = RotatingFileHandler(
        log_dir / "openclaw.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)

    # Console handler — chỉ WARNING+
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.WARNING)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)


logger = logging.getLogger(__name__)


def scan(timeframes: list = None):
    """Full pipeline."""
    start = datetime.utcnow()
    displays = [Config.SYMBOL_DISPLAY.get(s, s) for s in Config.SYMBOLS]
    print(f"\n{'='*60}")
    print(f"🐾 Full Scan | {start.strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"📋 {displays}")
    print(f"{'='*60}")

    try:
        from telegram_bot import is_paused
        if is_paused():
            print("  ⏸️ Paused via Telegram.")
            return
    except ImportError:
        pass

    orig_tf = Config.TIMEFRAMES
    if timeframes:
        Config.TIMEFRAMES = timeframes

    history = SignalHistory()

    try:
        print("\n📥 Fetching...")
        data = DataFetcher().fetch_all()

        print(f"\n📊 Scanning...")
        signals = SignalScanner().scan_all(data)
        print(f"  {len(signals)} raw signals")

        if not signals:
            print("  ℹ️  No signals.")
            return

        # Classify by tier
        strong = [s for s in signals if s.get("tier") == "STRONG"]
        medium = [s for s in signals if s.get("tier") == "MEDIUM"]
        weak = [s for s in signals if s.get("tier") == "WEAK"]
        print(f"  Tiers: 🟢 {len(strong)} Strong | 🟡 {len(medium)} Medium | 🔵 {len(weak)} Weak")

        notifier = TelegramNotifier()

        # ── WEAK tier: chỉ gửi summary, không gọi AI ──
        if weak and Config.TIER_NOTIFY_ALL:
            notifier.notify_weak_signals(weak)

        # ── MEDIUM + STRONG: qua conflict resolution + cooldown ──
        ai_candidates = strong + medium
        if not ai_candidates:
            print("  ℹ️  No signals for AI analysis.")
            return

        ai_candidates = resolve_conflicts(ai_candidates)
        print(f"  {len(ai_candidates)} after conflict resolution")

        active = []
        for s in ai_candidates:
            if check_cooldown(s["symbol"], s["timeframe"], s["direction"]):
                active.append(s)
            else:
                print(f"  ⏸️ Cooldown: {s['symbol']} [{s['timeframe']}]")
        ai_candidates = active

        if not ai_candidates:
            print("  ℹ️  All signals on cooldown.")
            return

        print(f"\n🐾 AI analyzing {len(ai_candidates)} signals...")
        results = asyncio.run(OpenClawTrader().analyze_batch(ai_candidates))

        # Separate results by original tier
        strong_results = []
        medium_results = []
        for r in results:
            sig_tier = r.get("signal", {}).get("tier", "STRONG")
            if sig_tier == "STRONG":
                strong_results.append(r)
            else:
                medium_results.append(r)

        # ── STRONG: approved → save DB + full notify ──
        approved = [r for r in strong_results if r.get("risk_approved")]
        print(f"\n📨 STRONG: {len(approved)} approved / {len(strong_results)} total")

        for r in approved:
            history.save_signal(r)
            record_signal(
                r["signal"]["symbol"], r["signal"]["timeframe"], r["decision"]
            )

        notifier.notify(strong_results)

        # ── MEDIUM: gửi tất cả (approved hay không), user tự quyết ──
        if medium_results:
            print(f"📨 MEDIUM: {len(medium_results)} signals (user decides)")
            notifier.notify_medium_signals(medium_results)

        _update_tracking(history)

        elapsed = (datetime.utcnow() - start).total_seconds()
        print(f"\n✅ Done in {elapsed:.1f}s")
        logger.info(f"Scan complete: {len(approved)} approved, {elapsed:.1f}s")

    except Exception as e:
        print(f"\n❌ Error: {e}")
        logger.error(f"Scan error: {e}", exc_info=True)
        import traceback
        traceback.print_exc()
    finally:
        Config.TIMEFRAMES = orig_tf
        history.close()


def _update_tracking(history: SignalHistory):
    """Update open signals + send SL/TP alerts."""
    fetcher = DataFetcher()
    symbol_map = {v: k for k, v in Config.SYMBOL_DISPLAY.items()}

    def fetch_price(display_name):
        binance_sym = symbol_map.get(display_name, display_name)
        return fetcher.fetch_price(binance_sym)

    try:
        events = history.update_prices(fetch_price)
        if events:
            notifier = TelegramNotifier()
            notifier.notify_events(events)
            for e in events:
                logger.info(f"Event: {e['type']} - {e['signal'].get('symbol', '?')}")
    except Exception as e:
        print(f"  ⚠️ Tracking update error: {e}")
        logger.warning(f"Tracking error: {e}")


def collect(interval: int = None):
    """Collect data — $0 token cost."""
    interval = interval or Config.COLLECT_INTERVAL
    displays = [Config.SYMBOL_DISPLAY.get(s, s) for s in Config.SYMBOLS]
    print(f"\n📡 Collect Mode | Interval: {interval}s | $0 token")
    print(f"📋 {displays}\n")

    fetcher = DataFetcher()
    scanner = SignalScanner()
    cycle = 0

    try:
        while True:
            cycle += 1
            now = datetime.utcnow()
            print(f"[{now.strftime('%H:%M:%S')}] #{cycle}")
            try:
                data = fetcher.fetch_all()
                signals = scanner.scan_all(data)
                if signals:
                    print(f"  🎯 {len(signals)} signals:")
                    for s in signals:
                        emoji = "🟢" if s["direction"] == "LONG" else "🔴"
                        tier_icon = {"STRONG": "🟢🟢🟢", "MEDIUM": "🟡🟡", "WEAK": "🔵"}.get(s.get("tier", ""), "")
                        print(f"    {emoji} {s['symbol']} [{s['timeframe']}] "
                              f"→ {s['direction']} ({s['confidence']*100:.0f}%) "
                              f"{tier_icon} {s.get('tier', '')} | {s['price']}")
                else:
                    print("  ⏸️ No signals")
            except Exception as e:
                print(f"  ✗ {e}")
            time.sleep(interval)
    except KeyboardInterrupt:
        print(f"\n👋 Stopped after {cycle} cycles.")


def run_backtest(symbols: list = None, walk_forward: bool = False):
    """Backtest all pairs."""
    from backtest import Backtester
    bt = Backtester()
    symbols = symbols or Config.SYMBOLS
    results = []
    for symbol in symbols:
        for tf in Config.TIMEFRAMES:
            if walk_forward:
                r = bt.run_walk_forward(symbol, tf)
            else:
                r = bt.run(symbol, tf)
            if r and r.get("total_signals", 0) > 0:
                results.append(r)
    return results


def show_stats(days: int = 30):
    """Show performance stats."""
    history = SignalHistory()
    stats = history.get_stats(days)
    history.close()

    if stats.get("total", 0) == 0:
        print("📭 Chưa có dữ liệu.")
        return

    print(f"\n📊 Performance {days} ngày")
    print(f"{'='*40}")
    print(f"  Tổng:      {stats['total']}")
    print(f"  Mở:        {stats['open']} | Đóng: {stats['closed']}")
    print(f"  Thắng:     {stats['wins']} | Thua: {stats['losses']}")
    print(f"  Win Rate:  {stats['win_rate']}%")
    print(f"  Avg PnL:   {stats['avg_pnl']}%")
    print(f"  Total PnL: {stats['total_pnl']}%")
    print(f"  TP1: {stats['tp1_hits']} | TP2: {stats['tp2_hits']} | TP3: {stats['tp3_hits']} | SL: {stats['sl_hits']}")


def run_bot():
    """Start Telegram 2-way bot + scheduler + WebSocket stream."""
    from telegram_bot import setup_telegram_bot
    from price_stream import PriceStream
    import threading

    print("🐾 OpenClaw Trader — Bot Mode")
    print(f"📋 Symbols: {Config.SYMBOLS}")
    print(f"⏱️  Timeframes: {Config.TIMEFRAMES}")

    def on_price(symbol, price):
        pass

    def on_kline_close(symbol, timeframe, close_price, volume):
        display = Config.SYMBOL_DISPLAY.get(symbol, symbol)
        print(f"\n🕯️ Candle closed: {display} [{timeframe}] @ {close_price}")
        logger.info(f"Kline close: {display} [{timeframe}] @ {close_price}")
        try:
            scan(timeframes=[timeframe])
        except Exception as e:
            print(f"  ✗ Event scan error: {e}")
            logger.error(f"Event scan error: {e}")

    stream = PriceStream(on_price=on_price, on_kline_close=on_kline_close)
    stream.start()

    # ── Periodic SL/TP tracking with alerts ──
    def tracking_loop():
        notifier = TelegramNotifier()
        while True:
            time.sleep(60)
            try:
                history = SignalHistory()
                ws_prices = stream.prices
                if ws_prices:
                    symbol_map = {v: k for k, v in Config.SYMBOL_DISPLAY.items()}

                    def fetch_price(display_name):
                        binance_sym = symbol_map.get(display_name, display_name)
                        price = ws_prices.get(binance_sym.lower())
                        if price:
                            return price
                        return DataFetcher().fetch_price(binance_sym)

                    events = history.update_prices(fetch_price)
                    # Send SL/TP alerts immediately
                    if events:
                        notifier.notify_events(events)
                        for e in events:
                            logger.info(f"Tracking event: {e['type']} - {e['signal'].get('symbol', '?')}")
                history.close()
            except Exception as e:
                logger.error(f"Tracking loop error: {e}")

    tracking_thread = threading.Thread(target=tracking_loop, daemon=True)
    tracking_thread.start()
    print("🔄 SL/TP tracking + alerts started (60s interval)")

    scheduler = setup_scheduler(scan)

    def run_scheduler():
        try:
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            pass

    sched_thread = threading.Thread(target=run_scheduler, daemon=True)
    sched_thread.start()
    print("📅 Scheduler started (fallback)")

    app = setup_telegram_bot(
        scan_func=scan,
        backtest_func=run_backtest,
    )
    print("🤖 Telegram bot listening...")
    print(f"{'='*60}")
    app.run_polling(drop_pending_updates=True)


def test_connections():
    """Test connections."""
    print("🧪 Testing...\n")
    fetcher = DataFetcher()
    for symbol in Config.SYMBOLS:
        display = Config.SYMBOL_DISPLAY.get(symbol, symbol)
        try:
            df = fetcher.fetch_ohlcv(symbol, "1d")
            price = df["close"].iloc[-1]
            print(f"  ✓ {display}: {len(df)} candles, price: {price:.2f}")
        except Exception as e:
            print(f"  ✗ {display}: {e}")

    # Funding rate test
    print("\n  📊 Order Flow:")
    for symbol in Config.SYMBOLS:
        display = Config.SYMBOL_DISPLAY.get(symbol, symbol)
        fr = fetcher.fetch_funding_rate(symbol)
        ls = fetcher.fetch_long_short_ratio(symbol)
        if fr:
            print(f"    {display} Funding: {fr['rate']}% ({fr['signal']})")
        if ls:
            print(f"    {display} L/S: {ls['ratio']} (L:{ls['long_pct']}% S:{ls['short_pct']}%)")

    print(f"\n  AI Backend: {Config.AI_BACKEND}")

    if Config.AI_BACKEND == "zeroclaw":
        _test_zeroclaw()
    else:
        print(f"  Anthropic: {'✓ Key set' if Config.ANTHROPIC_API_KEY else '⚠️ No key'}")

    print(f"  Telegram: {'✓ Configured' if Config.TELEGRAM_BOT_TOKEN else '⚠️ Not set'}")
    print("\n✅ Done.")


def _test_zeroclaw():
    """Test ZeroClaw connection."""
    from zeroclaw_client import ZeroClawClient
    client = ZeroClawClient()
    health = client.health_check()

    if health["available"]:
        print(f"  ZeroClaw: ✓ Available (mode: {health['mode']})")
        if health.get("version"):
            print(f"  Version: {health['version']}")
        if health.get("cli_status"):
            print(f"  Status: {health['cli_status'][:100]}")
    else:
        print(f"  ZeroClaw: ❌ Not available (mode: {health['mode']})")
        print("  → Install: https://github.com/zeroclaw-labs/zeroclaw")
        print("  → Or switch to Anthropic: AI_BACKEND=anthropic")


def main():
    _setup_logging()

    parser = argparse.ArgumentParser(description="🐾 OpenClaw Trader")
    parser.add_argument("mode", choices=["scan", "collect", "backtest", "bot", "schedule", "stats", "test"])
    parser.add_argument("--timeframes", "-tf", nargs="+", default=None)
    parser.add_argument("--symbols", "-s", nargs="+", default=None)
    parser.add_argument("--interval", "-i", type=int, default=None)
    parser.add_argument("--days", "-d", type=int, default=30)
    parser.add_argument("--walk-forward", "-wf", action="store_true", help="Walk-forward backtest")
    args = parser.parse_args()

    if args.mode == "scan":
        scan(timeframes=args.timeframes)
    elif args.mode == "collect":
        collect(interval=args.interval)
    elif args.mode == "backtest":
        run_backtest(symbols=args.symbols, walk_forward=args.walk_forward)
    elif args.mode == "bot":
        run_bot()
    elif args.mode == "schedule":
        scheduler = setup_scheduler(scan)
        try:
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            print("\n👋 Stopped.")
    elif args.mode == "stats":
        show_stats(days=args.days)
    elif args.mode == "test":
        test_connections()


if __name__ == "__main__":
    main()