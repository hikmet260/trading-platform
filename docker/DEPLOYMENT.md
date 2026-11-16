# Deployment and operational limits

## Local stack

Copy `.env.example` to `.env`, set strong local secrets, then run:

```bash
docker compose config --quiet
docker compose up --build -d
curl -f http://localhost:8000/health
```

The development stack publishes PostgreSQL, Redis, FastAPI, and Next.js ports and mounts source directories. Do not expose it directly to the internet.

## Production stack

The production Compose file uses PostgreSQL 16, Redis 7, FastAPI, the Telegram worker, Next.js standalone output, and Caddy TLS. Configure a real DNS name and strong values for `POSTGRES_PASSWORD`, `JWT_SECRET_KEY`, `BOOTSTRAP_ADMIN_EMAIL`, `BOOTSTRAP_ADMIN_PASSWORD`, and `BROKER_CREDENTIALS_KEY`. Generate encryption keys with:

```bash
python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

Start the services with:

```bash
docker compose -f docker-compose.production.yml config --quiet
docker compose -f docker-compose.production.yml up --build -d
```

The initial user is created in `TENANT_ID` during startup. User sessions are tenant scoped. Broker credentials stored through the API are Fernet encrypted; losing or rotating the encryption key without re-encrypting credentials makes them unreadable. Keep `.env` restricted and maintain encrypted off-host database backups.

## Execution and risk controls

Paper mode is the default. `ENGINE_ENABLED` defaults to `false`, `LIVE_TRADING_ENABLED` defaults to `false`, and `ENGINE_PAPER_MODE` defaults to `true`. For explicit live strategy execution, enable the engine and both live settings, select an enabled broker account per strategy, and set `EXECUTION_MODE=full_auto` or `semi_auto`. Semi-auto uses Telegram approval or the trade-history dashboard. The same broker service rechecks current price, account equity, risk cap, news blackout, and attached SL/TP immediately before submitting. MT5 live positions are polled by position ticket; CCXT live execution is restricted to linear derivatives, rejects pre-existing positions for that symbol, and reconciles positions and exit fills. Enabling a broker account seeds a UTC-day account-equity baseline in Redis. The background reconciler checks broker equity and trips the tenant kill switch at the configured drawdown cap. The dashboard and Telegram kill switches attempt to close ledger-tracked live positions and report failures; the runner remains halted, and it cannot resume while those records remain unreconciled. These paths still need venue-by-venue sandbox integration and failure testing before unattended production use. Some venues expose different definitions of account equity; verify that adapter's reported equity matches the broker's liquidation-risk equity before enabling live execution. MT5’s Python terminal adapter requires a Windows deployment host and is not available in the provided Linux containers.

The economic calendar uses Trading Economics and requires an operator supplied API key. Set `NEWS_CALENDAR_REQUIRED=true` to block new entries when the feed is not configured or its snapshot is stale. With it false, only the explicitly configured `NEWS_EVENTS_JSON` list is used. Provider access and coverage depend on the operator's subscription.

The MT5 Python adapter requires a Windows host with the MetaTrader 5 terminal installed; it is unavailable in the standard Linux Compose container. Crypto market data uses CCXT REST polling when CCXT Pro WebSockets are unavailable. `EXECUTION_MODE=full_auto` controls automatic submissions; `semi_auto` requires a confirmation. Paper mode remains the default. Live execution is present behind explicit deployment and account gates, but requires venue-specific sandbox integration, reconciliation, and failure testing before unattended use. No latency, fill, outage, or risk outcome guarantees are implied.

Before public or multi-tenant SaaS launch, complete a security review, broker reconciliation and kill-close path, independent risk testing, operational alerting, database restore drills, provider outage handling, and capacity testing. Compose health checks and restart policies are not an availability guarantee.
