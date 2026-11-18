from __future__ import annotations

import asyncio
import json
import logging
import math
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.connectors.crypto import CryptoMarketData
from app.connectors.mt5 import MT5Connector
from app.mt5_agent_service import MT5AgentOutcomeUnknown, RemoteMT5Connector
from app.core.security import CredentialVault
from app.instruments import InstrumentError, notional_in_account, resolve_instrument
from app.storage.cache import MarketCache
from app.settings import settings
from app.storage.database import Database
from app.storage.models import AuditRecord, BrokerAccountRecord, SystemSettingsRecord, TradeRecord
from app.notifications.service import enqueue_notification_in_session
from app.risk.controls import RiskManager, TradeProposal
from app.risk.exposure import load_exposure
from app.risk.news import NewsEvent

logger = logging.getLogger(__name__)


class LivePositionManager:
    """Reconciles only platform-ledger positions and closes them via their owning account."""

    def __init__(self, database: Database, cache: MarketCache, interval_seconds: int = 10) -> None:
        self.database = database
        self.cache = cache
        self.interval_seconds = max(3, interval_seconds)
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.last_reconcile: str | None = None
        self.last_errors: list[str] = []

    async def start(self) -> None:
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="live-position-reconciler")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def close_tenant(self, tenant_id: str, actor: str) -> dict[str, Any]:
        trades = await self._open_trades(tenant_id)
        closed = 0
        errors: list[dict[str, str]] = []
        for trade in trades:
            try:
                result = await self._close_one(trade)
                await self._persist_closed(trade.id, tenant_id, actor, result)
                closed += 1
            except Exception as exc:
                logger.exception("Live close failed for trade %s", trade.id)
                errors.append({"trade_id": trade.id, "error": type(exc).__name__})
        self.last_errors = [f"{item['trade_id']}:{item['error']}" for item in errors]
        return {"closed": closed, "errors": errors, "remaining": len(trades) - closed}

    async def has_open_positions(self, tenant_id: str) -> bool:
        async with self.database.sessions() as session:
            value = await session.scalar(select(TradeRecord.id).where(
                TradeRecord.tenant_id == tenant_id, TradeRecord.mode == "live",
                TradeRecord.status.in_(["open", "submitting", "unknown"]),
            ).limit(1))
            return value is not None

    async def guard_equity(self, tenant_id: str, account_id: str, equity: float, max_drawdown_pct: float) -> dict[str, float | bool]:
        if equity <= 0:
            raise RuntimeError("Broker returned non-positive account equity")
        day = datetime.now(timezone.utc).date().isoformat()
        key = f"risk:live:equity-baseline:{tenant_id}:{account_id}:{day}"
        await self.cache.redis.set(key, str(equity), nx=True, ex=259_200)
        baseline = float(await self.cache.redis.get(key) or equity)
        drawdown = max(0.0, (baseline - equity) / baseline * 100)
        tripped = drawdown >= max_drawdown_pct
        if tripped:
            await self.cache.set_kill_switch(True, tenant_id)
        return {"baseline": baseline, "equity": equity, "drawdown_pct": drawdown, "tripped": tripped}

    async def seed_account(self, account: BrokerAccountRecord) -> dict[str, float | bool]:
        broker, connector = await self._connector_for_account(account)
        try:
            if broker == "mt5":
                equity = await connector.account_equity()
            else:
                equity = await connector.account_equity(str((account.configuration or {}).get("risk_currency", "USDT")))
            return await self.guard_equity(account.tenant_id, account.id, equity, 100.0)
        finally:
            await connector.close()

    async def execute_strategy_order(
        self, *, tenant_id: str, account_id: str, strategy_id: str, symbol: str, side: str,
        risk_pct: float, stop_distance: float, target_distance: float,
        risk_manager: RiskManager, events: list[NewsEvent], trade_id: str | None = None,
        feed_broker: str | None = None, calendar_fresh: bool = True,
        tier_lot: float | None = None, max_tiers: int = 1,
        client_order_id: str | None = None, fixed_quantity: float | None = None,
        stop_loss_price: float | None = None, take_profit_price: float | None = None,
    ) -> dict[str, Any]:
        """Submit one protected order with a durable intent and idempotency key.

        The local ``client_order_id`` is persisted *before* the broker call.  A
        duplicate caller, a process crash, or a network timeout can therefore
        never silently place the same signal twice: the reconciler adopts an
        existing broker position or marks the intent ``unknown`` for operator
        review instead of resubmitting.
        """
        if not settings.live_trading_enabled:
            raise RuntimeError("Live execution is disabled")
        if await self.cache.is_kill_switch_active(tenant_id):
            raise RuntimeError("Emergency kill-switch is active")
        if settings.news_calendar_required and not calendar_fresh:
            raise RuntimeError("Required high-impact news calendar is unavailable or stale")
        if side.lower() not in {"buy", "sell"}:
            raise RuntimeError("Side must be buy or sell")
        if min(stop_distance, target_distance) <= 0:
            raise RuntimeError("Stop-loss and take-profit distances must be positive")
        fixed = tier_lot if tier_lot is not None else fixed_quantity
        request_key = (client_order_id or "").strip() or f"tp-{trade_id or uuid4()}"
        if len(request_key) > 80:
            raise RuntimeError("Idempotency key must be at most 80 characters")
        if tier_lot is not None and fixed_quantity is not None:
            raise RuntimeError("Provide either a tier size or a fixed quantity, not both")

        lock = self.cache.redis.lock(f"live:submit:{request_key}", timeout=90, blocking_timeout=0)
        if not await lock.acquire(blocking=False):
            raise RuntimeError("An order with this idempotency key is already being submitted")
        reserved = False
        intent_id: str | None = trade_id
        try:
            if trade_id:
                async with self.database.sessions() as session:
                    pending = await session.scalar(select(TradeRecord).where(
                        TradeRecord.id == trade_id, TradeRecord.tenant_id == tenant_id,
                        TradeRecord.mode == "live",
                    ))
                    if pending is None:
                        raise RuntimeError("Semi-automated trade proposal is no longer pending")
                    if pending.status in {"open", "submitting"}:
                        if pending.client_order_id == request_key:
                            return {"id": pending.id, "status": pending.status, "reused": True,
                                    "quantity": pending.quantity, "entry_price": pending.entry_price}
                        raise RuntimeError("Trade proposal was already submitted")
                    if pending.status != "proposed":
                        raise RuntimeError("Semi-automated trade proposal is no longer pending")
            else:
                async with self.database.sessions() as session:
                    existing = await session.scalar(select(TradeRecord).where(
                        TradeRecord.tenant_id == tenant_id,
                        TradeRecord.client_order_id == request_key,
                    ))
                    if existing is not None:
                        if existing.status in {"open", "submitting", "unknown"}:
                            return {"id": existing.id, "status": existing.status, "reused": True,
                                    "quantity": existing.quantity, "entry_price": existing.entry_price}
                        raise RuntimeError("An order with this idempotency key already exists")

            account = await self._get_account(account_id, tenant_id)
            if not account.enabled:
                raise RuntimeError("Strategy broker account is disabled")
            broker, connector = await self._connector_for_account(account)
            if feed_broker and feed_broker != broker:
                raise RuntimeError("Strategy market feed and selected execution broker do not match")
            try:
                try:
                    spec = resolve_instrument(symbol)
                except InstrumentError as exc:
                    raise RuntimeError(str(exc)) from exc
                account_currency = str(
                    (account.configuration or {}).get("risk_currency", settings.account_currency)
                ).upper()
                if broker == "mt5":
                    existing_positions = await connector.positions_for_symbol(symbol)
                    if len(existing_positions) >= max_tiers:
                        raise RuntimeError(f"Active MT5 positions limit ({max_tiers}) reached for {symbol}")
                    equity = await connector.account_equity()
                    quote = await connector.quote(symbol)
                    entry = float(quote["ask"] if side == "buy" else quote["bid"])
                    stop = stop_loss_price if stop_loss_price is not None else (
                        entry - stop_distance if side == "buy" else entry + stop_distance
                    )
                    target = take_profit_price if take_profit_price is not None else (
                        entry + target_distance if side == "buy" else entry - target_distance
                    )
                    stop_distance = abs(entry - stop)
                    target_distance = abs(target - entry)
                    exposure = await load_exposure(self.database, tenant_id, "live", account_currency)
                    proposal = TradeProposal(
                        symbol=symbol, side=side, entry_price=entry, stop_loss=stop,
                        take_profit=target, risk_pct=risk_pct, account_equity=equity,
                        fixed_quantity=fixed, account_currency=account_currency,
                    )
                    assessment = risk_manager.validate(
                        proposal, kill_switch=False, events=events,
                        open_risk_amount=exposure.open_risk_amount,
                        open_notional=exposure.open_notional,
                        open_positions=exposure.open_positions,
                        trades_today=exposure.trades_today,
                    )
                    if not assessment["approved"]:
                        raise RuntimeError("Risk validation rejected the live strategy signal: "
                                           + "; ".join(assessment["reasons"]))
                    guard = await self.guard_equity(tenant_id, account_id, equity, risk_manager.max_daily_drawdown_pct)
                    if guard["tripped"]:
                        raise RuntimeError("Live account daily drawdown limit reached")
                    info = await connector.symbol_info(symbol)
                    step = float(info["volume_step"])
                    risk_per_lot = await connector.risk_per_lot(symbol, entry, stop)
                    if step <= 0 or risk_per_lot <= 0:
                        raise RuntimeError("MT5 volume step or risk value is unavailable")
                    if fixed is not None:
                        amount = math.floor((fixed + step * 1e-6) / step) * step
                        risk_budget = equity * risk_manager.max_trade_risk_pct / 100
                    else:
                        risk_budget = equity * risk_pct / 100
                        amount = math.floor((risk_budget / risk_per_lot + step * 1e-6) / step) * step
                    amount = round(amount, 8)
                    if amount < float(info["volume_min"]) or amount > float(info["volume_max"]):
                        raise RuntimeError("Normalized volume is outside MT5 symbol limits")
                    actual_risk = amount * risk_per_lot
                    if actual_risk > risk_budget * 1.001 + 1e-9:
                        raise RuntimeError("Normalized MT5 volume exceeds the approved risk budget")
                    contract_size = float(info.get("contract_size") or spec.contract_size)
                    actual_notional = amount * contract_size * entry
                    self._check_live_caps(
                        risk_manager, equity, exposure, actual_risk, actual_notional,
                    )
                    quantity_unit = "lots"
                else:
                    config = account.configuration or {}
                    equity = await connector.account_equity(str(config.get("risk_currency", "USDT")))
                    await connector.rest.load_markets()
                    market = connector.rest.market(symbol)
                    account_currency = str(config.get("risk_currency", "USDT")).upper()
                    if market.get("linear") is not True or str(market.get("quote", "")).upper() != account_currency:
                        raise RuntimeError("Live crypto risk sizing requires a linear contract quoted in risk currency")
                    existing_positions = await connector.positions_for_symbol(symbol)
                    if len(existing_positions) >= max_tiers:
                        raise RuntimeError(f"Active derivatives positions limit ({max_tiers}) reached for {symbol}")
                    quote = await connector.quote(symbol)
                    entry = float((quote.get("ask") if side == "buy" else quote.get("bid")) or quote.get("last") or 0)
                    if entry <= 0:
                        raise RuntimeError("A positive live quote is required")
                    stop = stop_loss_price if stop_loss_price is not None else (
                        entry - stop_distance if side == "buy" else entry + stop_distance
                    )
                    target = take_profit_price if take_profit_price is not None else (
                        entry + target_distance if side == "buy" else entry - target_distance
                    )
                    stop_distance = abs(entry - stop)
                    target_distance = abs(target - entry)
                    exposure = await load_exposure(self.database, tenant_id, "live", account_currency)
                    proposal = TradeProposal(
                        symbol=symbol, side=side, entry_price=entry, stop_loss=stop,
                        take_profit=target, risk_pct=risk_pct, account_equity=equity,
                        fixed_quantity=fixed, account_currency=account_currency,
                    )
                    assessment = risk_manager.validate(
                        proposal, kill_switch=False, events=events,
                        open_risk_amount=exposure.open_risk_amount,
                        open_notional=exposure.open_notional,
                        open_positions=exposure.open_positions,
                        trades_today=exposure.trades_today,
                    )
                    if not assessment["approved"]:
                        raise RuntimeError("Risk validation rejected the live strategy signal: "
                                           + "; ".join(assessment["reasons"]))
                    guard = await self.guard_equity(tenant_id, account_id, equity, risk_manager.max_daily_drawdown_pct)
                    if guard["tripped"]:
                        raise RuntimeError("Live account daily drawdown limit reached")
                    contract_size = float(market.get("contractSize") or spec.contract_size)
                    per_contract_risk = abs(entry - stop) * contract_size
                    if per_contract_risk <= 0:
                        raise RuntimeError("Contract risk metadata is unavailable")
                    if fixed is not None:
                        amount = float(connector.rest.amount_to_precision(symbol, fixed))
                        risk_budget = equity * risk_manager.max_trade_risk_pct / 100
                    else:
                        risk_budget = equity * risk_pct / 100
                        amount = float(connector.rest.amount_to_precision(
                            symbol, risk_budget / per_contract_risk,
                        ))
                    if amount <= 0:
                        raise RuntimeError("Normalized contract amount is zero")
                    actual_risk = amount * per_contract_risk
                    if actual_risk > risk_budget * 1.001 + 1e-9:
                        raise RuntimeError("Normalized contract amount exceeds the approved risk budget")
                    actual_notional = amount * contract_size * entry
                    self._check_live_caps(
                        risk_manager, equity, exposure, actual_risk, actual_notional,
                    )
                    quantity_unit = "contracts"

                intent_id, reserved = await self._reserve_intent(
                    trade_id=trade_id, client_order_id=request_key, tenant_id=tenant_id,
                    account_id=account_id, strategy_id=strategy_id, broker=broker,
                    symbol=symbol, side=side, entry=entry, stop=stop, target=target,
                    quantity=amount, risk_pct=risk_pct, risk_amount=actual_risk,
                    notional=actual_notional, quantity_unit=quantity_unit,
                )
                try:
                    if broker == "mt5":
                        order = await connector.execute_protected_market_order(
                            symbol, side, amount, stop, target, client_order_id=request_key,
                        )
                    else:
                        order = await connector.execute_protected_market_order(
                            symbol, side, amount, stop, target,
                        )
                except Exception as exc:
                    await self._mark_intent(
                        intent_id, tenant_id, "unknown",
                        {"last_error": f"{type(exc).__name__}: {exc}",
                         "error_at": datetime.now(timezone.utc).isoformat(),
                         **({"mt5_agent_command_id": exc.command_id}
                            if isinstance(exc, MT5AgentOutcomeUnknown) else {})},
                        audit_action="live_order_outcome_unknown",
                    )
                    raise RuntimeError(
                        "Broker order outcome is unknown; it will not be retried automatically. "
                        "The reconciler will adopt or flag the position."
                    ) from exc
                result = {**order, "entry_price": float(order.get("entry_price") or entry),
                          "stop_loss": stop, "take_profit": target,
                          "risk_amount": actual_risk, "notional": actual_notional,
                          "contract_size": contract_size, "quote_currency": spec.quote_currency,
                          "quantity_unit": quantity_unit, "account_currency": account_currency,
                          "client_order_id": request_key}
                executed_quantity = float(result.get("quantity") or 0)
                order_status = str(result.get("status") or "accepted").lower()
                if executed_quantity > 0 and executed_quantity < amount:
                    fill_ratio = executed_quantity / amount
                    result["risk_amount"] = actual_risk * fill_ratio
                    result["notional"] = actual_notional * fill_ratio
                if order_status == "pending" or executed_quantity <= 0:
                    result["pending_review"] = True
                elif executed_quantity > amount * 1.001:
                    raise RuntimeError("Broker filled more than the approved order quantity")
            finally:
                try:
                    await connector.close()
                except Exception:
                    logger.exception("Broker connector cleanup failed after strategy order")

            async with self.database.sessions() as session:
                trade = await session.get(TradeRecord, intent_id)
                if trade is None or trade.tenant_id != tenant_id:
                    raise RuntimeError("Live order intent disappeared before confirmation")
                trade.broker_account_id = account_id
                trade.entry_price = result["entry_price"]
                trade.quantity = float(result.get("quantity") or trade.quantity)
                trade.stop_loss = stop
                trade.take_profit = target
                trade.status = "unknown" if result.get("pending_review") else "open"
                trade.mode = "live"
                trade.client_order_id = request_key
                trade.metadata_json = {
                    **(trade.metadata_json or {}), "broker": broker,
                    "broker_account_id": account_id,
                    "broker_order_id": str(result["order_id"]),
                    "broker_status": result.get("status"),
                    "position_ticket": result.get("position_ticket"),
                    "position_side": side, "risk_pct": risk_pct,
                    "risk_amount": result["risk_amount"],
                    "notional": result["notional"],
                    "contract_size": result["contract_size"],
                    "quote_currency": result.get("quote_currency"),
                    "quantity_unit": result.get("quantity_unit"),
                    "account_currency": result.get("account_currency"),
                    "client_order_id": request_key,
                    "tier_lot_size": tier_lot,
                    "fixed_quantity": fixed,
                    "stop_distance": stop_distance,
                    "target_distance": target_distance,
                }
                session.add(AuditRecord(
                    tenant_id=tenant_id, actor="strategy_engine",
                    action="live_order_outcome_pending_review" if result.get("pending_review") else "live_strategy_order_submitted", entity_id=trade.id,
                    details={"strategy_id": strategy_id, "account_id": account_id,
                             "symbol": symbol, "side": side, "quantity": trade.quantity,
                             "broker_order_id": str(result["order_id"]),
                             "client_order_id": request_key},
                ))
                tenant_settings = await session.get(SystemSettingsRecord, tenant_id)
                telegram_enabled = tenant_settings.telegram_enabled if tenant_settings else settings.telegram_enabled
                if result.get("pending_review"):
                    notification_kind = "trade_execution_issue"
                    notification_title = "MT5 order needs review"
                    notification_body = f"The {side.upper()} {symbol} order was accepted without a confirmed fill. Review the broker and trade history before retrying."
                    notification_dedupe = f"live-order-review:{request_key}"
                else:
                    notification_kind = "trade_executed"
                    notification_title = "Live order submitted"
                    notification_body = f"{side.upper()} {symbol} was submitted by strategy {strategy_id[:8]}."
                    notification_dedupe = f"live-order-submitted:{request_key}"
                await enqueue_notification_in_session(
                    session,
                    tenant_id=tenant_id,
                    kind=notification_kind,
                    payload={
                        "kind": notification_kind, "title": notification_title,
                        "body": notification_body,
                        "url": "/trades", "tag": request_key,
                    },
                    dedupe_key=notification_dedupe,
                    telegram=telegram_enabled,
                    push=True,
                )
                await session.commit()
                result["id"] = trade.id
            return result
        except Exception:
            if not reserved and trade_id:
                await self._mark_intent(
                    trade_id, tenant_id, "rejected",
                    {"last_error": "Live submission was rejected before reaching the broker"},
                    audit_action="live_order_rejected_pre_submit",
                )
            raise
        finally:
            try:
                await lock.release()
            except Exception:
                logger.exception("Could not release live submission lock %s", request_key)

    @staticmethod
    def _check_live_caps(
        risk_manager: RiskManager, equity: float, exposure, actual_risk: float,
        actual_notional: float,
    ) -> None:
        risk_cap = equity * risk_manager.max_open_risk_pct / 100
        if exposure.open_risk_amount + actual_risk > risk_cap * 1.001 + 1e-9:
            raise RuntimeError(
                f"Live aggregate risk would exceed the {risk_manager.max_open_risk_pct:g}% cap"
            )
        notional_cap = equity * risk_manager.max_notional_multiple
        if exposure.open_notional + actual_notional > notional_cap * 1.001 + 1e-9:
            raise RuntimeError(
                f"Live aggregate notional would exceed the {risk_manager.max_notional_multiple:g}x cap"
            )

    async def _reserve_intent(
        self, *, trade_id: str | None, client_order_id: str, tenant_id: str,
        account_id: str, strategy_id: str, broker: str, symbol: str, side: str,
        entry: float, stop: float, target: float, quantity: float, risk_pct: float,
        risk_amount: float, notional: float, quantity_unit: str,
    ) -> tuple[str, bool]:
        intent_id = trade_id or str(uuid4())
        async with self.database.sessions() as session:
            if trade_id:
                trade = await session.get(TradeRecord, trade_id)
                if trade is None or trade.tenant_id != tenant_id or trade.status != "proposed":
                    raise RuntimeError("Semi-automated trade proposal is no longer pending")
            else:
                trade = TradeRecord(
                    id=intent_id, tenant_id=tenant_id, broker_account_id=account_id,
                    strategy_id=strategy_id, symbol=symbol, side=side, entry_price=entry,
                    exit_price=None, quantity=quantity, stop_loss=stop, take_profit=target,
                    pnl=None, status="submitting", mode="live",
                    client_order_id=client_order_id,
                )
                session.add(trade)
            trade.broker_account_id = account_id
            trade.entry_price = entry
            trade.quantity = quantity
            trade.stop_loss = stop
            trade.take_profit = target
            trade.status = "submitting"
            trade.mode = "live"
            trade.client_order_id = client_order_id
            trade.metadata_json = {
                **(trade.metadata_json or {}), "broker": broker,
                "broker_account_id": account_id, "risk_pct": risk_pct,
                "risk_amount": risk_amount, "notional": notional,
                "quantity_unit": quantity_unit, "client_order_id": client_order_id,
                "intent_created_at": datetime.now(timezone.utc).isoformat(),
                "broker_order_id": None,
            }
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise RuntimeError(
                    "An order with this idempotency key already exists"
                ) from exc
            return intent_id, True

    async def _mark_intent(
        self, trade_id: str, tenant_id: str, status: str, metadata: dict[str, Any],
        *, audit_action: str, quantity: float | None = None, entry_price: float | None = None,
        pnl: float | None = None,
    ) -> None:
        try:
            async with self.database.sessions() as session:
                trade = await session.scalar(select(TradeRecord).where(
                    TradeRecord.id == trade_id, TradeRecord.tenant_id == tenant_id,
                    TradeRecord.mode == "live",
                ))
                if trade is None:
                    return
                trade.status = status
                if quantity is not None and quantity > 0:
                    trade.quantity = quantity
                if entry_price is not None and entry_price > 0:
                    trade.entry_price = entry_price
                if pnl is not None:
                    trade.pnl = pnl
                if status.startswith("closed"):
                    trade.closed_at = datetime.now(timezone.utc)
                trade.metadata_json = {**(trade.metadata_json or {}), **metadata}
                session.add(AuditRecord(
                    tenant_id=tenant_id, actor="strategy_engine", action=audit_action,
                    entity_id=trade_id, details={"status": status, **metadata},
                ))
                await session.commit()
        except Exception:
            logger.exception("Could not persist live intent state %s for trade %s", status, trade_id)

    async def reconcile_once(self) -> None:
        await self._recover_unknown()
        trades = await self._open_trades()
        errors: list[str] = []
        accounts_checked: set[str] = set()
        tenants_tripped: set[str] = set()
        for trade in trades:
            try:
                if trade.broker_account_id and trade.broker_account_id not in accounts_checked:
                    account = await self._get_account(trade.broker_account_id, trade.tenant_id or settings.tenant_id)
                    broker, equity_connector = await self._connector_for_account(account)
                    try:
                        equity = (await equity_connector.account_equity() if broker == "mt5" else
                                  await equity_connector.account_equity(str((account.configuration or {}).get("risk_currency", "USDT"))))
                    finally:
                        await equity_connector.close()
                    tenant = trade.tenant_id or settings.tenant_id
                    cap = await self._tenant_drawdown_cap(tenant)
                    guard = await self.guard_equity(tenant, account.id, equity, cap)
                    accounts_checked.add(account.id)
                    if guard["tripped"]:
                        tenants_tripped.add(tenant)
                result = await self._inspect_one(trade)
                if result is not None:
                    await self._persist_closed(trade.id, trade.tenant_id or settings.tenant_id,
                                               "broker_reconciler", result)
            except Exception as exc:
                errors.append(f"{trade.id}:{type(exc).__name__}")
                logger.exception("Live position reconciliation failed for trade %s", trade.id)
        for tenant in tenants_tripped:
            result = await self.close_tenant(tenant, "live_drawdown_circuit_breaker")
            if result["remaining"]:
                errors.append(f"{tenant}:drawdown_close_incomplete:{result['remaining']}")
        self.last_errors = errors
        self.last_reconcile = datetime.now(timezone.utc).isoformat()

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.reconcile_once()
            except Exception as exc:
                self.last_errors = [f"cycle:{type(exc).__name__}"]
                logger.exception("Live position reconciliation cycle failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                pass

    async def _recover_unknown(self) -> None:
        """Adopt positions from an interrupted submission without re-sending it.

        An order intent that never received a definitive broker response is
        never retried.  If the broker shows a matching position we attach it to
        the ledger; if the venue explicitly rejected the order we mark it
        failed; otherwise it remains ``unknown`` for operator review.
        """
        async with self.database.sessions() as session:
            rows = await session.execute(select(TradeRecord).where(
                TradeRecord.mode == "live",
                TradeRecord.status.in_(["submitting", "unknown"]),
            ))
            pending = list(rows.scalars())
        for trade in pending:
            age = (datetime.now(timezone.utc) - trade.created_at).total_seconds()
            if trade.status == "submitting" and age < 90:
                continue
            try:
                broker, connector = await self._account_and_connector(trade)
                try:
                    positions = await connector.positions_for_symbol(trade.symbol)
                    tolerance = max(abs(trade.quantity) * 0.02, 1e-9)
                    matches = [
                        row for row in positions
                        if row.get("side") == trade.side
                        and abs(float(row.get("quantity", 0)) - trade.quantity) <= tolerance
                    ]
                    if matches:
                        position = matches[0]
                        await self._mark_intent(
                            trade.id, trade.tenant_id or settings.tenant_id, "open",
                            {"position_ticket": position.get("ticket"),
                             "broker_status": "recovered_from_position",
                             "reconciled_at": datetime.now(timezone.utc).isoformat()},
                            audit_action="live_order_recovered_from_position",
                            quantity=float(position.get("quantity", trade.quantity)),
                            entry_price=float(position.get("entry_price") or trade.entry_price),
                        )
                        continue
                    metadata = dict(trade.metadata_json or {})
                    order_id = str(metadata.get("broker_order_id") or "")
                    if broker == "mt5" and order_id:
                        state = await connector.order_status(order_id)
                        if state in {"rejected", "canceled", "cancelled", "expired"}:
                            await self._mark_intent(
                                trade.id, trade.tenant_id or settings.tenant_id, "failed",
                                {"broker_status": state, "last_error": "MT5 order was not filled"},
                                audit_action="live_order_reconciled_failed",
                            )
                            continue
                        if state in {"open", "new", "pending", "partial"}:
                            continue
                    elif broker != "mt5" and order_id:
                        state = await connector.order_status(trade.symbol, order_id)
                        if state in {"rejected", "canceled", "cancelled", "expired"}:
                            await self._mark_intent(
                                trade.id, trade.tenant_id or settings.tenant_id, "failed",
                                {"broker_status": state, "last_error": "Broker rejected the order"},
                                audit_action="live_order_reconciled_failed",
                            )
                            continue
                        if state in {"closed", "filled"}:
                            exit_info = await connector.position_exit(
                                trade.symbol, trade.side, trade.quantity,
                                int(trade.created_at.timestamp() * 1000), trade.entry_price,
                            )
                            if exit_info.get("trade_ids"):
                                await self._mark_intent(
                                    trade.id, trade.tenant_id or settings.tenant_id,
                                    "closed_unreconciled",
                                    {"broker_status": state,
                                     "exit_trades": exit_info.get("trade_ids", []),
                                     "pnl": exit_info.get("pnl")},
                                    audit_action="live_order_recovered_closed",
                                    pnl=exit_info.get("pnl"),
                                )
                                continue
                    if trade.status == "submitting":
                        await self._mark_intent(
                            trade.id, trade.tenant_id or settings.tenant_id, "unknown",
                            {"last_error": "No definitive broker response after submission timeout"},
                            audit_action="live_order_outcome_unknown",
                        )
                finally:
                    await connector.close()
            except Exception:
                logger.exception("Could not recover live order intent %s", trade.id)

    async def _open_trades(self, tenant_id: str | None = None) -> list[TradeRecord]:
        async with self.database.sessions() as session:
            query = select(TradeRecord).where(
                TradeRecord.mode == "live",
                TradeRecord.status.in_(["open", "submitting", "unknown"]),
            )
            if tenant_id is not None:
                query = query.where(TradeRecord.tenant_id == tenant_id)
            rows = await session.execute(query.order_by(TradeRecord.created_at))
            # Detach-safe session factory keeps loaded scalar fields available after exit.
            return list(rows.scalars())

    async def _get_account(self, account_id: str, tenant_id: str) -> BrokerAccountRecord:
        async with self.database.sessions() as session:
            account = await session.get(BrokerAccountRecord, account_id)
            if account is None or account.tenant_id != tenant_id:
                raise RuntimeError("Owning broker account is unavailable")
            return account

    async def _connector_for_account(self, account: BrokerAccountRecord):
        configuration = dict(account.configuration or {})
        broker = account.broker
        if broker == "mt5":
            if configuration.get("transport") == "windows_agent" and configuration.get("agent_id"):
                connector = RemoteMT5Connector(self.database, account.tenant_id, str(configuration["agent_id"]))
            else:
                credentials = CredentialVault().decrypt(account.encrypted_credentials)
                connector = MT5Connector(login=credentials.get("login", ""), password=credentials.get("password", ""),
                                         server=credentials.get("server", ""),
                                         terminal_path=configuration.get("terminal_path", ""))
                await connector.connect(enabled=True)
        elif broker in {"binance", "bybit", "okx"}:
            credentials = CredentialVault().decrypt(account.encrypted_credentials)
            if str(configuration.get("default_type", "swap")) not in {"swap", "future", "futures"}:
                raise RuntimeError("Live reconciliation supports derivatives accounts only")
            connector = CryptoMarketData(
                exchange_id=broker, api_key=credentials.get("apiKey", ""),
                secret=credentials.get("secret", ""), password=credentials.get("password", ""),
                sandbox=bool(configuration.get("sandbox", True)),
                default_type=str(configuration.get("default_type", "swap")),
            )
        else:
            raise RuntimeError("Unsupported broker for reconciliation")
        return broker, connector

    async def _account_and_connector(self, trade: TradeRecord):
        if not trade.broker_account_id:
            raise RuntimeError("Live trade has no owning broker account")
        account = await self._get_account(trade.broker_account_id, trade.tenant_id or settings.tenant_id)
        return await self._connector_for_account(account)

    async def _tenant_drawdown_cap(self, tenant_id: str) -> float:
        value = await self.cache.redis.get(f"risk:settings:{tenant_id}")
        if value:
            return float(json.loads(value)["max_daily_drawdown_pct"])
        return settings.engine_max_daily_drawdown_pct

    async def _inspect_one(self, trade: TradeRecord) -> dict[str, Any] | None:
        broker, connector = await self._account_and_connector(trade)
        try:
            if broker == "mt5":
                ticket = str((trade.metadata_json or {}).get("position_ticket") or
                             (trade.metadata_json or {}).get("broker_order_id") or "")
                if not ticket:
                    return None
                position = await connector.position(ticket)
                if position is not None:
                    if position["side"] != trade.side or position["symbol"] != trade.symbol:
                        raise RuntimeError("MT5 ticket no longer matches the recorded symbol and side")
                    return None
                exit_info = await connector.position_exit(ticket)
                if not exit_info:
                    return None
                return {"price": (exit_info or {}).get("price", trade.entry_price),
                        "pnl": (exit_info or {}).get("pnl"), "status": "closed_broker_protection",
                        "metadata": {"exit_deals": (exit_info or {}).get("deal_ids", [])}}
            symbol = trade.symbol
            position = await connector.position(symbol, trade.side)
            if position is not None:
                if position["quantity"] > trade.quantity * 1.001:
                    raise RuntimeError("Exchange position is larger than the platform-managed amount")
                return None
            exit_info = await connector.position_exit(
                symbol, trade.side, trade.quantity, int(trade.created_at.timestamp() * 1000), trade.entry_price,
            )
            order_state = await connector.order_status(symbol, str(trade.metadata_json.get("broker_order_id", "")))
            if order_state in {"open", "new", "pending"}:
                return None
            if not exit_info.get("trade_ids") and order_state not in {"closed", "filled"}:
                return None
            return {"price": exit_info.get("price", trade.entry_price), "pnl": exit_info.get("pnl"),
                    "status": "closed_broker_protection" if exit_info.get("trade_ids") else "closed_unreconciled",
                    "metadata": {"exit_trades": exit_info.get("trade_ids", [])}}
        finally:
            await connector.close()

    async def _close_one(self, trade: TradeRecord) -> dict[str, Any]:
        broker, connector = await self._account_and_connector(trade)
        try:
            if broker == "mt5":
                ticket = str((trade.metadata_json or {}).get("position_ticket") or
                             (trade.metadata_json or {}).get("broker_order_id") or "")
                if not ticket:
                    raise RuntimeError("MT5 order outcome is unresolved and has no broker ticket to close safely")
                close = await connector.close_position(ticket)
                exit_info = await connector.position_exit(ticket)
                if close.get("already_closed") and not exit_info:
                    return {"price": trade.entry_price, "pnl": None, "status": "closed_unreconciled",
                            "metadata": {"close_confirmation": "position_absent_no_exit_deal"}}
                return {"price": (exit_info or {}).get("price", close.get("price", trade.entry_price)),
                        "pnl": (exit_info or {}).get("pnl"), "status": "closed_kill_switch",
                        "metadata": {"close_deal_id": close.get("deal_id"),
                                     "exit_deals": (exit_info or {}).get("deal_ids", [])}}
            broker_order_id = str((trade.metadata_json or {}).get("broker_order_id", "")) or None
            if trade.status != "open" and not broker_order_id:
                raise RuntimeError("Broker order outcome is unresolved and has no order id to close safely")
            close = await connector.close_position(trade.symbol, trade.side, trade.quantity, broker_order_id)
            exit_info = await connector.position_exit(
                trade.symbol, trade.side, trade.quantity, int(trade.created_at.timestamp() * 1000), trade.entry_price,
            )
            if close.get("already_closed") and not exit_info.get("trade_ids"):
                return {"price": trade.entry_price, "pnl": None, "status": "closed_unreconciled",
                        "metadata": {"close_confirmation": "position_absent_no_exit_trade"}}
            return {"price": exit_info.get("price") or close.get("price") or trade.entry_price,
                    "pnl": exit_info.get("pnl"), "status": "closed_kill_switch",
                    "metadata": {"close_order_id": close.get("order_id"),
                                 "exit_trades": exit_info.get("trade_ids", [])}}
        finally:
            await connector.close()

    async def _persist_closed(self, trade_id: str, tenant_id: str, actor: str, result: dict[str, Any]) -> None:
        async with self.database.sessions() as session:
            trade = await session.scalar(select(TradeRecord).where(
                TradeRecord.id == trade_id, TradeRecord.tenant_id == tenant_id,
                TradeRecord.mode == "live",
                TradeRecord.status.in_(["open", "submitting", "unknown"]),
            ))
            if trade is None:
                return
            trade.exit_price = float(result.get("price") or trade.entry_price)
            pnl = result.get("pnl")
            trade.pnl = float(pnl) if pnl is not None else None
            trade.status = str(result.get("status", "closed_broker_protection"))
            trade.closed_at = datetime.now(timezone.utc)
            trade.metadata_json = {**trade.metadata_json, **result.get("metadata", {})}
            session.add(AuditRecord(tenant_id=tenant_id, actor=actor, action="live_position_reconciled",
                                    entity_id=trade_id, details={"status": trade.status, "pnl": trade.pnl}))
            tenant_settings = await session.get(SystemSettingsRecord, tenant_id)
            telegram_enabled = tenant_settings.telegram_enabled if tenant_settings else settings.telegram_enabled
            await enqueue_notification_in_session(
                session,
                tenant_id=tenant_id,
                kind="trade_closed",
                payload={
                    "kind": "trade_closed", "title": "Live position closed",
                    "body": f"{trade.side.upper()} {trade.symbol} was closed ({trade.status.replace('_', ' ')}).",
                    "url": "/trades", "tag": f"closed:{trade.id}",
                },
                dedupe_key=f"live-trade-closed:{trade.id}",
                telegram=telegram_enabled,
                push=True,
            )
            await session.commit()

    def status(self) -> dict[str, Any]:
        return {"running": self._task is not None, "last_reconcile": self.last_reconcile,
                "last_errors": self.last_errors[:20]}
