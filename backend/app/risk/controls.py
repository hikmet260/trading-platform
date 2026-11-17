from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.instruments import (
    InstrumentError,
    notional_in_account,
    quote_to_account_rate,
    resolve_instrument,
    round_quantity,
)


class TradeProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    symbol: str = Field(min_length=3, max_length=40)
    side: str = Field(pattern=r"(?i)^(buy|sell)$")
    entry_price: float = Field(gt=0)
    stop_loss: float | None = None
    take_profit: float | None = None
    risk_pct: float = Field(default=0.5, gt=0, le=1.0)
    account_equity: float = Field(gt=0)
    fixed_quantity: float | None = Field(default=None, gt=0)
    account_currency: str = Field(default="USD", min_length=3, max_length=8)
    # Optional venue metadata.  When omitted the instrument registry is used.
    contract_size: float | None = Field(default=None, gt=0, le=1_000_000_000)
    quote_currency: str | None = Field(default=None, min_length=2, max_length=8)

    @model_validator(mode="after")
    def normalize_currency(self) -> "TradeProposal":
        object.__setattr__(self, "account_currency", self.account_currency.strip().upper())
        if self.quote_currency is not None:
            object.__setattr__(self, "quote_currency", self.quote_currency.strip().upper())
        return self


@dataclass(frozen=True)
class NewsEvent:
    name: str
    occurs_at: datetime
    currency: str = "ALL"
    impact: str = "high"
    date_only: bool = False


