from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

import httpx

# Run from the backend directory so the agent reuses the platform's MT5 adapter.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.connectors.mt5 import MT5Connector  # noqa: E402

VERSION = "0.1.0"
LOG = logging.getLogger("mt5-agent")
BASE_URL = os.environ.get("TRADING_PLATFORM_URL", "").rstrip("/")
TOKEN = os.environ.get("TRADING_PLATFORM_AGENT_TOKEN", "")
STATE_PATH = Path(os.environ.get("TRADING_PLATFORM_AGENT_STATE", Path.home() / ".trading-platform" / "mt5-agent.sqlite3"))
POLL_SECONDS = max(0.25, float(os.environ.get("MT5_AGENT_POLL_SECONDS", "0.75")))


class Journal:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS orders (command_id TEXT PRIMARY KEY, state TEXT NOT NULL, response TEXT, error TEXT)")
        self.db.commit()

    def get(self, command_id: str) -> tuple[str, dict[str, Any] | None, str | None] | None:
        row = self.db.execute("SELECT state,response,error FROM orders WHERE command_id=?", (command_id,)).fetchone()
        return (row[0], json.loads(row[1]) if row[1] else None, row[2]) if row else None

    def start(self, command_id: str) -> None:
        self.db.execute("INSERT INTO orders(command_id,state) VALUES(?, 'started')", (command_id,))
        self.db.commit()

    def finish(self, command_id: str, status: str, response: dict[str, Any] | None, error: str | None) -> None:
        self.db.execute("UPDATE orders SET state=?,response=?,error=? WHERE command_id=?",
                        (status, json.dumps(response) if response is not None else None, error, command_id))
        self.db.commit()


def _json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


async def run() -> None:
    if not BASE_URL.startswith("https://") and "localhost" not in BASE_URL and "127.0.0.1" not in BASE_URL:
        raise SystemExit("TRADING_PLATFORM_URL must use HTTPS (localhost is allowed for development)")
    if not TOKEN:
        raise SystemExit("Set TRADING_PLATFORM_AGENT_TOKEN in the Windows user's environment")
    connector = MT5Connector()
    await connector.connect(enabled=True)
    if connector.state != "connected":
        raise SystemExit(connector.detail or "Could not connect to the logged-in MT5 terminal")

    journal = Journal(STATE_PATH)
    timeout = httpx.Timeout(20.0, connect=10.0)
    async with httpx.AsyncClient(base_url=BASE_URL, headers={"Authorization": f"Bearer {TOKEN}"},
                                 timeout=timeout) as client:
        last_heartbeat = 0.0
        last_subscriptions = 0.0
        subscriptions: list[dict[str, str]] = []
        sent_candles: dict[tuple[str, str], int] = {}
        try:
            while True:
                now = asyncio.get_running_loop().time()
                if now - last_heartbeat >= 5:
                    status = await connector.account_status()
                    response = await client.post("/mt5/agent/heartbeat", json={"account": status, "agent_version": VERSION})
                    response.raise_for_status()
                    last_heartbeat = now
                if now - last_subscriptions >= 10:
                    response = await client.get("/mt5/agent/subscriptions")
                    response.raise_for_status()
                    subscriptions = response.json().get("subscriptions", [])
                    for sub in subscriptions:
                        key = (sub["symbol"], sub["timeframe"])
                        if key not in sent_candles:
                            history = await connector.candles(*key, 301)
                            if history:
                                closed = history[:-1]
                                if closed:
                                    (await client.post("/mt5/agent/market-data", json={
                                        "symbol": key[0], "timeframe": key[1], "kind": "history", "candles": closed,
                                    })).raise_for_status()
                                sent_candles[key] = history[-2]["timestamp"] if len(history) > 1 else 0
                    last_subscriptions = now
                for sub in subscriptions:
                    key = (sub["symbol"], sub["timeframe"])
                    candles = await connector.candles(*key, 2)
                    if len(candles) < 2:
                        continue
                    closed = candles[-2]
                    if closed["timestamp"] > sent_candles.get(key, 0):
                        (await client.post("/mt5/agent/market-data", json={
                            "symbol": key[0], "timeframe": key[1], "kind": "candle", "candles": [closed],
                        })).raise_for_status()
                        sent_candles[key] = closed["timestamp"]
                response = await client.post("/mt5/agent/commands/claim")
                response.raise_for_status()
                command = response.json()
                if command:
                    await _handle_command(client, connector, journal, command)
                await asyncio.sleep(POLL_SECONDS)
        finally:
            await connector.close()


