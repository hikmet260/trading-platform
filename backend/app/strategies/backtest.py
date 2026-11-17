from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd
import numpy as np

from app.instruments import (
    InstrumentError,
    notional_in_account,
    quote_to_account_rate,
    resolve_instrument,
    round_quantity,
)
from app.strategies.rules import RuleCondition, StrategyRule

_TIMEFRAME_SECONDS = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1_800,
    "1h": 3_600, "4h": 14_400, "1d": 86_400,
}


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
    gross_pnl: float = 0.0
    fees: float = 0.0
    funding: float = 0.0
    risk_amount: float = 0.0
    notional: float = 0.0


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


def _signal_series(frame: pd.DataFrame, conditions: tuple[RuleCondition, ...]) -> pd.Series:
    signal = pd.Series(True, index=frame.index)
    for condition in conditions:
        signal &= _condition_series(frame, condition).fillna(False)
    return signal


def _price_fill(price: float, side: str, cost_bps: float) -> float:
    """Apply half-spread plus slippage adversely to an opening or closing fill."""
    cost = cost_bps / 10_000
    if side == "buy":
        return price * (1 + cost)
    return price * (1 - cost)


def run_backtest(
    rule: StrategyRule,
    candles: list[dict[str, Any]],
    starting_balance: float = 10_000.0,
    fee_bps: float = 5.0,
    *,
    spread_bps: float = 0.0,
    slippage_bps: float = 0.0,
    funding_bps_per_day: float = 0.0,
    max_leverage: float | None = None,
    account_currency: str = "USD",
    max_trade_risk_pct: float = 1.0,
    max_open_risk_pct: float = 2.0,
) -> dict[str, Any]:
    """Event-driven backtest with contract sizing and realistic frictions.

    Signals are evaluated on closed candles and fills occur at the next open.
    When OHLC cannot reveal whether the stop or target was hit first, the stop
    is assumed to have been hit first (conservative).  Commissions are charged
    on entry and exit notionals, spread/slippage are applied adversely, and
    per-bar funding is deducted for the holding period.
    """
    for name, value in {
        "starting_balance": starting_balance, "fee_bps": fee_bps,
        "spread_bps": spread_bps, "slippage_bps": slippage_bps,
    }.items():
        if value < 0:
            raise ValueError(f"{name} cannot be negative")
    if starting_balance <= 0:
        raise ValueError("Starting balance must be positive")
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
    eps = 1e-9
    if ((frame["high"] < frame[["open", "close"]].max(axis=1) - eps) |
            (frame["low"] > frame[["open", "close"]].min(axis=1) + eps) |
            (frame["high"] < frame["low"] - eps)).any():
        raise ValueError("Candle high/low values do not contain the open and close")

    try:
        spec = resolve_instrument(rule.symbol)
    except InstrumentError as exc:
        raise ValueError(str(exc)) from exc
    conversion_rate = quote_to_account_rate(spec, float(frame["close"].iloc[len(frame) // 2]), account_currency)
    bar_seconds = _TIMEFRAME_SECONDS.get(rule.timeframe, 60)
    gaps = frame["timestamp"].diff().dropna()
    gap_count = int((gaps > bar_seconds * 1_000 * 1.5).sum())
    max_gap_seconds = float(gaps.max() / 1000) if not gaps.empty else 0.0
    warnings: list[str] = []
    if gap_count:
        warnings.append(f"{gap_count} candle gap(s) exceed 1.5x the configured timeframe")

    long_signals = _signal_series(frame, rule.conditions)
    if rule.side == "both":
        short_signals = _signal_series(frame, rule.short_conditions or ())
    elif rule.side == "sell":
        short_signals = long_signals.copy()
        long_signals = pd.Series(False, index=frame.index)
    else:
        short_signals = pd.Series(False, index=frame.index)

    stop_distance = rule.stop_loss_pips * rule.pip_size
    target_distance = rule.take_profit_pips * rule.pip_size
    fee_rate = fee_bps / 10_000
    funding_rate = funding_bps_per_day / 10_000
    fill_cost_bps = max(0.0, spread_bps) / 2 + max(0.0, slippage_bps)
    leverage = float(max_leverage if max_leverage is not None else spec.leverage_hint)
    if leverage <= 0:
        raise ValueError("max_leverage must be positive")

    equity = float(starting_balance)
    equity_curve = [equity]
    trades: list[SimulatedTrade] = []
    rejected_signals = 0
    total_fees = 0.0
    total_funding = 0.0

    def _entry_quantity(entry: float, fixed_quantity: float | None) -> tuple[float, float, float] | None:
        rate = quote_to_account_rate(spec, entry, account_currency)
        per_unit = stop_distance * spec.contract_size * rate
        if per_unit <= 0:
            return None
        if fixed_quantity is not None:
            quantity = round_quantity(spec, fixed_quantity)
            if quantity < spec.min_quantity:
                return None
            risk_amount = quantity * per_unit
            if risk_amount > equity * max_trade_risk_pct / 100 + 1e-9:
                return None
        else:
            risk_amount = equity * rule.risk_pct / 100
            quantity = round_quantity(spec, risk_amount / per_unit)
            if quantity < spec.min_quantity:
                return None
            risk_amount = quantity * per_unit
        notional = notional_in_account(spec, quantity, entry, account_currency)
        if notional > equity * leverage + 1e-9:
            cap = round_quantity(spec, (equity * leverage) / (spec.contract_size * entry * rate))
            if cap < spec.min_quantity:
                return None
            quantity = cap
            risk_amount = quantity * per_unit
            notional = notional_in_account(spec, quantity, entry, account_currency)
        if quantity > spec.max_quantity:
            return None
        return quantity, risk_amount, notional

    def _pnl(quantity: float, entry: float, exit_price: float, is_long: bool,
             exit_time: int, entry_time: int) -> tuple[float, float, float, float, float]:
        rate = quote_to_account_rate(spec, exit_price, account_currency)
        direction = 1.0 if is_long else -1.0
        gross = (exit_price - entry) * quantity * spec.contract_size * direction * rate
        entry_notional = notional_in_account(spec, quantity, entry, account_currency)
        exit_notional = notional_in_account(spec, quantity, exit_price, account_currency)
        fees = (entry_notional + exit_notional) * fee_rate
        days = max(0.0, (exit_time - entry_time) / 1000) / 86_400
        funding = entry_notional * funding_rate * days * direction
        return gross, fees, funding, gross - fees - funding, entry_notional

    def _simulated(side: str, entry_time: int, exit_time: int, entry: float, exit_price: float,
                   quantity: float, exit_reason: str, risk_amount: float, notional: float,
                   is_long: bool) -> SimulatedTrade:
        nonlocal equity, total_fees, total_funding
        gross, fees, funding, pnl, _ = _pnl(quantity, entry, exit_price, is_long, exit_time, entry_time)
        equity += pnl
        equity_curve.append(equity)
        total_fees += fees
        total_funding += funding
        return SimulatedTrade(
            side=side, entry_time=entry_time, exit_time=exit_time,
            entry_price=entry, exit_price=exit_price, quantity=quantity,
            pnl=pnl, exit_reason=exit_reason, gross_pnl=gross,
            fees=fees, funding=funding, risk_amount=risk_amount, notional=notional,
        )

    if rule.tier_lots:
        max_tiers = len(rule.tier_lots)
        open_trades: list[dict[str, Any]] = []
        for i in range(len(frame)):
            bar = frame.iloc[i]
            opened = float(bar["open"])
            low = float(bar["low"])
            high = float(bar["high"])

            still_open: list[dict[str, Any]] = []
            for item in open_trades:
                is_long = item["is_long"]
                stop, target = item["stop"], item["target"]
                if is_long:
                    stop_hit = low <= stop
                    target_hit = high >= target
                    raw_stop = min(stop, opened) if opened < stop else stop
                    raw_target = max(target, opened) if opened > target else target
                    stop_fill = _price_fill(raw_stop, "sell", fill_cost_bps)
                    target_fill = _price_fill(raw_target, "sell", fill_cost_bps)
                else:
                    stop_hit = high >= stop
                    target_hit = low <= target
                    raw_stop = max(stop, opened) if opened > stop else stop
                    raw_target = min(target, opened) if opened < target else target
                    stop_fill = _price_fill(raw_stop, "buy", fill_cost_bps)
                    target_fill = _price_fill(raw_target, "buy", fill_cost_bps)
                if stop_hit or target_hit:
                    exit_price = stop_fill if stop_hit else target_fill
                    reason = "stop_loss" if stop_hit else "take_profit"
                    trades.append(_simulated(
                        "buy" if is_long else "sell", item["entry_time"], int(bar["timestamp"]),
                        item["entry"], exit_price, item["quantity"], reason,
                        item["risk_amount"], item["notional"], is_long,
                    ))
                else:
                    still_open.append(item)
            open_trades = still_open

            long_fired = bool(long_signals.iloc[i])
            short_fired = bool(short_signals.iloc[i])
            if long_fired and short_fired:
                rejected_signals += 1
                continue
            if (long_fired or short_fired) and i < len(frame) - 1 and len(open_trades) < max_tiers:
                is_long = long_fired
                same_direction = sum(1 for item in open_trades if item["is_long"] == is_long)
                tier_index = min(same_direction, max_tiers - 1)
                next_bar = frame.iloc[i + 1]
                side = "buy" if is_long else "sell"
                raw_entry = float(next_bar["open"])
                entry = _price_fill(raw_entry, side, fill_cost_bps)
                sized = _entry_quantity(entry, rule.tier_lots[tier_index])
                if sized is None:
                    rejected_signals += 1
                    continue
                quantity, risk_amount, notional = sized
                open_risk = sum(item["risk_amount"] for item in open_trades)
                if open_risk + risk_amount > equity * max_open_risk_pct / 100 + 1e-9:
                    rejected_signals += 1
                    continue
                stop = entry - stop_distance if is_long else entry + stop_distance
                target = entry + target_distance if is_long else entry - target_distance
                open_trades.append({
                    "is_long": is_long, "entry": entry, "stop": stop, "target": target,
                    "quantity": quantity, "entry_time": int(next_bar["timestamp"]),
                    "risk_amount": risk_amount, "notional": notional,
                })

        if open_trades:
            last_close_raw = float(frame.iloc[-1]["close"])
            last_time = int(frame.iloc[-1]["timestamp"])
            for item in open_trades:
                side = "buy" if item["is_long"] else "sell"
                exit_price = _price_fill(last_close_raw, side, fill_cost_bps)
                trades.append(_simulated(
                    side, item["entry_time"], last_time, item["entry"], exit_price,
                    item["quantity"], "end_of_data", item["risk_amount"], item["notional"], item["is_long"],
                ))
    else:
        index = 0
        while index < len(frame) - 1:
            long_fired = bool(long_signals.iloc[index])
            short_fired = bool(short_signals.iloc[index])
            if long_fired and short_fired:
                rejected_signals += 1
                index += 1
                continue
            if not long_fired and not short_fired:
                index += 1
                continue
            is_long = long_fired
            side = "buy" if is_long else "sell"
            entry_index = index + 1
            entry_bar = frame.iloc[entry_index]
            raw_entry = float(entry_bar["open"])
            entry = _price_fill(raw_entry, side, fill_cost_bps)
            sized = _entry_quantity(entry, None)
            if sized is None:
                rejected_signals += 1
                index += 1
                continue
            quantity, risk_amount, notional = sized
            stop = entry - stop_distance if is_long else entry + stop_distance
            target = entry + target_distance if is_long else entry - target_distance
            exit_index = len(frame) - 1
            exit_price = _price_fill(float(frame.iloc[exit_index]["close"]), "sell" if is_long else "buy", fill_cost_bps)
            exit_reason = "end_of_data"

            for candidate in range(entry_index, len(frame)):
                bar = frame.iloc[candidate]
                opened = float(bar["open"])
                if is_long:
                    stop_hit, target_hit = float(bar["low"]) <= stop, float(bar["high"]) >= target
                    raw_stop = min(stop, opened) if opened < stop else stop
                    raw_target = max(target, opened) if opened > target else target
                else:
                    stop_hit, target_hit = float(bar["high"]) >= stop, float(bar["low"]) <= target
                    raw_stop = max(stop, opened) if opened > stop else stop
                    raw_target = min(target, opened) if opened < target else target
                if stop_hit:
                    exit_index = candidate
                    exit_price = _price_fill(raw_stop, "sell" if is_long else "buy", fill_cost_bps)
                    exit_reason = "stop_loss"
                    break
                if target_hit:
                    exit_index = candidate
                    exit_price = _price_fill(raw_target, "sell" if is_long else "buy", fill_cost_bps)
                    exit_reason = "take_profit"
                    break

            trades.append(_simulated(
                side, int(entry_bar["timestamp"]), int(frame.iloc[exit_index]["timestamp"]),
                entry, exit_price, quantity, exit_reason, risk_amount, notional, is_long,
            ))
            index = exit_index + 1

    winners = [trade.pnl for trade in trades if trade.pnl > 0]
    losers = [trade.pnl for trade in trades if trade.pnl < 0]
    peak = starting_balance
    max_drawdown = 0.0
    current_streak = 0
    max_consecutive_losses = 0
    for mark in equity_curve:
        peak = max(peak, mark)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - mark) / peak * 100)
    for trade in trades:
        if trade.pnl < 0:
            current_streak += 1
            max_consecutive_losses = max(max_consecutive_losses, current_streak)
        else:
            current_streak = 0
    total_pnl = equity - starting_balance
    profit_factor: float | str
    if losers:
        profit_factor = sum(winners) / abs(sum(losers))
    elif winners:
        profit_factor = "infinity"
    else:
        profit_factor = 0.0
    return {
        "symbol": rule.symbol,
        "timeframe": rule.timeframe,
        "starting_balance": starting_balance,
        "ending_balance": equity,
        "total_pnl": total_pnl,
        "total_return_pct": total_pnl / starting_balance * 100,
        "trade_count": len(trades),
        "rejected_signals": rejected_signals,
        "win_rate_pct": len(winners) / len(trades) * 100 if trades else 0.0,
        "profit_factor": profit_factor,
        "gross_profit": sum(winners),
        "gross_loss": sum(losers),
        "average_trade": total_pnl / len(trades) if trades else 0.0,
        "average_win": sum(winners) / len(winners) if winners else 0.0,
        "average_loss": sum(losers) / len(losers) if losers else 0.0,
        "expectancy": total_pnl / len(trades) if trades else 0.0,
        "max_consecutive_losses": max_consecutive_losses,
        "max_drawdown_pct": max_drawdown,
        "equity_curve": equity_curve,
        "fees_bps": fee_bps,
        "spread_bps": spread_bps,
        "slippage_bps": slippage_bps,
        "funding_bps_per_day": funding_bps_per_day,
        "total_fees": total_fees,
        "total_funding": total_funding,
        "contract_size": spec.contract_size,
        "quantity_unit": spec.quantity_unit,
        "account_currency": account_currency.upper(),
        "leverage_cap": leverage,
        "data_quality": {
            "gap_count": gap_count,
            "max_gap_seconds": max_gap_seconds,
            "warnings": warnings,
        },
        "warnings": warnings,
        "trades": [asdict(trade) for trade in trades],
    }


def run_walk_forward(
    rule: StrategyRule,
    candles: list[dict[str, Any]],
    *,
    folds: int = 4,
    starting_balance: float = 10_000.0,
    fee_bps: float = 5.0,
    spread_bps: float = 0.0,
    slippage_bps: float = 0.0,
    funding_bps_per_day: float = 0.0,
    account_currency: str = "USD",
) -> dict[str, Any]:
    """Split the sample into sequential folds and report out-of-sample results.

    This is a stability check, not an optimiser: the same rule is evaluated on
    each contiguous fold so a result driven by one regime is visible.
    """
    if folds < 2 or folds > 20:
        raise ValueError("folds must be between 2 and 20")
    if len(candles) < folds * 3:
        raise ValueError("At least three candles per fold are required")
    ordered = sorted(candles, key=lambda item: item["timestamp"])
    size = len(ordered) // folds
    fold_reports = []
    for index in range(folds):
        start = index * size
        end = len(ordered) if index == folds - 1 else (index + 1) * size
        segment = ordered[start:end]
        if len(segment) < 3:
            continue
        report = run_backtest(
            rule, segment, starting_balance, fee_bps,
            spread_bps=spread_bps, slippage_bps=slippage_bps,
            funding_bps_per_day=funding_bps_per_day, account_currency=account_currency,
        )
        fold_reports.append({
            "fold": index + 1,
            "start": segment[0]["timestamp"],
            "end": segment[-1]["timestamp"],
            "total_return_pct": report["total_return_pct"],
            "trade_count": report["trade_count"],
            "win_rate_pct": report["win_rate_pct"],
            "max_drawdown_pct": report["max_drawdown_pct"],
            "profit_factor": report["profit_factor"],
        })
    returns = [item["total_return_pct"] for item in fold_reports]
    return {
        "symbol": rule.symbol,
        "timeframe": rule.timeframe,
        "folds": fold_reports,
        "positive_folds": sum(1 for value in returns if value > 0),
        "average_return_pct": sum(returns) / len(returns) if returns else 0.0,
        "worst_fold_return_pct": min(returns) if returns else 0.0,
        "best_fold_return_pct": max(returns) if returns else 0.0,
    }
