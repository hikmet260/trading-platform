from __future__ import annotations

import asyncio
import json
import logging
import math
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from app.connectors.crypto import CryptoMarketData
from app.connectors.mt5 import MT5Connector
from app.core.security import CredentialVault
from app.storage.cache import MarketCache
from app.settings import settings
from app.storage.database import Database
from app.storage.models import AuditRecord, BrokerAccountRecord, TradeRecord
from app.risk.controls import RiskManager, TradeProposal
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
                TradeRecord.tenant_id == tenant_id, TradeRecord.mode == "live", TradeRecord.status == "open",
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
    ) -> dict[str, Any]:
        if not settings.live_trading_enabled:
            raise RuntimeError("Live execution is disabled")
        if await self.cache.is_kill_switch_active(tenant_id):
            raise RuntimeError("Emergency kill-switch is active")
        if settings.news_calendar_required and not calendar_fresh:
            raise RuntimeError("Required high-impact news calendar is unavailable or stale")
        if trade_id:
            async with self.database.sessions() as session:
                pending = await session.scalar(select(TradeRecord.id).where(
                    TradeRecord.id == trade_id, TradeRecord.tenant_id == tenant_id,
                    TradeRecord.mode == "live", TradeRecord.status == "proposed",
                ))
                if pending is None:
                    raise RuntimeError("Semi-automated trade proposal is no longer pending")
        account = await self._get_account(account_id, tenant_id)
        if not account.enabled:
            raise RuntimeError("Strategy broker account is disabled")
        broker, connector = await self._connector_for_account(account)
        if feed_broker and feed_broker != broker:
            try:
                await connector.close()
            except Exception:
                logger.exception("Broker connector cleanup failed after execution-venue mismatch")
            raise RuntimeError("Strategy market feed and selected execution broker do not match")
        try:
            if broker == "mt5":
                if await connector.positions_for_symbol(symbol):
                    raise RuntimeError("Existing MT5 position blocks isolated strategy execution")
                equity = await connector.account_equity()
                quote = await connector.quote(symbol)
                entry = float(quote["ask"] if side == "buy" else quote["bid"])
                stop = entry - stop_distance if side == "buy" else entry + stop_distance
                target = entry + target_distance if side == "buy" else entry - target_distance
                proposal = TradeProposal(symbol=symbol, side=side, entry_price=entry, stop_loss=stop,
                                         take_profit=target, risk_pct=risk_pct, account_equity=equity)
                assessment = risk_manager.validate(proposal, kill_switch=False, events=events)
                if not assessment["approved"]:
                    raise RuntimeError("Risk validation rejected the live strategy signal")
                guard = await self.guard_equity(tenant_id, account_id, equity, risk_manager.max_daily_drawdown_pct)
                if guard["tripped"]:
                    raise RuntimeError("Live account daily drawdown limit reached")
                risk_per_lot = await connector.risk_per_lot(symbol, entry, stop)
                info = await asyncio.to_thread(connector._require_connection().symbol_info, symbol)
                step = float(info.volume_step)
                amount = math.floor((assessment["risk_amount"] / risk_per_lot) / step) * step
                if amount < float(info.volume_min) or amount > float(info.volume_max):
                    raise RuntimeError("Risk-sized volume is outside MT5 symbol limits")
                if amount * risk_per_lot > assessment["risk_amount"]:
                    raise RuntimeError("Normalized MT5 volume exceeds the risk budget")
                order = await connector.execute_protected_market_order(symbol, side, amount, stop, target)
            else:
                config = account.configuration or {}
                equity = await connector.account_equity(str(config.get("risk_currency", "USDT")))
                await connector.rest.load_markets()
                market = connector.rest.market(symbol)
                risk_currency = str(config.get("risk_currency", "USDT")).upper()
                if market.get("linear") is not True or str(market.get("quote", "")).upper() != risk_currency:
                    raise RuntimeError("Live crypto risk sizing requires a linear contract quoted in risk currency")
                if await connector.positions_for_symbol(symbol):
                    raise RuntimeError("Existing derivatives position blocks isolated strategy execution")
                quote = await connector.quote(symbol)
                entry = float((quote.get("ask") if side == "buy" else quote.get("bid")) or quote.get("last") or 0)
                stop = entry - stop_distance if side == "buy" else entry + stop_distance
                target = entry + target_distance if side == "buy" else entry - target_distance
                proposal = TradeProposal(symbol=symbol, side=side, entry_price=entry, stop_loss=stop,
                                         take_profit=target, risk_pct=risk_pct, account_equity=equity)
                assessment = risk_manager.validate(proposal, kill_switch=False, events=events)
                if not assessment["approved"]:
                    raise RuntimeError("Risk validation rejected the live strategy signal")
                guard = await self.guard_equity(tenant_id, account_id, equity, risk_manager.max_daily_drawdown_pct)
                if guard["tripped"]:
                    raise RuntimeError("Live account daily drawdown limit reached")
                per_contract_risk = abs(entry - stop) * float(market.get("contractSize") or 1)
                amount = float(connector.rest.amount_to_precision(symbol, assessment["risk_amount"] / per_contract_risk))
                if amount <= 0 or amount * per_contract_risk > assessment["risk_amount"]:
                    raise RuntimeError("Normalized contract amount exceeds the risk budget")
                order = await connector.execute_protected_market_order(symbol, side, amount, stop, target)
            result = {**order, "entry_price": float(order.get("entry_price") or entry),
                      "stop_loss": stop, "take_profit": target,
                      "risk_amount": assessment["risk_amount"]}
        finally:
            try:
                await connector.close()
            except Exception:
                logger.exception("Broker connector cleanup failed after strategy order")

        async with self.database.sessions() as session:
            trade = await session.get(TradeRecord, trade_id) if trade_id else None
            if trade_id and (trade is None or trade.tenant_id != tenant_id or trade.status != "proposed"):
                raise RuntimeError("Semi-automated trade proposal is no longer pending")
            if trade is None:
                trade = TradeRecord(
                    id=trade_id or str(uuid4()), tenant_id=tenant_id,
                    strategy_id=strategy_id, symbol=symbol, side=side,
                    entry_price=result["entry_price"], exit_price=None,
                    quantity=float(result["quantity"]), stop_loss=stop, take_profit=target,
                    pnl=None, status="open", mode="live",
                )
                session.add(trade)
            else:
                trade.broker_account_id = account_id
                trade.entry_price = result["entry_price"]
                trade.quantity = float(result["quantity"])
                trade.stop_loss = stop
                trade.take_profit = target
                trade.status = "open"
                trade.mode = "live"
            trade.broker_account_id = account_id
            trade.metadata_json = {
                **(trade.metadata_json or {}), "broker": broker,
                "broker_order_id": str(result["order_id"]), "broker_status": result.get("status"),
                "position_ticket": result.get("position_ticket"), "position_side": side,
                "risk_pct": risk_pct, "risk_amount": result["risk_amount"],
            }
            session.add(AuditRecord(tenant_id=tenant_id, actor="strategy_engine",
                                    action="live_strategy_order_submitted", entity_id=trade.id,
                                    details={"strategy_id": strategy_id, "account_id": account_id,
                                             "symbol": symbol, "side": side, "quantity": trade.quantity,
                                             "broker_order_id": str(result["order_id"])}))
            await session.commit()
            result["id"] = trade.id
        return result

    async def reconcile_once(self) -> None:
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

    async def _open_trades(self, tenant_id: str | None = None) -> list[TradeRecord]:
        async with self.database.sessions() as session:
            query = select(TradeRecord).where(TradeRecord.mode == "live", TradeRecord.status == "open")
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
        credentials = CredentialVault().decrypt(account.encrypted_credentials)
        configuration = dict(account.configuration or {})
        broker = account.broker
        if broker == "mt5":
            connector = MT5Connector(login=credentials.get("login", ""), password=credentials.get("password", ""),
                                     server=credentials.get("server", ""),
                                     terminal_path=configuration.get("terminal_path", ""))
            await connector.connect(enabled=True)
        elif broker in {"binance", "bybit", "okx"}:
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
                ticket = str(trade.metadata_json["position_ticket"])
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
                ticket = str(trade.metadata_json["position_ticket"])
                close = await connector.close_position(ticket)
                exit_info = await connector.position_exit(ticket)
                if close.get("already_closed") and not exit_info:
                    return {"price": trade.entry_price, "pnl": None, "status": "closed_unreconciled",
                            "metadata": {"close_confirmation": "position_absent_no_exit_deal"}}
                return {"price": (exit_info or {}).get("price", close.get("price", trade.entry_price)),
                        "pnl": (exit_info or {}).get("pnl"), "status": "closed_kill_switch",
                        "metadata": {"close_deal_id": close.get("deal_id"),
                                     "exit_deals": (exit_info or {}).get("deal_ids", [])}}
            close = await connector.close_position(trade.symbol, trade.side, trade.quantity,
                                                   str(trade.metadata_json.get("broker_order_id", "")) or None)
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
                TradeRecord.mode == "live", TradeRecord.status == "open",
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
            await session.commit()

    def status(self) -> dict[str, Any]:
        return {"running": self._task is not None, "last_reconcile": self.last_reconcile,
                "last_errors": self.last_errors[:20]}
