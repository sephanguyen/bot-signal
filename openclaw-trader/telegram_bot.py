"""Telegram 2-way bot — nhận lệnh từ Telegram.

Thêm: inline keyboard buttons, walk-forward backtest.
"""

import asyncio
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

from config import Config
from signal_history import SignalHistory

logger = logging.getLogger(__name__)

_paused = False
_scan_func = None
_backtest_func = None


def is_authorized(update: Update) -> bool:
    chat_id = update.effective_chat.id if update.effective_chat else None
    return str(chat_id) == str(Config.TELEGRAM_CHAT_ID)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return

    keyboard = [
        [
            InlineKeyboardButton("🔍 Scan", callback_data="scan_all"),
            InlineKeyboardButton("📊 Status", callback_data="status"),
        ],
        [
            InlineKeyboardButton("📜 History", callback_data="history"),
            InlineKeyboardButton("📈 Stats 30d", callback_data="stats_30"),
        ],
        [
            InlineKeyboardButton("📊 Backtest", callback_data="backtest"),
            InlineKeyboardButton("📈 Stats 7d", callback_data="stats_7"),
        ],
        [
            InlineKeyboardButton("⏸️ Pause", callback_data="pause"),
            InlineKeyboardButton("▶️ Resume", callback_data="resume"),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "🐾 *OpenClaw Trader*\n\nChọn lệnh hoặc gõ /command:",
        parse_mode="Markdown",
        reply_markup=reply_markup,
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline button presses."""
    query = update.callback_query
    if not query:
        return

    chat_id = query.message.chat.id if query.message else None
    if str(chat_id) != str(Config.TELEGRAM_CHAT_ID):
        await query.answer("⛔ Unauthorized")
        return

    await query.answer()
    data = query.data

    if data == "scan_all":
        await query.message.reply_text("🔍 Đang scan...")
        try:
            if _scan_func:
                _scan_func()
            await query.message.reply_text("✅ Scan hoàn tất.")
        except Exception as e:
            await query.message.reply_text(f"❌ Lỗi: {e}")

    elif data == "status":
        await _send_status(query.message)

    elif data == "history":
        await _send_history(query.message)

    elif data.startswith("stats_"):
        days = int(data.split("_")[1])
        await _send_stats(query.message, days)

    elif data == "backtest":
        await query.message.reply_text("📊 Đang chạy backtest...")
        try:
            if _backtest_func:
                results = _backtest_func()
                msg = "📊 *Backtest Results:*\n\n"
                for r in results:
                    msg += (
                        f"*{r['symbol']}* [{r['timeframe']}]\n"
                        f"  Win Rate: {r['win_rate']}% | PnL: {r['total_pnl']}%\n"
                        f"  PF: {r['profit_factor']} | MaxDD: {r['max_drawdown']}%\n"
                        f"  Fees: {r.get('total_fees_pct', 0)}%\n\n"
                    )
                await query.message.reply_text(msg, parse_mode="Markdown")
        except Exception as e:
            await query.message.reply_text(f"❌ Lỗi: {e}")

    elif data == "pause":
        global _paused
        _paused = True
        await query.message.reply_text("⏸️ Scheduler đã tạm dừng.")

    elif data == "resume":
        _paused = False
        await query.message.reply_text("▶️ Scheduler đã tiếp tục.")


async def _send_status(message):
    history = SignalHistory()
    open_sigs = history.get_open_signals()
    history.close()

    if not open_sigs:
        await message.reply_text("📭 Không có tín hiệu đang mở.")
        return

    msg = f"📊 *{len(open_sigs)} tín hiệu đang mở:*\n\n"
    for s in open_sigs:
        emoji = "🟢" if s["direction"] == "LONG" else "🔴"
        tp_status = ""
        if s["hit_tp1"]:
            tp_status += "✅1 "
        if s["hit_tp2"]:
            tp_status += "✅2 "
        if s["hit_tp3"]:
            tp_status += "🏆3 "

        # Show trailing info
        sl_info = f"`{s['stop_loss']}`"
        if s.get("original_sl") and s["stop_loss"] != s.get("original_sl"):
            sl_info += f" (trailed from `{s['original_sl']}`)"

        msg += (
            f"{emoji} {s['symbol']} [{s['timeframe']}] {s['direction']}\n"
            f"   Entry: `{s['entry']}` | SL: {sl_info}\n"
            f"   TP: {tp_status or 'chưa hit'}\n\n"
        )
    await message.reply_text(msg, parse_mode="Markdown")


async def _send_history(message):
    history = SignalHistory()
    recent = history.get_recent(10)
    history.close()

    if not recent:
        await message.reply_text("📭 Chưa có lịch sử.")
        return

    msg = "📜 *10 tín hiệu gần nhất:*\n\n"
    for s in recent:
        emoji = "🟢" if s["direction"] == "LONG" else "🔴"
        status = s["status"]
        pnl = f"{s['pnl_pct']:+.2f}%" if s["pnl_pct"] else "—"
        msg += f"{emoji} {s['symbol']} [{s['timeframe']}] {s['direction']} | {status} | {pnl}\n"

    await message.reply_text(msg, parse_mode="Markdown")


async def _send_stats(message, days: int = 30):
    history = SignalHistory()
    stats = history.get_stats(days)
    history.close()

    if stats.get("total", 0) == 0:
        await message.reply_text("📭 Chưa có dữ liệu.")
        return

    msg = f"""📊 *Performance {days} ngày*

Tổng tín hiệu: {stats['total']}
Đang mở: {stats['open']} | Đã đóng: {stats['closed']}
Thắng: {stats['wins']} | Thua: {stats['losses']}
Win Rate: *{stats['win_rate']}%*

Avg PnL: `{stats['avg_pnl']}%`
Total PnL: `{stats['total_pnl']}%`

TP1: {stats['tp1_hits']} | TP2: {stats['tp2_hits']} | TP3: {stats['tp3_hits']}
SL: {stats['sl_hits']}"""

    await message.reply_text(msg, parse_mode="Markdown")


# ── Command handlers (text commands vẫn hoạt động) ──

async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    await update.message.reply_text("🔍 Đang scan...")
    try:
        tf = context.args if context.args else None
        if _scan_func:
            _scan_func(timeframes=tf)
        await update.message.reply_text("✅ Scan hoàn tất.")
    except Exception as e:
        await update.message.reply_text(f"❌ Lỗi: {e}")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    await _send_status(update.message)


async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    await _send_history(update.message)


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    days = int(context.args[0]) if context.args else 30
    await _send_stats(update.message, days)


async def cmd_backtest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    await update.message.reply_text("📊 Đang chạy backtest...")
    try:
        if _backtest_func:
            results = _backtest_func()
            msg = "📊 *Backtest Results:*\n\n"
            for r in results:
                msg += (
                    f"*{r['symbol']}* [{r['timeframe']}]\n"
                    f"  Win Rate: {r['win_rate']}% | PnL: {r['total_pnl']}%\n"
                    f"  PF: {r['profit_factor']} | MaxDD: {r['max_drawdown']}%\n\n"
                )
            await update.message.reply_text(msg, parse_mode="Markdown")
        else:
            await update.message.reply_text("⚠️ Backtest chưa được cấu hình.")
    except Exception as e:
        await update.message.reply_text(f"❌ Lỗi: {e}")


async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    global _paused
    _paused = True
    await update.message.reply_text("⏸️ Scheduler đã tạm dừng. Dùng /resume để tiếp tục.")


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    global _paused
    _paused = False
    await update.message.reply_text("▶️ Scheduler đã tiếp tục.")


def is_paused() -> bool:
    return _paused


def setup_telegram_bot(scan_func=None, backtest_func=None) -> Application:
    global _scan_func, _backtest_func
    _scan_func = scan_func
    _backtest_func = backtest_func

    app = Application.builder().token(Config.TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("start", cmd_help))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("history", cmd_history))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("backtest", cmd_backtest))
    app.add_handler(CommandHandler("pause", cmd_pause))
    app.add_handler(CommandHandler("resume", cmd_resume))
    app.add_handler(CallbackQueryHandler(handle_callback))

    return app