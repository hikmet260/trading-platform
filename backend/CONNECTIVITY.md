# Market connectivity

## Crypto: Binance, Bybit, and OKX

The API uses asynchronous CCXT REST clients and CCXT Pro streaming clients. REST market data is public; exchange API keys are optional. If you configure keys, create market-data-only keys unless private account operations are explicitly needed. Never enable withdrawals on exchange keys.

Set these values in the root Compose `.env` or backend environment:

```dotenv
CRYPTO_EXCHANGE=binance
CRYPTO_API_KEY=
CRYPTO_API_SECRET=
CRYPTO_API_PASSWORD=
CRYPTO_SANDBOX=true
CRYPTO_DEFAULT_TYPE=spot
```

`CRYPTO_API_PASSWORD` is the OKX API passphrase. CCXT unified symbols use forms such as `BTC/USDT`; market type can be selected with `CRYPTO_DEFAULT_TYPE` (for example `spot` or `swap`). `CRYPTO_SANDBOX` defaults to true. Verify the venue's test environment is available before relying on it; live mode should be an explicit deployment decision.

REST examples:

```text
GET /market/crypto/ticker?symbol=BTC%2FUSDT
GET /market/crypto/candles?symbol=BTC%2FUSDT&timeframe=1m&limit=300
GET /market/crypto/ticker?exchange=bybit&symbol=ETH%2FUSDT
```

WebSocket ticker stream:

```text
ws://localhost:8000/ws/crypto/ticker?exchange=binance&symbol=BTC%2FUSDT
```

Use `GET /connectors/status` to see which adapters initialized. The standard Python `ccxt` distribution includes the `ccxt.pro` async WebSocket classes used by the ticker stream. The backend enforces CCXT's rate limiter; it does not promise exchange-side or network latency bounds.

## Forex and gold: MetaTrader 5

The MT5 Python package communicates with a local MT5 terminal over interprocess communication. Run this backend adapter on the Windows machine or Windows VM where the terminal is installed and logged in. The current Linux Docker service cannot host the MT5 terminal or use its Windows-only Python integration; setting `MT5_ENABLED=true` inside this Linux container will report `unavailable`.

On the Windows host, install project dependencies and the MT5 package:

```powershell
py -m pip install -r backend/requirements-mt5-windows.txt
```

Set `MT5_ENABLED=true`, `MT5_LOGIN`, `MT5_PASSWORD`, and `MT5_SERVER`; `MT5_TERMINAL_PATH` is optional when the terminal is discoverable. Start the API on that host and use:

```text
GET /market/mt5/EURUSD/ticker
GET /market/mt5/XAUUSD/candles?timeframe=5m&limit=500
```

Broker symbol names vary (for example `XAUUSD.a`), so use the exact name shown in the broker's MT5 Market Watch. The adapter implements quotes, candles, protected market orders, and position reconciliation when hosted alongside the terminal. The supplied Linux Compose service cannot provide the MT5 terminal required for these operations.

## Operational limits

All credentials must stay out of source control. Production HTTP operator routes require a tenant-scoped JWT or the configured deployment access token. The market ticker WebSocket currently streams public market data without bearer-token authentication. Local development can run without authentication when neither credential mechanism is configured; do not expose that configuration publicly. Validate symbols, venue permissions, sandbox behavior, account equity, and time synchronization before enabling a live account.