async def _handle_command(client: httpx.AsyncClient, connector: MT5Connector, journal: Journal,
                          command: dict[str, Any]) -> None:
    command_id, command_type, payload = command["id"], command["type"], command.get("payload", {})
    status, response, error = "completed", {}, None
    if command_type == "execute_order":
        prior = journal.get(command_id)
        if prior:
            state, stored, old_error = prior
            if state == "completed":
                response = stored or {}
            elif state == "failed":
                status, error = "failed", old_error
            else:
                await _post_result(client, command_id, "unknown", error="Agent restarted after order submission began; manual reconciliation required")
                return
        else:
            try:
                account = await connector.account_status()
                if account["trade_mode"] != "demo" and not command.get("allow_live_orders", False):
                    raise RuntimeError("This agent is restricted to demo MT5 accounts")
                if not account["trade_allowed"] or not account["trade_expert"]:
                    raise RuntimeError("MT5 terminal or account has automated trading disabled")
                journal.start(command_id)
                response = await connector.execute_protected_market_order(
                    payload["symbol"], payload["side"], float(payload["volume"]),
                    float(payload["stop_loss"]), float(payload["take_profit"]),
                    client_order_id=str(payload.get("client_order_id", command_id)),
                )
                response = _json(response)
                journal.finish(command_id, "completed", response, None)
            except Exception as exc:
                # Once journaled as started the terminal may have accepted an order
                # even if the IPC response was lost. Never repeat that command.
                if journal.get(command_id):
                    status, error = "unknown", f"{type(exc).__name__}: {exc}"
                    journal.finish(command_id, "unknown", None, error)
                    await _post_result(client, command_id, status, error=error)
                    return
                status, error = "failed", f"{type(exc).__name__}: {exc}"
    else:
        try:
            response = await _dispatch(connector, command_type, payload)
            response = _json(response)
        except Exception as exc:
            status, error = "failed", f"{type(exc).__name__}: {exc}"
    await _post_result(client, command_id, status, response=response, error=error)


async def _dispatch(connector: MT5Connector, command_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    if command_type == "quote":
        return await connector.quote(payload["symbol"])
    if command_type == "candles":
        return {"candles": await connector.candles(payload["symbol"], payload["timeframe"], int(payload["limit"]))}
    if command_type == "account_equity":
        return {"equity": await connector.account_equity()}
    if command_type == "account_status":
        return await connector.account_status()
    if command_type == "symbol_info":
        return await connector.symbol_info(payload["symbol"])
    if command_type == "risk_per_lot":
        return {"risk_per_lot": await connector.risk_per_lot(payload["symbol"], float(payload["entry"]), float(payload["stop_loss"]))}
    if command_type == "positions_for_symbol":
        positions = await connector.positions_for_symbol(payload["symbol"])
        return {"positions": [{**position, "quantity": position.get("volume")} for position in positions]}
    if command_type == "position":
        position = await connector.position(str(payload["ticket"]))
        if position:
            position = {**position, "quantity": position.get("volume"),
                        "entry_price": position.get("price_open")}
        return {"position": position}
    if command_type == "order_status":
        return {"status": await connector.order_status(str(payload["order_id"]))}
    if command_type == "close_position":
        return await connector.close_position(str(payload["ticket"]))
    if command_type == "position_exit":
        return {"exit": await connector.position_exit(str(payload["ticket"]))}
    raise ValueError(f"Unsupported MT5 agent command: {command_type}")


async def _post_result(client: httpx.AsyncClient, command_id: str, status: str,
                       response: dict[str, Any] | None = None, error: str | None = None) -> None:
    result = await client.post(f"/mt5/agent/commands/{command_id}/result", json={
        "status": status, "response": response or {}, "error": error,
    })
    result.raise_for_status()


if __name__ == "__main__":
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
