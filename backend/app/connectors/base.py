from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class BrokerConnector(Protocol):
    """Common read-side contract implemented by market data broker adapters."""

    @property
    def broker_id(self) -> str: ...

    async def quote(self, symbol: str) -> dict[str, Any]: ...

    async def candles(self, symbol: str, timeframe: str, limit: int) -> list[dict[str, Any]]: ...

    async def close(self) -> None: ...


class BrokerExecutionError(RuntimeError):
    """A broker rejected an execution request or its outcome is uncertain."""

