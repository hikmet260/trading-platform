from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from app.connectors.crypto import CryptoMarketData
from app.connectors.mt5 import MT5Connector
from app.storage.cache import MarketCache

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MarketEvent:
    kind: str
    exchange: str
    symbol: str
    payload: dict[str, Any]


class MarketEngine:
    """Long-running async exchange feed supervisor with a bounded processing queue."""

    def __init__(
        self,
        connector: CryptoMarketData,
        cache: MarketCache,
        symbols: list[str],
        timeframe: str,
        *,
        on_event: Callable[[MarketEvent], Awaitable[None]] | None = None,
        queue_size: int = 10_000,
    ) -> None:
        self.connector = connector
        self.cache = cache
        self.symbols = symbols
        self.timeframe = timeframe
        self.on_event = on_event
        self.queue: asyncio.Queue[MarketEvent] = asyncio.Queue(maxsize=queue_size)
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self._dropped = 0
        self._last_event_at: str | None = None
        self._processed = 0

    async def start(self) -> None:
        if self._tasks:
            return
        self._stop.clear()
        # WebSocket candle streams only emit updates from the moment they are
        # subscribed. Prime the rule runtime first so indicator-based rules
        # have the same recent history as the REST polling path.
        if self.connector.ws is not None and self.connector.ws.has.get("watchOHLCV"):
            for symbol in self.symbols:
                try:
                    history = await self.connector.candles(symbol, self.timeframe, 300)
                    if history and self.on_event:
                        await self.on_event(MarketEvent(
                            "history", self.connector.exchange_id, symbol,
                            {"candles": history[:-1]},
                        ))
                except Exception:
                    logger.exception("Could not prime candle history for %s", symbol)
        self._tasks.append(asyncio.create_task(self._process_queue(), name="market-event-processor"))
        for symbol in self.symbols:
            if self.connector.websocket_available:
                ticker_feed = self._ticker_feed(symbol)
            else:
                ticker_feed = self._poll_ticker_feed(symbol)
            self._tasks.append(asyncio.create_task(self._supervise(ticker_feed), name=f"ticker:{symbol}"))
            if self.connector.ws is not None and self.connector.ws.has.get("watchOHLCV"):
                self._tasks.append(asyncio.create_task(self._supervise(self._candle_feed(symbol)), name=f"candles:{symbol}"))
            else:
                self._tasks.append(asyncio.create_task(self._supervise(self._poll_candle_feed(symbol)), name=f"candles:{symbol}"))

    async def stop(self) -> None:
        self._stop.set()
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    def status(self) -> dict[str, Any]:
        return {
            "running": bool(self._tasks),
            "exchange": self.connector.exchange_id,
            "symbols": self.symbols,
            "timeframe": self.timeframe,
            "queue_depth": self.queue.qsize(),
            "dropped_events": self._dropped,
            "processed_events": self._processed,
            "last_event_at": self._last_event_at,
            "paper_only": True,
            "feed_mode": "websocket" if self.connector.websocket_available else "rate_limited_rest_polling",
        }

    async def _ticker_feed(self, symbol: str) -> None:
        while not self._stop.is_set():
            ticker = await self.connector.watch_ticker(symbol)
            await self._offer(MarketEvent("tick", self.connector.exchange_id, symbol, ticker))

    async def _candle_feed(self, symbol: str) -> None:
        previous_timestamp: int | None = None
        previous_row: list[Any] | None = None
        while not self._stop.is_set():
            rows = await self.connector.watch_candles(symbol, self.timeframe)
            if not rows:
                continue
            row = rows[-1]
            timestamp = int(row[0])
            if previous_timestamp is not None and timestamp != previous_timestamp and previous_row is not None:
                closed = {"timestamp": previous_row[0], "open": previous_row[1], "high": previous_row[2],
                          "low": previous_row[3], "close": previous_row[4], "volume": previous_row[5]}
                await self._offer(MarketEvent("candle", self.connector.exchange_id, symbol, closed))
            previous_timestamp, previous_row = timestamp, row

    async def _poll_ticker_feed(self, symbol: str) -> None:
        while not self._stop.is_set():
            ticker = await self.connector.ticker(symbol)
            await self._offer(MarketEvent("tick", self.connector.exchange_id, symbol, ticker))
            await asyncio.sleep(2.5)

    async def _poll_candle_feed(self, symbol: str) -> None:
        last_closed: int | None = None
        primed = False
        while not self._stop.is_set():
            rows = await self.connector.candles(symbol, self.timeframe, 300)
            if rows:
                if not primed:
                    await self._offer(MarketEvent("history", self.connector.exchange_id, symbol,
                                                  {"candles": rows[:-1]}))
                    primed = True
                latest_open = int(rows[-1]["timestamp"])
                if last_closed is not None and latest_open > last_closed:
                    closed = rows[-2] if len(rows) > 1 else None
                    if closed is not None:
                        await self._offer(MarketEvent("candle", self.connector.exchange_id, symbol, closed))
                last_closed = latest_open
            await asyncio.sleep(5.0)

    async def _offer(self, event: MarketEvent) -> None:
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            self._dropped += 1
            logger.error("Market event queue full; dropping %s for %s", event.kind, event.symbol)

    async def _supervise(self, feed: Awaitable[None]) -> None:
        # Each feed reconnects with bounded exponential backoff without taking down the API.
        coroutine = feed
        delay = 1.0
        try:
            while not self._stop.is_set():
                try:
                    await coroutine
                    delay = 1.0
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Market feed failed; retrying in %.1fs", delay)
                    await asyncio.sleep(delay + random.random() * min(delay, 1.0))
                    # Recreate coroutine using its symbol/name route after an exception.
                    name = asyncio.current_task().get_name() if asyncio.current_task() else ""
                    symbol = name.split(":", 1)[-1]
                    if name.startswith("ticker:"):
                        coroutine = self._ticker_feed(symbol) if self.connector.websocket_available else self._poll_ticker_feed(symbol)
                    else:
                        websocket_candles = self.connector.ws is not None and self.connector.ws.has.get("watchOHLCV")
                        coroutine = self._candle_feed(symbol) if websocket_candles else self._poll_candle_feed(symbol)
                    delay = min(delay * 2, 30.0)
        finally:
            if hasattr(coroutine, "close"):
                coroutine.close()

    async def _process_queue(self) -> None:
        while not self._stop.is_set():
            event = await self.queue.get()
            try:
                if event.kind == "tick":
                    await self.cache.set_tick(event.payload)
                elif event.kind == "candle":
                    await self.cache.set_candle(event.payload, event.exchange, event.symbol, self.timeframe)
                self._processed += 1
                self._last_event_at = event.payload.get("datetime") or str(event.payload.get("timestamp"))
                if self.on_event:
                    await self.on_event(event)
            except Exception:
                logger.exception("Market event processing failed")
            finally:
                self.queue.task_done()


