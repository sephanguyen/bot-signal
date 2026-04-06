from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger


def _auto_archive():
    """Archive signals cũ hơn 90 ngày ra CSV."""
    from signal_history import SignalHistory
    history = SignalHistory()
    result = history.archive_old(keep_days=90)
    if result:
        print(f"  📦 Auto-archive done: {result}")
    else:
        print(f"  📦 Nothing to archive. DB size: {history.db_size()}")
    history.close()


def _daily_heartbeat():
    """Gửi heartbeat hàng ngày lên Telegram — xác nhận bot vẫn sống."""
    import asyncio
    from datetime import datetime, timezone
    from signal_history import SignalHistory
    from config import Config

    now = datetime.now(timezone.utc)
    history = SignalHistory()

    # Stats hôm nay
    today_stats = history.get_stats(days=1)
    open_sigs = history.get_open_signals()
    db_size = history.db_size()
    history.close()

    # Check WebSocket
    ws_status = "N/A"
    try:
        from price_stream import PriceStream
        # Nếu có prices → WS đang connected
        ws_status = "🟢 Connected"
    except Exception:
        ws_status = "🔴 Unknown"

    msg = f"""💓 *Daily Heartbeat*
⏰ {now.strftime('%Y-%m-%d %H:%M')} UTC

🤖 Bot: 🟢 Running
📡 WebSocket: {ws_status}
💾 DB: {db_size}

📊 *Hôm nay:*
  Signals: {today_stats.get('total', 0)}
  Approved: {today_stats.get('closed', 0) + today_stats.get('open', 0)}
  Open: {len(open_sigs)}

📋 *Lệnh đang mở:*"""

    if open_sigs:
        for s in open_sigs[:5]:
            emoji = "🟢" if s["direction"] == "LONG" else "🔴"
            tp = ""
            if s.get("hit_tp1"):
                tp += "✅1 "
            if s.get("hit_tp2"):
                tp += "✅2 "
            msg += f"\n  {emoji} {s['symbol']} [{s['timeframe']}] {tp or '—'}"
        if len(open_sigs) > 5:
            msg += f"\n  ... +{len(open_sigs) - 5} more"
    else:
        msg += "\n  Không có"

    _send_telegram(msg)


def _weekly_report():
    """Gửi báo cáo tuần lên Telegram mỗi Chủ Nhật."""
    from signal_history import SignalHistory

    history = SignalHistory()
    stats = history.get_stats(days=7)

    # Tìm best/worst trade
    recent = history.get_recent(limit=100)
    week_trades = [
        r for r in recent
        if r.get("pnl_pct") is not None and r["status"] != "OPEN"
    ]
    history.close()

    if stats.get("total", 0) == 0:
        _send_telegram("📊 *Weekly Report*\n\nKhông có tín hiệu nào tuần này.")
        return

    best = max(week_trades, key=lambda x: x["pnl_pct"]) if week_trades else None
    worst = min(week_trades, key=lambda x: x["pnl_pct"]) if week_trades else None

    msg = f"""📊 *Weekly Report*

📈 *Tổng kết 7 ngày:*
  Signals: {stats['total']}
  Win/Loss: {stats['wins']}/{stats['losses']}
  Win Rate: *{stats['win_rate']}%*
  Total PnL: `{stats['total_pnl']}%`
  Avg PnL: `{stats['avg_pnl']}%`

🎯 *TP/SL:*
  TP1: {stats['tp1_hits']} | TP2: {stats['tp2_hits']} | TP3: {stats['tp3_hits']}
  SL: {stats['sl_hits']}"""

    if best:
        msg += f"""

🏆 *Best trade:*
  {best['symbol']} [{best['timeframe']}] → `{best['pnl_pct']:+.2f}%`"""

    if worst:
        msg += f"""

💀 *Worst trade:*
  {worst['symbol']} [{worst['timeframe']}] → `{worst['pnl_pct']:+.2f}%`"""

    _send_telegram(msg)


def _send_telegram(text: str):
    """Helper gửi message lên Telegram."""
    import asyncio
    from telegram import Bot
    from config import Config

    if not Config.TELEGRAM_BOT_TOKEN or not Config.TELEGRAM_CHAT_ID:
        print(text)
        return

    async def _send():
        bot = Bot(token=Config.TELEGRAM_BOT_TOKEN)
        await bot.send_message(
            chat_id=Config.TELEGRAM_CHAT_ID,
            text=text,
            parse_mode="Markdown",
        )

    asyncio.run(_send())


def setup_scheduler(scan_func):
    """Lập lịch chạy tự động.

    - Khung 4H: chạy mỗi 4 giờ (0, 4, 8, 12, 16, 20 UTC)
    - Khung 1D: chạy lúc 00:05 UTC mỗi ngày (sau khi nến daily đóng)
    """
    scheduler = BlockingScheduler()

    # Quét khung 1H - mỗi giờ
    scheduler.add_job(
        scan_func,
        CronTrigger(minute=5),
        id="scan_1h",
        name="Scan 1H timeframe",
        kwargs={"timeframes": ["1h"]},
    )

    # Quét khung 4H - mỗi 4 giờ
    scheduler.add_job(
        scan_func,
        CronTrigger(hour="0,4,8,12,16,20", minute=7),
        id="scan_4h",
        name="Scan 4H timeframe",
        kwargs={"timeframes": ["4h"]},
    )

    # Quét khung Daily - mỗi ngày lúc 00:10 UTC
    scheduler.add_job(
        scan_func,
        CronTrigger(hour=0, minute=10),
        id="scan_daily",
        name="Scan Daily timeframe",
        kwargs={"timeframes": ["1d"]},
    )

    # Quét full - mỗi ngày lúc 00:15 UTC
    scheduler.add_job(
        scan_func,
        CronTrigger(hour=0, minute=15),
        id="scan_full",
        name="Full scan all timeframes",
    )

    # Archive data cũ - mỗi Chủ Nhật lúc 03:00 UTC
    scheduler.add_job(
        _auto_archive,
        CronTrigger(day_of_week="sun", hour=3, minute=0),
        id="archive",
        name="Archive old signals (>90 days)",
    )

    # Heartbeat - mỗi ngày lúc 08:00 UTC
    scheduler.add_job(
        _daily_heartbeat,
        CronTrigger(hour=8, minute=0),
        id="heartbeat",
        name="Daily heartbeat",
    )

    # Weekly report - mỗi Chủ Nhật lúc 08:30 UTC
    scheduler.add_job(
        _weekly_report,
        CronTrigger(day_of_week="sun", hour=8, minute=30),
        id="weekly_report",
        name="Weekly performance report",
    )

    print("📅 Scheduler configured:")
    for job in scheduler.get_jobs():
        print(f"  - {job.name}: {job.trigger}")

    return scheduler
