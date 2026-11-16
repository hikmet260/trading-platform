# Container configuration

The root `docker-compose.yml` defines local development services. PostgreSQL and Redis use named volumes; the backend and frontend source directories are bind-mounted for reloads.

Copy the root `.env.example` to `.env` for local Compose. Replace local-only passwords before exposing services outside your machine. Production uses tenant-scoped JWT sessions and a bootstrap administrator; the frontend sign-in control is in the lower right corner.

Use `docker-compose.production.yml` for deployment. Generate and preserve a `BROKER_CREDENTIALS_KEY` before storing account credentials; rotating or losing it makes existing encrypted credentials unreadable. See [DEPLOYMENT.md](DEPLOYMENT.md) for execution and operational limits. Live execution is separately disabled by default and is not ready for unattended production use.
