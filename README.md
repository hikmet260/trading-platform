# Trading Platform

A containerized multi-asset trading workspace with a FastAPI service, deterministic strategy rules, paper trading and backtesting, a Next.js dashboard, optional Telegram control, and broker adapters for CCXT exchanges and MetaTrader 5.

The application defaults to a disabled, paper-only engine. Live execution is explicitly gated and should only be enabled after venue-specific sandbox and operational review. MT5's Python API requires a Windows host with the desktop terminal; it is unavailable in the standard Linux Compose stack.

## Start locally

1. Copy `.env.example` to `.env` and set local database credentials. Keep the live execution and engine switches disabled for a first run.
2. Start the development stack:

   ```bash
   docker compose up --build -d
   ```

3. Open the dashboard at `http://localhost:3000` and check API health at `http://localhost:8000/health`.

The first startup applies Alembic migrations. PostgreSQL and Redis data use named Docker volumes. The frontend uses the API URL in `NEXT_PUBLIC_API_URL`; leave its local default for local Compose.

## Main areas

- `backend/app/main.py`: API, authentication, strategies, risk, market data, and order routes.
- `backend/app/engine/`: market feeds, strategy evaluation, indicator calculations, and live position reconciliation.
- `backend/app/connectors/`: CCXT crypto and MT5 adapters.
- `backend/app/strategies/`: strict prompt parser and historical simulator.
- `backend/app/telegram_bot/`: notifications, approvals, and emergency controls.
- `frontend/src/`: dashboard, strategy studio, account setup, trade history, and backtest UI.
- `docker-compose.production.yml` and `docker/`: production stack and deployment notes.
- `SRS.md`: product scope and requirements.

## Configuration and operations

Use the root `.env.example` as the Compose configuration reference. Production requires strong database/JWT/bootstrap-admin secrets, a stable `BROKER_CREDENTIALS_KEY`, a DNS name, and the production Compose file. See [deployment notes](docker/DEPLOYMENT.md) for startup, TLS, backup, calendar, and live execution limitations.

Strategy prompts use a deliberately small deterministic grammar; unsupported or ambiguous language is rejected instead of sent to an LLM. The market dashboard uses public exchange data unless credentials are configured. Telegram, live order execution, and automated scanning are optional.

Before relying on real funds, verify the selected broker's equity and order semantics, exercise attached stop/target orders and reconciliation in its sandbox, and validate kill-switch behavior under network failures. The supplied adapters and Compose health checks do not guarantee execution quality or availability.
