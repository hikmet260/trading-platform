from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from app.core.security import CredentialVault
from app.settings import settings
from app.storage.database import Database
from app.storage.models import SystemSettingsRecord


@dataclass(frozen=True)
class TenantRuntimeConfig:
    execution_mode: str
    paper_account_equity: float
    account_currency: str
    max_daily_drawdown_pct: float
    news_blackout_minutes: int
    max_trade_risk_pct: float
    max_open_risk_pct: float
    max_notional_multiple: float
    max_positions_per_tenant: int
    max_daily_trades: int
    proposal_ttl_seconds: int
    telegram_enabled: bool
    telegram_bot_token: str
    telegram_chat_id: str
    telegram_allowed_user_ids: str
    telegram_timezone: str


def _decrypt(vault: CredentialVault | None, value: str | None, key: str) -> str:
    if not value or vault is None:
        return ""
    try:
        return str(vault.decrypt(value).get(key, ""))
    except Exception:
        return ""


async def load_runtime_config(database: Database, tenant_id: str) -> TenantRuntimeConfig:
    async with database.sessions() as session:
        record = await session.scalar(select(SystemSettingsRecord).where(
            SystemSettingsRecord.tenant_id == tenant_id,
        ))
    vault: CredentialVault | None
    try:
        vault = CredentialVault()
    except (RuntimeError, ValueError):
        vault = None

    token = _decrypt(vault, record.encrypted_telegram_bot_token if record else None, "token")
    if not token:
        token = settings.telegram_bot_token
    return TenantRuntimeConfig(
        execution_mode=(record.execution_mode if record else settings.execution_mode),
        paper_account_equity=float(record.paper_account_equity if record else settings.paper_account_equity),
        account_currency=str(getattr(record, "account_currency", None) or settings.account_currency).upper(),
        max_daily_drawdown_pct=float(record.max_daily_drawdown_pct if record else settings.engine_max_daily_drawdown_pct),
        news_blackout_minutes=int(record.news_blackout_minutes if record else settings.engine_news_blackout_minutes),
        max_trade_risk_pct=float(getattr(record, "max_trade_risk_pct", None) or settings.max_trade_risk_pct),
        max_open_risk_pct=float(getattr(record, "max_open_risk_pct", None) or settings.max_open_risk_pct),
        max_notional_multiple=float(getattr(record, "max_notional_multiple", None) or settings.max_notional_multiple),
        max_positions_per_tenant=int(getattr(record, "max_positions_per_tenant", None) or settings.max_positions_per_tenant),
        max_daily_trades=int(getattr(record, "max_daily_trades", None) or settings.max_daily_trades),
        proposal_ttl_seconds=int(getattr(record, "proposal_ttl_seconds", None) or settings.proposal_ttl_seconds),
        telegram_enabled=bool(record.telegram_enabled if record else settings.telegram_enabled),
        telegram_bot_token=token,
        telegram_chat_id=str((record.telegram_chat_id if record and record.telegram_chat_id is not None else settings.telegram_chat_id) or ""),
        telegram_allowed_user_ids=str((record.telegram_allowed_user_ids if record and record.telegram_allowed_user_ids is not None else settings.telegram_allowed_user_ids) or ""),
        telegram_timezone=str((record.telegram_timezone if record else settings.telegram_timezone) or "UTC"),
    )


def apply_runtime_config(runtime, config: TenantRuntimeConfig) -> None:
    runtime.apply_settings(
        execution_mode=config.execution_mode,
        paper_account_equity=config.paper_account_equity,
        max_daily_drawdown_pct=config.max_daily_drawdown_pct,
        news_blackout_minutes=config.news_blackout_minutes,
        account_currency=config.account_currency,
        max_trade_risk_pct=config.max_trade_risk_pct,
        max_open_risk_pct=config.max_open_risk_pct,
        max_notional_multiple=config.max_notional_multiple,
        max_positions_per_tenant=config.max_positions_per_tenant,
        max_daily_trades=config.max_daily_trades,
        proposal_ttl_seconds=config.proposal_ttl_seconds,
    )
