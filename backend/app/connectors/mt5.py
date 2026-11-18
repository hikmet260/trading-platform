from __future__ import annotations

import asyncio
import importlib
import platform
from typing import Any

from app.connectors.base import BrokerConnector


class MT5Connector(BrokerConnector):
    """Adapter around the MetaTrader5 terminal IPC Python API."""

    def __init__(self, login: str = "", password: str = "", server: str = "", terminal_path: str = "") -> None:
        self.login = int(login) if login else None
        self.password = password
        self.server = server
        self.terminal_path = terminal_path or None
        self._mt5: Any | None = None
        self._lock = asyncio.Lock()
        self.state = "disabled"
        self.detail: str | None = None

    @property
    def broker_id(self) -> str:
        return "mt5"

    async def connect(self, enabled: bool) -> None:
        if not enabled:
            self.state = "disabled"
            return
        if platform.system() != "Windows":
            self.state = "unavailable"
            self.detail = "MetaTrader5 Python API requires a Windows host with the MT5 terminal installed."
            return
        try:
            self._mt5 = importlib.import_module("MetaTrader5")
        except ImportError:
            self.state = "unavailable"
            self.detail = "Install backend/requirements-mt5-windows.txt on the Windows MT5 host."
            return

        async with self._lock:
            initialized = await asyncio.to_thread(self._mt5.initialize, self.terminal_path) if self.terminal_path else await asyncio.to_thread(self._mt5.initialize)
            if not initialized:
                self.state = "disconnected"
                self.detail = f"MT5 initialize failed: {self._mt5.last_error()}"
                return
            if self.login and not await asyncio.to_thread(self._mt5.login, self.login, password=self.password, server=self.server):
                self.state = "disconnected"
                self.detail = f"MT5 login failed: {self._mt5.last_error()}"
                await asyncio.to_thread(self._mt5.shutdown)
                return
            self.state = "connected"
            self.detail = None

    async def quote(self, symbol: str) -> dict[str, Any]:
        async with self._lock:
            mt5 = self._require_connection()
            selected = await asyncio.to_thread(mt5.symbol_select, symbol, True)
            if not selected:
                raise ValueError(f"Could not select MT5 symbol '{symbol}': {mt5.last_error()}")
            tick = await asyncio.to_thread(mt5.symbol_info_tick, symbol)
            if tick is None:
                raise RuntimeError(f"No MT5 tick available for '{symbol}': {mt5.last_error()}")
            return {
                "broker": "mt5",
                "symbol": symbol,
                "timestamp": int(tick.time_msc),
                "bid": tick.bid,
                "ask": tick.ask,
                "last": tick.last,
                "volume": tick.volume,
            }

    async def candles(self, symbol: str, timeframe: str, limit: int) -> list[dict[str, Any]]:
        async with self._lock:
            mt5 = self._require_connection()
            selected = await asyncio.to_thread(mt5.symbol_select, symbol, True)
            if not selected:
                raise ValueError(f"Could not select MT5 symbol '{symbol}': {mt5.last_error()}")
            tf_constant = self._timeframe_constant(mt5, timeframe)
            rows = await asyncio.to_thread(mt5.copy_rates_from_pos, symbol, tf_constant, 0, limit)
            if rows is None:
                raise RuntimeError(f"MT5 candle request failed: {mt5.last_error()}")
            return [
                {"timestamp": int(row["time"]) * 1000, "open": float(row["open"]), "high": float(row["high"]),
                 "low": float(row["low"]), "close": float(row["close"]), "volume": float(row["tick_volume"])}
                for row in rows
            ]

    async def execute_protected_market_order(
        self, symbol: str, side: str, volume: float, stop_loss: float, take_profit: float,
        *, deviation: int = 20, magic: int = 260114, client_order_id: str | None = None,
    ) -> dict[str, Any]:
        """Submit one market order with SL/TP in the same MT5 request; never opens naked risk."""
        if side.lower() not in {"buy", "sell"} or min(volume, stop_loss, take_profit) <= 0:
            raise ValueError("Side, volume, stop-loss, and take-profit must be valid positive values")
        async with self._lock:
            mt5 = self._require_connection()
            if not await asyncio.to_thread(mt5.symbol_select, symbol, True):
                raise ValueError(f"Could not select MT5 symbol '{symbol}'")
            info = await asyncio.to_thread(mt5.symbol_info, symbol)
            tick = await asyncio.to_thread(mt5.symbol_info_tick, symbol)
            if info is None or tick is None:
                raise RuntimeError(f"MT5 symbol metadata or quote unavailable for '{symbol}'")
            is_buy = side.lower() == "buy"
            price = float(tick.ask if is_buy else tick.bid)
            if price <= 0 or (is_buy and not (stop_loss < price < take_profit)) or (not is_buy and not (take_profit < price < stop_loss)):
                raise ValueError("Protective prices are on the wrong side of the live market")
            step = float(info.volume_step)
            if step <= 0 or volume < float(info.volume_min) or volume > float(info.volume_max):
                raise ValueError("Requested volume is outside the broker's permitted range")
            normalized_volume = round(round(volume / step) * step, 8)
            if normalized_volume < float(info.volume_min):
                raise ValueError("Requested volume rounds below the broker minimum")
            digits = int(info.digits)
            if int(info.filling_mode) & int(getattr(mt5, "SYMBOL_FILLING_IOC", 2)):
                filling = mt5.ORDER_FILLING_IOC
            elif int(info.filling_mode) & int(getattr(mt5, "SYMBOL_FILLING_FOK", 1)):
                filling = mt5.ORDER_FILLING_FOK
            else:
                filling = mt5.ORDER_FILLING_RETURN
            request = {
                "action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": normalized_volume,
                "type": mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL,
                "price": round(price, digits), "sl": round(stop_loss, digits),
                "tp": round(take_profit, digits), "deviation": deviation, "magic": magic,
                "comment": (f"tp-{client_order_id}"[:31] if client_order_id else "trading-platform-protected"),
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": filling,
            }
            checked = await asyncio.to_thread(mt5.order_check, request)
            if checked is None or getattr(checked, "retcode", None) != 0:
                raise RuntimeError(f"MT5 rejected protected order check: {getattr(checked, 'comment', mt5.last_error())}")
            result = await asyncio.to_thread(mt5.order_send, request)
            accepted = {mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED, mt5.TRADE_RETCODE_DONE_PARTIAL}
            if result is None or result.retcode not in accepted:
                raise RuntimeError(f"MT5 rejected protected order: {getattr(result, 'comment', mt5.last_error())}")
            deal_rows = await asyncio.to_thread(mt5.history_deals_get, ticket=int(result.deal)) if result.deal else None
            position_ticket = str(getattr(deal_rows[0], "position_id", result.order)) if deal_rows else None
            executed_volume = float(getattr(result, "volume", 0) or 0)
            if result.retcode == mt5.TRADE_RETCODE_DONE and executed_volume <= 0:
                executed_volume = normalized_volume
            if result.retcode == mt5.TRADE_RETCODE_DONE_PARTIAL and executed_volume <= 0:
                raise RuntimeError("MT5 reported a partial fill without an executed volume")
            order_status = (
                "pending" if result.retcode == mt5.TRADE_RETCODE_PLACED else
                "partial" if result.retcode == mt5.TRADE_RETCODE_DONE_PARTIAL else "filled"
            )
            return {"broker": "mt5", "order_id": str(result.order), "deal_id": str(result.deal),
                    "position_ticket": position_ticket,
                    "symbol": symbol, "side": side.lower(), "quantity": executed_volume,
                    "entry_price": float(result.price or price), "stop_loss": round(stop_loss, digits),
                    "take_profit": round(take_profit, digits), "status": order_status}

    async def account_equity(self) -> float:
        async with self._lock:
            info = await asyncio.to_thread(self._require_connection().account_info)
            equity = float(getattr(info, "equity", 0) or 0) if info is not None else 0
            if equity <= 0:
                raise RuntimeError("MT5 account equity is unavailable")
            return equity

    async def account_status(self) -> dict[str, Any]:
        async with self._lock:
            mt5 = self._require_connection()
            info = await asyncio.to_thread(mt5.account_info)
            terminal = await asyncio.to_thread(mt5.terminal_info)
            if info is None or terminal is None:
                raise RuntimeError("MT5 account or terminal status is unavailable")
            trade_mode = {
                getattr(mt5, "ACCOUNT_TRADE_MODE_DEMO", -1): "demo",
                getattr(mt5, "ACCOUNT_TRADE_MODE_CONTEST", -2): "contest",
                getattr(mt5, "ACCOUNT_TRADE_MODE_REAL", -3): "real",
            }.get(getattr(info, "trade_mode", None), "unknown")
            return {
                "login": str(getattr(info, "login", "")),
                "server": str(getattr(info, "server", "")),
                "company": str(getattr(info, "company", "")),
                "currency": str(getattr(info, "currency", "")),
                "equity": float(getattr(info, "equity", 0) or 0),
                "trade_mode": trade_mode,
                "trade_allowed": bool(getattr(terminal, "trade_allowed", False)),
                "trade_expert": bool(getattr(terminal, "trade_expert", False)),
                "terminal_version": str(getattr(terminal, "build", "")),
            }

    async def symbol_info(self, symbol: str) -> dict[str, Any]:
        async with self._lock:
            mt5 = self._require_connection()
            if not await asyncio.to_thread(mt5.symbol_select, symbol, True):
                raise ValueError(f"Could not select MT5 symbol '{symbol}'")
            info = await asyncio.to_thread(mt5.symbol_info, symbol)
            if info is None:
                raise RuntimeError(f"MT5 symbol metadata unavailable for '{symbol}'")
            return {
                "volume_step": float(info.volume_step),
                "volume_min": float(info.volume_min),
                "volume_max": float(info.volume_max),
                "digits": int(info.digits),
                "contract_size": float(getattr(info, "trade_contract_size", 0) or 0),
                "trade_tick_size": float(getattr(info, "trade_tick_size", 0) or 0),
                "trade_tick_value": float(getattr(info, "trade_tick_value", 0) or 0),
            }

    async def risk_per_lot(self, symbol: str, entry: float, stop_loss: float) -> float:
        async with self._lock:
            info = await asyncio.to_thread(self._require_connection().symbol_info, symbol)
            tick_size = float(getattr(info, "trade_tick_size", 0) or 0) if info is not None else 0
            tick_value = float(getattr(info, "trade_tick_value", 0) or 0) if info is not None else 0
            if tick_size <= 0 or tick_value <= 0:
                raise RuntimeError("MT5 symbol risk metadata is unavailable")
            return abs(entry - stop_loss) / tick_size * tick_value

    async def position(self, ticket: str) -> dict[str, Any] | None:
        async with self._lock:
            mt5 = self._require_connection()
            rows = await asyncio.to_thread(mt5.positions_get, ticket=int(ticket))
            if rows is None:
                raise RuntimeError(f"MT5 position query failed: {mt5.last_error()}")
            if not rows:
                return None
            row = rows[0]
            return {"ticket": str(row.ticket), "symbol": row.symbol, "volume": float(row.volume),
                    "side": "buy" if row.type == mt5.POSITION_TYPE_BUY else "sell",
                    "price_open": float(row.price_open), "price_current": float(row.price_current),
                    "profit": float(row.profit), "time": int(row.time)}

    async def positions_for_symbol(self, symbol: str) -> list[dict[str, Any]]:
        async with self._lock:
            mt5 = self._require_connection()
            rows = await asyncio.to_thread(mt5.positions_get, symbol=symbol)
            if rows is None:
                raise RuntimeError(f"MT5 position query failed: {mt5.last_error()}")
            return [{"ticket": str(row.ticket), "side": "buy" if row.type == mt5.POSITION_TYPE_BUY else "sell",
                     "volume": float(row.volume)} for row in rows]

    async def order_status(self, order_id: str) -> str | None:
        async with self._lock:
            mt5 = self._require_connection()
            ticket = int(order_id)
            active = await asyncio.to_thread(mt5.orders_get, ticket=ticket)
            if active is None:
                raise RuntimeError(f"MT5 active-order query failed: {mt5.last_error()}")
            if active:
                return "pending"
            history = await asyncio.to_thread(mt5.history_orders_get, ticket=ticket)
            if history is None:
                raise RuntimeError(f"MT5 order-history query failed: {mt5.last_error()}")
            if not history:
                return None
            state = history[0].state
            mapping = {
                getattr(mt5, "ORDER_STATE_STARTED", -1): "pending",
                getattr(mt5, "ORDER_STATE_PLACED", -2): "pending",
                getattr(mt5, "ORDER_STATE_PARTIAL", -3): "partial",
                getattr(mt5, "ORDER_STATE_FILLED", -4): "filled",
                getattr(mt5, "ORDER_STATE_CANCELED", -5): "canceled",
                getattr(mt5, "ORDER_STATE_REJECTED", -6): "rejected",
                getattr(mt5, "ORDER_STATE_EXPIRED", -7): "expired",
            }
            return mapping.get(state, "unknown")

    async def close_position(self, ticket: str, *, deviation: int = 30) -> dict[str, Any]:
        async with self._lock:
            mt5 = self._require_connection()
            rows = await asyncio.to_thread(mt5.positions_get, ticket=int(ticket))
            if rows is None:
                raise RuntimeError(f"MT5 position query failed: {mt5.last_error()}")
            if not rows:
                orders = await asyncio.to_thread(mt5.orders_get, ticket=int(ticket))
                if orders is None:
                    raise RuntimeError(f"MT5 pending-order query failed: {mt5.last_error()}")
                if orders:
                    removed = await asyncio.to_thread(mt5.order_send, {
                        "action": mt5.TRADE_ACTION_REMOVE, "order": int(ticket),
                        "comment": "trading-platform-kill-switch",
                    })
                    if removed is None or removed.retcode != mt5.TRADE_RETCODE_DONE:
                        raise RuntimeError(f"MT5 pending-order cancel rejected: {getattr(removed, 'comment', mt5.last_error())}")
                    return {"already_closed": False, "canceled_unfilled_entry": True, "ticket": ticket}
                return {"already_closed": True, "ticket": ticket}
            position = rows[0]
            is_buy = position.type == mt5.POSITION_TYPE_BUY
            tick = await asyncio.to_thread(mt5.symbol_info_tick, position.symbol)
            info = await asyncio.to_thread(mt5.symbol_info, position.symbol)
            if tick is None or info is None:
                raise RuntimeError("MT5 quote or symbol metadata unavailable while closing position")
            request = {
                "action": mt5.TRADE_ACTION_DEAL, "symbol": position.symbol,
                "position": position.ticket, "volume": float(position.volume),
                "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
                "price": float(tick.bid if is_buy else tick.ask), "deviation": deviation,
                "magic": 260114, "comment": "trading-platform-kill-switch",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC if int(info.filling_mode) & int(getattr(mt5, "SYMBOL_FILLING_IOC", 2)) else mt5.ORDER_FILLING_FOK,
            }
            result = await asyncio.to_thread(mt5.order_send, request)
            accepted = {mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_DONE_PARTIAL}
            if result is None or result.retcode not in accepted:
                raise RuntimeError(f"MT5 close rejected: {getattr(result, 'comment', mt5.last_error())}")
            remaining = await asyncio.to_thread(mt5.positions_get, ticket=int(ticket))
            if remaining is None:
                raise RuntimeError(f"MT5 close could not be verified: {mt5.last_error()}")
            if remaining:
                raise RuntimeError("MT5 reports a remaining partial position after close request")
            return {"already_closed": False, "ticket": ticket, "deal_id": str(result.deal),
                    "price": float(result.price or request["price"]), "volume": float(result.volume)}

    async def position_exit(self, ticket: str) -> dict[str, Any] | None:
        async with self._lock:
            mt5 = self._require_connection()
            rows = await asyncio.to_thread(mt5.history_deals_get, position=int(ticket))
            if rows is None:
                raise RuntimeError(f"MT5 deal history query failed: {mt5.last_error()}")
            exits = [row for row in rows if row.entry in {mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY}]
            if not exits:
                return None
            volume = sum(float(row.volume) for row in exits)
            price = sum(float(row.price) * float(row.volume) for row in exits) / volume if volume else 0.0
            pnl = sum(float(row.profit) + float(row.swap) + float(row.commission) + float(row.fee) for row in exits)
            return {"price": price, "pnl": pnl, "volume": volume, "deal_ids": [str(row.ticket) for row in exits]}

    async def close(self) -> None:
        if self._mt5 is not None and self.state == "connected":
            async with self._lock:
                await asyncio.to_thread(self._mt5.shutdown)
        if self.state == "connected":
            self.state = "disconnected"

    def _require_connection(self) -> Any:
        if self.state != "connected" or self._mt5 is None:
            raise RuntimeError(self.detail or "MT5 connector is not connected.")
        return self._mt5

    @staticmethod
    def _timeframe_constant(mt5: Any, timeframe: str) -> int:
        values = {
            "1m": mt5.TIMEFRAME_M1, "5m": mt5.TIMEFRAME_M5, "15m": mt5.TIMEFRAME_M15,
            "30m": mt5.TIMEFRAME_M30, "1h": mt5.TIMEFRAME_H1, "4h": mt5.TIMEFRAME_H4,
            "1d": mt5.TIMEFRAME_D1,
        }
        try:
            return values[timeframe.lower()]
        except KeyError as exc:
            raise ValueError("Unsupported timeframe. Use 1m, 5m, 15m, 30m, 1h, 4h, or 1d.") from exc
