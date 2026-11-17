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

The bootstrap user is created in `TENANT_ID` during startup only when that email does not already exist. Later changes to `BOOTSTRAP_ADMIN_PASSWORD` do not reset the account; use the authenticated password-change flow to rotate it. User sessions are tenant scoped. Broker credentials stored through the API are Fernet encrypted; losing or rotating the encryption key without re-encrypting credentials makes them unreadable. Keep `.env` restricted and maintain encrypted off-host database backups.

## Notifications and browser push

Trade proposals, trade entries and closures, and risk halts are written to the PostgreSQL notification outbox before delivery. The Telegram worker claims deliveries with expiring leases, retries transient failures with backoff, and leaves exhausted deliveries visible in Settings. Delivery is at least once: if a worker loses its connection after a provider accepted a message but before the database records success, the same alert can be sent again. Telegram and browser push are separate delivery channels.

Browser push requires HTTPS (localhost is also treated as secure by browsers), a running service worker, and a VAPID key pair. Generate the pair once with `pywebpush` installed:

```bash
vapid --gen --private-key private_key.pem --applicationServerKey
```

Copy the printed application server key to `WEBPUSH_PUBLIC_KEY`. Convert the generated private PEM to one-line base64url DER for `WEBPUSH_PRIVATE_KEY`:

```bash
python - <<'PY'
import base64
from cryptography.hazmat.primitives import serialization
with open("private_key.pem", "rb") as source:
    key = serialization.load_pem_private_key(source.read(), password=None)
der = key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                        serialization.NoEncryption())
print(base64.urlsafe_b64encode(der).rstrip(b"=").decode())
PY
```

Keep the private key in your secret store and set `WEBPUSH_PRIVATE_KEY` on the Telegram worker only. Set `WEBPUSH_SUBJECT` to an operator email or HTTPS URL. Keep the VAPID pair stable: rotating it requires users to re-enable notifications on their devices. Rebuild the backend and Telegram worker images after changing dependencies, then apply the database migration before using the notification settings page. Users opt in per browser under **Settings → Browser Notifications**; notification permission is never requested on page load.

## Execution and risk controls

Paper mode is the default. `ENGINE_ENABLED` defaults to `false`, `LIVE_TRADING_ENABLED` defaults to `false`, and `ENGINE_PAPER_MODE` defaults to `true`. For explicit live strategy execution, enable the engine and both live settings, select an enabled broker account per strategy, and set `EXECUTION_MODE=full_auto` or `semi_auto`. Semi-auto uses Telegram approval or the trade-history dashboard. The same broker service rechecks current price, account equity, risk cap, news blackout, and attached SL/TP immediately before submitting. MT5 live positions are polled by position ticket; CCXT live execution is restricted to linear derivatives, rejects pre-existing positions for that symbol, and reconciles positions and exit fills. Enabling a broker account seeds a UTC-day account-equity baseline in Redis. The background reconciler checks broker equity and trips the tenant kill switch at the configured drawdown cap. The dashboard and Telegram kill switches attempt to close ledger-tracked live positions and report failures; the runner remains halted, and it cannot resume while those records remain unreconciled. These paths still need venue-by-venue sandbox integration and failure testing before unattended production use. Some venues expose different definitions of account equity; verify that adapter's reported equity matches the broker's liquidation-risk equity before enabling live execution. MT5’s Python terminal adapter requires a Windows deployment host and is not available in the provided Linux containers.

The economic calendar uses Trading Economics and requires an operator supplied API key. Set `NEWS_CALENDAR_REQUIRED=true` to block new entries when the feed is not configured or its snapshot is stale. With it false, only the explicitly configured `NEWS_EVENTS_JSON` list is used. Provider access and coverage depend on the operator's subscription.

The MT5 Python adapter requires a Windows host with the MetaTrader 5 terminal installed; it is unavailable in the standard Linux Compose container. Crypto market data uses CCXT REST polling when CCXT Pro WebSockets are unavailable. `EXECUTION_MODE=full_auto` controls automatic submissions; `semi_auto` requires a confirmation. Paper mode remains the default. Live execution is present behind explicit deployment and account gates, but requires venue-specific sandbox integration, reconciliation, and failure testing before unattended use. No latency, fill, outage, or risk outcome guarantees are implied.

Before public or multi-tenant SaaS launch, complete a security review, broker reconciliation and kill-close path, independent risk testing, operational alerting, database restore drills, provider outage handling, and capacity testing. Compose health checks and restart policies are not an availability guarantee.

## Operations checks and database recovery

Configure an external uptime monitor (for example, from a separate host/provider) to request `https://YOUR_DOMAIN/health/ready` every minute and alert on any non-200 response. This endpoint checks Postgres, Redis, and the notification worker heartbeat. Keep `/health` as the local Compose liveness/dependency check because the worker starts after the API. The readiness endpoint is public and returns only component status.

The production Compose stack runs `postgres-backup`, which writes a compressed, custom-format `pg_dump` to the `postgres_backups` volume immediately at startup and then every 24 hours. It retains 14 days by default (`BACKUP_RETENTION_DAYS`). This volume is on the same host as the database and is not a disaster-recovery copy: sync it to encrypted off-host/object storage and alert on the backup container becoming unhealthy.

Verify a backup by restoring it into an isolated PostgreSQL database, never over the production database:

```bash
backup_file=/path/to/trading-YYYYMMDDTHHMMSSZ.dump.gz
gzip -dc "$backup_file" > /tmp/trading-restore-check.dump
createdb -h YOUR_POSTGRES_HOST -U YOUR_POSTGRES_USER trading_restore_check
pg_restore --exit-on-error --no-owner -h YOUR_POSTGRES_HOST -U YOUR_POSTGRES_USER \
  -d trading_restore_check /tmp/trading-restore-check.dump
psql -h YOUR_POSTGRES_HOST -U YOUR_POSTGRES_USER -d trading_restore_check \
  -c 'SELECT version_num FROM alembic_version;'
dropdb -h YOUR_POSTGRES_HOST -U YOUR_POSTGRES_USER trading_restore_check
rm -f /tmp/trading-restore-check.dump
```

Record the restore result and elapsed recovery time. Repeat after schema changes and at least quarterly.

## MT5 demo validation

The MT5 adapter requires a Windows host with the MetaTrader 5 terminal and Python package. Keep `LIVE_TRADING_ENABLED=false` and `ENGINE_PAPER_MODE=true` while validating connectivity and demo behavior. Use an MT5 demo server/account, verify the terminal is connected to that demo server, enable MT5 only on the Windows host, and confirm the broker account reports `demo` before placing any test order. Exercise a minimal-volume market order with attached SL/TP, a partial fill, a rejected stop level, a timeout after `order_send`, restart/reconciliation, and kill-switch closure. Confirm the broker position, order/deal history, and platform ledger agree after each case. Do not switch to a real account as part of this validation.

Keep strategies disabled until the first manual sandbox order passes. Run the engine in paper mode over at least 20 trading days, then compare every signal and simulated fill with broker/demo data, including fees, spread, slippage, max drawdown, and rejected signals. Review the result before any live-execution configuration change. This duration cannot be replaced by a successful build or unit test.
