"""
OpenClaw Trader — AI analysis engine.

Hỗ trợ 2 backend:
1. Anthropic: gọi trực tiếp Claude API (cần ANTHROPIC_API_KEY)
2. ZeroClaw: gọi qua ZeroClaw agent local (CLI hoặc gateway)

Retry logic + portfolio exposure awareness.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from pydantic import BaseModel, Field

from config import Config

logger = logging.getLogger(__name__)

# ── Retry config ──────────────────────────────────────────────────────
MAX_RETRIES = 2
RETRY_DELAY = 3  # seconds

# ── Rate limit semaphore ─────────────────────────────────────────────
_ai_semaphore: asyncio.Semaphore | None = None


def _get_semaphore() -> asyncio.Semaphore:
    global _ai_semaphore
    if _ai_semaphore is None:
        _ai_semaphore = asyncio.Semaphore(Config.AI_MAX_CONCURRENT)
    return _ai_semaphore


# ── Load skill prompts từ markdown files ──────────────────────────────

def _load_skill(name: str) -> str:
    path = Path(f"skills/{name}.md")
    if not path.exists():
        return ""
    content = path.read_text()
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            return parts[2].strip()
    return content


PRO_TRADER_PROMPT = _load_skill("pro-trader")
RISK_MANAGER_PROMPT = _load_skill("risk-manager")


# ── Structured Output Models ──────────────────────────────────────────

class TraderDecision(BaseModel):
    decision: str = Field(description="LONG | SHORT | WAIT")
    confidence_pct: int = Field(ge=0, le=100)
    entry: float | None = None
    stop_loss: float | None = None
    take_profit_1: float | None = None
    take_profit_2: float | None = None
    take_profit_3: float | None = None
    risk_reward: float | None = None
    invalidation: float | None = None
    trailing_stop_plan: str = ""
    reasoning: str = ""
    warnings: list[str] = Field(default_factory=list)


class RiskAssessment(BaseModel):
    approved: bool
    risk_score: int = Field(ge=1, le=10)
    reason: str = ""
    adjusted_sl: float | None = None
    adjusted_tp: float | None = None
    position_size_pct: float = Field(ge=0.5, le=5, default=2)
    correlation_warning: str | None = None
    event_warning: str | None = None
    max_drawdown_ok: bool = True
    trailing_stop_approved: bool = True
    warnings: list[str] = Field(default_factory=list)


# ── Helpers ───────────────────────────────────────────────────────────

def _calc_pct(entry: float | None, target: float | None) -> float | None:
    if entry and target and entry != 0:
        return round((target - entry) / entry * 100, 2)
    return None


async def _call_claude_async(
    system: str, prompt: str, model_class: type[BaseModel],
) -> BaseModel | None:
    """Gọi Claude API async, parse JSON → Pydantic model."""
    import anthropic
    client = anthropic.AsyncAnthropic(api_key=Config.ANTHROPIC_API_KEY)

    json_instruction = (
        "\n\nTrả lời CHỈ bằng JSON, không có text khác. Schema:\n"
        + json.dumps(model_class.model_json_schema(), indent=2)
    )

    response = await client.messages.create(
        model=Config.CLAUDE_MODEL,
        max_tokens=1500,
        system=system,
        messages=[{"role": "user", "content": prompt + json_instruction}],
    )

    raw = response.content[0].text
    try:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            data = json.loads(raw[start:end])
            return model_class.model_validate(data)
    except (json.JSONDecodeError, Exception):
        pass
    return None


async def _call_zeroclaw_async(
    system: str, prompt: str, model_class: type[BaseModel],
) -> BaseModel | None:
    """Gọi ZeroClaw agent, parse JSON → Pydantic model."""
    from zeroclaw_client import ZeroClawClient
    client = ZeroClawClient()
    return await client.ask(system, prompt, model_class)


async def _call_ai_async(
    system: str, prompt: str, model_class: type[BaseModel],
) -> BaseModel | None:
    """Route tới backend phù hợp, với retry logic + rate limiting."""
    call_fn = _call_zeroclaw_async if Config.AI_BACKEND == "zeroclaw" else _call_claude_async
    sem = _get_semaphore()

    async with sem:
        for attempt in range(MAX_RETRIES + 1):
            try:
                result = await call_fn(system, prompt, model_class)
                if result is not None:
                    # Delay giữa các calls để tránh rate limit
                    await asyncio.sleep(Config.AI_CALL_DELAY)
                    return result
                if attempt < MAX_RETRIES:
                    logger.warning(f"  ⚠️ AI returned None, retry {attempt + 1}/{MAX_RETRIES}...")
                    await asyncio.sleep(RETRY_DELAY)
            except Exception as e:
                if attempt < MAX_RETRIES:
                    logger.warning(f"  ⚠️ AI error: {e}, retry {attempt + 1}/{MAX_RETRIES}...")
                    await asyncio.sleep(RETRY_DELAY * (attempt + 1))
                else:
                    logger.error(f"  ❌ AI failed after {MAX_RETRIES + 1} attempts: {e}")
    return None


def _get_portfolio_exposure() -> dict:
    """Lấy thông tin exposure hiện tại từ DB."""
    try:
        from signal_history import SignalHistory
        history = SignalHistory()
        open_sigs = history.get_open_signals()
        history.close()

        total_exposure = sum(s.get("position_size_pct", 2) for s in open_sigs)
        long_count = sum(1 for s in open_sigs if s["direction"] == "LONG")
        short_count = sum(1 for s in open_sigs if s["direction"] == "SHORT")

        return {
            "open_count": len(open_sigs),
            "total_exposure_pct": round(total_exposure, 1),
            "long_count": long_count,
            "short_count": short_count,
            "symbols": [s["symbol"] for s in open_sigs],
        }
    except Exception:
        return {"open_count": 0, "total_exposure_pct": 0, "long_count": 0, "short_count": 0, "symbols": []}


class OpenClawTrader:
    """AI analysis engine với retry và portfolio awareness."""

    async def analyze_signal(
        self,
        signal: dict,
        daily_ref: dict | None = None,
        hourly_ref: dict | None = None,
        all_signals: list | None = None,
    ) -> dict:
        prompt = self._build_trader_prompt(signal, daily_ref, hourly_ref)

        # Step 1: Pro Trader
        print(f"  🧠 Pro Trader: {signal['symbol']} [{signal['timeframe']}]...")
        decision = await _call_ai_async(PRO_TRADER_PROMPT, prompt, TraderDecision)

        if not decision:
            return {
                "signal": signal, "decision": "WAIT", "confidence_pct": 0,
                "reasoning": "Failed to parse AI response", "risk_approved": False,
            }

        # Step 2: Risk Manager
        print(f"  🛡️ Risk Manager: evaluating...")
        risk_prompt = self._build_risk_prompt(signal, decision, all_signals)
        risk = await _call_ai_async(RISK_MANAGER_PROMPT, risk_prompt, RiskAssessment)

        approved = risk.approved if risk else False
        final_sl = (risk.adjusted_sl or decision.stop_loss) if risk else decision.stop_loss
        final_tp1 = (risk.adjusted_tp or decision.take_profit_1) if risk else decision.take_profit_1

        all_warnings = list(decision.warnings)
        if risk:
            all_warnings += risk.warnings
            if risk.correlation_warning:
                all_warnings.append(f"📊 Correlation: {risk.correlation_warning}")
            if risk.event_warning:
                all_warnings.append(f"📅 Event: {risk.event_warning}")

        return {
            "signal": signal,
            "decision": decision.decision,
            "confidence_pct": decision.confidence_pct,
            "entry": decision.entry,
            "stop_loss": final_sl,
            "take_profit_1": final_tp1,
            "take_profit_2": decision.take_profit_2,
            "take_profit_3": decision.take_profit_3,
            "risk_reward": decision.risk_reward,
            "invalidation": decision.invalidation,
            "trailing_stop_plan": decision.trailing_stop_plan,
            "sl_pct": _calc_pct(decision.entry, final_sl),
            "tp1_pct": _calc_pct(decision.entry, final_tp1),
            "tp2_pct": _calc_pct(decision.entry, decision.take_profit_2),
            "tp3_pct": _calc_pct(decision.entry, decision.take_profit_3),
            "reasoning": decision.reasoning,
            "warnings": all_warnings,
            "risk_approved": approved,
            "risk_score": risk.risk_score if risk else 10,
            "position_size_pct": risk.position_size_pct if risk else 0,
        }

    async def analyze_batch(self, signals: list) -> list:
        """Phân tích song song nhiều signals cùng lúc."""
        by_symbol: dict[str, list] = {}
        for s in signals:
            by_symbol.setdefault(s["symbol"], []).append(s)

        tasks = []
        task_info = []
        for symbol, sym_signals in by_symbol.items():
            daily = next((s for s in sym_signals if s["timeframe"] == "1d"), None)
            hourly = next((s for s in sym_signals if s["timeframe"] == "1h"), None)

            for sig in sym_signals:
                daily_ref = daily if sig["timeframe"] != "1d" else None
                hourly_ref = hourly if sig["timeframe"] not in ("1d", "1h") else None
                tasks.append(
                    self.analyze_signal(sig, daily_ref, hourly_ref, all_signals=signals)
                )
                task_info.append((symbol, sig["timeframe"]))

        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        results = []
        for i, raw in enumerate(raw_results):
            symbol, tf = task_info[i]
            if isinstance(raw, Exception):
                logger.error(f"  ✗ Error: {symbol} [{tf}] - {raw}")
                print(f"  ✗ Error: {symbol} [{tf}] - {raw}")
                sig = next(
                    s for s in signals
                    if s["symbol"] == Config.SYMBOL_DISPLAY.get(symbol, symbol)
                    and s["timeframe"] == tf
                )
                results.append({
                    "signal": sig, "decision": "WAIT",
                    "reasoning": str(raw), "risk_approved": False,
                })
            else:
                status = "✅" if raw["risk_approved"] else "❌"
                print(f"  {status} {symbol} [{tf}] → {raw['decision']}")
                results.append(raw)

        return results

    def _build_trader_prompt(
        self, signal: dict, daily_ref: dict | None, hourly_ref: dict | None,
    ) -> str:
        prompt = f"""Phân tích tín hiệu giao dịch:

