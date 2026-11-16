import asyncio

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import text

from app.settings import settings


class Database:
    def __init__(self, url: str = settings.database_url) -> None:
        self.engine = create_async_engine(
            url,
            pool_pre_ping=True,
            pool_size=settings.postgres_pool_size,
            max_overflow=settings.postgres_max_overflow,
            pool_timeout=settings.postgres_pool_timeout_seconds,
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)

    async def initialize(self) -> None:
        last_error: Exception | None = None
        for attempt in range(6):
            try:
                async with self.engine.connect() as connection:
                    await connection.execute(text("SELECT 1"))
                return
            except Exception as exc:
                last_error = exc
                await asyncio.sleep(min(2**attempt, 15))
        raise RuntimeError("Database initialization failed after retries") from last_error

    async def close(self) -> None:
        await self.engine.dispose()

    async def ping(self) -> bool:
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return True
