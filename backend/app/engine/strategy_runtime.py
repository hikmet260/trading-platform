from __future__ import annotations

import asyncio
import hashlib
import logging
import json
import math
from collections import defaultdict, deque
from datetime import datetime, time, timedelta, timezone
from uuid import NAMESPACE_URL, uuid4, uuid5

import pandas as pd
from sqlalchemy import func, select

from app.engine.runner import MarketEvent
from app.instruments import InstrumentError, pnl_in_account, resolve_instrument
from app.risk.controls import RiskManager, TradeProposal
from app.risk.exposure import load_exposure
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
    """Evaluates enabled deterministic rules on incoming candle-close events."""

    def __init__(self, database: Database, cache: MarketCache, telegram: TelegramController) -> None:
        self.database = database
        self.cache = cache
        self.telegram = telegram
        self.paper_mode = settings.engine_paper_mode
        self.execution_mode = settings.execution_mode
        self.paper_equity = settings.paper_account_equity
        self.account_currency = settings.account_currency.upper()
        self.proposal_ttl_seconds = max(60, settings.proposal_ttl_seconds)
        self._risks: dict[str, RiskManager] = {}
        self.risk_for(settings.tenant_id).reset_day(self.paper_equity)
        self.events = parse_news_events(settings.news_events_json)
        self.calendar = None
        self.live_positions = None
        self._bars: dict[tuple[str, str, str], deque[dict]] = defaultdict(lambda: deque(maxlen=1000))
        self._last_signal: dict[tuple[str, str, str], int] = {}
        self._equity_by_tenant: dict[str, float] = {settings.tenant_id: self.paper_equity}
        self._proposals = 0
        self._expiry_task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    @property
    def risk(self) -> RiskManager:
        return self.risk_for(settings.tenant_id)

    def risk_for(self, tenant_id: str) -> RiskManager:
        if tenant_id not in self._risks:
            risk = RiskManager(
                settings.engine_max_daily_drawdown_pct,
                settings.engine_news_blackout_minutes,
                max_trade_risk_pct=settings.max_trade_risk_pct,
                max_open_risk_pct=settings.max_open_risk_pct,
                max_notional_multiple=settings.max_notional_multiple,
                max_positions_per_tenant=settings.max_positions_per_tenant,
                max_daily_trades=settings.max_daily_trades,
                account_currency=self.account_currency,
            )
            risk.reset_day(self.paper_equity)
            self._risks[tenant_id] = risk
        return self._risks[tenant_id]

    def apply_settings(
        self,
        *,
        execution_mode: str,
        paper_account_equity: float,
        max_daily_drawdown_pct: float,
        news_blackout_minutes: int,
        account_currency: str = "USD",
        max_trade_risk_pct: float | None = None,
        max_open_risk_pct: float | None = None,
        max_notional_multiple: float | None = None,
        max_positions_per_tenant: int | None = None,
        max_daily_trades: int | None = None,
        proposal_ttl_seconds: int | None = None,
    ) -> None:
        if execution_mode not in {"semi_auto", "full_auto"}:
            raise ValueError("Execution mode must be semi_auto or full_auto")
        delta = paper_account_equity - self.paper_equity
        self.paper_equity = paper_account_equity
        self.execution_mode = execution_mode
        self.account_currency = account_currency.strip().upper()
        if proposal_ttl_seconds is not None:
            self.proposal_ttl_seconds = max(60, proposal_ttl_seconds)
        for tenant in set(self._equity_by_tenant) | {settings.tenant_id}:
            self._equity_by_tenant[tenant] = self._equity_by_tenant.get(tenant, paper_account_equity) + delta
        for risk in self._risks.values():
            risk.configure(
                max_daily_drawdown_pct, news_blackout_minutes,
                max_trade_risk_pct=max_trade_risk_pct,
                max_open_risk_pct=max_open_risk_pct,
                max_notional_multiple=max_notional_multiple,
                max_positions_per_tenant=max_positions_per_tenant,
                max_daily_trades=max_daily_trades,
                account_currency=self.account_currency,
            )
        if self.execution_mode == "full_auto":
            # A proposal created under semi-auto cannot silently become a live order.
            self.execution_mode = execution_mode

    async def start(self) -> None:
        if self._expiry_task is None:
            self._stop.clear()
            self._expiry_task = asyncio.create_task(self._expiry_loop(), name="proposal-expiry")

    async def stop(self) -> None:
        self._stop.set()
        if self._expiry_task:
            self._expiry_task.cancel()
            await asyncio.gather(self._expiry_task, return_exceptions=True)
            self._expiry_task = None

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
            equity = self.paper_equity + total_pnl
            self._equity_by_tenant[tenant] = equity
            risk = self.risk_for(tenant)
            stored = await self.cache.redis.get(f"risk:settings:{tenant}")
            if stored:
                config = json.loads(stored)
                risk.configure(
                    float(config.get("max_daily_drawdown_pct", risk.max_daily_drawdown_pct)),
                    int(config.get("news_blackout_minutes", int(risk.news_blackout.total_seconds() // 60))),
                    max_trade_risk_pct=float(config["max_trade_risk_pct"]) if config.get("max_trade_risk_pct") is not None else None,
                    max_open_risk_pct=float(config["max_open_risk_pct"]) if config.get("max_open_risk_pct") is not None else None,
                    max_notional_multiple=float(config["max_notional_multiple"]) if config.get("max_notional_multiple") is not None else None,
                    max_positions_per_tenant=int(config["max_positions_per_tenant"]) if config.get("max_positions_per_tenant") is not None else None,
                    max_daily_trades=int(config["max_daily_trades"]) if config.get("max_daily_trades") is not None else None,
                )
            risk.reset_day(equity - today_pnl, today.isoformat())
            if today_pnl:
                risk.record_realized_pnl(today_pnl, equity, today.isoformat())

    async def on_event(self, event: MarketEvent) -> None:
        if event.kind == "tick":
            await self._manage_open_positions(event)
            return
        timeframe = event.timeframe or settings.engine_timeframe
        if event.kind == "history":
            history = event.payload.get("candles", [])
            target = self._bars[(event.exchange, event.symbol, timeframe)]
            target.clear()
            target.extend(history[-1000:])
            return
        if event.kind != "candle":
            return
        key = (event.exchange, event.symbol, timeframe)
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
            try:
                rule = StrategyRule.model_validate(strategy.rule)
            except Exception:
                logger.exception("Stored strategy %s has an invalid rule", strategy.id)
                continue
            if self._market_key(rule.symbol) != self._market_key(event.symbol) or rule.timeframe != timeframe:
                continue
            signal_key = (strategy.id, event.symbol, timeframe)
            tenant_id = strategy.tenant_id or settings.tenant_id
            if not self.paper_mode and strategy_broker != event.exchange:
                continue
            if self.risk_for(tenant_id).halted or await self.cache.is_kill_switch_active(tenant_id):
                continue
            stamp = int(candle["timestamp"])
            if self._last_signal.get(signal_key) == stamp:
                continue
            if len(bars) < self._minimum_bars(rule):
                continue
            frame = pd.DataFrame(list(bars))
            side = self._signal_side(rule, frame)
            if side is None:
                continue
            self._last_signal[signal_key] = stamp
            await self._propose_paper_trade(
                strategy.id, rule, float(candle["close"]), side, list(bars),
                event.exchange, event.symbol, tenant_id, strategy.broker_account_id,
            )

    async def _propose_paper_trade(
        self, strategy_id: str, rule: StrategyRule, entry: float, side: str,
        candles: list[dict] | None = None, exchange: str = "binance",
        market_symbol: str | None = None, tenant_id: str | None = None,
        broker_account_id: str | None = None,
    ) -> None:
        tenant = tenant_id or settings.tenant_id
        live_mode = not self.paper_mode
        signal_stamp = str(candles[-1].get("timestamp")) if candles else str(uuid4())
        client_order_id = self.signal_order_id(
            tenant, strategy_id, market_symbol or rule.symbol, rule.timeframe, signal_stamp, side,
        )
        trade_id = str(uuid5(NAMESPACE_URL, client_order_id))
        if live_mode and (not settings.live_trading_enabled or not broker_account_id or self.live_positions is None):
            logger.error("Live strategy %s rejected: execution is disabled or no account is configured", strategy_id)
            return
        risk_manager = self.risk_for(tenant)
        is_buy = side == "buy"
        market = market_symbol or rule.symbol
        try:
            spec = resolve_instrument(market)
        except InstrumentError as exc:
            logger.error("Strategy %s rejected: %s", strategy_id, exc)
            return

        distance = rule.stop_loss_pips * rule.pip_size
        target_distance = rule.take_profit_pips * rule.pip_size
        stop = entry - distance if is_buy else entry + distance
        target = entry + target_distance if is_buy else entry - target_distance

        mode = "live" if live_mode else "paper"
        async with self.database.sessions() as session:
            existing_signal = await session.scalar(select(TradeRecord.id).where(
                TradeRecord.tenant_id == tenant,
                TradeRecord.client_order_id == client_order_id,
            ))
            if existing_signal is not None:
                logger.info("Ignoring duplicate strategy signal %s", client_order_id)
                return
            active_trades_result = await session.execute(
                select(TradeRecord).where(
                    TradeRecord.strategy_id == strategy_id,
                    TradeRecord.status.in_(["open", "proposed", "submitting", "unknown"]),
                    TradeRecord.mode == mode,
                )
            )
            active_trades = [
                item for item in active_trades_result.scalars()
                if item.side == side and item.status in {"open", "proposed", "submitting", "unknown"}
            ]

        active_count = len(active_trades)
        current_tier_lot: float | None = None
        tier_number = active_count + 1
        if rule.tier_lots:
            if active_count >= len(rule.tier_lots):
                logger.info(
                    "Strategy %s reached maximum %s tiers for %s; skipping signal",
                    strategy_id, side, rule.symbol,
                )
                return
            current_tier_lot = rule.tier_lots[active_count]

        equity = self._equity_by_tenant.get(tenant, self.paper_equity)
        exposure = await load_exposure(self.database, tenant, mode, self.account_currency)
        request = TradeProposal(
            symbol=market, side=side, entry_price=entry, stop_loss=stop,
            take_profit=target, risk_pct=rule.risk_pct, account_equity=equity,
            fixed_quantity=current_tier_lot, account_currency=self.account_currency,
        )
        assessment = risk_manager.validate(
            request,
            kill_switch=await self.cache.is_kill_switch_active(tenant),
            events=self.current_events(),
            open_risk_amount=exposure.open_risk_amount,
            open_notional=exposure.open_notional,
            open_positions=exposure.open_positions,
            trades_today=exposure.trades_today,
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

        expires_at = datetime.now(timezone.utc) + timedelta(seconds=self.proposal_ttl_seconds)
        if live_mode and self.execution_mode == "full_auto":
            try:
                await self.live_positions.execute_strategy_order(
                    tenant_id=tenant, account_id=broker_account_id, strategy_id=strategy_id,
                    symbol=market, side=side, risk_pct=rule.risk_pct,
                    stop_distance=distance, target_distance=target_distance,
                    risk_manager=risk_manager, events=self.current_events(), feed_broker=exchange,
                    calendar_fresh=self.calendar is not None and self.calendar.fresh,
                    tier_lot=current_tier_lot,
                    max_tiers=len(rule.tier_lots) if rule.tier_lots else 1,
                    client_order_id=client_order_id,
                )
                self._proposals += 1
                risk_manager.record_trade_opened()
            except Exception:
                logger.exception("Live strategy signal rejected or failed for strategy %s", strategy_id)
                await self._queue_trade_event(
                    tenant_id=tenant, kind="trade_execution_issue", title="Strategy order needs review",
                    body=f"A live {side.upper()} signal for {market} was not confirmed. Review trade history before retrying.",
                    dedupe_key=f"strategy-order-issue:{client_order_id}",
                )
            return

        record = TradeRecord(
            id=trade_id, tenant_id=tenant, broker_account_id=broker_account_id if live_mode else None,
            strategy_id=strategy_id, symbol=market, side=side,
            entry_price=entry, exit_price=None, quantity=assessment["quantity"], stop_loss=stop,
            take_profit=target, pnl=None,
            status="proposed" if self.execution_mode == "semi_auto" else "open",
            mode=mode, client_order_id=client_order_id,
            expires_at=expires_at if self.execution_mode == "semi_auto" else None,
            metadata_json={
                "risk_amount": assessment["risk_amount"], "risk_pct": rule.risk_pct,
                "exchange": exchange, "market_symbol": market,
                "stop_distance": distance, "target_distance": target_distance,
                "tier": tier_number if current_tier_lot is not None else 1,
                "tier_lot_size": current_tier_lot, "base_lot_size": rule.base_lot_size,
                "max_tiers": len(rule.tier_lots) if rule.tier_lots else 1,
                "notional": assessment["notional"], "contract_size": assessment["contract_size"],
                "quote_currency": assessment["quote_currency"],
                "conversion_rate": assessment["conversion_rate"],
                "quantity_unit": assessment["quantity_unit"],
                "account_currency": assessment["account_currency"],
                "per_trade_risk_pct": assessment["per_trade_risk_pct"],
                "client_order_id": client_order_id,
                "expires_at": expires_at.isoformat() if self.execution_mode == "semi_auto" else None,
                "warnings": assessment["warnings"],
            },
        )
        proposal_alert = {
            "id": trade_id, "symbol": market, "side": side, "entry_price": entry,
            "quantity": assessment["quantity"], "risk_amount": assessment["risk_amount"],
            "risk_pct": rule.risk_pct, "stop_loss": stop, "take_profit": target,
            "candles": candles or [], "timeframe": rule.timeframe, "tenant_id": tenant,
            "mode": mode, "tier": tier_number if current_tier_lot is not None else None,
            "quantity_unit": assessment["quantity_unit"], "expires_at": expires_at.isoformat(),
        }
        async with self.database.sessions() as session:
            session.add(record)
            session.add(AuditRecord(tenant_id=tenant,
                actor="strategy_engine", action="trade_proposed", entity_id=trade_id,
                details={"symbol": rule.symbol, "side": side, "mode": mode,
                         "risk_amount": assessment["risk_amount"],
                         "quantity": assessment["quantity"],
                         "warnings": assessment["warnings"]},
            ))
            if self.execution_mode == "semi_auto":
                await self.telegram.send_trade_alert(proposal_alert, session=session)
            elif not live_mode:
                await self.telegram.send_event(
                    tenant_id=tenant, kind="paper_trade_opened", title="Paper trade opened",
                    body=f"{side.upper()} {market} was opened in paper mode.",
                    url="/trades", dedupe_key=f"paper-trade-opened:{trade_id}", session=session,
                )
            await session.commit()
        self._proposals += 1
        if self.execution_mode != "semi_auto":
            risk_manager.record_trade_opened()

    @staticmethod
    def signal_order_id(
        tenant_id: str, strategy_id: str, symbol: str, timeframe: str, timestamp: str, side: str,
    ) -> str:
        identity = "|".join((tenant_id, strategy_id, symbol, timeframe, timestamp, side.lower()))
        return "signal-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()

    async def _queue_trade_event(self, *, tenant_id: str, kind: str, title: str,
                                 body: str, dedupe_key: str) -> None:
        try:
            await self.telegram.send_event(
                tenant_id=tenant_id, kind=kind, title=title, body=body,
                url="/trades", dedupe_key=dedupe_key,
            )
        except Exception:
            logger.exception("Could not persist %s notification", kind)

    async def approve_paper_trade(self, trade_id: str, approved: bool, tenant_id: str | None = None) -> bool:
        scoped_tenant = tenant_id or settings.tenant_id
        kill_switch = await self.cache.is_kill_switch_active(scoped_tenant)
        async with self.database.sessions() as session:
            trade = await session.scalar(select(TradeRecord).where(
                TradeRecord.id == trade_id, TradeRecord.tenant_id == scoped_tenant,
            ))
            if trade is None or trade.status != "proposed":
                return False
            metadata = dict(trade.metadata_json or {})
            expires_at = trade.expires_at
            if expires_at is None:
                expires_raw = metadata.get("expires_at")
                if expires_raw:
                    try:
                        expires_at = datetime.fromisoformat(str(expires_raw))
                    except ValueError:
                        logger.warning("Invalid proposal expiry on trade %s", trade_id)
            if expires_at is not None:
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
                if expires_at <= datetime.now(timezone.utc):
                    trade.status = "expired"
                    session.add(AuditRecord(tenant_id=scoped_tenant, actor="risk_engine",
                                            action="trade_proposal_expired", entity_id=trade_id, details={}))
                    await session.commit()
                    return False
            if not approved:
                trade.status = "skipped"
                session.add(AuditRecord(tenant_id=scoped_tenant, actor="telegram", action="live_trade_skipped" if trade.mode == "live" else "paper_trade_skipped",
                                        entity_id=trade_id, details={"strategy_id": trade.strategy_id}))
                await session.commit()
                return True
            if trade.mode == "live":
                if kill_switch or self.live_positions is None:
                    return False
                strategy_id = trade.strategy_id or ""
                account_id = trade.broker_account_id or ""
                symbol, side = trade.symbol, trade.side
                risk_pct = float(metadata.get("risk_pct", 0) or 0.5)
                stop_distance = float(metadata.get("stop_distance", abs(trade.entry_price - trade.stop_loss)))
                target_distance = float(metadata.get("target_distance", abs(trade.take_profit - trade.entry_price)))
                tier_lot = float(metadata["tier_lot_size"]) if metadata.get("tier_lot_size") is not None else None
                max_tiers = int(metadata.get("max_tiers", 2 if tier_lot is not None else 1))
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
                        tier_lot=tier_lot, max_tiers=max_tiers,
                    )
                except Exception:
                    logger.exception("Semi-automated live approval rejected for trade %s", trade_id)
                    return False
                finally:
                    try:
                        await approval_lock.release()
                    except Exception:
                        logger.exception("Could not release approval lock for trade %s", trade_id)
                self.risk_for(scoped_tenant).record_trade_opened()
                return True
            tier_lot = float(metadata["tier_lot_size"]) if metadata.get("tier_lot_size") is not None else None
            equity = self._equity_by_tenant.get(scoped_tenant, self.paper_equity)
            exposure = await load_exposure(self.database, scoped_tenant, "paper", self.account_currency)
            request = TradeProposal(
                symbol=trade.symbol, side=trade.side, entry_price=trade.entry_price,
                stop_loss=trade.stop_loss, take_profit=trade.take_profit,
                risk_pct=float(metadata.get("risk_pct") or 0.5), account_equity=equity,
                fixed_quantity=tier_lot, account_currency=self.account_currency,
            )
            assessment = self.risk_for(scoped_tenant).validate(
                request, kill_switch=kill_switch, events=self.current_events(),
                open_risk_amount=exposure.open_risk_amount,
                open_notional=exposure.open_notional,
                open_positions=exposure.open_positions,
                trades_today=exposure.trades_today,
            )
            if settings.news_calendar_required and (self.calendar is None or not self.calendar.fresh):
                return False
            if not assessment["approved"]:
                logger.info("Approved proposal revalidation failed for %s: %s", trade_id, assessment["reasons"])
                return False
            trade.quantity = assessment["quantity"]
            metadata.update({
                "risk_amount": assessment["risk_amount"], "notional": assessment["notional"],
                "contract_size": assessment["contract_size"], "conversion_rate": assessment["conversion_rate"],
                "quantity_unit": assessment["quantity_unit"], "warnings": assessment["warnings"],
            })
            trade.metadata_json = metadata
            trade.status = "open"
            session.add(AuditRecord(tenant_id=scoped_tenant,
                actor="telegram", action="paper_trade_approved",
                entity_id=trade_id, details={"paper_only": True, "risk_amount": assessment["risk_amount"]},
            ))
            self.risk_for(scoped_tenant).record_trade_opened()
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
                metadata = dict(trade.metadata_json or {})
                market = metadata.get("market_symbol", trade.symbol)
                exchange = metadata.get("exchange", settings.crypto_exchange)
                tick = await self.cache.get_tick(exchange, market)
                last = (tick or {}).get("last")
                try:
                    price = float(last)
                except (TypeError, ValueError):
                    price = 0.0
                trade.closed_at = datetime.now(timezone.utc)
                if price > 0 and math.isfinite(price):
                    pnl = self._trade_pnl(trade, price)
                    trade.exit_price = price
                    trade.pnl = pnl
                    trade.status = "closed_kill_switch"
                    equity = self._equity_by_tenant.get(tenant, self.paper_equity) + pnl
                    self._equity_by_tenant[tenant] = equity
                    self.risk_for(tenant).record_realized_pnl(pnl, equity)
                else:
                    # Never invent a zero P&L when the market is unavailable.
                    trade.exit_price = None
                    trade.pnl = None
                    trade.status = "closed_kill_switch_unpriced"
                    metadata["pricing_error"] = "no_fresh_tick"
                    trade.metadata_json = metadata
                await self.telegram.send_event(
                    tenant_id=tenant, kind="trade_closed", title="Paper position closed",
                    body=f"{trade.side.upper()} {trade.symbol} was closed ({trade.status.replace('_', ' ')}).",
                    url="/trades", dedupe_key=f"paper-trade-closed:{trade.id}", session=session,
                )
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
            if not all_open_trades:
                return
            open_trades = [trade for trade in all_open_trades
                           if self._market_key((trade.metadata_json or {}).get("market_symbol", trade.symbol))
                           == self._market_key(event.symbol)
                           and (trade.metadata_json or {}).get("exchange", event.exchange) == event.exchange]
            unrealized: dict[str, float] = defaultdict(float)
            for trade in all_open_trades:
                tenant = trade.tenant_id or settings.tenant_id
                trade_market = (trade.metadata_json or {}).get("market_symbol", trade.symbol)
                trade_exchange = (trade.metadata_json or {}).get("exchange", settings.crypto_exchange)
                if trade_exchange == event.exchange and self._market_key(trade_market) == self._market_key(event.symbol):
                    mark = price
                else:
                    tick = await self.cache.get_tick(trade_exchange, trade_market)
                    mark = float((tick or {}).get("last") or trade.entry_price)
                unrealized[tenant] += self._trade_pnl(trade, mark)
            for tenant, floating_pnl in unrealized.items():
                equity = self._equity_by_tenant.get(tenant, self.paper_equity) + floating_pnl
                was_halted = self.risk_for(tenant).halted
                if self.risk_for(tenant).mark_equity(equity):
                    tenants_to_close.add(tenant)
                    if not was_halted:
                        day = datetime.now(timezone.utc).date().isoformat()
                        await self.telegram.send_event(
                            tenant_id=tenant, kind="risk_halt", title="Paper trading halted",
                            body="The daily drawdown limit was reached. New strategy entries are paused.",
                            url="/settings", dedupe_key=f"risk-halt:{tenant}:{day}", session=session,
                        )
            for trade in open_trades:
                tenant = trade.tenant_id or settings.tenant_id
                killed = await self.cache.is_kill_switch_active(tenant)
                is_buy = trade.side == "buy"
                exit_reason = None
                if killed:
                    exit_reason = "closed_kill_switch"
                elif trade.stop_loss is not None and ((is_buy and price <= trade.stop_loss) or (not is_buy and price >= trade.stop_loss)):
                    exit_reason = "closed_stop_loss"
                elif trade.take_profit is not None and ((is_buy and price >= trade.take_profit) or (not is_buy and price <= trade.take_profit)):
                    exit_reason = "closed_take_profit"
                if exit_reason:
                    pnl = self._trade_pnl(trade, price)
                    trade.exit_price = price
                    trade.pnl = pnl
                    trade.status = exit_reason
                    trade.closed_at = datetime.now(timezone.utc)
                    equity = self._equity_by_tenant.get(tenant, self.paper_equity) + pnl
                    self._equity_by_tenant[tenant] = equity
                    risk_manager = self.risk_for(tenant)
                    was_halted = risk_manager.halted
                    risk_manager.record_realized_pnl(pnl, equity)
                    if self.risk_for(tenant).halted:
                        tenants_to_close.add(tenant)
                        if not was_halted:
                            day = datetime.now(timezone.utc).date().isoformat()
                            await self.telegram.send_event(
                                tenant_id=tenant, kind="risk_halt", title="Paper trading halted",
                                body="The daily drawdown limit was reached. New strategy entries are paused.",
                                url="/settings", dedupe_key=f"risk-halt:{tenant}:{day}", session=session,
                            )
                    session.add(AuditRecord(tenant_id=tenant, actor="paper_engine", action=exit_reason, entity_id=trade.id,
                                            details={"exit_price": price, "pnl": pnl}))
                    await self.telegram.send_event(
                        tenant_id=tenant, kind="trade_closed", title="Paper position closed",
                        body=f"{trade.side.upper()} {trade.symbol} was closed ({exit_reason.replace('_', ' ')}).",
                        url="/trades", dedupe_key=f"paper-trade-closed:{trade.id}", session=session,
                    )
            await session.commit()
        for tenant in tenants_to_close:
            await self.close_all_paper_positions(tenant)

    async def expire_proposals(self) -> int:
        now = datetime.now(timezone.utc)
        expired = 0
        async with self.database.sessions() as session:
            result = await session.execute(select(TradeRecord).where(
                TradeRecord.status == "proposed", TradeRecord.mode.in_(["paper", "live"]),
            ))
            for trade in result.scalars():
                expiry = trade.expires_at
                if expiry is None:
                    raw = (trade.metadata_json or {}).get("expires_at")
                    if not raw:
                        continue
                    try:
                        expiry = datetime.fromisoformat(str(raw))
                    except ValueError:
                        continue
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=timezone.utc)
                if expiry <= now:
                    trade.status = "expired"
                    expired += 1
                    session.add(AuditRecord(
                        tenant_id=trade.tenant_id or settings.tenant_id,
                        actor="risk_engine", action="trade_proposal_expired",
                        entity_id=trade.id, details={"mode": trade.mode},
                    ))
            if expired:
                await session.commit()
        return expired

    async def _expiry_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.expire_proposals()
            except Exception:
                logger.exception("Proposal expiry sweep failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=30)
            except asyncio.TimeoutError:
                pass

    @staticmethod
    def _signal_side(rule: StrategyRule, frame: pd.DataFrame) -> str | None:
        long_signal = pd.Series(True, index=frame.index)
        for condition in rule.conditions:
            long_signal &= _condition_series(frame, condition).fillna(False)
        long_fired = bool(long_signal.iloc[-1])
        if rule.side == "buy":
            return "buy" if long_fired else None
        if rule.side == "sell":
            return "sell" if long_fired else None
        short_conditions = rule.short_conditions or ()
        short_signal = pd.Series(True, index=frame.index)
        for condition in short_conditions:
            short_signal &= _condition_series(frame, condition).fillna(False)
        short_fired = bool(short_signal.iloc[-1]) if short_conditions else False
        if long_fired and short_fired:
            logger.warning("Ambiguous both-direction signal for %s; skipping", rule.symbol)
            return None
        if long_fired:
            return "buy"
        if short_fired:
            return "sell"
        return None

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

    def _trade_pnl(self, trade: TradeRecord, exit_price: float) -> float:
        metadata = dict(trade.metadata_json or {})
        account_currency = str(metadata.get("account_currency") or self.account_currency)
        try:
            spec = resolve_instrument(trade.symbol)
            return pnl_in_account(spec, trade.quantity, trade.entry_price, exit_price, trade.side, account_currency)
        except InstrumentError:
            logger.exception("Could not value P&L for trade %s (%s)", trade.id, trade.symbol)
            return 0.0

    @staticmethod
    def _minimum_bars(rule: StrategyRule) -> int:
        conditions = list(rule.conditions) + list(rule.short_conditions or ())
        periods = [condition.period or 2 for condition in conditions]
        periods.extend(condition.comparison_period or 2 for condition in conditions if condition.kind == "cross")
        return max(periods, default=2) + 2

    @staticmethod
    def _market_key(symbol: str) -> str:
        return symbol.upper().replace("/", "").replace("-", "").replace("_", "")

    def status(self) -> dict:
        return {
            "paper_only": self.paper_mode,
            "live_execution_enabled": settings.live_trading_enabled,
            "execution_mode": self.execution_mode,
            "paper_account_equity": self.paper_equity,
            "proposals": self._proposals,
            "proposal_ttl_seconds": self.proposal_ttl_seconds,
            "strategies_evaluated_on_candles": True,
        }

    def current_events(self):
        return self.calendar.events if self.calendar is not None and self.calendar.fresh else self.events
