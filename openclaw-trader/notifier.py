"""Gửi thông báo tín hiệu lên Telegram.

Bao gồm: signal alerts, SL/TP hit alerts, trailing stop updates.
Dùng httpx sync thay vì asyncio để tránh event loop conflict.
"""

import logging
import requests as _requests
from config import Config

logger = logging.getLogger(__name__)


def _send_telegram(text: str, parse_mode: str = "Markdown") -> bool:
    """Gửi message qua Telegram Bot API bằng HTTP trực tiếp.

    Tránh hoàn toàn asyncio — không bao giờ conflict event loop.
    """
    if not Config.TELEGRAM_BOT_TOKEN or not Config.TELEGRAM_CHAT_ID:
        return False

    url = f"https://api.telegram.org/bot{Config.TELEGRAM_BOT_TOKEN}/sendMessage"

    if len(text) > 4000:
        text = text[:4000] + "\n..."

    # Thử Markdown trước
    try:
        resp = _requests.post(url, json={
            "chat_id": Config.TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": parse_mode,
        }, timeout=15)
        if resp.ok:
            return True
    except Exception as e:
        logger.warning(f"  ⚠️ Telegram Markdown error: {e}")

    # Fallback: plain text (strip markdown chars)
    try:
        clean = text.replace('*', '').replace('_', '').replace('`', '')
        resp = _requests.post(url, json={
            "chat_id": Config.TELEGRAM_CHAT_ID,
            "text": clean,
        }, timeout=15)
        if resp.ok:
            return True
        logger.error(f"  ❌ Telegram error: {resp.text[:200]}")
    except Exception as e:
        logger.error(f"  ❌ Telegram send error: {e}")

    return False


