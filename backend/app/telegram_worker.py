from __future__ import annotations

import asyncio
import logging
import signal
import time

from app.engine.strategy_runtime import StrategyRuntime
from app.engine.live_positions import LivePositionManager
from app.config_service import apply_runtime_config, load_runtime_config
from app.risk.news import EconomicCalendar
from app.settings import settings
from app.storage.cache import MarketCache
from app.storage.database import Database
from app.notifications.service import deliver_one, webpush_available
from app.telegram_bot.copilot import TelegramCopilot
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
    copilot = TelegramCopilot(database, cache)
    telegram.on_ai_message = copilot.handle_message
    runtime = StrategyRuntime(database, cache, telegram)
    await runtime.initialize()
    runtime_config = await load_runtime_config(database, settings.tenant_id)
    apply_runtime_config(runtime, runtime_config)
    if settings.webpush_public_key and not settings.webpush_private_key:
        logger.error("Web Push has a public key configured but no private key is available to the worker")
    if settings.webpush_public_key and not webpush_available():
        logger.error("Web Push is configured but pywebpush is missing; rebuild the backend worker image")
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
    try:
        await telegram.apply_config(
            enabled=runtime_config.telegram_enabled,
            bot_token=runtime_config.telegram_bot_token,
            chat_id=runtime_config.telegram_chat_id,
            allowed_user_ids=runtime_config.telegram_allowed_user_ids,
            timezone_name=runtime_config.telegram_timezone,
        )
    except Exception:
        logger.exception("Telegram worker could not start with the saved tenant configuration")

    async def refresh_runtime_config() -> None:
        while True:
            await asyncio.sleep(10)
            try:
                config = await load_runtime_config(database, settings.tenant_id)
                apply_runtime_config(runtime, config)
                await telegram.apply_config(
                    enabled=config.telegram_enabled,
                    bot_token=config.telegram_bot_token,
                    chat_id=config.telegram_chat_id,
                    allowed_user_ids=config.telegram_allowed_user_ids,
                    timezone_name=config.telegram_timezone,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Could not refresh saved tenant settings in Telegram worker")

    config_task = asyncio.create_task(refresh_runtime_config(), name="telegram-config-refresh")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    listener = asyncio.create_task(_notification_delivery_loop(database, telegram, cache), name="notification-delivery-worker")
    try:
        await stop.wait()
    finally:
        config_task.cancel()
        await asyncio.gather(config_task, return_exceptions=True)
        if listener:
            listener.cancel()
            await asyncio.gather(listener, return_exceptions=True)
        await telegram.stop()
        await calendar.stop()
        await cache.close()
        await database.close()


async def _notification_delivery_loop(database: Database, telegram: TelegramController, cache: MarketCache) -> None:
    heartbeat_key = "notifications:worker:heartbeat"
    heartbeat_value = "push-ready" if (
        settings.webpush_public_key and settings.webpush_private_key and webpush_available()
    ) else "telegram-only"
    last_heartbeat = 0.0
    try:
        while True:
            try:
                now = time.monotonic()
                if now - last_heartbeat >= 5:
                    await cache.redis.set(heartbeat_key, heartbeat_value, ex=20)
                    last_heartbeat = now
                delivered = await deliver_one(database, telegram)
                if not delivered:
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Notification delivery worker encountered an error")
                await asyncio.sleep(2)
    finally:
        await cache.redis.delete(heartbeat_key)


if __name__ == "__main__":
    logging.basicConfig(level=settings.log_level.upper())
    asyncio.run(run())
