"""Signal History & Performance Tracking.

Lưu tín hiệu vào SQLite, track giá, auto trailing stop.
Tối ưu Pi: WAL mode cho concurrent reads, batch commits.
"""

import sqlite3
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path("data/signals.db")


class SignalHistory:

    def __init__(self):
        DB_PATH.parent.mkdir(exist_ok=True)
        self.conn = sqlite3.connect(str(DB_PATH))
        self.conn.row_factory = sqlite3.Row
        # WAL mode — tốt hơn cho concurrent read/write trên Pi
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self._create_tables()

    def _create_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                direction TEXT NOT NULL,
                confidence_pct INTEGER,
                entry REAL,
                stop_loss REAL,
                original_sl REAL,
                tp1 REAL,
                tp2 REAL,
                tp3 REAL,
                risk_reward REAL,
                invalidation REAL,
                reasoning TEXT,
                risk_score INTEGER,
                position_size_pct REAL,
                indicators TEXT,
                status TEXT DEFAULT 'OPEN',
                hit_tp1 INTEGER DEFAULT 0,
                hit_tp2 INTEGER DEFAULT 0,
                hit_tp3 INTEGER DEFAULT 0,
                hit_sl INTEGER DEFAULT 0,
                hit_invalidation INTEGER DEFAULT 0,
                closed_at TEXT,
                close_price REAL,
                pnl_pct REAL
            );
            CREATE TABLE IF NOT EXISTS daily_stats (
                date TEXT PRIMARY KEY,
                total_signals INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0,
                losses INTEGER DEFAULT 0,
                win_rate REAL DEFAULT 0,
                avg_pnl_pct REAL DEFAULT 0
            );
        """)
        # Add original_sl column if missing (migration)
        try:
            self.conn.execute("SELECT original_sl FROM signals LIMIT 1")
        except sqlite3.OperationalError:
            self.conn.execute("ALTER TABLE signals ADD COLUMN original_sl REAL")
        self.conn.commit()

    def save_signal(self, result: dict):
        """Lưu tín hiệu mới."""
        sig = result.get("signal", {})
        sl = result.get("stop_loss")
        self.conn.execute("""
            INSERT INTO signals (
                created_at, symbol, timeframe, direction, confidence_pct,
                entry, stop_loss, original_sl, tp1, tp2, tp3, risk_reward, invalidation,
                reasoning, risk_score, position_size_pct, indicators
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.now(timezone.utc).isoformat(),
            sig.get("symbol", ""),
            sig.get("timeframe", ""),
            result.get("decision", ""),
            result.get("confidence_pct", 0),
            result.get("entry"),
            sl,
            sl,  # original_sl = initial SL
            result.get("take_profit_1"),
            result.get("take_profit_2"),
            result.get("take_profit_3"),
            result.get("risk_reward"),
            result.get("invalidation"),
            result.get("reasoning", ""),
            result.get("risk_score"),
            result.get("position_size_pct"),
            json.dumps(sig.get("indicators", {})),
        ))
        self.conn.commit()

    def update_prices(self, fetch_price_fn) -> list[dict]:
        """Check giá hiện tại vs tín hiệu OPEN, auto trailing stop.

        Returns: list of events (TP hit, SL hit, trailing) cho notification.
        """
        open_signals = self.conn.execute(
            "SELECT * FROM signals WHERE status = 'OPEN'"
        ).fetchall()

        events = []

        for row in open_signals:
            try:
                current_price = fetch_price_fn(row["symbol"])
            except Exception:
                continue

            is_long = row["direction"] == "LONG"
            updates = {}
            event = None

            # Check SL
            if row["stop_loss"]:
                if (is_long and current_price <= row["stop_loss"]) or \
                   (not is_long and current_price >= row["stop_loss"]):
                    updates["hit_sl"] = 1
                    updates["status"] = "CLOSED_SL"
                    updates["close_price"] = current_price
                    event = {"type": "SL_HIT", "signal": dict(row), "price": current_price}

            # Check invalidation
            if row["invalidation"]:
                if (is_long and current_price <= row["invalidation"]) or \
                   (not is_long and current_price >= row["invalidation"]):
                    updates["hit_invalidation"] = 1
                    if "status" not in updates:
                        event = {"type": "INVALIDATION", "signal": dict(row), "price": current_price}

            # Check TPs + auto trailing stop
            if row["tp1"] and not row["hit_tp1"]:
                if (is_long and current_price >= row["tp1"]) or \
                   (not is_long and current_price <= row["tp1"]):
                    updates["hit_tp1"] = 1
                    # Trailing: dời SL về entry (breakeven)
                    if row["entry"]:
                        updates["stop_loss"] = row["entry"]
                    event = {"type": "TP1_HIT", "signal": dict(row), "price": current_price,
                             "trailing": f"SL dời về entry {row['entry']}"}

            if row["tp2"] and not row["hit_tp2"]:
                if (is_long and current_price >= row["tp2"]) or \
                   (not is_long and current_price <= row["tp2"]):
                    updates["hit_tp2"] = 1
                    # Trailing: dời SL lên TP1
                    if row["tp1"]:
                        updates["stop_loss"] = row["tp1"]
                    event = {"type": "TP2_HIT", "signal": dict(row), "price": current_price,
                             "trailing": f"SL dời lên TP1 {row['tp1']}"}

            if row["tp3"] and not row["hit_tp3"]:
                if (is_long and current_price >= row["tp3"]) or \
                   (not is_long and current_price <= row["tp3"]):
                    updates["hit_tp3"] = 1
                    updates["status"] = "CLOSED_TP3"
                    updates["close_price"] = current_price
                    event = {"type": "TP3_HIT", "signal": dict(row), "price": current_price}

            if updates:
                if "close_price" in updates and row["entry"]:
                    if is_long:
                        updates["pnl_pct"] = round(
                            (updates["close_price"] - row["entry"]) / row["entry"] * 100, 2
                        )
                    else:
                        updates["pnl_pct"] = round(
                            (row["entry"] - updates["close_price"]) / row["entry"] * 100, 2
                        )
                    updates["closed_at"] = datetime.now(timezone.utc).isoformat()

                set_clause = ", ".join(f"{k} = ?" for k in updates)
                values = list(updates.values()) + [row["id"]]
                self.conn.execute(
                    f"UPDATE signals SET {set_clause} WHERE id = ?", values
                )

            if event:
                events.append(event)

        self.conn.commit()
        return events

    def get_stats(self, days: int = 30) -> dict:
        """Thống kê performance."""
        rows = self.conn.execute("""
            SELECT * FROM signals
            WHERE created_at >= datetime('now', ?)
            ORDER BY created_at DESC
        """, (f"-{days} days",)).fetchall()

        if not rows:
            return {"total": 0, "message": "Chưa có dữ liệu"}

        total = len(rows)
        closed = [r for r in rows if r["status"] != "OPEN"]
        wins = [r for r in closed if r["pnl_pct"] and r["pnl_pct"] > 0]
        losses = [r for r in closed if r["pnl_pct"] and r["pnl_pct"] <= 0]
        tp1_hits = sum(1 for r in rows if r["hit_tp1"])
        tp2_hits = sum(1 for r in rows if r["hit_tp2"])
        tp3_hits = sum(1 for r in rows if r["hit_tp3"])
        sl_hits = sum(1 for r in rows if r["hit_sl"])

        pnls = [r["pnl_pct"] for r in closed if r["pnl_pct"] is not None]
        avg_pnl = round(sum(pnls) / len(pnls), 2) if pnls else 0
        total_pnl = round(sum(pnls), 2) if pnls else 0
        win_rate = round(len(wins) / len(closed) * 100, 1) if closed else 0

        return {
            "days": days,
            "total": total,
            "open": total - len(closed),
            "closed": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": win_rate,
            "avg_pnl": avg_pnl,
            "total_pnl": total_pnl,
            "tp1_hits": tp1_hits,
            "tp2_hits": tp2_hits,
            "tp3_hits": tp3_hits,
            "sl_hits": sl_hits,
        }

    def get_open_signals(self) -> list:
        rows = self.conn.execute(
            "SELECT * FROM signals WHERE status = 'OPEN' ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_recent(self, limit: int = 10) -> list:
        rows = self.conn.execute(
            "SELECT * FROM signals ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def archive_old(self, keep_days: int = 90) -> str | None:
        """Archive signals cũ ra CSV, xóa khỏi DB."""
        import csv

        rows = self.conn.execute("""
            SELECT * FROM signals
            WHERE created_at < datetime('now', ?)
            AND status != 'OPEN'
        """, (f"-{keep_days} days",)).fetchall()

        if not rows:
            return None

        archive_dir = Path("data/archive")
        archive_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        csv_path = archive_dir / f"signals_{timestamp}.csv"

        columns = rows[0].keys()
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=columns)
            writer.writeheader()
            for row in rows:
                writer.writerow(dict(row))

        self.conn.execute("""
            DELETE FROM signals
            WHERE created_at < datetime('now', ?)
            AND status != 'OPEN'
        """, (f"-{keep_days} days",))
        self.conn.execute("VACUUM")
        self.conn.commit()

        logger.info(f"  📦 Archived {len(rows)} signals → {csv_path}")
        return str(csv_path)

    def db_size(self) -> str:
        size = DB_PATH.stat().st_size if DB_PATH.exists() else 0
        if size < 1024:
            return f"{size} B"
        elif size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        else:
            return f"{size / (1024 * 1024):.1f} MB"

    def close(self):
        self.conn.close()