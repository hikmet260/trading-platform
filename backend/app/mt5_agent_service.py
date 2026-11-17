from __future__ import annotations

import asyncio
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from app.storage.database import Database
from app.storage.models import MT5AgentCommandRecord, MT5AgentRecord

AGENT_HEARTBEAT_TTL_SECONDS = 20
COMMAND_LEASE_SECONDS = 30
COMMAND_RESULT_WAIT_SECONDS = 12


class MT5AgentError(RuntimeError):
    pass


class MT5AgentOutcomeUnknown(MT5AgentError):
    def __init__(self, command_id: str, message: str = "The MT5 agent outcome is unknown") -> None:
        super().__init__(message)
        self.command_id = command_id


def make_agent_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(48)
    return token, hashlib.sha256(token.encode("ascii")).hexdigest()


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def agent_is_online(agent: MT5AgentRecord, now: datetime | None = None) -> bool:
    if agent.last_seen_at is None:
        return False
    seen = agent.last_seen_at
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    return ((now or datetime.now(timezone.utc)) - seen).total_seconds() <= AGENT_HEARTBEAT_TTL_SECONDS


async def queue_agent_command(
    database: Database,
    *,
    agent_id: str,
    tenant_id: str,
    command_type: str,
    payload: dict[str, Any],
    idempotency_key: str | None = None,
    ttl_seconds: int = 15,
    require_online: bool = True,
) -> MT5AgentCommandRecord:
    now = datetime.now(timezone.utc)
    async with database.sessions() as session:
        agent = await session.scalar(select(MT5AgentRecord).where(
            MT5AgentRecord.id == agent_id,
            MT5AgentRecord.tenant_id == tenant_id,
        ))
        if agent is None:
            raise MT5AgentError("MT5 agent is not registered for this tenant")
        if require_online and not agent_is_online(agent, now):
            raise MT5AgentError("MT5 agent is offline")
        key = (idempotency_key or str(uuid4())).strip()
        if len(key) > 120:
            raise MT5AgentError("MT5 command idempotency key is too long")
        existing = await session.scalar(select(MT5AgentCommandRecord).where(
            MT5AgentCommandRecord.idempotency_key == key,
        ))
        if existing is not None:
            if existing.agent_id != agent_id or existing.command_type != command_type:
                raise MT5AgentError("MT5 command idempotency key is already used")
            return existing
        command = MT5AgentCommandRecord(
            id=str(uuid4()), tenant_id=tenant_id, agent_id=agent_id,
            command_type=command_type, idempotency_key=key, payload=payload,
            status="queued", attempts=0,
            expires_at=now + timedelta(seconds=max(3, min(ttl_seconds, 60))),
        )
        session.add(command)
        await session.commit()
        await session.refresh(command)
        return command


async def wait_for_agent_command(
    database: Database, command_id: str, timeout_seconds: float = COMMAND_RESULT_WAIT_SECONDS,
) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        async with database.sessions() as session:
            command = await session.get(MT5AgentCommandRecord, command_id)
            if command is None:
                raise MT5AgentError("MT5 command disappeared")
            if command.status == "completed":
                return dict(command.response or {})
            if command.status in {"failed", "canceled"}:
                raise MT5AgentError(command.error or "MT5 agent rejected the command")
            if command.status == "unknown":
                raise MT5AgentOutcomeUnknown(command.id, command.error or "MT5 command outcome is unknown")
            if command.expires_at <= datetime.now(timezone.utc) and command.status == "queued":
                command.status = "failed"
                command.error = "MT5 command expired before the agent claimed it"
                command.finished_at = datetime.now(timezone.utc)
                await session.commit()
                raise MT5AgentError(command.error)
        await asyncio.sleep(0.2)

    async with database.sessions() as session:
        command = await session.get(MT5AgentCommandRecord, command_id)
        if command is None:
            raise MT5AgentError("MT5 command disappeared")
        if command.status == "queued":
            command.status = "failed"
            command.error = "MT5 agent did not claim the command before the response timeout"
            command.finished_at = datetime.now(timezone.utc)
            await session.commit()
            raise MT5AgentError(command.error)
        command.status = "unknown"
        command.error = "Agent claimed the command but did not confirm its outcome; do not retry it"
        command.finished_at = datetime.now(timezone.utc)
        await session.commit()
    raise MT5AgentOutcomeUnknown(command_id)


class RemoteMT5Connector:
    """BrokerConnector-compatible RPC adapter for a tenant's Windows MT5 agent."""

    def __init__(self, database: Database, tenant_id: str, agent_id: str) -> None:
        self.database = database
        self.tenant_id = tenant_id
        self.agent_id = agent_id
        self.state = "remote"

    @property
    def broker_id(self) -> str:
        return "mt5"

    async def _rpc(
        self,
        command_type: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str | None = None,
        timeout_seconds: float = COMMAND_RESULT_WAIT_SECONDS,
    ) -> dict[str, Any]:
        command = await queue_agent_command(
            self.database, agent_id=self.agent_id, tenant_id=self.tenant_id,
            command_type=command_type, payload=payload,
            idempotency_key=idempotency_key, ttl_seconds=max(5, int(timeout_seconds + 2)),
        )
        return await wait_for_agent_command(self.database, command.id, timeout_seconds)

    async def quote(self, symbol: str) -> dict[str, Any]:
        return await self._rpc("quote", {"symbol": symbol})

    async def candles(self, symbol: str, timeframe: str, limit: int) -> list[dict[str, Any]]:
        result = await self._rpc("candles", {"symbol": symbol, "timeframe": timeframe, "limit": limit})
        return list(result.get("candles") or [])

    async def account_equity(self, currency: str = "") -> float:
        result = await self._rpc("account_equity", {})
        return float(result["equity"])

    async def risk_per_lot(self, symbol: str, entry: float, stop_loss: float) -> float:
        result = await self._rpc("risk_per_lot", {
            "symbol": symbol, "entry": entry, "stop_loss": stop_loss,
        })
        return float(result["risk_per_lot"])

    async def symbol_info(self, symbol: str) -> dict[str, Any]:
        return await self._rpc("symbol_info", {"symbol": symbol})

    async def execute_protected_market_order(
        self, symbol: str, side: str, amount: float, stop_loss: float, take_profit: float,
        *, client_order_id: str | None = None,
    ) -> dict[str, Any]:
        key = client_order_id or str(uuid4())
        return await self._rpc("execute_order", {
            "symbol": symbol, "side": side, "volume": amount,
            "stop_loss": stop_loss, "take_profit": take_profit,
            "client_order_id": key,
        }, idempotency_key=f"mt5-order:{self.agent_id}:{key}")

    async def positions_for_symbol(self, symbol: str) -> list[dict[str, Any]]:
        result = await self._rpc("positions_for_symbol", {"symbol": symbol})
        return list(result.get("positions") or [])

    async def position(self, ticket: str) -> dict[str, Any] | None:
        result = await self._rpc("position", {"ticket": ticket})
        return result.get("position")

    async def order_status(self, order_id: str) -> str | None:
        result = await self._rpc("order_status", {"order_id": order_id})
        return result.get("status")

    async def close_position(self, ticket: str) -> dict[str, Any]:
        return await self._rpc("close_position", {"ticket": ticket})

    async def position_exit(self, ticket: str) -> dict[str, Any] | None:
        result = await self._rpc("position_exit", {"ticket": ticket})
        return result.get("exit")

    async def close(self) -> None:
        return None