class TelegramNotifier:

    def __init__(self):
        self.enabled = bool(Config.TELEGRAM_BOT_TOKEN and Config.TELEGRAM_CHAT_ID)

    def notify(self, results: list):
        """Gửi kết quả STRONG tier lên Telegram."""
        approved = [r for r in results if r.get("risk_approved")]

        if not approved:
            print("  ℹ️  Không có tín hiệu STRONG nào được approve")
            return

        if not self.enabled:
            self._print_console(approved)
            return

        summary = f"🟢🟢🟢 *STRONG Signal Alert*\n📊 {len(approved)} tín hiệu:\n\n"
        for r in approved:
            sig = r["signal"]
            emoji = "🟢" if r["decision"] == "LONG" else "🔴"
            summary += (
                f"{emoji} {sig['symbol']} [{sig['timeframe']}] "
                f"→ {r['decision']} ({r.get('confidence_pct', 0)}%)\n"
            )
        self._send(summary)

        for r in approved:
            self._send(self._format(r))

    def notify_medium_signals(self, results: list):
        """Gửi MEDIUM tier — AI đã phân tích, user tự quyết định."""
        if not results or not self.enabled:
            return

        msg = f"🟡🟡 *MEDIUM Signal* — Tham khảo, tự quyết định\n"
        msg += f"📊 {len(results)} tín hiệu:\n{'━' * 28}\n\n"

        for r in results:
            sig = r["signal"]
            emoji = "🟢" if r["decision"] == "LONG" else "🔴"
            conf = r.get("confidence_pct", 0)
            approved_str = "✅ AI Approved" if r.get("risk_approved") else "⚠️ AI Cautious"

            msg += f"{emoji} *{sig['symbol']}* [{sig['timeframe']}] → *{r['decision']}*\n"
            msg += f"   Confidence: {conf}% | {approved_str}\n"

            if r.get("entry"):
                msg += f"   Entry: `{r['entry']}`"
                if r.get("stop_loss"):
                    msg += f" | SL: `{r['stop_loss']}`"
                msg += "\n"

            if r.get("take_profit_1"):
                msg += f"   TP1: `{r['take_profit_1']}`"
                if r.get("risk_reward"):
                    msg += f" | R:R: `{r['risk_reward']}`"
                msg += "\n"

            if r.get("reasoning"):
                reason = r["reasoning"][:150]
                msg += f"   💬 _{reason}_\n"

            if r.get("warnings"):
                for w in r["warnings"][:2]:
                    msg += f"   ⚠️ {w}\n"

            msg += "\n"

        msg += f"{'━' * 28}\n"
        msg += "_🟡 Medium = setup chưa hoàn hảo, cần xác nhận thêm._"
        self._send(msg)

    def notify_weak_signals(self, signals: list):
        """Gửi WEAK tier — chỉ summary ngắn, không có AI analysis."""
        if not signals or not self.enabled:
            return

        msg = f"🔵 *WEAK Signals* — Chỉ tham khảo\n{'━' * 28}\n\n"

        for s in signals:
            emoji = "🟢" if s["direction"] == "LONG" else "🔴"
            conf_pct = int(s["confidence"] * 100)
            msg += f"{emoji} {s['symbol']} [{s['timeframe']}] → {s['direction']} ({conf_pct}%)\n"
            msg += f"   Giá: `{s['price']}` | Score: {s['score']}\n"

            # Chỉ hiện indicators chính
            inds = s.get("indicators", {})
            key_inds = []
            for name in ["ichimoku", "macd", "rsi", "market_structure"]:
                if name in inds:
                    key_inds.append(f"{name}: {inds[name]}")
            if key_inds:
                msg += f"   {' | '.join(key_inds)}\n"
            msg += "\n"

        msg += f"{'━' * 28}\n"
        msg += "_🔵 Weak = ít chỉ báo đồng thuận, chờ thêm xác nhận._"
        self._send(msg)

    def notify_events(self, events: list):
        """Gửi alert khi SL/TP hit hoặc trailing stop update."""
        if not events or not self.enabled:
            return

        for event in events:
            msg = self._format_event(event)
            if msg:
                self._send(msg)

    def _format_event(self, event: dict) -> str | None:
        """Format SL/TP event thành Telegram message."""
        sig = event.get("signal", {})
        price = event.get("price", 0)
        event_type = event.get("type", "")

        symbol = sig.get("symbol", "?")
        tf = sig.get("timeframe", "?")
        direction = sig.get("direction", "?")
        entry = sig.get("entry", 0)

        if event_type == "TP1_HIT":
            pnl = round((price - entry) / entry * 100, 2) if entry else 0
            if direction == "SHORT" and entry:
                pnl = round((entry - price) / entry * 100, 2)
            trailing = event.get("trailing", "")
            return (
                f"✅ *TP1 HIT* — {symbol} [{tf}]\n"
                f"{'━' * 24}\n"
                f"Entry: `{entry}` → TP1: `{price}`\n"
                f"PnL: `{pnl:+.2f}%` (40% chốt)\n"
                f"📈 Trailing: {trailing}\n"
                f"{'━' * 24}"
            )

        elif event_type == "TP2_HIT":
            pnl = round((price - entry) / entry * 100, 2) if entry else 0
            if direction == "SHORT" and entry:
                pnl = round((entry - price) / entry * 100, 2)
            trailing = event.get("trailing", "")
            return (
                f"✅✅ *TP2 HIT* — {symbol} [{tf}]\n"
                f"{'━' * 24}\n"
                f"Entry: `{entry}` → TP2: `{price}`\n"
                f"PnL: `{pnl:+.2f}%` (30% chốt)\n"
                f"📈 Trailing: {trailing}\n"
                f"{'━' * 24}"
            )

        elif event_type == "TP3_HIT":
            pnl = round((price - entry) / entry * 100, 2) if entry else 0
            if direction == "SHORT" and entry:
                pnl = round((entry - price) / entry * 100, 2)
            return (
                f"🏆 *TP3 HIT — FULL CLOSE* — {symbol} [{tf}]\n"
                f"{'━' * 24}\n"
                f"Entry: `{entry}` → TP3: `{price}`\n"
                f"PnL: `{pnl:+.2f}%` 🎉\n"
                f"{'━' * 24}"
            )

        elif event_type == "SL_HIT":
            pnl = round((price - entry) / entry * 100, 2) if entry else 0
            if direction == "SHORT" and entry:
                pnl = round((entry - price) / entry * 100, 2)
            original_sl = sig.get("original_sl")
            was_trailed = original_sl and sig.get("stop_loss") != original_sl
            trail_note = " (trailing SL)" if was_trailed else ""
            return (
                f"🛑 *SL HIT{trail_note}* — {symbol} [{tf}]\n"
                f"{'━' * 24}\n"
                f"Entry: `{entry}` → SL: `{price}`\n"
                f"PnL: `{pnl:+.2f}%`\n"
                f"{'━' * 24}"
            )

        elif event_type == "INVALIDATION":
            return (
                f"🚫 *INVALIDATION* — {symbol} [{tf}]\n"
                f"Giá `{price}` đã phá mức invalidation.\n"
                f"⚠️ Cân nhắc đóng lệnh thủ công."
            )

        return None

    def _format(self, r: dict) -> str:
        sig = r["signal"]
        is_long = r["decision"] == "LONG"
        emoji = "🟢" if is_long else "🔴"
        direction = "LONG ↑" if is_long else "SHORT ↓"
        conf = r.get("confidence_pct", 0)
        bar = "█" * (conf // 10) + "░" * (10 - conf // 10)
        tier = sig.get("tier", "STRONG")
        tier_badge = {"STRONG": "🟢🟢🟢", "MEDIUM": "🟡🟡", "WEAK": "🔵"}.get(tier, "")

        msg = f"""{emoji} *{sig['symbol']}* | {sig['timeframe']} | *{direction}*
{tier_badge} *{tier}*
{'━' * 28}

💰 Giá hiện tại: `{sig['price']}`
📊 Confidence: [{bar}] {conf}%
"""

        msg += f"\n{'─' * 28}\n"
        msg += "📋 *TRADING PLAN*\n\n"

        if r.get("entry"):
            msg += f"🎯 *Entry:*  `{r['entry']}`\n"

        if r.get("stop_loss"):
            sl_pct = r.get("sl_pct")
            sl_str = f" ({sl_pct:+.2f}%)" if sl_pct else ""
            msg += f"🛑 *SL:*       `{r['stop_loss']}`{sl_str}\n"

        msg += "\n"

        if r.get("take_profit_1"):
            tp1_pct = r.get("tp1_pct")
            tp1_str = f" ({tp1_pct:+.2f}%)" if tp1_pct else ""
            msg += f"✅ *TP1:*     `{r['take_profit_1']}`{tp1_str}  ← 40%\n"

        if r.get("take_profit_2"):
            tp2_pct = r.get("tp2_pct")
            tp2_str = f" ({tp2_pct:+.2f}%)" if tp2_pct else ""
            msg += f"✅ *TP2:*     `{r['take_profit_2']}`{tp2_str}  ← 30%\n"

        if r.get("take_profit_3"):
            tp3_pct = r.get("tp3_pct")
            tp3_str = f" ({tp3_pct:+.2f}%)" if tp3_pct else ""
            msg += f"🏆 *TP3:*     `{r['take_profit_3']}`{tp3_str}  ← 30%\n"

        if r.get("risk_reward"):
            msg += f"\n📐 *R:R:*     `{r['risk_reward']}`\n"
        if r.get("position_size_pct"):
            msg += f"📏 *Size:*    `{r['position_size_pct']}%` vốn\n"

        if r.get("invalidation"):
            msg += f"\n🚫 *Invalidation:* `{r['invalidation']}`\n"
            msg += "_(Đóng nến qua mức này → hủy setup, không chờ SL)_\n"

        if r.get("trailing_stop_plan"):
            msg += f"\n📈 *Trailing SL:* {r['trailing_stop_plan']}\n"

        msg += f"\n{'─' * 28}\n"
        msg += "📊 *CHỈ BÁO*\n"
        for ind, val in sig["indicators"].items():
            msg += f"  • {ind}: {val}\n"

        if sig.get("atr"):
            msg += f"  • ATR: {sig['atr']} ({sig.get('atr_pct', '?')}%)\n"
        if sig.get("session"):
            msg += f"  • Session: {sig['session']}\n"

        sr = sig.get("support_resistance", {})
        if sr:
            msg += f"\n*S/R:* R2=`{sr.get('r2')}` R1=`{sr.get('r1')}` "
            msg += f"P=`{sr.get('pivot')}` S1=`{sr.get('s1')}` S2=`{sr.get('s2')}`\n"

        # Fibonacci
        fib = sig.get("fibonacci", {})
        if fib:
            msg += f"\n*Fib ({fib.get('trend', '?')}):* "
            msg += f"38.2%=`{fib.get('fib_382')}` 50%=`{fib.get('fib_500')}` 61.8%=`{fib.get('fib_618')}`\n"

        msg += f"\n{'─' * 28}\n"
        reasoning = self._escape_md(r.get('reasoning', 'N/A'))
        msg += f"🤖 *PHÂN TÍCH AI*\n{reasoning}\n"

        if r.get("warnings"):
            msg += f"\n⚠️ *Cảnh báo:*\n"
            for w in r["warnings"]:
                msg += f"  • {self._escape_md(w)}\n"

        msg += f"\n🛡️ Risk Score: {r.get('risk_score', 'N/A')}/10"
        msg += f"\n{'━' * 28}"
        msg += "\n_⚠️ Tín hiệu tham khảo, không phải lời khuyên đầu tư._"
        return msg

    @staticmethod
    def _escape_md(text: str) -> str:
        """Escape ký tự đặc biệt Markdown trong text từ AI."""
        if not text:
            return ""
        for ch in ['*', '_', '`', '[', ']']:
            text = text.replace(ch, '')
        return text

    def _send(self, text: str):
        _send_telegram(text)

    def _print_console(self, results: list):
        print("\n⚠️  Telegram chưa cấu hình - in ra console:\n")
        for r in results:
            sig = r["signal"]
            is_long = r.get("decision") == "LONG"
            emoji = "🟢" if is_long else "🔴"
            print(f"{emoji} {sig['symbol']} [{sig['timeframe']}] → {r.get('decision')}")
            print(f"   Confidence: {r.get('confidence_pct', 0)}% | Price: {sig['price']}")
            if r.get("entry"):
                sl_pct = f" ({r['sl_pct']:+.2f}%)" if r.get("sl_pct") else ""
                print(f"   Entry: {r['entry']}")
                print(f"   SL:    {r.get('stop_loss')}{sl_pct}")
            if r.get("take_profit_1"):
                tp1_pct = f" ({r['tp1_pct']:+.2f}%)" if r.get("tp1_pct") else ""
                print(f"   TP1:   {r['take_profit_1']}{tp1_pct}  ← 40%")
            if r.get("take_profit_2"):
                tp2_pct = f" ({r['tp2_pct']:+.2f}%)" if r.get("tp2_pct") else ""
                print(f"   TP2:   {r['take_profit_2']}{tp2_pct}  ← 30%")
            if r.get("take_profit_3"):
                tp3_pct = f" ({r['tp3_pct']:+.2f}%)" if r.get("tp3_pct") else ""
                print(f"   TP3:   {r['take_profit_3']}{tp3_pct}  ← 30%")
            if r.get("risk_reward"):
                print(f"   R:R:   {r['risk_reward']}")
            print(f"   {r.get('reasoning', '')[:300]}\n")
