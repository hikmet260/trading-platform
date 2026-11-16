from __future__ import annotations

import asyncio
import json
import logging
import signal

from app.engine.strategy_runtime import StrategyRuntime
from app.engine.live_positions import LivePositionManager
from app.risk.news import EconomicCalendar
from app.settings import settings
from app.storage.cache import MarketCache
from app.storage.database import Database
from app.telegram_bot.controller import TelegramController

logger = logging.getLogger("telegram-worker")


async def run() -> None:
    database = Database()
    cache = MarketCache()
    await database.initialize()
    if not await cache.ping():
        await database.close()
        raise RuntimeError("Redis is unavailable")
    telegram = TelegramController(cache, database)
    runtime = StrategyRuntime(database, cache, telegram)
    await runtime.initialize()
    live_positions = LivePositionManager(database, cache)
    runtime.live_positions = live_positions
    calendar = EconomicCalendar(settings.news_calendar_api_key, settings.news_calendar_poll_seconds)
    await calendar.start()
    runtime.calendar = calendar

    async def telegram_kill():
        paper_closed = await runtime.emergency_stop("telegram", settings.tenant_id)
        live_result = await live_positions.close_tenant(settings.tenant_id, "telegram")
        return {"closed_paper_positions": paper_closed, "closed_live_positions": live_result["closed"],
                "unclosed_live_positions": live_result["remaining"]}

    telegram.on_kill = telegram_kill
    async def telegram_resume():
        if await live_positions.has_open_positions(settings.tenant_id):
            return False
        await runtime.resume("telegram", settings.tenant_id)
        return True

    telegram.on_resume = telegram_resume
    telegram.on_decision = runtime.approve_paper_trade
    await telegram.start()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    listener: asyncio.Task | None = None
    if telegram.state == "running":
        listener = asyncio.create_task(_alerts(cache, telegram), name="telegram-alert-listener")
    else:
        logger.warning("Telegram worker is %s; alerts will not be consumed", telegram.state)
    try:
        await stop.wait()
    finally:
        if listener:
            listener.cancel()
            await asyncio.gather(listener, return_exceptions=True)
        await telegram.stop()
        await calendar.stop()
        await cache.close()
        await database.close()


async def _alerts(cache: MarketCache, telegram: TelegramController) -> None:
    pubsub = cache.redis.pubsub()
    await pubsub.subscribe("telegram:alerts")
    try:
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            try:
                await telegram.send_trade_alert(json.loads(message["data"]))
            except Exception:
                logger.exception("Could not deliver trade alert")
    finally:
        await pubsub.aclose()


if __name__ == "__main__":
    logging.basicConfig(level=settings.log_level.upper())
    asyncio.run(run())
