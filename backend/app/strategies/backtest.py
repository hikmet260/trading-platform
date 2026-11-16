from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd
import numpy as np

from app.strategies.rules import RuleCondition, StrategyRule


@dataclass(frozen=True)
class SimulatedTrade:
    side: str
    entry_time: int
    exit_time: int
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float
    exit_reason: str


def _indicator(frame: pd.DataFrame, name: str, period: int | None) -> pd.Series:
    if name == "close":
        return frame["close"]
    assert period is not None
    if name == "ema":
        return frame["close"].ewm(span=period, adjust=False, min_periods=period).mean()
    if name == "sma":
        return frame["close"].rolling(period, min_periods=period).mean()
    if name == "rsi":
        changes = frame["close"].diff()
        gains = changes.clip(lower=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
        losses = (-changes.clip(upper=0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
        relative_strength = gains / losses.replace(0, float("nan"))
        result = 100 - (100 / (1 + relative_strength))
        return result.mask((losses == 0) & (gains > 0), 100).mask((losses == 0) & (gains == 0), 50)
    raise ValueError(f"Unsupported indicator '{name}'")


def _condition_series(frame: pd.DataFrame, condition: RuleCondition) -> pd.Series:
    left = _indicator(frame, condition.indicator, condition.period)
    if condition.kind == "threshold":
        assert condition.value is not None
        return {
            "<": left < condition.value,
            "<=": left <= condition.value,
            ">": left > condition.value,
            ">=": left >= condition.value,
        }[condition.operator]
    assert condition.comparison_indicator and condition.comparison_period
    right = _indicator(frame, condition.comparison_indicator, condition.comparison_period)
    if condition.operator == "crosses_above":
        return (left > right) & (left.shift(1) <= right.shift(1))
    return (left < right) & (left.shift(1) >= right.shift(1))


def run_backtest(
    rule: StrategyRule,
    candles: list[dict[str, Any]],
    starting_balance: float = 10_000.0,
    fee_bps: float = 5.0,
) -> dict[str, Any]:
    if starting_balance <= 0 or fee_bps < 0:
        raise ValueError("Starting balance must be positive and fees cannot be negative")
    if len(candles) < 3:
        raise ValueError("At least three candles are required")
    frame = pd.DataFrame(candles)
    required = {"timestamp", "open", "high", "low", "close"}
    if not required.issubset(frame.columns):
        raise ValueError(f"Candles must contain: {', '.join(sorted(required))}")
    numeric = frame[["timestamp", "open", "high", "low", "close"]].apply(pd.to_numeric, errors="coerce")
    if numeric.isna().any().any() or not np.isfinite(numeric.to_numpy()).all():
        raise ValueError("Candle timestamps and prices must be finite numbers")
    frame[["timestamp", "open", "high", "low", "close"]] = numeric
    if (frame[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("Candle prices must be positive")
    frame = frame.sort_values("timestamp", kind="stable").reset_index(drop=True)
    if frame["timestamp"].duplicated().any():
        raise ValueError("Candle timestamps must be unique")
    if ((frame["high"] < frame[["open", "close"]].max(axis=1)) |
            (frame["low"] > frame[["open", "close"]].min(axis=1)) |
            (frame["high"] < frame["low"])).any():
        raise ValueError("Candle high/low values do not contain the open and close")
    signals = pd.Series(True, index=frame.index)
    for condition in rule.conditions:
        signals &= _condition_series(frame, condition).fillna(False)

    equity = starting_balance
    equity_curve = [equity]
    trades: list[SimulatedTrade] = []
    stop_distance = rule.stop_loss_pips * rule.pip_size
    target_distance = rule.take_profit_pips * rule.pip_size
    fee_rate = fee_bps / 10_000
    index = 0
    while index < len(frame) - 1:
        if not bool(signals.iloc[index]):
            index += 1
            continue
        entry_index = index + 1
        entry_bar = frame.iloc[entry_index]
        entry = float(entry_bar["open"])
        is_long = rule.side == "buy"
        stop = entry - stop_distance if is_long else entry + stop_distance
        target = entry + target_distance if is_long else entry - target_distance
        risk_cash = equity * rule.risk_pct / 100
        quantity = risk_cash / stop_distance
        exit_index = len(frame) - 1
        exit_price = float(frame.iloc[exit_index]["close"])
        exit_reason = "end_of_data"

        for candidate in range(entry_index, len(frame)):
            bar = frame.iloc[candidate]
            opened = float(bar["open"])
            if is_long:
                stop_hit, target_hit = float(bar["low"]) <= stop, float(bar["high"]) >= target
                stop_fill = min(stop, opened) if opened < stop else stop
                target_fill = max(target, opened) if opened > target else target
            else:
                stop_hit, target_hit = float(bar["high"]) >= stop, float(bar["low"]) <= target
                stop_fill = max(stop, opened) if opened > stop else stop
                target_fill = min(target, opened) if opened < target else target
            # If OHLC cannot reveal which barrier was first, use the adverse stop fill.
            if stop_hit:
                exit_index, exit_price, exit_reason = candidate, stop_fill, "stop_loss"
                break
            if target_hit:
                exit_index, exit_price, exit_reason = candidate, target_fill, "take_profit"
                break

        direction = 1 if is_long else -1
        gross = quantity * (exit_price - entry) * direction
        fees = quantity * (entry + exit_price) * fee_rate
        pnl = gross - fees
        equity += pnl
        equity_curve.append(equity)
        trades.append(SimulatedTrade(
            side=rule.side, entry_time=int(entry_bar["timestamp"]), exit_time=int(frame.iloc[exit_index]["timestamp"]),
            entry_price=entry, exit_price=exit_price, quantity=quantity, pnl=pnl, exit_reason=exit_reason,
        ))
        index = exit_index + 1

    winners = [trade.pnl for trade in trades if trade.pnl > 0]
    losers = [trade.pnl for trade in trades if trade.pnl < 0]
    peak = starting_balance
    max_drawdown = 0.0
    for mark in equity_curve:
        peak = max(peak, mark)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - mark) / peak * 100)
    total_pnl = equity - starting_balance
    return {
        "symbol": rule.symbol,
        "timeframe": rule.timeframe,
        "starting_balance": starting_balance,
        "ending_balance": equity,
        "total_pnl": total_pnl,
        "total_return_pct": total_pnl / starting_balance * 100,
        "trade_count": len(trades),
        "win_rate_pct": len(winners) / len(trades) * 100 if trades else 0.0,
        "profit_factor": sum(winners) / abs(sum(losers)) if losers else (None if not winners else "infinity"),
        "max_drawdown_pct": max_drawdown,
        "equity_curve": [starting_balance, *[starting_balance + sum(t.pnl for t in trades[:index + 1])
                                                for index in range(len(trades))]],
        "fees_bps": fee_bps,
        "trades": [asdict(trade) for trade in trades],
    }