class RiskManager:
    """Stateful risk gate.  Monetary values are in the account currency."""

    def __init__(
        self,
        max_daily_drawdown_pct: float = 3.0,
        news_blackout_minutes: int = 15,
        *,
        max_trade_risk_pct: float = 1.0,
        max_open_risk_pct: float = 2.0,
        max_notional_multiple: float = 3.0,
        max_positions_per_tenant: int = 5,
        max_daily_trades: int = 20,
        account_currency: str = "USD",
    ) -> None:
        self._validate_limits(max_daily_drawdown_pct, news_blackout_minutes)
        self.max_daily_drawdown_pct = max_daily_drawdown_pct
        self.news_blackout = timedelta(minutes=news_blackout_minutes)
        self.max_trade_risk_pct = max_trade_risk_pct
        self.max_open_risk_pct = max_open_risk_pct
        self.max_notional_multiple = max_notional_multiple
        self.max_positions_per_tenant = max_positions_per_tenant
        self.max_daily_trades = max_daily_trades
        self.account_currency = account_currency.strip().upper()
        self._day: str | None = None
        self._day_start_equity: float | None = None
        self._realized_pnl = 0.0
        self._halted = False
        self._trades_today = 0
        self._validate_extended_limits()

    @staticmethod
    def _validate_limits(max_daily_drawdown_pct: float, news_blackout_minutes: int) -> None:
        if not 0 < max_daily_drawdown_pct <= 100:
            raise ValueError("Daily drawdown limit must be between 0 and 100 percent")
        if not 0 <= news_blackout_minutes <= 240:
            raise ValueError("News blackout must be between 0 and 240 minutes")

    def _validate_extended_limits(self) -> None:
        if not 0 < self.max_trade_risk_pct <= 100:
            raise ValueError("Per-trade risk limit must be between 0 and 100 percent")
        if not 0 < self.max_open_risk_pct <= 100:
            raise ValueError("Aggregate open-risk limit must be between 0 and 100 percent")
        if self.max_trade_risk_pct > self.max_open_risk_pct:
            raise ValueError("Per-trade risk cannot exceed aggregate open-risk limit")
        if self.max_notional_multiple <= 0:
            raise ValueError("Maximum notional multiple must be positive")
        if self.max_positions_per_tenant < 1 or self.max_daily_trades < 1:
            raise ValueError("Position and daily-trade limits must be at least one")

    def configure(
        self,
        max_daily_drawdown_pct: float,
        news_blackout_minutes: int,
        *,
        max_trade_risk_pct: float | None = None,
        max_open_risk_pct: float | None = None,
        max_notional_multiple: float | None = None,
        max_positions_per_tenant: int | None = None,
        max_daily_trades: int | None = None,
        account_currency: str | None = None,
    ) -> None:
        self._validate_limits(max_daily_drawdown_pct, news_blackout_minutes)
        self.max_daily_drawdown_pct = max_daily_drawdown_pct
        self.news_blackout = timedelta(minutes=news_blackout_minutes)
        if max_trade_risk_pct is not None:
            self.max_trade_risk_pct = max_trade_risk_pct
        if max_open_risk_pct is not None:
            self.max_open_risk_pct = max_open_risk_pct
        if max_notional_multiple is not None:
            self.max_notional_multiple = max_notional_multiple
        if max_positions_per_tenant is not None:
            self.max_positions_per_tenant = max_positions_per_tenant
        if max_daily_trades is not None:
            self.max_daily_trades = max_daily_trades
        if account_currency is not None:
            self.account_currency = account_currency.strip().upper()
        self._validate_extended_limits()

    def reset_day(self, equity: float, day: str | None = None) -> None:
        self._day = day or datetime.now(timezone.utc).date().isoformat()
        self._day_start_equity = equity
        self._realized_pnl = 0.0
        self._halted = False
        self._trades_today = 0

    def record_trade_opened(self) -> None:
        self._trades_today += 1

    def record_realized_pnl(self, pnl: float, equity: float, day: str | None = None) -> bool:
        day_key = day or datetime.now(timezone.utc).date().isoformat()
        if self._day != day_key:
            self.reset_day(max(equity - pnl, 0.01), day_key)
        self._realized_pnl += pnl
        baseline = self._day_start_equity or equity
        if self._realized_pnl <= -(baseline * self.max_daily_drawdown_pct / 100):
            self._halted = True
        return self._halted

    def mark_equity(self, equity: float, day: str | None = None) -> bool:
        """Trip the same daily breaker on realized plus mark-to-market paper equity."""
        day_key = day or datetime.now(timezone.utc).date().isoformat()
        if self._day != day_key:
            self.reset_day(equity, day_key)
        baseline = self._day_start_equity or equity
        if equity <= baseline * (1 - self.max_daily_drawdown_pct / 100):
            self._halted = True
        return self._halted

    def validate(
        self,
        proposal: TradeProposal,
        *,
        kill_switch: bool,
        events: list[NewsEvent] | None = None,
        now: datetime | None = None,
        open_risk_amount: float = 0.0,
        open_notional: float = 0.0,
        open_positions: int = 0,
        trades_today: int | None = None,
    ) -> dict:
        reasons: list[str] = []
        warnings: list[str] = []
        side = proposal.side.lower()
        if side not in {"buy", "sell"}:
            reasons.append("Side must be buy or sell")
        if proposal.stop_loss is None or proposal.stop_loss <= 0:
            reasons.append("A valid stop-loss is mandatory")
        if proposal.take_profit is None or proposal.take_profit <= 0:
            reasons.append("A valid take-profit is mandatory")
        if proposal.stop_loss is not None:
            if side == "buy" and proposal.stop_loss >= proposal.entry_price:
                reasons.append("Buy stop-loss must be below entry price")
            if side == "sell" and proposal.stop_loss <= proposal.entry_price:
                reasons.append("Sell stop-loss must be above entry price")
        if proposal.take_profit is not None:
            if side == "buy" and proposal.take_profit <= proposal.entry_price:
                reasons.append("Buy take-profit must be above entry price")
            if side == "sell" and proposal.take_profit >= proposal.entry_price:
                reasons.append("Sell take-profit must be below entry price")

        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        day_key = current.astimezone(timezone.utc).date().isoformat()
        if self._day != day_key:
            self.reset_day(proposal.account_equity, day_key)
        if kill_switch:
            reasons.append("Emergency kill-switch is active")
        if self._halted:
            reasons.append("Daily drawdown circuit breaker is active")
        if proposal.account_currency != self.account_currency:
            reasons.append(
                f"Proposal account currency {proposal.account_currency} does not match risk engine {self.account_currency}"
            )

        try:
            spec = resolve_instrument(proposal.symbol)
        except InstrumentError as exc:
            return self._rejected(str(exc), proposal, reasons)

        contract_size = proposal.contract_size or spec.contract_size
        conversion_rate = 0.0
        try:
            conversion_rate = quote_to_account_rate(spec, proposal.entry_price, proposal.account_currency)
        except InstrumentError as exc:
            reasons.append(str(exc))

        for event in events or []:
            if event.impact.lower() != "high":
                continue
            currency = event.currency.upper()
            currencies = {spec.base_currency, spec.quote_currency, spec.settlement_currency}
            if currency != "ALL" and currency not in currencies:
                continue
            event_time = event.occurs_at if event.occurs_at.tzinfo else event.occurs_at.replace(tzinfo=timezone.utc)
            in_window = (event_time.date() == current.date()) if event.date_only else (
                abs((event_time - current).total_seconds()) <= self.news_blackout.total_seconds()
            )
            if in_window:
                reasons.append(f"High-impact news blackout: {event.name}")

        stop_distance = round(abs(proposal.entry_price - proposal.stop_loss), spec.price_decimals) if proposal.stop_loss else 0.0
        target_distance = round(abs(proposal.take_profit - proposal.entry_price), spec.price_decimals) if proposal.take_profit else 0.0
        if 0 < stop_distance < spec.pip_size:
            reasons.append(f"Stop distance is smaller than one {spec.pip_size:g} pip/tick")
        if 0 < target_distance < spec.pip_size:
            reasons.append(f"Take-profit distance is smaller than one {spec.pip_size:g} pip/tick")

        per_unit_risk = stop_distance * contract_size * conversion_rate
        quantity = 0.0
        risk_amount = 0.0
        notional = 0.0

        if per_unit_risk > 0 and conversion_rate > 0:
            if proposal.fixed_quantity is not None:
                quantity = round_quantity(spec, proposal.fixed_quantity, rounding="down")
                if quantity < spec.min_quantity:
                    reasons.append(
                        f"Fixed quantity {proposal.fixed_quantity:g} is below the minimum {spec.min_quantity:g} {spec.quantity_unit}"
                    )
                risk_amount = quantity * per_unit_risk
                max_trade_risk = proposal.account_equity * self.max_trade_risk_pct / 100
                if risk_amount > max_trade_risk:
                    reasons.append(
                        f"Fixed-size risk {risk_amount:.2f} exceeds the {self.max_trade_risk_pct:g}% per-trade limit ({max_trade_risk:.2f})"
                    )
                if risk_amount > proposal.account_equity:
                    reasons.append("Calculated risk amount exceeds account equity")
                notional = notional_in_account(spec, quantity, proposal.entry_price, proposal.account_currency)
                max_notional = proposal.account_equity * self.max_notional_multiple
                if notional > max_notional:
                    reasons.append(
                        f"Position notional {notional:.2f} exceeds the {self.max_notional_multiple:g}x equity cap ({max_notional:.2f})"
                    )
            else:
                risk_amount = proposal.account_equity * proposal.risk_pct / 100
                quantity = round_quantity(spec, risk_amount / per_unit_risk, rounding="down")
                if quantity < spec.min_quantity:
                    reasons.append(
                        f"Risk-based quantity is below the minimum {spec.min_quantity:g} {spec.quantity_unit}; "
                        "increase risk, widen the stop, or use a smaller-contract instrument"
                    )
                notional = notional_in_account(spec, quantity, proposal.entry_price, proposal.account_currency)
                max_notional = proposal.account_equity * self.max_notional_multiple
                available_notional = max(0.0, max_notional - open_notional)
                if notional > max_notional:
                    cap_quantity = round_quantity(
                        spec,
                        available_notional / (contract_size * proposal.entry_price * conversion_rate),
                        rounding="down",
                    )
                    if cap_quantity < spec.min_quantity:
                        reasons.append(
                            f"No notional capacity remains under the {self.max_notional_multiple:g}x equity cap"
                        )
                    else:
                        quantity = cap_quantity
                        risk_amount = quantity * per_unit_risk
                        notional = notional_in_account(spec, quantity, proposal.entry_price, proposal.account_currency)
                        warnings.append(
                            "Quantity was reduced to respect the aggregate notional/leverage cap; "
                            "actual risk is below the requested percentage"
                        )
                elif notional > available_notional and available_notional > 0:
                    warnings.append(
                        "This position uses most of the remaining aggregate notional capacity"
                    )
            if quantity > spec.max_quantity:
                quantity = spec.max_quantity
                reasons.append(
                    f"Quantity exceeds the instrument maximum of {spec.max_quantity:g} {spec.quantity_unit}"
                )

        if open_positions >= self.max_positions_per_tenant:
            reasons.append(
                f"Maximum open positions ({self.max_positions_per_tenant}) reached for this tenant"
            )
        effective_trades = self._trades_today if trades_today is None else trades_today
        if effective_trades >= self.max_daily_trades:
            reasons.append(f"Maximum daily trades ({self.max_daily_trades}) reached")
        if risk_amount > 0:
            aggregate_cap = proposal.account_equity * self.max_open_risk_pct / 100
            if open_risk_amount + risk_amount > aggregate_cap:
                reasons.append(
                    f"Aggregate open risk would reach {open_risk_amount + risk_amount:.2f}, "
                    f"above the {self.max_open_risk_pct:g}% cap ({aggregate_cap:.2f})"
                )
            notional_cap = proposal.account_equity * self.max_notional_multiple
            if open_notional + notional > notional_cap:
                reasons.append(
                    f"Aggregate notional would reach {open_notional + notional:.2f}, "
                    f"above the {self.max_notional_multiple:g}x equity cap ({notional_cap:.2f})"
                )

        return {
            "approved": not reasons,
            "reasons": reasons,
            "warnings": warnings,
            "risk_amount": round(risk_amount, 8),
            "quantity": quantity,
            "quantity_unit": spec.quantity_unit,
            "stop_distance": stop_distance,
            "target_distance": target_distance,
            "notional": round(notional, 8),
            "margin_estimate": round(notional / spec.leverage_hint, 8) if spec.leverage_hint > 0 else None,
            "contract_size": contract_size,
            "quote_currency": proposal.quote_currency or spec.quote_currency,
            "settlement_currency": spec.settlement_currency,
            "account_currency": proposal.account_currency,
            "conversion_rate": conversion_rate,
            "per_trade_risk_pct": (risk_amount / proposal.account_equity * 100) if proposal.account_equity > 0 else 0.0,
            "open_risk_amount": open_risk_amount,
            "open_notional": open_notional,
            "open_positions": open_positions,
            "trades_today": effective_trades,
        }

    @staticmethod
    def _rejected(reason: str, proposal: TradeProposal, reasons: list[str]) -> dict:
        return {
            "approved": False,
            "reasons": [*reasons, reason],
            "warnings": [],
            "risk_amount": 0.0,
            "quantity": 0.0,
            "quantity_unit": "units",
            "stop_distance": 0.0,
            "target_distance": 0.0,
            "notional": 0.0,
            "margin_estimate": None,
            "contract_size": proposal.contract_size or 1.0,
            "quote_currency": proposal.quote_currency or "",
            "settlement_currency": proposal.quote_currency or "",
            "account_currency": proposal.account_currency,
            "conversion_rate": 0.0,
            "per_trade_risk_pct": 0.0,
            "open_risk_amount": 0.0,
            "open_notional": 0.0,
            "open_positions": 0,
            "trades_today": 0,
        }

    @property
    def halted(self) -> bool:
        return self._halted

    @property
    def trades_today(self) -> int:
        return self._trades_today

    @property
    def daily_drawdown_pct(self) -> float:
        baseline = self._day_start_equity or 0.0
        return (max(0.0, -self._realized_pnl) / baseline * 100) if baseline > 0 else 0.0
