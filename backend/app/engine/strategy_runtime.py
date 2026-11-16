from __future__ import annotations

import logging
import json
import math
from collections import defaultdict, deque
from datetime import datetime, time, timezone
from uuid import uuid4

import pandas as pd
from sqlalchemy import func, select

from app.engine.runner import MarketEvent
from app.risk.controls import RiskManager, TradeProposal
from app.risk.news import parse_news_events
from app.settings import settings
from app.storage.cache import MarketCache
from app.storage.database import Database
from app.storage.models import AuditRecord, BrokerAccountRecord, StrategyRecord, TradeRecord
from app.strategies.backtest import _condition_series
from app.strategies.rules import StrategyRule
from app.telegram_bot.controller import TelegramController

logger = logging.getLogger(__name__)


class StrategyRuntime:
    """Evaluates enabled deterministic rules on incoming candle-close events in paper mode."""

    def __init__(self, database: Database, cache: MarketCache, telegram: TelegramController) -> None:
        self.database = database
        self.cache = cache
        self.telegram = telegram
        self._risks: dict[str, RiskManager] = {}
        self.risk_for(settings.tenant_id).reset_day(settings.paper_account_equity)
        self.events = parse_news_events(settings.news_events_json)
        self.calendar = None
        self.live_positions = None
        self._bars: dict[tuple[str, str, str], deque[dict]] = defaultdict(lambda: deque(maxlen=1000))
        self._last_signal: dict[tuple[str, str], int] = {}
        self._equity_by_tenant: dict[str, float] = {settings.tenant_id: settings.paper_account_equity}
        self._proposals = 0

    @property
    def risk(self) -> RiskManager:
        return self.risk_for(settings.tenant_id)

    def risk_for(self, tenant_id: str) -> RiskManager:
        if tenant_id not in self._risks:
            risk = RiskManager(settings.engine_max_daily_drawdown_pct, settings.engine_news_blackout_minutes)
            risk.reset_day(settings.paper_account_equity)
            self._risks[tenant_id] = risk
        return self._risks[tenant_id]

    async def initialize(self) -> None:
        """Rebuild paper equity and today's breaker state from the durable trade ledger."""
        today = datetime.now(timezone.utc).date()
        start_of_day = datetime.combine(today, time.min, timezone.utc)
        async with self.database.sessions() as session:
            totals = await session.execute(select(TradeRecord.tenant_id, func.coalesce(func.sum(TradeRecord.pnl), 0)).where(
                TradeRecord.closed_at.is_not(None), TradeRecord.pnl.is_not(None), TradeRecord.mode == "paper",
            ).group_by(TradeRecord.tenant_id))
            daily = await session.execute(select(TradeRecord.tenant_id, func.coalesce(func.sum(TradeRecord.pnl), 0)).where(
                TradeRecord.closed_at >= start_of_day, TradeRecord.pnl.is_not(None), TradeRecord.mode == "paper",
            ).group_by(TradeRecord.tenant_id))
        total_by_tenant = {tenant or settings.tenant_id: float(total) for tenant, total in totals}
        daily_by_tenant = {tenant or settings.tenant_id: float(total) for tenant, total in daily}
        tenants = set(total_by_tenant) | set(daily_by_tenant) | {settings.tenant_id}
        for tenant in tenants:
            total_pnl = total_by_tenant.get(tenant, 0.0)
            today_pnl = daily_by_tenant.get(tenant, 0.0)
            equity = settings.paper_account_equity + total_pnl
            self._equity_by_tenant[tenant] = equity
            risk = self.risk_for(tenant)
            stored = await self.cache.redis.get(f"risk:settings:{tenant}")
            if stored:
                config = json.loads(stored)
                risk.configure(float(config["max_daily_drawdown_pct"]), int(config["news_blackout_minutes"]))
            risk.reset_day(equity - today_pnl, today.isoformat())
            if today_pnl:
                risk.record_realized_pnl(today_pnl, equity, today.isoformat())

    async def on_event(self, event: MarketEvent) -> None:
        if event.kind == "tick":
            await self._manage_open_positions(event)
            return
        if event.kind == "history":
            history = event.payload.get("candles", [])
            target = self._bars[(event.exchange, event.symbol, settings.engine_timeframe)]
            target.extend(history[-1000:])
            return
        if event.kind != "candle":
            return
        key = (event.exchange, event.symbol, settings.engine_timeframe)
        bars = self._bars[key]
        candle = event.payload
        if bars and bars[-1]["timestamp"] == candle["timestamp"]:
            bars[-1] = candle
        else:
            bars.append(candle)
        async with self.database.sessions() as session:
            result = await session.execute(
                select(StrategyRecord, BrokerAccountRecord.broker)
                .outerjoin(BrokerAccountRecord, BrokerAccountRecord.id == StrategyRecord.broker_account_id)
                .where(StrategyRecord.enabled.is_(True))
            )
            strategies = list(result.all())

        for strategy, strategy_broker in strategies:
            rule = StrategyRule.model_validate(strategy.rule)
            if self._market_key(rule.symbol) != self._market_key(event.symbol) or rule.timeframe != settings.engine_timeframe:
                continue
            signal_key = (strategy.id, event.symbol)
            tenant_id = strategy.tenant_id or settings.tenant_id
            if not settings.engine_paper_mode and strategy_broker != event.exchange:
                continue
            if self.risk_for(tenant_id).halted or await self.cache.is_kill_switch_active(tenant_id):
                continue
            stamp = int(candle["timestamp"])
            if self._last_signal.get(signal_key) == stamp:
                continue
            if len(bars) < self._minimum_bars(rule):
                continue
            frame = pd.DataFrame(list(bars))
            signal = pd.Series(True, index=frame.index)
            for condition in rule.conditions:
                signal &= _condition_series(frame, condition).fillna(False)
            if bool(signal.iloc[-1]):
                self._last_signal[signal_key] = stamp
                await self._propose_paper_trade(strategy.id, rule, float(candle["close"]), list(bars),
                                                event.exchange, event.symbol, tenant_id, strategy.broker_account_id)

    async def _propose_paper_trade(self, strategy_id: str, rule: StrategyRule, entry: float,
                                  candles: list[dict] | None = None, exchange: str = "binance",
                                  market_symbol: str | None = None, tenant_id: str | None = None,
                                  broker_account_id: str | None = None) -> None:
        tenant = tenant_id or settings.tenant_id
        live_mode = not settings.engine_paper_mode
        if live_mode and (not settings.live_trading_enabled or not broker_account_id or self.live_positions is None):
            logger.error("Live strategy %s rejected: execution is disabled or no account is configured", strategy_id)
            return
        risk_manager = self.risk_for(tenant)
        is_buy = rule.side == "buy"
        distance = rule.stop_loss_pips * rule.pip_size
        stop = entry - distance if is_buy else entry + distance
        target = entry + rule.take_profit_pips * rule.pip_size if is_buy else entry - rule.take_profit_pips * rule.pip_size
        stop_distance = distance
        target_distance = rule.take_profit_pips * rule.pip_size
        request = TradeProposal(
            symbol=rule.symbol, side=rule.side, entry_price=entry, stop_loss=stop,
            take_profit=target, risk_pct=rule.risk_pct, account_equity=self._equity_by_tenant.get(tenant, settings.paper_account_equity),
        )
        assessment = risk_manager.validate(
            request, kill_switch=await self.cache.is_kill_switch_active(tenant), events=self.current_events(),
        )
        if settings.news_calendar_required and (self.calendar is None or not self.calendar.fresh):
            assessment["approved"] = False
            assessment["reasons"].append("Required high-impact news calendar is unavailable or stale")
            if live_mode:
                logger.error("Live strategy signal rejected because the required calendar is stale")
                return
        if live_mode and risk_manager.halted:
            logger.error("Live strategy signal rejected because the daily circuit breaker is active")
            return
        if not assessment["approved"]:
            logger.info("Strategy signal rejected by RMS for %s: %s", rule.symbol, assessment["reasons"])
            return

        if live_mode and settings.execution_mode == "full_auto":
            try:
                await self.live_positions.execute_strategy_order(
                    tenant_id=tenant, account_id=broker_account_id, strategy_id=strategy_id,
                    symbol=market_symbol or rule.symbol, side=rule.side, risk_pct=rule.risk_pct,
                    stop_distance=stop_distance, target_distance=target_distance,
                    risk_manager=risk_manager, events=self.current_events(), feed_broker=exchange,
                    calendar_fresh=self.calendar is not None and self.calendar.fresh,
                )
                self._proposals += 1
            except Exception:
                logger.exception("Live strategy signal rejected or failed for strategy %s", strategy_id)
            return

        trade_id = str(uuid4())
        record = TradeRecord(
            id=trade_id, tenant_id=tenant, broker_account_id=broker_account_id if live_mode else None,
            strategy_id=strategy_id, symbol=market_symbol or rule.symbol, side=rule.side,
            entry_price=entry, exit_price=None, quantity=assessment["quantity"], stop_loss=stop,
            take_profit=target, pnl=None,
            status="proposed" if settings.execution_mode == "semi_auto" else "open",
            mode="live" if live_mode else "paper",
            metadata_json={"risk_amount": assessment["risk_amount"], "risk_pct": rule.risk_pct,
                           "exchange": exchange, "market_symbol": market_symbol or rule.symbol,
                           "stop_distance": stop_distance, "target_distance": target_distance},
        )
        async with self.database.sessions() as session:
            session.add(record)
            session.add(AuditRecord(tenant_id=tenant,
                actor="strategy_engine", action="trade_proposed", entity_id=trade_id,
                details={"symbol": rule.symbol, "mode": "live" if live_mode else "paper"},
            ))
            await session.commit()
        self._proposals += 1
        if settings.execution_mode == "semi_auto":
            await self.telegram.send_trade_alert({
            "id": trade_id, "symbol": market_symbol or rule.symbol, "side": rule.side, "entry_price": entry,
            "quantity": assessment["quantity"], "risk_amount": assessment["risk_amount"],
            "risk_pct": rule.risk_pct, "stop_loss": stop, "take_profit": target,
                "candles": candles or [], "timeframe": rule.timeframe, "tenant_id": tenant,
                "mode": "live" if live_mode else "paper",
            })

    async def approve_paper_trade(self, trade_id: str, approved: bool, tenant_id: str | None = None) -> bool:
        scoped_tenant = tenant_id or settings.tenant_id
        kill_switch = await self.cache.is_kill_switch_active(scoped_tenant)
        async with self.database.sessions() as session:
            trade = await session.scalar(select(TradeRecord).where(
                TradeRecord.id == trade_id, TradeRecord.tenant_id == scoped_tenant,
            ))
            if trade is None or trade.status != "proposed":
                return False
            if trade.mode == "live":
                if not approved:
                    trade.status = "skipped"
                    session.add(AuditRecord(tenant_id=scoped_tenant, actor="telegram", action="live_trade_skipped",
                                            entity_id=trade_id, details={"strategy_id": trade.strategy_id}))
                    await session.commit()
                    return True
                if await self.cache.is_kill_switch_active(scoped_tenant) or self.live_positions is None:
                    return False
                strategy_id = trade.strategy_id or ""
                account_id = trade.broker_account_id or ""
                symbol, side = trade.symbol, trade.side
                metadata = dict(trade.metadata_json or {})
                risk_pct = float(metadata.get("risk_pct", 0))
                stop_distance = float(metadata.get("stop_distance", abs(trade.entry_price - trade.stop_loss)))
                target_distance = float(metadata.get("target_distance", abs(trade.take_profit - trade.entry_price)))
                if not account_id or not strategy_id:
                    return False
                approval_lock = self.cache.redis.lock(f"live:approval:{trade_id}", timeout=90, blocking_timeout=0)
                if not await approval_lock.acquire(blocking=False):
                    return False
                try:
                    await self.live_positions.execute_strategy_order(
                        tenant_id=scoped_tenant, account_id=account_id, strategy_id=strategy_id,
                        symbol=symbol, side=side, risk_pct=risk_pct,
                        stop_distance=stop_distance, target_distance=target_distance,
                        risk_manager=self.risk_for(scoped_tenant), events=self.current_events(), trade_id=trade_id,
                        feed_broker=str(metadata.get("exchange", "")) or None,
                        calendar_fresh=self.calendar is not None and self.calendar.fresh,
                    )
                except Exception:
                    logger.exception("Semi-automated live approval rejected for trade %s", trade_id)
                    return False
                finally:
                    try:
                        await approval_lock.release()
                    except Exception:
                        logger.exception("Could not release approval lock for trade %s", trade_id)
                return True
            if approved:
                request = TradeProposal(
                    symbol=trade.symbol, side=trade.side, entry_price=trade.entry_price,
                    stop_loss=trade.stop_loss, take_profit=trade.take_profit,
                    risk_pct=float(trade.metadata_json.get("risk_pct", 0)),
                    account_equity=self._equity_by_tenant.get(scoped_tenant, settings.paper_account_equity),
                )
                assessment = self.risk_for(scoped_tenant).validate(request, kill_switch=kill_switch, events=self.current_events())
                if settings.news_calendar_required and (self.calendar is None or not self.calendar.fresh):
                    return False
                if not assessment["approved"]:
                    return False
            trade.status = "open" if approved else "skipped"
            session.add(AuditRecord(tenant_id=scoped_tenant,
                actor="telegram", action="paper_trade_approved" if approved else "paper_trade_skipped",
                entity_id=trade_id, details={"paper_only": True},
            ))
            await session.commit()
        return True

    async def close_all_paper_positions(self, tenant_id: str | None = None) -> int:
        tenant = tenant_id or settings.tenant_id
        async with self.database.sessions() as session:
            result = await session.execute(select(TradeRecord).where(
                TradeRecord.tenant_id == tenant, TradeRecord.status == "open", TradeRecord.mode == "paper",
            ))
            open_trades = list(result.scalars())
            for trade in open_trades:
                market = trade.metadata_json.get("market_symbol", trade.symbol)
                exchange = trade.metadata_json.get("exchange", settings.crypto_exchange)
                tick = await self.cache.get_tick(exchange, market)
                price = float((tick or {}).get("last") or trade.entry_price)
                pnl = self._trade_pnl(trade, price)
                trade.exit_price = price
                trade.pnl = pnl
                trade.status = "closed_kill_switch"
                trade.closed_at = datetime.now(timezone.utc)
                equity = self._equity_by_tenant.get(tenant, settings.paper_account_equity) + pnl
                self._equity_by_tenant[tenant] = equity
                self.risk_for(tenant).record_realized_pnl(pnl, equity)
            if open_trades:
                session.add(AuditRecord(tenant_id=tenant, actor="telegram",
                                        action="paper_positions_closed", details={"count": len(open_trades)}))
            await session.commit()
            return len(open_trades)

    async def emergency_stop(self, actor: str, tenant_id: str | None = None) -> int:
        """Halt signal intake first, then close every simulated open position."""
        tenant = tenant_id or settings.tenant_id
        await self.cache.set_kill_switch(True, tenant)
        count = await self.close_all_paper_positions(tenant)
        async with self.database.sessions() as session:
            session.add(AuditRecord(tenant_id=tenant, actor=actor, action="emergency_kill_switch_activated",
                                    details={"closed_paper_positions": count}))
            await session.commit()
        return count

    async def resume(self, actor: str, tenant_id: str | None = None) -> None:
        tenant = tenant_id or settings.tenant_id
        await self.cache.set_kill_switch(False, tenant)
        async with self.database.sessions() as session:
            session.add(AuditRecord(tenant_id=tenant, actor=actor, action="emergency_kill_switch_cleared",
                                    details={"paper_only": True}))
            await session.commit()

    async def _manage_open_positions(self, event: MarketEvent) -> None:
        tick_price = self._tick_mark_price(event.payload)
        if tick_price is None:
            return
        price = tick_price
        tenants_to_close: set[str] = set()
        async with self.database.sessions() as session:
            result = await session.execute(select(TradeRecord).where(
                TradeRecord.status == "open", TradeRecord.mode == "paper",
            ))
            all_open_trades = list(result.scalars())
            open_trades = [trade for trade in all_open_trades
                           if self._market_key(trade.metadata_json.get("market_symbol", trade.symbol))
                           == self._market_key(event.symbol)
                           and trade.metadata_json.get("exchange", event.exchange) == event.exchange]
            unrealized: dict[str, float] = defaultdict(float)
            for trade in all_open_trades:
                tenant = trade.tenant_id or settings.tenant_id
                trade_market = trade.metadata_json.get("market_symbol", trade.symbol)
                trade_exchange = trade.metadata_json.get("exchange", settings.crypto_exchange)
                if trade_exchange == event.exchange and self._market_key(trade_market) == self._market_key(event.symbol):
                    mark = price
                else:
                    tick = await self.cache.get_tick(trade_exchange, trade_market)
                    mark = float((tick or {}).get("last") or trade.entry_price)
                unrealized[tenant] += self._trade_pnl(trade, mark)
            for tenant, floating_pnl in unrealized.items():
                equity = self._equity_by_tenant.get(tenant, settings.paper_account_equity) + floating_pnl
                if self.risk_for(tenant).mark_equity(equity):
                    tenants_to_close.add(tenant)
            for trade in open_trades:
                tenant = trade.tenant_id or settings.tenant_id
                killed = await self.cache.is_kill_switch_active(tenant)
                is_buy = trade.side == "buy"
                exit_reason = None
                if killed:
                    exit_reason = "closed_kill_switch"
                elif (is_buy and price <= trade.stop_loss) or (not is_buy and price >= trade.stop_loss):
                    exit_reason = "closed_stop_loss"
                elif (is_buy and price >= trade.take_profit) or (not is_buy and price <= trade.take_profit):
                    exit_reason = "closed_take_profit"
                if exit_reason:
                    pnl = self._trade_pnl(trade, price)
                    trade.exit_price = price
                    trade.pnl = pnl
                    trade.status = exit_reason
                    trade.closed_at = datetime.now(timezone.utc)
                    equity = self._equity_by_tenant.get(tenant, settings.paper_account_equity) + pnl
                    self._equity_by_tenant[tenant] = equity
                    self.risk_for(tenant).record_realized_pnl(pnl, equity)
                    if self.risk_for(tenant).halted:
                        tenants_to_close.add(tenant)
                    session.add(AuditRecord(tenant_id=tenant, actor="paper_engine", action=exit_reason, entity_id=trade.id,
                                            details={"exit_price": price, "pnl": pnl}))
            await session.commit()
        for tenant in tenants_to_close:
            await self.close_all_paper_positions(tenant)

    @staticmethod
    def _tick_mark_price(payload: dict) -> float | None:
        tick_price = payload.get("last")
        try:
            tick_price = float(tick_price) if tick_price is not None else 0.0
        except (TypeError, ValueError):
            tick_price = 0.0
        if not math.isfinite(tick_price) or tick_price <= 0:
            bid, ask = payload.get("bid"), payload.get("ask")
            try:
                bid, ask = float(bid), float(ask)
            except (TypeError, ValueError):
                return None
            if not math.isfinite(bid) or not math.isfinite(ask) or bid <= 0 or ask <= 0 or bid > ask:
                return None
            tick_price = (bid + ask) / 2
        return tick_price

    @staticmethod
    def _trade_pnl(trade: TradeRecord, exit_price: float) -> float:
        direction = 1 if trade.side == "buy" else -1
        return trade.quantity * (exit_price - trade.entry_price) * direction

    @staticmethod
    def _minimum_bars(rule: StrategyRule) -> int:
        return max((condition.period or 2 for condition in rule.conditions), default=2) + 2

    @staticmethod
    def _market_key(symbol: str) -> str:
        return symbol.upper().replace("/", "").replace("-", "").replace("_", "")

    def status(self) -> dict:
        return {"paper_only": settings.engine_paper_mode, "live_execution_enabled": settings.live_trading_enabled,
                "proposals": self._proposals, "strategies_evaluated_on_candles": True}

    def current_events(self):
        return self.calendar.events if self.calendar is not None and self.calendar.fresh else self.events