class MT5MarketEngine:
    """Polling supervisor for MT5 terminals, which expose IPC rather than CCXT Pro streams."""

    def __init__(self, connector: MT5Connector, cache: MarketCache, symbols: list[str], timeframe: str,
                 on_event: Callable[[MarketEvent], Awaitable[None]]) -> None:
        self.connector, self.cache, self.symbols, self.timeframe = connector, cache, symbols, timeframe
        self.on_event = on_event
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self._last_bars: dict[str, int] = {}
        self._processed = 0
        self._errors = 0

    async def start(self) -> None:
        if self.connector.state != "connected":
            raise RuntimeError("MT5 terminal is not connected")
        self._stop.clear()
        self._tasks = [asyncio.create_task(self._supervise(symbol), name=f"mt5:{symbol}") for symbol in self.symbols]

    async def stop(self) -> None:
        self._stop.set()
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    def status(self) -> dict[str, Any]:
        return {"running": bool(self._tasks), "exchange": "mt5", "symbols": self.symbols,
                "timeframe": self.timeframe, "processed_events": self._processed,
                "errors": self._errors, "paper_only": True}

    async def _supervise(self, symbol: str) -> None:
        delay = 1.0
        primed = False
        last_closed_timestamp: int | None = None
        while not self._stop.is_set():
            try:
                quote = await self.connector.quote(symbol)
                quote["exchange"] = "mt5"
                await self.cache.set_tick(quote)
                await self.on_event(MarketEvent("tick", "mt5", symbol, quote))
                rows = await self.connector.candles(symbol, self.timeframe, 300)
                if rows:
                    latest_timestamp = int(rows[-1]["timestamp"])
                    if not primed:
                        if len(rows) > 1:
                            await self.on_event(MarketEvent("history", "mt5", symbol, {"candles": rows[:-1]}))
                        primed = True
                    elif latest_timestamp != self._last_bars.get(symbol):
                        previous = rows[-2] if len(rows) > 1 else None
                        if previous and int(previous["timestamp"]) != last_closed_timestamp:
                            await self.cache.set_candle(previous, "mt5", symbol, self.timeframe)
                            await self.on_event(MarketEvent("candle", "mt5", symbol, previous))
                            last_closed_timestamp = int(previous["timestamp"])
                    self._last_bars[symbol] = latest_timestamp
                self._processed += 1
                delay = 1.0
                await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                raise
            except Exception:
                self._errors += 1
                logger.exception("MT5 market poll failed for %s; retrying in %.1fs", symbol, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)
