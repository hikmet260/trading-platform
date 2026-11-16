import json
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.settings import settings


class MarketCache:
    def __init__(self, url: str = settings.redis_url) -> None:
        self.redis: Redis = Redis.from_url(url, decode_responses=True, health_check_interval=30)

    async def ping(self) -> bool:
        try:
            return bool(await self.redis.ping())
        except RedisError:
            return False

    async def set_tick(self, tick: dict[str, Any], ttl_seconds: int = 60) -> None:
        if ttl_seconds < 1:
            raise ValueError("Tick cache TTL must be a positive number of seconds.")
        if not tick.get("exchange") or not tick.get("symbol"):
            raise ValueError("A cached tick must include its exchange and symbol.")
        key = f"market:tick:{tick['exchange']}:{tick['symbol']}"
        encoded = json.dumps(tick, separators=(",", ":"), allow_nan=False)
        async with self.redis.pipeline(transaction=False) as pipeline:
            pipeline.set(key, encoded, ex=ttl_seconds)
            pipeline.publish("market:ticks", encoded)
            await pipeline.execute()

    async def set_candle(self, candle: dict[str, Any], exchange: str, symbol: str, timeframe: str, ttl_seconds: int = 86_400) -> None:
        if ttl_seconds < 1:
            raise ValueError("Candle cache TTL must be a positive number of seconds.")
        if not exchange or not symbol or not timeframe:
            raise ValueError("Exchange, symbol, and timeframe are required for a cached candle.")
        key = f"market:candle:{exchange}:{symbol}:{timeframe}"
        encoded = json.dumps(candle, separators=(",", ":"), allow_nan=False)
        async with self.redis.pipeline(transaction=False) as pipeline:
            pipeline.set(key, encoded, ex=ttl_seconds)
            pipeline.publish("market:candles", encoded)
            await pipeline.execute()

    async def get_tick(self, exchange: str, symbol: str) -> dict[str, Any] | None:
        value = await self.redis.get(f"market:tick:{exchange}:{symbol}")
        return json.loads(value) if value else None

    async def get_candle(self, exchange: str, symbol: str, timeframe: str) -> dict[str, Any] | None:
        value = await self.redis.get(f"market:candle:{exchange}:{symbol}:{timeframe}")
        return json.loads(value) if value else None

    async def is_kill_switch_active(self, tenant_id: str | None = None) -> bool:
        tenant = tenant_id or settings.tenant_id
        return await self.redis.get(f"engine:kill_switch:{tenant}") == "1"

    async def set_kill_switch(self, active: bool, tenant_id: str | None = None) -> None:
        tenant = tenant_id or settings.tenant_id
        key = f"engine:kill_switch:{tenant}"
        if active:
            await self.redis.set(key, "1")
        else:
            await self.redis.delete(key)

    async def close(self) -> None:
        await self.redis.aclose()
