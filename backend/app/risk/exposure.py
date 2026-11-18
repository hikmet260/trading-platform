from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone

from sqlalchemy import select

from app.instruments import InstrumentError, notional_in_account, resolve_instrument
from app.storage.database import Database
from app.storage.models import TradeRecord

ACTIVE_STATUSES = ("open", "proposed", "submitting", "unknown")
NON_TRADE_STATUSES = ("skipped", "expired", "rejected", "failed")


@dataclass(frozen=True)
class Exposure:
    open_risk_amount: float
    open_notional: float
    open_positions: int
    trades_today: int


async def load_exposure(
    database: Database,
    tenant_id: str,
    mode: str,
    account_currency: str = "USD",
) -> Exposure:
    """Aggregate tenant exposure from the durable trade ledger.

    Risk is read from stored assessment metadata when available.  Legacy rows
    are re-priced with the instrument registry so a unit change cannot silently
    under-count risk.
    """
    now = datetime.now(timezone.utc)
    start_of_day = datetime.combine(now.date(), time.min, timezone.utc)
    async with database.sessions() as session:
        rows = await session.execute(select(TradeRecord).where(
            TradeRecord.tenant_id == tenant_id,
            TradeRecord.mode == mode,
            TradeRecord.created_at >= start_of_day,
        ))
        today_trades = list(rows.scalars())
        active_result = await session.execute(select(TradeRecord).where(
            TradeRecord.tenant_id == tenant_id,
            TradeRecord.mode == mode,
            TradeRecord.status.in_(ACTIVE_STATUSES),
        ))
        active_trades = list(active_result.scalars())

    open_risk = 0.0
    open_notional = 0.0
    open_positions = 0
    for trade in active_trades:
        if trade.status == "open":
            open_positions += 1
        metadata = dict(trade.metadata_json or {})
        risk_amount = _as_float(metadata.get("risk_amount"))
        notional = _as_float(metadata.get("notional"))
        if risk_amount is None or notional is None:
            computed_risk, computed_notional = _legacy_values(trade, account_currency)
            risk_amount = risk_amount if risk_amount is not None else computed_risk
            notional = notional if notional is not None else computed_notional
        open_risk += max(0.0, risk_amount or 0.0)
        open_notional += max(0.0, notional or 0.0)

    trades_today = sum(1 for trade in today_trades if trade.status not in NON_TRADE_STATUSES)
    return Exposure(
        open_risk_amount=open_risk,
        open_notional=open_notional,
        open_positions=open_positions,
        trades_today=trades_today,
    )


def _legacy_values(trade: TradeRecord, account_currency: str) -> tuple[float | None, float | None]:
    try:
        spec = resolve_instrument(trade.symbol)
    except InstrumentError:
        return None, None
    stop_distance = abs((trade.entry_price or 0.0) - (trade.stop_loss or 0.0))
    metadata = dict(trade.metadata_json or {})
    contract_size = _as_float(metadata.get("contract_size")) or spec.contract_size
    try:
        notional = notional_in_account(spec, trade.quantity, trade.entry_price, account_currency)
        # A position's original risk is based on its stop, not the current mark.
        from app.instruments import quote_to_account_rate
        rate = quote_to_account_rate(spec, trade.entry_price, account_currency)
        risk = trade.quantity * stop_distance * contract_size * rate
        return risk, notional
    except InstrumentError:
        return None, None


def _as_float(value: object) -> float | None:
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return result if result == result and result >= 0 else None
