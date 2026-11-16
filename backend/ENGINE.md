# Async engine, strategies, risk, and QA

## Engine lifecycle

FastAPI owns the asyncio lifecycle. PostgreSQL tables are initialized, Redis connectivity is checked, optional Telegram polling is started, and optional CCXT Pro ticker/closed-candle feeds are supervised with bounded exponential reconnects. The engine writes the latest tick to Redis, publishes ticks/candles on Redis Pub/Sub, and evaluates enabled strategies on candle boundaries. A bounded queue prevents unbounded memory growth; queue depth and dropped events are visible at `GET /engine/status`.

The engine remains off by default (`ENGINE_ENABLED=false`). For a sandbox/paper run, configure `CRYPTO_EXCHANGE`, `ENGINE_SYMBOLS`, and `ENGINE_TIMEFRAME`, keep `ENGINE_PAPER_MODE=true`, then enable the feed. One Uvicorn worker is intentional so startup creates exactly one feed runner and Telegram poller.

## Deterministic strategy grammar

There is no model-generated execution code. The compiler accepts a narrow exact grammar and rejects unsupported wording, indicators, operators, missing risk controls, or risk above 1%:

```text
BUY EUR/USD on 15m when RSI(14) < 30 AND EMA(9) crosses above EMA(21) risk 0.5% stop loss 20 pips take profit 40 pips
```

Supported indicators are `RSI(n)`, `EMA(n)`, `SMA(n)`, and `PRICE`/`CLOSE`, with numeric threshold comparisons and EMA/SMA crossings. Clauses combine with `AND`. Forex pip sizes are inferred for standard currency pairs; non-FX symbols use a 0.01 price unit. Treat that as a simulation convention and set venue-specific sizing before using any real execution adapter.

`POST /strategies/compile` validates and returns a rule without saving it. `POST /strategies` persists a rule; callers choose whether it starts enabled. In paper mode the runtime evaluates enabled rules against the configured feed. Live strategy execution additionally requires `LIVE_TRADING_ENABLED`, a selected enabled broker account, matching feed and execution venues, and a successful fresh risk check. Keep live mode off until the selected adapter has been validated in its sandbox.

## Backtesting and paper risk

`POST /backtests` accepts the deterministic prompt and historical OHLCV candles. Signals are evaluated on a candle and filled at the next candle open. If one OHLC bar touches both stop and target, the simulator assumes the stop happened first. Fees are configurable in basis points. Results include trade log, win rate, profit factor, return, and peak-to-trough maximum drawdown. This is a basic bar simulator; it does not model market impact, funding, spread, slippage, partial fills, or venue-specific contract multipliers.

The RMS rejects missing/invalid stop-loss and take-profit values, wrong-side protection, active kill-switches, daily drawdown breaches, and high-impact news within the configured blackout. News schedules can be supplied as UTC JSON in `NEWS_EVENTS_JSON`, for example:

```json
[{"name":"US CPI","currency":"USD","impact":"high","occurs_at":"2026-12-10T13:30:00Z"}]
```

The optional Trading Economics calendar is configured with `NEWS_CALENDAR_API_KEY`; `NEWS_CALENDAR_REQUIRED=true` blocks new entries when its data is missing or stale. Approved Telegram paper proposals become simulated positions and are closed against streamed prices when their stop, target, or kill-switch is hit. In explicitly enabled live mode, approvals route through the same venue, risk, and attached-protection checks as automatic orders. Live positions are polled and reconciled against the owning broker account; failed close or reconciliation operations remain visible in service status.

## Telegram controls

Enable the bot with `TELEGRAM_ENABLED=true`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, and trusted user IDs in `TELEGRAM_ALLOWED_USER_IDS`. Alerts contain entry, computed position size, risk amount, stop and target. Approve/Skip and kill-switch actions require both the configured chat and an allowed user. The kill-switch halts new proposals and attempts to close tracked paper and live positions. The daily digest is scheduled at 20:00 in `TELEGRAM_TIMEZONE`.

## QA commands

From `backend/`, run the deterministic unit suite with:

```bash
python -m unittest discover -s tests -v
```

For integration smoke checks with Compose, see `docker/DEPLOYMENT.md`.
