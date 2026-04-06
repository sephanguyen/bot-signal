"""Cooldown & Signal Conflict Detection.

- Cooldown: tránh spam tín hiệu cùng pair/timeframe
- Conflict: ưu tiên khung lớn khi có xung đột hướng
"""

from datetime import datetime, timezone, timedelta

# Cooldown periods per timeframe
COOLDOWN_MAP = {
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "1d": timedelta(days=1),
}

# In-memory cooldown tracker
_last_signal: dict[str, dict] = {}


def check_cooldown(symbol: str, timeframe: str, direction: str) -> bool:
    """Return True nếu OK để gửi, False nếu đang cooldown.

    Cho phép gửi nếu:
    - Chưa có tín hiệu trước đó
    - Đã hết cooldown period
    - Hướng đảo chiều (reversal) → luôn cho phép
    """
    key = f"{symbol}:{timeframe}"
    now = datetime.now(timezone.utc)

    if key not in _last_signal:
        return True

    last = _last_signal[key]
    cooldown = COOLDOWN_MAP.get(timeframe, timedelta(hours=4))

    # Reversal → luôn cho phép
    if last["direction"] != direction:
        return True

    # Check cooldown
    elapsed = now - last["time"]
    return elapsed >= cooldown


def record_signal(symbol: str, timeframe: str, direction: str):
    """Ghi nhận tín hiệu đã gửi."""
    key = f"{symbol}:{timeframe}"
    _last_signal[key] = {
        "direction": direction,
        "time": datetime.now(timezone.utc),
    }


def resolve_conflicts(signals: list) -> list:
    """Lọc xung đột giữa các khung thời gian.

    Quy tắc:
    - Nếu 1D và 4H/1H ngược hướng → bỏ 4H/1H
    - Nếu 4H và 1H ngược hướng → bỏ 1H
    - Nếu cùng hướng → giữ tất cả
    """
    # Group by symbol
    by_symbol: dict[str, list] = {}
    for s in signals:
        by_symbol.setdefault(s["symbol"], []).append(s)

    filtered = []
    for symbol, sym_signals in by_symbol.items():
        daily = next((s for s in sym_signals if s["timeframe"] == "1d"), None)
        h4 = next((s for s in sym_signals if s["timeframe"] == "4h"), None)
        h1 = next((s for s in sym_signals if s["timeframe"] == "1h"), None)

        # Always keep daily
        if daily:
            filtered.append(daily)

        # 4H: keep if aligns with daily or no daily
        if h4:
            if daily and daily["direction"] != h4["direction"]:
                print(f"  ⚠️ Conflict: {symbol} 1D={daily['direction']} vs 4H={h4['direction']} → skip 4H")
            else:
                filtered.append(h4)

        # 1H: keep if aligns with 4H (or daily if no 4H)
        if h1:
            ref = h4 or daily
            if ref and ref["direction"] != h1["direction"]:
                print(f"  ⚠️ Conflict: {symbol} {ref['timeframe']}={ref['direction']} vs 1H={h1['direction']} → skip 1H")
            else:
                filtered.append(h1)

    return filtered
