from __future__ import annotations

import importlib
from typing import Any

import ccxt.async_support as ccxt
from ccxt.base.errors import BadRequest, BadSymbol, ExchangeError, ExchangeNotAvailable, NetworkError

from app.connectors.base import BrokerConnector

SUPPORTED_EXCHANGES = frozenset({"binance", "bybit", "okx"})


def _exchange_class(module: Any, exchange_id: str) -> type:
    if exchange_id not in SUPPORTED_EXCHANGES:
        raise ValueError(f"Unsupported exchange '{exchange_id}'. Choose binance, bybit, or okx.")
    try:
        return getattr(module, exchange_id)
    except AttributeError as exc:
        raise ValueError(f"Exchange '{exchange_id}' is not available in the installed CCXT version.") from exc


class CryptoMarketData(BrokerConnector):
    """Async CCXT REST market data and optional CCXT Pro WebSocket streaming."""

    def __init__(
        self,
        exchange_id: str,
        api_key: str = "",
        secret: str = "",
        password: str = "",
        sandbox: bool = True,
        default_type: str = "spot",
    ) -> None:
        self.exchange_id = exchange_id.lower()
        exchange_type = _exchange_class(ccxt, self.exchange_id)
        config: dict[str, Any] = {
            "enableRateLimit": True,
            "timeout": 8_000,
            "options": {"defaultType": default_type},
        }
        if api_key:
            config["apiKey"] = api_key
        if secret:
            config["secret"] = secret
        if password:
            config["password"] = password
        self.rest = exchange_type(config)
        self.sandbox = sandbox
        self.pro_module = self._load_pro_module()
        self.ws = None
        if self.pro_module is not None:
            pro_type = _exchange_class(self.pro_module, self.exchange_id)
            self.ws = pro_type(config)
        if sandbox:
            for exchange in (self.rest, self.ws):
                if exchange is not None and exchange.has.get("sandbox"):
                    exchange.set_sandbox_mode(True)

    @property
    def broker_id(self) -> str:
        return self.exchange_id

    async def quote(self, symbol: str) -> dict[str, Any]:
        return await self.ticker(symbol)

    @staticmethod
    def _load_pro_module() -> Any | None:
        for name in ("ccxtpro", "ccxt.pro"):
            try:
                return importlib.import_module(name)
            except ImportError:
                continue
        return None

    @property
    def websocket_available(self) -> bool:
        return self.ws is not None and bool(self.ws.has.get("watchTicker"))

    async def ticker(self, symbol: str) -> dict[str, Any]:
        ticker = await self.rest.fetch_ticker(symbol)
        return self._ticker_payload(ticker, self.exchange_id, symbol)

    async def candles(self, symbol: str, timeframe: str, limit: int) -> list[dict[str, Any]]:
        rows = await self.rest.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        return [
            {"timestamp": row[0], "open": row[1], "high": row[2], "low": row[3], "close": row[4], "volume": row[5]}
            for row in rows
        ]

    async def execute_protected_market_order(
        self, symbol: str, side: str, amount: float, stop_loss: float, take_profit: float,
    ) -> dict[str, Any]:
        """Place only when the venue advertises atomic attached stop and target support."""
        if not self.rest.apiKey or not self.rest.secret:
            raise RuntimeError("Authenticated exchange credentials are required for order execution")
        if self.rest.options.get("defaultType") not in {"swap", "future", "futures"}:
            raise ValueError("Live crypto orders require a derivatives market for position reconciliation and reduce-only closure")
        if side.lower() not in {"buy", "sell"} or min(amount, stop_loss, take_profit) <= 0:
            raise ValueError("Side, amount, stop-loss, and take-profit must be valid positive values")
        ticker = await self.rest.fetch_ticker(symbol)
        market = self.rest.market(symbol)
        if market.get("linear") is not True:
            raise ValueError("Only linear derivatives are supported by the live risk model")
        price = float(ticker.get("last") or 0)
        if price <= 0 or (side.lower() == "buy" and not stop_loss < price < take_profit) or (
            side.lower() == "sell" and not take_profit < price < stop_loss
        ):
            raise ValueError("Protective prices are on the wrong side of the live market")
        if side.lower() == "sell" and not market.get("contract"):
            raise ValueError("Short selling requires a derivatives market; spot sell orders are not short positions")
        for feature in ("stopLoss", "takeProfit"):
            supported = self.rest.feature_value(symbol, "createOrder", feature)
            if supported is not True:
                raise RuntimeError(f"Exchange does not explicitly guarantee attached {feature} orders for {symbol}")
        normalized_amount = float(self.rest.amount_to_precision(symbol, amount))
        params = {
            "stopLoss": {"triggerPrice": float(self.rest.price_to_precision(symbol, stop_loss))},
            "takeProfit": {"triggerPrice": float(self.rest.price_to_precision(symbol, take_profit))},
        }
        order = await self.rest.create_order(symbol, "market", side.lower(), normalized_amount, None, params)
        if not order.get("id"):
            raise RuntimeError("Exchange returned no order identifier; execution outcome requires reconciliation")
        return {"broker": self.exchange_id, "order_id": str(order["id"]), "symbol": symbol,
                "side": side.lower(), "quantity": normalized_amount,
                "entry_price": float(order.get("average") or order.get("price") or price),
                "stop_loss": stop_loss, "take_profit": take_profit,
                "status": str(order.get("status") or "accepted")}

    async def account_equity(self, currency: str = "USDT") -> float:
        if not self.rest.apiKey or not self.rest.secret:
            raise RuntimeError("Authenticated exchange credentials are required to read account equity")
        balance = await self.rest.fetch_balance()
        info = balance.get("info") or {}
        authoritative = None
        if isinstance(info, dict):
            for field in ("totalMarginBalance", "totalEquity", "equity", "totalEq"):
                try:
                    candidate = float(info.get(field) or 0)
                except (TypeError, ValueError):
                    continue
                if candidate > 0:
                    authoritative = candidate
                    break
        total = authoritative or float((balance.get("total") or {}).get(currency.upper()) or 0)
        if authoritative is None and self.rest.has.get("fetchPositions"):
            positions = await self.rest.fetch_positions()
            total += sum(float(position.get("unrealizedPnl") or 0) for position in positions)
        if total <= 0:
            raise RuntimeError(f"Exchange account has no readable {currency.upper()} equity")
        return total

    async def position(self, symbol: str, side: str) -> dict[str, Any] | None:
        await self.rest.load_markets()
        if not self.rest.has.get("fetchPositions"):
            raise RuntimeError("Exchange does not support position reconciliation")
        positions = await self.rest.fetch_positions([symbol])
        expected_side = "long" if side.lower() == "buy" else "short"
        for position in positions:
            contracts = abs(float(position.get("contracts") or 0))
            position_side = str(position.get("side", "")).lower()
            if contracts and position_side in {expected_side, side.lower()}:
                return {"symbol": symbol, "side": side.lower(), "quantity": contracts,
                        "entry_price": float(position.get("entryPrice") or 0),
                        "mark_price": float(position.get("markPrice") or 0),
                        "unrealized_pnl": float(position.get("unrealizedPnl") or 0)}
        return None

    async def positions_for_symbol(self, symbol: str) -> list[dict[str, Any]]:
        await self.rest.load_markets()
        if not self.rest.has.get("fetchPositions"):
            raise RuntimeError("Exchange does not support position reconciliation")
        positions = await self.rest.fetch_positions([symbol])
        return [{"symbol": symbol, "side": ("buy" if str(row.get("side", "")).lower() == "long" else "sell" if str(row.get("side", "")).lower() == "short" else str(row.get("side", "")).lower()),
                 "quantity": abs(float(row.get("contracts") or 0))}
                for row in positions if abs(float(row.get("contracts") or 0)) > 0]

    async def order_status(self, symbol: str, order_id: str) -> str | None:
        if not self.rest.has.get("fetchOrder"):
            return None
        order = await self.rest.fetch_order(order_id, symbol)
        return str(order.get("status") or "").lower() or None

    async def close_position(self, symbol: str, side: str, amount: float, entry_order_id: str | None = None) -> dict[str, Any]:
        await self.rest.load_markets()
        if not self.rest.market(symbol).get("contract"):
            raise RuntimeError("Automatic close is supported only for derivative positions")
        current = await self.position(symbol, side)
        if current is None:
            if entry_order_id:
                state = await self.order_status(symbol, entry_order_id)
                if state in {"open", "new", "pending"}:
                    canceled = await self.rest.cancel_order(entry_order_id, symbol)
                    return {"already_closed": False, "canceled_unfilled_entry": True,
                            "order_id": str(canceled.get("id") or entry_order_id), "symbol": symbol, "side": side}
            return {"already_closed": True, "symbol": symbol, "side": side}
        quantity = min(float(amount), float(current["quantity"]))
        close_side = "sell" if side.lower() == "buy" else "buy"
        order = await self.rest.create_order(
            symbol, "market", close_side, float(self.rest.amount_to_precision(symbol, quantity)),
            None, {"reduceOnly": True},
        )
        if not order.get("id"):
            raise RuntimeError("Exchange returned no close-order identifier")
        remaining = await self.position(symbol, side)
        if remaining is not None and float(remaining["quantity"]) > max(quantity * 0.001, 1e-10):
            raise RuntimeError("Exchange reports a remaining position after the close order")
        return {"already_closed": False, "order_id": str(order["id"]), "symbol": symbol,
                "side": side, "price": float(order.get("average") or order.get("price") or 0),
                "quantity": quantity}

    async def position_exit(
        self, symbol: str, side: str, amount: float, since: int, entry_price: float,
    ) -> dict[str, Any]:
        """Estimate realized gross P&L from opposite-side fills after the platform entry."""
        await self.rest.load_markets()
        if not self.rest.has.get("fetchMyTrades"):
            return {"price": entry_price, "pnl": None, "trade_ids": []}
        market = self.rest.market(symbol)
        contract_size = float(market.get("contractSize") or 1)
        required = float(amount)
        cumulative = 0.0
        exit_value = 0.0
        fees = 0.0
        trade_ids: list[str] = []
        expected_side = "sell" if side.lower() == "buy" else "buy"
        rows = await self.rest.fetch_my_trades(symbol, since=since, limit=200)
        for trade in sorted(rows, key=lambda item: int(item.get("timestamp") or 0)):
            trade_amount = float(trade.get("amount") or 0)
            if trade.get("side") != expected_side or trade_amount <= 0:
                continue
            used = min(trade_amount, required - cumulative)
            if used <= 0:
                break
            cumulative += used
            exit_value += float(trade.get("price") or 0) * used
            fee = trade.get("fee") or {}
            if str(fee.get("currency", "")).upper() == str(market.get("quote", "")).upper():
                fees += float(fee.get("cost") or 0)
            trade_ids.append(str(trade.get("id") or trade.get("order") or ""))
            if cumulative >= required * 0.999:
                break
        if cumulative < required * 0.999 or cumulative <= 0:
            return {"price": entry_price, "pnl": None, "trade_ids": trade_ids}
        exit_price = exit_value / cumulative
        direction = 1 if side.lower() == "buy" else -1
        pnl = (exit_price - entry_price) * cumulative * contract_size * direction - fees
        return {"price": exit_price, "pnl": pnl, "trade_ids": trade_ids}

    async def watch_ticker(self, symbol: str) -> dict[str, Any]:
        if not self.websocket_available:
            raise RuntimeError("CCXT Pro WebSocket support is not installed or this exchange lacks watchTicker.")
        ticker = await self.ws.watch_ticker(symbol)
        return self._ticker_payload(ticker, self.exchange_id, symbol)

    async def watch_candles(self, symbol: str, timeframe: str) -> list[list[Any]]:
        if self.ws is None or not self.ws.has.get("watchOHLCV"):
            raise RuntimeError("CCXT WebSocket OHLCV is unavailable for this exchange.")
        return await self.ws.watch_ohlcv(symbol, timeframe)

    @staticmethod
    def _ticker_payload(ticker: dict[str, Any], exchange: str, symbol: str) -> dict[str, Any]:
        return {
            "exchange": exchange,
            "symbol": symbol,
            "timestamp": ticker.get("timestamp"),
            "datetime": ticker.get("datetime"),
            "bid": ticker.get("bid"),
            "ask": ticker.get("ask"),
            "last": ticker.get("last"),
            "high": ticker.get("high"),
            "low": ticker.get("low"),
            "base_volume": ticker.get("baseVolume"),
            "quote_volume": ticker.get("quoteVolume"),
        }

    async def close(self) -> None:
        if self.ws is not None:
            await self.ws.close()
        await self.rest.close()


def classify_ccxt_error(error: Exception) -> tuple[int, str]:
    if isinstance(error, (ExchangeNotAvailable, NetworkError)):
        return 502, "Exchange network error"
    if isinstance(error, (BadSymbol, BadRequest)):
        return 422, "Exchange rejected the requested market or parameters"
    if isinstance(error, ExchangeError):
        return 502, "Exchange request failed"
    if isinstance(error, ValueError):
        return 422, str(error)
    return 502, "Market data request failed"
