from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, ConfigDict, Field


class TradeProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    symbol: str = Field(min_length=3, max_length=40)
    side: str = Field(pattern=r"(?i)^(buy|sell)$")
    entry_price: float = Field(gt=0)
    stop_loss: float | None = None
    take_profit: float | None = None
    risk_pct: float = Field(gt=0, le=1.0)
    account_equity: float = Field(gt=0)


@dataclass(frozen=True)
class NewsEvent:
    name: str
    occurs_at: datetime
    currency: str = "ALL"
    impact: str = "high"
    date_only: bool = False


class RiskManager:
    def __init__(self, max_daily_drawdown_pct: float = 3.0, news_blackout_minutes: int = 15) -> None:
        if not 0 < max_daily_drawdown_pct <= 100:
            raise ValueError("Daily drawdown limit must be between 0 and 100 percent")
        if not 0 <= news_blackout_minutes <= 240:
            raise ValueError("News blackout must be between 0 and 240 minutes")
        self.max_daily_drawdown_pct = max_daily_drawdown_pct
        self.news_blackout = timedelta(minutes=news_blackout_minutes)
        self._day: str | None = None
        self._day_start_equity: float | None = None
        self._realized_pnl = 0.0
        self._halted = False

    def configure(self, max_daily_drawdown_pct: float, news_blackout_minutes: int) -> None:
        if not 0 < max_daily_drawdown_pct <= 100:
            raise ValueError("Daily drawdown limit must be between 0 and 100 percent")
        if not 0 <= news_blackout_minutes <= 240:
            raise ValueError("News blackout must be between 0 and 240 minutes")
        self.max_daily_drawdown_pct = max_daily_drawdown_pct
        self.news_blackout = timedelta(minutes=news_blackout_minutes)

    def reset_day(self, equity: float, day: str | None = None) -> None:
        self._day = day or datetime.now(timezone.utc).date().isoformat()
        self._day_start_equity = equity
        self._realized_pnl = 0.0
        self._halted = False

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
    ) -> dict:
        reasons: list[str] = []
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
        for event in events or []:
            if event.impact.lower() != "high":
                continue
            currency = event.currency.upper()
            if currency != "ALL" and currency not in proposal.symbol.upper().replace("/", "").replace("-", ""):
                continue
            event_time = event.occurs_at if event.occurs_at.tzinfo else event.occurs_at.replace(tzinfo=timezone.utc)
            in_window = (event_time.date() == current.date()) if event.date_only else (
                abs((event_time - current).total_seconds()) <= self.news_blackout.total_seconds()
            )
            if in_window:
                reasons.append(f"High-impact news blackout: {event.name}")

        stop_distance = abs(proposal.entry_price - proposal.stop_loss) if proposal.stop_loss else 0.0
        risk_amount = proposal.account_equity * proposal.risk_pct / 100
        quantity = risk_amount / stop_distance if stop_distance else 0.0
        return {
            "approved": not reasons,
            "reasons": reasons,
            "paper_only": True,
            "risk_amount": risk_amount,
            "quantity": quantity,
            "stop_distance": stop_distance,
        }

    @property
    def halted(self) -> bool:
        return self._halted

    @property
    def daily_drawdown_pct(self) -> float:
        baseline = self._day_start_equity or 0.0
        return (max(0.0, -self._realized_pnl) / baseline * 100) if baseline > 0 else 0.0