Symbol: {signal['symbol']} | Khung: {signal['timeframe']}
Giá: {signal['price']}
Hướng sơ bộ: {signal['direction']} | Confidence: {signal['confidence']}
Score: {signal['score']}
Session: {signal.get('session', 'N/A')}

Chỉ báo:
"""
        for ind, val in signal["indicators"].items():
            prompt += f"  - {ind}: {val}\n"

        sr = signal["support_resistance"]
        prompt += f"""
S/R: R2={sr['r2']} R1={sr['r1']} Pivot={sr['pivot']} S1={sr['s1']} S2={sr['s2']}
Volume Ratio: {signal.get('volume_ratio', 'N/A')}x
Cloud: {'Bullish' if signal.get('cloud_bullish') else 'Bearish'}
ATR: {signal.get('atr', 'N/A')} ({signal.get('atr_pct', 'N/A')}%)
Market Structure: {signal.get('market_structure', 'N/A')}
Invalidation (từ scanner): {signal.get('invalidation', 'N/A')}
"""
        # Fibonacci levels
        fib = signal.get("fibonacci", {})
        if fib:
            prompt += f"\nFibonacci ({fib.get('trend', '?')}): "
            prompt += f"0%={fib.get('fib_0')} 23.6%={fib.get('fib_236')} "
            prompt += f"38.2%={fib.get('fib_382')} 50%={fib.get('fib_500')} "
            prompt += f"61.8%={fib.get('fib_618')} 78.6%={fib.get('fib_786')}\n"

        prompt += "\n--- Multi-Timeframe ---\n"
        if daily_ref:
            prompt += f"Daily: {daily_ref['direction']} (conf: {daily_ref['confidence']})\n"
            prompt += f"  Ichimoku: {daily_ref['indicators'].get('ichimoku', 'N/A')}\n"
            prompt += f"  MACD: {daily_ref['indicators'].get('macd', 'N/A')}\n"
            prompt += f"  MS: {daily_ref.get('market_structure', 'N/A')}\n"
        if hourly_ref:
            prompt += f"1H: {hourly_ref['direction']} (conf: {hourly_ref['confidence']})\n"
            prompt += f"  Ichimoku: {hourly_ref['indicators'].get('ichimoku', 'N/A')}\n"
            prompt += f"  MS: {hourly_ref.get('market_structure', 'N/A')}\n"
        return prompt

    def _build_risk_prompt(
        self, signal: dict, decision: TraderDecision, all_signals: list | None,
    ) -> str:
        # Portfolio exposure context
        exposure = _get_portfolio_exposure()

        prompt = f"""Đánh giá rủi ro:

Symbol: {signal['symbol']} | Khung: {signal['timeframe']}
Giá: {signal['price']}
Quyết định: {decision.decision} | Confidence: {decision.confidence_pct}%
Entry: {decision.entry} | SL: {decision.stop_loss}
TP1: {decision.take_profit_1} | TP2: {decision.take_profit_2} | TP3: {decision.take_profit_3}
R:R: {decision.risk_reward}
Invalidation: {decision.invalidation}
Trailing Stop: {decision.trailing_stop_plan}
ATR: {signal.get('atr', 'N/A')} ({signal.get('atr_pct', 'N/A')}%)
Session: {signal.get('session', 'N/A')}

--- Portfolio Exposure ---
Lệnh đang mở: {exposure['open_count']}
Tổng exposure: {exposure['total_exposure_pct']}% vốn
Long: {exposure['long_count']} | Short: {exposure['short_count']}
Symbols đang mở: {exposure['symbols']}

Chỉ báo: {json.dumps(signal['indicators'])}
S/R: {json.dumps(signal['support_resistance'])}
Reasoning: {decision.reasoning}
"""
        if all_signals and len(all_signals) > 1:
            prompt += "\n--- Correlation ---\n"
            for s in all_signals:
                if s["symbol"] != signal["symbol"] or s["timeframe"] != signal["timeframe"]:
                    prompt += f"  {s['symbol']} [{s['timeframe']}]: {s['direction']} (conf: {s['confidence']})\n"
        return prompt
