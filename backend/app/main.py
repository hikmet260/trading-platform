from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import asyncio
import base64
import hashlib
import json
import logging
import re
import secrets
from urllib.parse import urlsplit
from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select, update
from sqlalchemy.exc import IntegrityError
from starlette.responses import JSONResponse

from typing import Literal

import httpx
from app.connectors.crypto import CryptoMarketData, SUPPORTED_EXCHANGES, classify_ccxt_error
from app.config_service import apply_runtime_config, load_runtime_config
from app.core.auth import hash_password, issue_access_token, verify_access_token, verify_password
from app.core.security import CredentialVault
from app.connectors.mt5 import MT5Connector
from app.engine.runner import MarketEngine, MT5MarketEngine, MarketEvent
from app.engine.live_positions import LivePositionManager
from app.mt5_agent_service import agent_is_online, make_agent_token, token_digest
from app.engine.indicators import calculate_indicators
from app.engine.strategy_runtime import StrategyRuntime
from app.risk.controls import RiskManager, TradeProposal
from app.risk.exposure import load_exposure
from app.risk.news import EconomicCalendar
from app.settings import settings
from app.storage.cache import MarketCache
from app.storage.database import Database
from app.storage.models import (
    AuditRecord, BrokerAccountRecord, NotificationDeliveryRecord, PushSubscriptionRecord,
    MT5AgentCommandRecord, MT5AgentRecord, StrategyRecord, SystemSettingsRecord, TradeRecord, UserRecord,
)
from app.notifications.service import enqueue_notification
from app.strategies.backtest import run_backtest, run_walk_forward
from app.strategies.rules import StrategyParseError, StrategyRule, parse_strategy_prompt
from app.telegram_bot.controller import TelegramController

logger = logging.getLogger(__name__)


class CompileRequest(BaseModel):
    prompt: str = Field(min_length=15, max_length=1000)


class StrategyCreateRequest(CompileRequest):
    name: str = Field(min_length=1, max_length=120)
    enabled: bool = False
    broker_account_id: str | None = Field(default=None, min_length=1, max_length=36)


class StrategyEnabledRequest(BaseModel):
    enabled: bool


class AIGenerateStrategyRequest(BaseModel):
    user_prompt: str = Field(min_length=2, max_length=1500)
    symbol: str | None = None
    timeframe: str | None = None


class CopilotMessage(BaseModel):
    role: str
    content: str


class CopilotChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2500)
    history: list[CopilotMessage] = Field(default_factory=list)
    symbol: str | None = None
    timeframe: str | None = None
    current_page: str | None = None
    model: str | None = Field(default=None, min_length=1, max_length=80)


class BacktestRequest(CompileRequest):
    candles: list[dict] = Field(min_length=3, max_length=100_000)
    starting_balance: float = Field(default=10_000, gt=0)
    fee_bps: float = Field(default=5, ge=0, le=1_000)
    spread_bps: float = Field(default=0, ge=0, le=1_000)
    slippage_bps: float = Field(default=0, ge=0, le=1_000)
    funding_bps_per_day: float = Field(default=0, ge=-1_000, le=1_000)
    max_leverage: float | None = Field(default=None, gt=0, le=1_000)
    account_currency: str = Field(default="USD", min_length=3, max_length=8)


class WalkForwardRequest(BacktestRequest):
    folds: int = Field(default=4, ge=2, le=20)


class IndicatorRequest(BaseModel):
    candles: list[dict] = Field(min_length=2, max_length=100_000)


class PaperTradeRequest(TradeProposal):
    pass


class RiskSettingsRequest(BaseModel):
    max_daily_drawdown_pct: float = Field(gt=0, le=100)
    news_blackout_minutes: int = Field(ge=0, le=240)


class TradeDecisionRequest(BaseModel):
    approved: bool


class SessionRequest(BaseModel):
    access_token: str = Field(min_length=1, max_length=512)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)
    tenant_id: str = Field(default="local", min_length=1, max_length=36)


class UserCreateRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=12, max_length=1024)
    tenant_id: str = Field(default="local", min_length=1, max_length=36)
    is_platform_admin: bool = False


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=12, max_length=1024)


class SystemSettingsUpdateRequest(BaseModel):
    ai_provider: str = Field(default="openai", min_length=1, max_length=40)
    ai_model: str = Field(default="gpt-4o", min_length=1, max_length=80)
    ai_api_key: str | None = None
    ai_temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    ai_system_prompt: str | None = None

    telegram_enabled: bool = False
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    telegram_allowed_user_ids: str | None = None
    telegram_timezone: str = "UTC"

    execution_mode: Literal["semi_auto", "full_auto"] = "semi_auto"
    max_daily_drawdown_pct: float = Field(default=3.0, gt=0, le=100.0)
    news_blackout_minutes: int = Field(default=15, ge=0, le=1440)
    paper_account_equity: float = Field(default=10000.0, gt=0)
    account_currency: str | None = Field(default=None, min_length=3, max_length=8)
    max_trade_risk_pct: float | None = Field(default=None, gt=0, le=100)
    max_open_risk_pct: float | None = Field(default=None, gt=0, le=100)
    max_notional_multiple: float | None = Field(default=None, gt=0, le=100)
    max_positions_per_tenant: int | None = Field(default=None, ge=1, le=100)
    max_daily_trades: int | None = Field(default=None, ge=1, le=10_000)
    proposal_ttl_seconds: int | None = Field(default=None, ge=60, le=86_400)


class TestTelegramRequest(BaseModel):
    bot_token: str | None = None
    chat_id: str | None = None


class TestAIRequest(BaseModel):
    provider: str = "openai"
    api_key: str | None = None
    model: str = "gpt-4o"


class PushSubscriptionKeys(BaseModel):
    p256dh: str = Field(min_length=16, max_length=256)
    auth: str = Field(min_length=8, max_length=256)


class PushSubscriptionRequest(BaseModel):
    endpoint: str = Field(min_length=16, max_length=2048)
    keys: PushSubscriptionKeys


class PushUnsubscribeRequest(BaseModel):
    endpoint: str = Field(min_length=16, max_length=2048)


def mask_secret(secret: str | None) -> str:
    if not secret:
        return ""
    if len(secret) <= 8:
        return "********"
    return f"{secret[:4]}...{secret[-4:]}"


def extract_copilot_strategy_prompt(reply: str) -> str | None:
    match = re.search(
        r"((?:BUY|SELL)\s+[A-Za-z0-9/\-_]+\s+on\s+[0-9]+[mhd]\s+when\s+.+?\s+"
        r"risk\s+[0-9]+(?:\.[0-9]+)?%\s+stop\s+loss\s+[0-9]+(?:\.[0-9]+)?\s+pips?\s+"
        r"take\s+profit\s+[0-9]+(?:\.[0-9]+)?\s+pips?)",
        reply,
        re.IGNORECASE,
    )
    return match.group(1).strip() if match else None


def _get_vault() -> CredentialVault | None:
    try:
        return CredentialVault()
    except Exception:
        return None


class BrokerAccountCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    broker: str = Field(pattern=r"^(binance|bybit|okx|mt5)$")
    credentials: dict[str, str]
    configuration: dict = Field(default_factory=dict)


class BrokerAccountEnabledRequest(BaseModel):
    enabled: bool


class MT5AgentCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class MT5AgentHeartbeatRequest(BaseModel):
    account: dict
    agent_version: str = Field(default="0.1.0", max_length=32)


class MT5AgentResultRequest(BaseModel):
    status: Literal["completed", "failed", "unknown"]
    response: dict = Field(default_factory=dict)
    error: str | None = Field(default=None, max_length=500)


class MT5AgentMarketDataRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=40)
    timeframe: str = Field(min_length=1, max_length=8)
    kind: Literal["history", "candle"]
    candles: list[dict] = Field(min_length=1, max_length=1000)


class LiveOrderRequest(BaseModel):
    account_id: str = Field(min_length=1, max_length=36)
    symbol: str = Field(min_length=3, max_length=40)
    side: str = Field(pattern=r"^(buy|sell)$")
    quantity: float = Field(gt=0)
    stop_loss: float = Field(gt=0)
    take_profit: float = Field(gt=0)
    risk_pct: float = Field(gt=0, le=1.0)
    client_order_id: str | None = Field(default=None, max_length=80)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.app_env.lower() == "production":
        if len(settings.jwt_secret_key) < 32:
            raise RuntimeError("Production requires a JWT_SECRET_KEY with at least 32 characters")
        if not settings.bootstrap_admin_email or len(settings.bootstrap_admin_password) < 12:
            raise RuntimeError("Production requires a bootstrap admin email and a password with at least 12 characters")
        try:
            CredentialVault()
        except (RuntimeError, ValueError) as exc:
            raise RuntimeError("Production requires a valid BROKER_CREDENTIALS_KEY") from exc
    app.state.crypto = None
    app.state.crypto_connectors = {}
    app.state.database = Database()
    await app.state.database.initialize()
    if settings.bootstrap_admin_email and settings.bootstrap_admin_password:
        async with app.state.database.sessions() as session:
            existing = await session.scalar(select(UserRecord).where(
                UserRecord.tenant_id == settings.tenant_id,
                UserRecord.email == settings.bootstrap_admin_email.strip().lower(),
            ))
            if existing is None:
                session.add(UserRecord(
                    id=str(uuid4()), tenant_id=settings.tenant_id,
                    email=settings.bootstrap_admin_email.strip().lower(),
                    password_hash=hash_password(settings.bootstrap_admin_password),
                    is_active=True, is_platform_admin=True,
                ))
                await session.commit()
    app.state.cache = MarketCache()
    if not await app.state.cache.ping():
        await app.state.database.close()
        raise RuntimeError("Redis did not become available during startup")
    app.state.mt5 = MT5Connector(
        login=settings.mt5_login,
        password=settings.mt5_password,
        server=settings.mt5_server,
        terminal_path=settings.mt5_terminal_path,
    )
    await app.state.mt5.connect(settings.mt5_enabled)
    app.state.engine = None
    app.state.engines = []
    app.state.engine_error = None
    app.state.telegram = TelegramController(app.state.cache, app.state.database)
    app.state.strategy_runtime = StrategyRuntime(app.state.database, app.state.cache, app.state.telegram)
    runtime_config = await load_runtime_config(app.state.database, settings.tenant_id)
    app.state.runtime_config = runtime_config
    apply_runtime_config(app.state.strategy_runtime, runtime_config)
    await app.state.strategy_runtime.initialize()
    try:
        await app.state.telegram.apply_config(
            enabled=runtime_config.telegram_enabled,
            bot_token=runtime_config.telegram_bot_token,
            chat_id=runtime_config.telegram_chat_id,
            allowed_user_ids=runtime_config.telegram_allowed_user_ids,
            timezone_name=runtime_config.telegram_timezone,
        )
    except Exception as exc:
        app.state.telegram.state = "error"
        app.state.telegram_error = str(exc)
        logger.exception("Telegram could not be configured from tenant settings")
    app.state.live_positions = LivePositionManager(app.state.database, app.state.cache)
    await app.state.live_positions.reconcile_once()
    await app.state.live_positions.start()
    await app.state.strategy_runtime.start()
    app.state.calendar = EconomicCalendar(settings.news_calendar_api_key, settings.news_calendar_poll_seconds)
    await app.state.calendar.start()
    app.state.strategy_runtime.calendar = app.state.calendar
    async def telegram_kill():
        return await _activate_kill_switch("telegram", settings.tenant_id)

    async def telegram_resume():
        if await app.state.live_positions.has_open_positions(settings.tenant_id):
            return False
        await app.state.strategy_runtime.resume("telegram", settings.tenant_id)
        return True

    app.state.telegram.on_kill = telegram_kill
    app.state.telegram.on_resume = telegram_resume
    app.state.telegram.on_decision = app.state.strategy_runtime.approve_paper_trade
    try:
        exchange_id = settings.crypto_exchange.lower()
        if exchange_id not in SUPPORTED_EXCHANGES:
            raise ValueError(f"CRYPTO_EXCHANGE must be one of {', '.join(sorted(SUPPORTED_EXCHANGES))}.")
        app.state.crypto = CryptoMarketData(
            exchange_id=exchange_id,
            api_key=settings.crypto_api_key,
            secret=settings.crypto_api_secret,
            password=settings.crypto_api_password,
            sandbox=settings.crypto_sandbox,
            default_type=settings.crypto_default_type,
        )
        app.state.crypto_connectors[exchange_id] = app.state.crypto
    except Exception as exc:
        app.state.crypto_error = str(exc)
    app.state.strategy_runtime.live_positions = app.state.live_positions
    if settings.engine_enabled and not settings.engine_paper_mode and not settings.live_trading_enabled:
        app.state.engine_error = "Live strategy mode requires LIVE_TRADING_ENABLED=true; no strategy engine was started."
    elif settings.engine_enabled:
        errors: list[str] = []
        crypto_feeds: dict[tuple[str, str, str], set[str]] = {}
        mt5_feeds: dict[str, set[str]] = {}
        if app.state.crypto is not None:
            crypto_feeds[(exchange_id, settings.crypto_default_type, settings.engine_timeframe)] = set(settings.engine_symbol_list)
        mt5_feeds.setdefault(settings.engine_timeframe, set()).update(settings.engine_mt5_symbol_list)
        async with app.state.database.sessions() as session:
            routes = await session.execute(
                select(StrategyRecord.rule, BrokerAccountRecord.broker)
                .join(BrokerAccountRecord, BrokerAccountRecord.id == StrategyRecord.broker_account_id)
                .where(StrategyRecord.enabled.is_(True), BrokerAccountRecord.enabled.is_(True))
            )
            for rule_data, broker in routes:
                try:
                    rule = StrategyRule.model_validate(rule_data)
                except Exception:
                    logger.exception("Skipping invalid strategy rule while starting market feeds")
                    continue
                symbol = rule.symbol
                timeframe = rule.timeframe
                if broker in SUPPORTED_EXCHANGES:
                    feed_exchange = exchange_id if settings.engine_paper_mode else broker
                    feed_type = settings.crypto_default_type if settings.engine_paper_mode else "swap"
                    crypto_feeds.setdefault((feed_exchange, feed_type, timeframe), set()).add(symbol)
                elif broker == "mt5":
                    mt5_feeds.setdefault(timeframe, set()).add(symbol.replace("/", ""))
        crypto_connectors: dict[tuple[str, str], CryptoMarketData] = {}
        for (feed_exchange, feed_type, _timeframe) in crypto_feeds:
            if (feed_exchange, feed_type) in crypto_connectors:
                continue
            if (app.state.crypto is not None and feed_exchange == app.state.crypto.exchange_id
                    and feed_type == settings.crypto_default_type):
                crypto_connectors[(feed_exchange, feed_type)] = app.state.crypto
                continue
            try:
                connector = CryptoMarketData(
                    exchange_id=feed_exchange, sandbox=settings.crypto_sandbox, default_type=feed_type,
                )
                crypto_connectors[(feed_exchange, feed_type)] = connector
                app.state.crypto_connectors[f"feed:{feed_exchange}:{feed_type}"] = connector
            except Exception as exc:
                errors.append(f"{feed_exchange}/{feed_type}: {exc}")
        ticker_owner: dict[tuple[str, str, str], str] = {}
        for key in sorted(crypto_feeds, key=lambda item: (item[0], item[1], item[2])):
            for symbol in crypto_feeds[key]:
                ticker_owner.setdefault((key[0], key[1], symbol), key[2])
        for (feed_exchange, feed_type, timeframe), feed_symbols in crypto_feeds.items():
            connector = crypto_connectors.get((feed_exchange, feed_type))
            if connector is None:
                continue
            ticker_symbols = [
                symbol for symbol in sorted(feed_symbols)
                if ticker_owner.get((feed_exchange, feed_type, symbol)) == timeframe
            ]
            try:
                crypto_engine = MarketEngine(
                    connector, app.state.cache, sorted(feed_symbols), timeframe,
                    on_event=app.state.strategy_runtime.on_event,
                    ticker_symbols=ticker_symbols,
                )
                await crypto_engine.start()
                app.state.engines.append(crypto_engine)
            except Exception as exc:
                errors.append(f"{feed_exchange}/{feed_type}/{timeframe}: {exc}")
        if app.state.mt5.state == "connected":
            for timeframe, mt5_symbols in mt5_feeds.items():
                if not mt5_symbols:
                    continue
                try:
                    mt5_engine = MT5MarketEngine(
                        app.state.mt5, app.state.cache, sorted(mt5_symbols), timeframe,
                        on_event=app.state.strategy_runtime.on_event,
                    )
                    await mt5_engine.start()
                    app.state.engines.append(mt5_engine)
                except Exception as exc:
                    errors.append(f"mt5/{timeframe}: {exc}")
        if app.state.engines:
            app.state.engine = app.state.engines[0]
        else:
            app.state.engine_error = "; ".join(errors) or "No market-data adapter is available for the runner."
    try:
        yield
    finally:
        for engine in app.state.engines:
            await engine.stop()
        await app.state.strategy_runtime.stop()
        await app.state.live_positions.stop()
        await app.state.telegram.stop()
        await app.state.calendar.stop()
        for connector in app.state.crypto_connectors.values():
            await connector.close()
        await app.state.mt5.close()
        await app.state.cache.close()
        await app.state.database.close()


app = FastAPI(
    title="Trading Platform API",
    version="0.1.0",
    description="API foundation for the multi-asset trading platform.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.post("/auth/session", tags=["authentication"])
async def create_session(request: SessionRequest) -> dict:
    if not settings.api_access_token:
        if settings.app_env.lower() == "production":
            raise HTTPException(status_code=503, detail="API_ACCESS_TOKEN is not configured")
        return {"valid": True, "auth_required": False}
    if not secrets.compare_digest(request.access_token, settings.api_access_token):
        raise HTTPException(status_code=401, detail="Invalid access token")
    return {"valid": True, "auth_required": True}


@app.post("/auth/login", tags=["authentication"])
async def login(request: LoginRequest, context: Request) -> dict:
    if len(settings.jwt_secret_key) < 32:
        raise HTTPException(status_code=503, detail="JWT sessions are not configured")
    email = request.email.strip().lower()
    remote = context.client.host if context.client else "unknown"
    failure_key = "auth:login-fail:" + hashlib.sha256(f"{remote}:{request.tenant_id}:{email}".encode()).hexdigest()
    if int(await app.state.cache.redis.get(failure_key) or 0) >= 5:
        raise HTTPException(status_code=429, detail="Too many failed logins; retry in 15 minutes")
    async with app.state.database.sessions() as session:
        user = await session.scalar(select(UserRecord).where(
            UserRecord.email == email, UserRecord.tenant_id == request.tenant_id,
        ))
        if user is None or not user.is_active or not verify_password(request.password, user.password_hash):
            async with app.state.cache.redis.pipeline(transaction=True) as pipeline:
                pipeline.incr(failure_key)
                pipeline.expire(failure_key, 900)
                await pipeline.execute()
            raise HTTPException(status_code=401, detail="Invalid email or password")
        await app.state.cache.redis.delete(failure_key)
        token = issue_access_token(user.id, user.tenant_id, settings.jwt_secret_key,
                                   settings.access_token_ttl_seconds)
        session.add(AuditRecord(tenant_id=user.tenant_id, actor=user.id, action="user_login",
                                details={"email": user.email}))
        await session.commit()
    return {"access_token": token, "token_type": "bearer", "expires_in": settings.access_token_ttl_seconds,
            "user": {"id": user.id, "email": user.email, "tenant_id": user.tenant_id,
                     "is_platform_admin": user.is_platform_admin}}


@app.post("/auth/users", status_code=201, tags=["authentication"])
async def create_user(request: Request, payload: UserCreateRequest) -> dict:
    if not getattr(request.state, "is_platform_admin", False):
        raise HTTPException(status_code=403, detail="Platform administrator access required")
    email = payload.email.strip().lower()
    async with app.state.database.sessions() as session:
        existing = await session.scalar(select(UserRecord.id).where(
            UserRecord.tenant_id == payload.tenant_id, UserRecord.email == email,
        ))
        if existing:
            raise HTTPException(status_code=409, detail="A user with this email already exists in the tenant")
        user = UserRecord(id=str(uuid4()), tenant_id=payload.tenant_id, email=email,
                          password_hash=hash_password(payload.password), is_active=True,
                          is_platform_admin=payload.is_platform_admin)
        session.add(user)
        session.add(AuditRecord(tenant_id=payload.tenant_id, actor=request.state.user_id,
                                action="user_created", entity_id=user.id,
                                details={"email": email, "is_platform_admin": payload.is_platform_admin}))
        await session.commit()
    return {"id": user.id, "tenant_id": user.tenant_id, "email": user.email,
            "is_active": user.is_active, "is_platform_admin": user.is_platform_admin}


@app.get("/auth/me", tags=["authentication"])
async def current_user(request: Request) -> dict:
    if not getattr(request.state, "user_id", None):
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {
        "id": getattr(request.state, "user_id", None),
        "email": getattr(request.state, "email", None),
        "tenant_id": getattr(request.state, "tenant_id", settings.tenant_id),
        "is_platform_admin": getattr(request.state, "is_platform_admin", False),
    }


@app.post("/auth/logout", tags=["authentication"])
async def logout(request: Request) -> dict:
    user_id = getattr(request.state, "user_id", None)
    tenant_id = getattr(request.state, "tenant_id", settings.tenant_id)
    if user_id:
        try:
            async with app.state.database.sessions() as session:
                session.add(AuditRecord(
                    tenant_id=tenant_id,
                    actor=user_id,
                    action="user_logout",
                    details={"email": getattr(request.state, "email", None)},
                ))
                await session.commit()
        except Exception:
            pass
    return {"success": True, "message": "Logged out successfully"}


@app.post("/auth/change-password", tags=["authentication"])
async def change_password(request: Request, payload: ChangePasswordRequest) -> dict:
    user_id = getattr(request.state, "user_id", None)
    tenant_id = getattr(request.state, "tenant_id", settings.tenant_id)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    if user_id in ("deployment-admin", "local-development"):
        raise HTTPException(
            status_code=400,
            detail="Static administrative accounts cannot change password via web. Please update your environment configuration."
        )

    async with app.state.database.sessions() as session:
        user = await session.get(UserRecord, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="User account not found")
        if not verify_password(payload.current_password, user.password_hash):
            raise HTTPException(status_code=400, detail="Current password is incorrect")

        user.password_hash = hash_password(payload.new_password)
        session.add(AuditRecord(
            tenant_id=tenant_id,
            actor=user_id,
            action="password_changed",
            entity_id=user_id,
            details={"email": user.email}
        ))
        await session.commit()
    return {"success": True, "message": "Password updated successfully"}


@app.middleware("http")
async def require_control_token(request, call_next):
    """Require a deployment token for all operator and account data routes when configured."""
    public_paths = {"/", "/health", "/health/ready", "/auth/session", "/auth/login", "/auth/logout", "/docs", "/openapi.json", "/redoc"}
    expected = settings.api_access_token
    if request.method == "OPTIONS" or request.url.path in public_paths:
        return await call_next(request)
    if request.url.path.startswith("/mt5/agent/"):
        # These routes authenticate the dedicated, hashed agent token below.
        return await call_next(request)
    supplied = request.headers.get("authorization", "")
    token = supplied.removeprefix("Bearer ") if supplied.startswith("Bearer ") else ""
    if expected and token and secrets.compare_digest(token, expected):
        request.state.user_id = "deployment-admin"
        request.state.email = "deployment-admin@trading.local"
        request.state.tenant_id = settings.tenant_id
        request.state.is_platform_admin = True
        return await call_next(request)
    if settings.jwt_secret_key and token:
        try:
            claims = verify_access_token(token, settings.jwt_secret_key)
            async with app.state.database.sessions() as session:
                user = await session.get(UserRecord, claims["sub"])
            if user is not None and user.is_active and user.tenant_id == claims["tid"]:
                request.state.user_id = user.id
                request.state.email = user.email
                request.state.tenant_id = user.tenant_id
                request.state.is_platform_admin = user.is_platform_admin
                return await call_next(request)
        except (ValueError, KeyError, TypeError):
            pass
    if not settings.api_access_token and not settings.jwt_secret_key and settings.app_env.lower() != "production":
        request.state.user_id = "local-development"
        request.state.email = "dev@trading.local"
        request.state.tenant_id = settings.tenant_id
        request.state.is_platform_admin = True
        return await call_next(request)
    if not expected and not settings.jwt_secret_key:
        return JSONResponse({"detail": "Authentication is not configured"}, status_code=503)
    if not token:
        return JSONResponse({"detail": "Valid bearer token required"}, status_code=401,
                            headers={"WWW-Authenticate": "Bearer"})
    return JSONResponse({"detail": "Invalid or expired bearer token"}, status_code=401,
                        headers={"WWW-Authenticate": "Bearer"})


@app.get("/system/status", tags=["system"])
async def system_status(request: Request) -> dict:
    return {
        "environment": settings.app_env,
        "execution_mode": app.state.strategy_runtime.execution_mode,
        "paper_mode": settings.engine_paper_mode,
        "account_currency": app.state.strategy_runtime.account_currency,
        "paper_account_equity": app.state.strategy_runtime.paper_equity,
        "live_order_api_enabled": settings.live_trading_enabled,
        "engine_enabled": settings.engine_enabled,
        "engine_running": bool(app.state.engine and app.state.engine.status()["running"]),
        "engines": [engine.status() for engine in app.state.engines],
        "kill_switch": await app.state.cache.is_kill_switch_active(request.state.tenant_id),
        "daily_drawdown_halted": app.state.strategy_runtime.risk_for(request.state.tenant_id).halted,
        "daily_drawdown_pct": app.state.strategy_runtime.risk_for(request.state.tenant_id).daily_drawdown_pct,
        "daily_drawdown_limit_pct": app.state.strategy_runtime.risk_for(request.state.tenant_id).max_daily_drawdown_pct,
        "live_position_manager": app.state.live_positions.status(),
    }


@app.get("/notifications/push/config", tags=["notifications"])
async def push_notification_config() -> dict:
    enabled = bool(settings.webpush_public_key)
    worker = await app.state.cache.redis.get("notifications:worker:heartbeat")
    worker_ready = worker == b"push-ready" or worker == "push-ready"
    return {
        "enabled": enabled,
        "public_key": settings.webpush_public_key if enabled else "",
        "worker_ready": worker_ready,
    }


@app.get("/notifications/push/subscriptions", tags=["notifications"])
async def list_push_subscriptions(request: Request) -> dict:
    async with app.state.database.sessions() as session:
        rows = await session.execute(select(PushSubscriptionRecord).where(
            PushSubscriptionRecord.tenant_id == request.state.tenant_id,
            PushSubscriptionRecord.user_id == request.state.user_id,
        ).order_by(desc(PushSubscriptionRecord.created_at)))
        subscriptions = list(rows.scalars())
        deliveries = 0
        failed = 0
        channel_summary: dict[str, dict[str, int]] = {}
        if subscriptions:
            ids = [item.id for item in subscriptions]
            delivery_rows = await session.execute(select(
                NotificationDeliveryRecord.status, func.count(),
            ).where(
                NotificationDeliveryRecord.subscription_id.in_(ids),
                NotificationDeliveryRecord.channel == "webpush",
                NotificationDeliveryRecord.created_at >= datetime.now(timezone.utc) - timedelta(days=7),
            ).group_by(NotificationDeliveryRecord.status))
            totals = {status: count for status, count in delivery_rows}
            deliveries = sum(totals.values())
            failed = totals.get("failed", 0)
        tenant_rows = await session.execute(select(
            NotificationDeliveryRecord.channel,
            NotificationDeliveryRecord.status,
            func.count(),
        ).where(
            NotificationDeliveryRecord.tenant_id == request.state.tenant_id,
            NotificationDeliveryRecord.created_at >= datetime.now(timezone.utc) - timedelta(days=7),
        ).group_by(NotificationDeliveryRecord.channel, NotificationDeliveryRecord.status))
        for channel, status, count in tenant_rows:
            channel_summary.setdefault(channel, {})[status] = count
    return {
        "configured": bool(settings.webpush_public_key),
        "subscriptions": [{
            "id": item.id,
            "endpoint_host": urlsplit(item.endpoint).hostname,
            "endpoint_fingerprint": hashlib.sha256(item.endpoint.encode("utf-8")).hexdigest(),
            "created_at": item.created_at.isoformat(),
            "last_success_at": item.last_success_at.isoformat() if item.last_success_at else None,
            "last_error": item.last_error,
        } for item in subscriptions],
        "deliveries_last_7_days": deliveries,
        "failed_deliveries_last_7_days": failed,
        "delivery_summary": channel_summary,
    }


@app.post("/notifications/push/subscriptions", status_code=201, tags=["notifications"])
async def create_push_subscription(payload: PushSubscriptionRequest, request: Request) -> dict:
    if not settings.webpush_public_key:
        raise HTTPException(status_code=503, detail="Browser push is not configured on this server")
    parts = urlsplit(payload.endpoint)
    host = (parts.hostname or "").lower().rstrip(".")
    try:
        port = parts.port
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Unsupported browser push endpoint") from exc
    allowed_hosts = (
        host == "fcm.googleapis.com"
        or host.endswith(".push.services.mozilla.com")
        or host == "updates.push.services.mozilla.com"
        or host == "web.push.apple.com"
        or host.endswith(".notify.windows.com")
    )
    if (parts.scheme != "https" or not allowed_hosts or parts.username or parts.password
            or port not in (None, 443) or not parts.path):
        raise HTTPException(status_code=422, detail="Unsupported browser push endpoint")

    if (re.fullmatch(r"[A-Za-z0-9_-]+", payload.keys.p256dh) is None
            or re.fullmatch(r"[A-Za-z0-9_-]+", payload.keys.auth) is None):
        raise HTTPException(status_code=422, detail="Browser push encryption keys are invalid")
    try:
        p256dh = base64.urlsafe_b64decode(payload.keys.p256dh + "=" * (-len(payload.keys.p256dh) % 4))
        auth_secret = base64.urlsafe_b64decode(payload.keys.auth + "=" * (-len(payload.keys.auth) % 4))
    except Exception as exc:
        raise HTTPException(status_code=422, detail="Browser push encryption keys are invalid") from exc
    if len(p256dh) != 65 or p256dh[0] != 4 or len(auth_secret) < 16:
        raise HTTPException(status_code=422, detail="Browser push encryption keys are invalid")

    async with app.state.database.sessions() as session:
        existing = await session.scalar(select(PushSubscriptionRecord).where(
            PushSubscriptionRecord.endpoint == payload.endpoint,
        ))
        if existing:
            if (existing.tenant_id, existing.user_id) != (request.state.tenant_id, request.state.user_id):
                raise HTTPException(status_code=409, detail="This browser subscription belongs to another account")
            existing.p256dh = payload.keys.p256dh
            existing.auth = payload.keys.auth
            existing.last_error = None
            record = existing
        else:
            count = await session.scalar(select(func.count()).select_from(PushSubscriptionRecord).where(
                PushSubscriptionRecord.tenant_id == request.state.tenant_id,
                PushSubscriptionRecord.user_id == request.state.user_id,
            ))
            if int(count or 0) >= 10:
                raise HTTPException(status_code=429, detail="This account has reached the 10-device notification limit")
            record = PushSubscriptionRecord(
                id=str(uuid4()), tenant_id=request.state.tenant_id,
                user_id=request.state.user_id, endpoint=payload.endpoint,
                p256dh=payload.keys.p256dh, auth=payload.keys.auth,
            )
            session.add(record)
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            existing = await session.scalar(select(PushSubscriptionRecord).where(
                PushSubscriptionRecord.endpoint == payload.endpoint,
            ))
            if existing is None or (existing.tenant_id, existing.user_id) != (request.state.tenant_id, request.state.user_id):
                raise HTTPException(status_code=409, detail="This browser subscription could not be assigned to this account") from exc
            existing.p256dh = payload.keys.p256dh
            existing.auth = payload.keys.auth
            existing.last_error = None
            await session.commit()
            record = existing
        return {"id": record.id, "status": "subscribed"}


@app.delete("/notifications/push/subscriptions/{subscription_id}", tags=["notifications"])
async def delete_push_subscription(subscription_id: str, request: Request) -> dict:
    async with app.state.database.sessions() as session:
        subscription = await session.scalar(select(PushSubscriptionRecord).where(
            PushSubscriptionRecord.id == subscription_id,
            PushSubscriptionRecord.tenant_id == request.state.tenant_id,
            PushSubscriptionRecord.user_id == request.state.user_id,
        ))
        if subscription is None:
            raise HTTPException(status_code=404, detail="Notification device not found")
        await session.execute(update(NotificationDeliveryRecord).where(
            NotificationDeliveryRecord.subscription_id == subscription.id,
            NotificationDeliveryRecord.status.in_(["pending", "retry"]),
        ).values(status="cancelled", last_error="Device was unsubscribed before delivery"))
        await session.delete(subscription)
        await session.commit()
    return {"status": "unsubscribed"}


@app.post("/notifications/push/unsubscribe", tags=["notifications"])
async def unsubscribe_push_device(payload: PushUnsubscribeRequest, request: Request) -> dict:
    async with app.state.database.sessions() as session:
        subscription = await session.scalar(select(PushSubscriptionRecord).where(
            PushSubscriptionRecord.endpoint == payload.endpoint,
            PushSubscriptionRecord.tenant_id == request.state.tenant_id,
            PushSubscriptionRecord.user_id == request.state.user_id,
        ))
        if subscription:
            await session.execute(update(NotificationDeliveryRecord).where(
                NotificationDeliveryRecord.subscription_id == subscription.id,
                NotificationDeliveryRecord.status.in_(["pending", "retry"]),
            ).values(status="cancelled", last_error="Device was unsubscribed before delivery"))
            await session.delete(subscription)
            await session.commit()
    return {"status": "unsubscribed"}


@app.post("/notifications/push/test", status_code=202, tags=["notifications"])
async def send_push_test(request: Request) -> dict:
    if not settings.webpush_public_key:
        raise HTTPException(status_code=503, detail="Browser push is not configured on this server")
    _, queued = await enqueue_notification(
        app.state.database,
        tenant_id=request.state.tenant_id,
        kind="push_test",
        payload={"kind": "push_test", "title": "Notifications are on", "body": "This browser can receive trading platform alerts.", "url": "/settings"},
        dedupe_key=f"push-test:{uuid4()}",
        telegram=False,
        push=True,
        target_user_id=request.state.user_id,
    )
    if queued == 0:
        raise HTTPException(status_code=409, detail="Enable browser notifications on this device first")
    return {"status": "queued", "deliveries": queued}


@app.post("/system/kill-switch", tags=["system"])
async def activate_kill_switch(request: Request) -> dict:
    return {"active": True, **await _activate_kill_switch(request.state.user_id, request.state.tenant_id)}


@app.delete("/system/kill-switch", tags=["system"])
async def clear_kill_switch(request: Request) -> dict:
    if await app.state.live_positions.has_open_positions(request.state.tenant_id):
        raise HTTPException(status_code=423, detail="Cannot resume while broker positions remain open or unreconciled")
    await app.state.strategy_runtime.resume("dashboard", request.state.tenant_id)
    return {"active": False, "paper_only": True}


async def _activate_kill_switch(actor: str, tenant_id: str) -> dict:
    paper_closed = await app.state.strategy_runtime.emergency_stop(actor, tenant_id)
    live_result = await app.state.live_positions.close_tenant(tenant_id, actor)
    return {"closed_paper_positions": paper_closed, "closed_live_positions": live_result["closed"],
            "unclosed_live_positions": live_result["remaining"], "close_errors": live_result["errors"],
            "paper_only": False}


@app.get("/risk/settings", tags=["risk"])
async def get_risk_settings(request: Request) -> dict:
    risk = app.state.strategy_runtime.risk_for(request.state.tenant_id)
    return {
        "max_daily_drawdown_pct": risk.max_daily_drawdown_pct,
        "news_blackout_minutes": int(risk.news_blackout.total_seconds() // 60),
        "max_trade_risk_pct": risk.max_trade_risk_pct,
        "max_open_risk_pct": risk.max_open_risk_pct,
        "max_notional_multiple": risk.max_notional_multiple,
        "max_positions_per_tenant": risk.max_positions_per_tenant,
        "max_daily_trades": risk.max_daily_trades,
        "account_currency": risk.account_currency,
        "halted": risk.halted, "daily_drawdown_pct": risk.daily_drawdown_pct,
        "trades_today": risk.trades_today,
    }


@app.put("/risk/settings", tags=["risk"])
async def set_risk_settings(request: RiskSettingsRequest, context: Request) -> dict:
    tenant_id = context.state.tenant_id
    async with app.state.database.sessions() as session:
        record = await session.get(SystemSettingsRecord, tenant_id)
        if record is None:
            record = SystemSettingsRecord(tenant_id=tenant_id)
            session.add(record)
        record.max_daily_drawdown_pct = request.max_daily_drawdown_pct
        record.news_blackout_minutes = request.news_blackout_minutes
        session.add(AuditRecord(tenant_id=tenant_id, actor=context.state.user_id,
                                action="risk_settings_updated", details=request.model_dump()))
        await session.commit()
    config = await load_runtime_config(app.state.database, tenant_id)
    app.state.runtime_config = config
    apply_runtime_config(app.state.strategy_runtime, config)
    await app.state.cache.redis.set(f"risk:settings:{tenant_id}", json.dumps({
        "max_daily_drawdown_pct": config.max_daily_drawdown_pct,
        "news_blackout_minutes": config.news_blackout_minutes,
        "max_trade_risk_pct": config.max_trade_risk_pct,
        "max_open_risk_pct": config.max_open_risk_pct,
        "max_notional_multiple": config.max_notional_multiple,
        "max_positions_per_tenant": config.max_positions_per_tenant,
        "max_daily_trades": config.max_daily_trades,
    }))
    return await get_risk_settings(context)


@app.get("/system/settings", tags=["system settings"])
async def get_system_settings(request: Request) -> dict:
    tenant_id = getattr(request.state, "tenant_id", settings.tenant_id)
    vault = _get_vault()

    async with app.state.database.sessions() as session:
        record = await session.get(SystemSettingsRecord, tenant_id)

    # AI settings resolution
    ai_provider = record.ai_provider if record else "openai"
    ai_model = record.ai_model if record else "gpt-4o"
    ai_temperature = record.ai_temperature if record else 0.2
    ai_system_prompt = record.ai_system_prompt if record else ""

    raw_ai_key = ""
    if record and record.encrypted_ai_api_key and vault:
        try:
            raw_ai_key = vault.decrypt(record.encrypted_ai_api_key).get("key", "")
        except Exception:
            pass
    if not raw_ai_key:
        if ai_provider == "anthropic" and settings.anthropic_api_key:
            raw_ai_key = settings.anthropic_api_key
        elif ai_provider == "gemini" and settings.gemini_api_key:
            raw_ai_key = settings.gemini_api_key
        elif settings.openai_api_key:
            raw_ai_key = settings.openai_api_key

    # Telegram settings resolution
    raw_telegram_token = ""
    if record and record.encrypted_telegram_bot_token and vault:
        try:
            raw_telegram_token = vault.decrypt(record.encrypted_telegram_bot_token).get("token", "")
        except Exception:
            pass
    if not raw_telegram_token and settings.telegram_bot_token:
        raw_telegram_token = settings.telegram_bot_token

    telegram_enabled = record.telegram_enabled if record else settings.telegram_enabled
    telegram_chat_id = record.telegram_chat_id if (record and record.telegram_chat_id is not None) else settings.telegram_chat_id
    telegram_allowed_user_ids = record.telegram_allowed_user_ids if (record and record.telegram_allowed_user_ids is not None) else settings.telegram_allowed_user_ids
    telegram_timezone = record.telegram_timezone if record else settings.telegram_timezone

    # Risk & Engine settings resolution
    execution_mode = record.execution_mode if record else settings.execution_mode
    max_daily_drawdown_pct = record.max_daily_drawdown_pct if record else settings.engine_max_daily_drawdown_pct
    news_blackout_minutes = record.news_blackout_minutes if record else settings.engine_news_blackout_minutes
    paper_account_equity = record.paper_account_equity if record else settings.paper_account_equity
    account_currency = (getattr(record, "account_currency", None) if record else None) or settings.account_currency
    max_trade_risk_pct = (getattr(record, "max_trade_risk_pct", None) if record else None) or settings.max_trade_risk_pct
    max_open_risk_pct = (getattr(record, "max_open_risk_pct", None) if record else None) or settings.max_open_risk_pct
    max_notional_multiple = (getattr(record, "max_notional_multiple", None) if record else None) or settings.max_notional_multiple
    max_positions_per_tenant = (getattr(record, "max_positions_per_tenant", None) if record else None) or settings.max_positions_per_tenant
    max_daily_trades = (getattr(record, "max_daily_trades", None) if record else None) or settings.max_daily_trades
    proposal_ttl_seconds = (getattr(record, "proposal_ttl_seconds", None) if record else None) or settings.proposal_ttl_seconds

    return {
        "tenant_id": tenant_id,
        "ai": {
            "provider": ai_provider,
            "model": ai_model,
            "configured": bool(raw_ai_key),
            "masked_key": mask_secret(raw_ai_key),
            "temperature": ai_temperature,
            "system_prompt": ai_system_prompt,
        },
        "telegram": {
            "enabled": telegram_enabled,
            "configured": bool(raw_telegram_token),
            "masked_token": mask_secret(raw_telegram_token),
            "chat_id": telegram_chat_id,
            "allowed_user_ids": telegram_allowed_user_ids,
            "timezone": telegram_timezone,
        },
        "risk": {
            "execution_mode": execution_mode,
            "max_daily_drawdown_pct": max_daily_drawdown_pct,
            "news_blackout_minutes": news_blackout_minutes,
            "paper_account_equity": paper_account_equity,
            "account_currency": account_currency,
            "max_trade_risk_pct": max_trade_risk_pct,
            "max_open_risk_pct": max_open_risk_pct,
            "max_notional_multiple": max_notional_multiple,
            "max_positions_per_tenant": max_positions_per_tenant,
            "max_daily_trades": max_daily_trades,
            "proposal_ttl_seconds": proposal_ttl_seconds,
        },
        "updated_at": record.updated_at.isoformat() if record else None,
    }


@app.put("/system/settings", tags=["system settings"])
async def update_system_settings(request: Request, payload: SystemSettingsUpdateRequest) -> dict:
    tenant_id = getattr(request.state, "tenant_id", settings.tenant_id)
    user_id = getattr(request.state, "user_id", "unknown")
    vault = _get_vault()
    if abs(payload.paper_account_equity - app.state.strategy_runtime.paper_equity) > 1e-9:
        async with app.state.database.sessions() as session:
            open_paper = await session.scalar(select(func.count(TradeRecord.id)).where(
                TradeRecord.tenant_id == tenant_id,
                TradeRecord.mode == "paper",
                TradeRecord.status.in_(["open", "proposed", "submitting"]),
            ))
        if open_paper:
            raise HTTPException(
                status_code=409,
                detail="Close all paper positions before changing the paper account equity",
            )

    async with app.state.database.sessions() as session:
        record = await session.get(SystemSettingsRecord, tenant_id)
        if record is None:
            record = SystemSettingsRecord(tenant_id=tenant_id)
            session.add(record)

        record.ai_provider = payload.ai_provider
        record.ai_model = payload.ai_model
        record.ai_temperature = payload.ai_temperature
        record.ai_system_prompt = payload.ai_system_prompt

        # Encrypt AI key if a new one was provided (not masked)
        if payload.ai_api_key == "CLEAR":
            record.encrypted_ai_api_key = None
        elif payload.ai_api_key and "..." not in payload.ai_api_key and not payload.ai_api_key.startswith("****"):
            if not vault:
                raise HTTPException(status_code=500, detail="Encryption key (BROKER_CREDENTIALS_KEY) is not configured")
            record.encrypted_ai_api_key = vault.encrypt({"key": payload.ai_api_key.strip()})

        record.telegram_enabled = payload.telegram_enabled
        record.telegram_chat_id = payload.telegram_chat_id.strip() if payload.telegram_chat_id else ""
        record.telegram_allowed_user_ids = payload.telegram_allowed_user_ids.strip() if payload.telegram_allowed_user_ids else ""
        record.telegram_timezone = payload.telegram_timezone.strip() if payload.telegram_timezone else "UTC"

        # Encrypt Telegram bot token if a new one was provided (not masked)
        if payload.telegram_bot_token == "CLEAR":
            record.encrypted_telegram_bot_token = None
        elif payload.telegram_bot_token and "..." not in payload.telegram_bot_token and not payload.telegram_bot_token.startswith("****"):
            if not vault:
                raise HTTPException(status_code=500, detail="Encryption key (BROKER_CREDENTIALS_KEY) is not configured")
            record.encrypted_telegram_bot_token = vault.encrypt({"token": payload.telegram_bot_token.strip()})

        record.execution_mode = payload.execution_mode
        record.max_daily_drawdown_pct = payload.max_daily_drawdown_pct
        record.news_blackout_minutes = payload.news_blackout_minutes
        record.paper_account_equity = payload.paper_account_equity
        if payload.account_currency is not None:
            record.account_currency = payload.account_currency.strip().upper()
        if payload.max_trade_risk_pct is not None:
            record.max_trade_risk_pct = payload.max_trade_risk_pct
        if payload.max_open_risk_pct is not None:
            record.max_open_risk_pct = payload.max_open_risk_pct
        if payload.max_notional_multiple is not None:
            record.max_notional_multiple = payload.max_notional_multiple
        if payload.max_positions_per_tenant is not None:
            record.max_positions_per_tenant = payload.max_positions_per_tenant
        if payload.max_daily_trades is not None:
            record.max_daily_trades = payload.max_daily_trades
        if payload.proposal_ttl_seconds is not None:
            record.proposal_ttl_seconds = payload.proposal_ttl_seconds

        session.add(AuditRecord(
            tenant_id=tenant_id,
            actor=user_id,
            action="system_settings_updated",
            entity_id=tenant_id,
            details={
                "ai_provider": payload.ai_provider,
                "ai_model": payload.ai_model,
                "telegram_enabled": payload.telegram_enabled,
                "execution_mode": payload.execution_mode,
                "max_daily_drawdown_pct": payload.max_daily_drawdown_pct,
            }
        ))
        await session.commit()

    # Apply the durable settings to the live runtime immediately.  This is the
    # single source of truth for execution mode, paper equity, risk limits and
    # Telegram; the environment only supplies startup defaults.
    try:
        config = await load_runtime_config(app.state.database, tenant_id)
        app.state.runtime_config = config
        apply_runtime_config(app.state.strategy_runtime, config)
        await app.state.cache.redis.set(
            f"risk:settings:{tenant_id}",
            json.dumps({
                "max_daily_drawdown_pct": config.max_daily_drawdown_pct,
                "news_blackout_minutes": config.news_blackout_minutes,
                "max_trade_risk_pct": config.max_trade_risk_pct,
                "max_open_risk_pct": config.max_open_risk_pct,
                "max_notional_multiple": config.max_notional_multiple,
                "max_positions_per_tenant": config.max_positions_per_tenant,
                "max_daily_trades": config.max_daily_trades,
            }),
        )
        await app.state.telegram.apply_config(
            enabled=config.telegram_enabled,
            bot_token=config.telegram_bot_token,
            chat_id=config.telegram_chat_id,
            allowed_user_ids=config.telegram_allowed_user_ids,
            timezone_name=config.telegram_timezone,
        )
    except Exception as exc:
        logger.exception("Could not apply updated system settings: %s", exc)

    return await get_system_settings(request)


@app.post("/system/settings/test-telegram", tags=["system settings"])
async def test_telegram(request: Request, payload: TestTelegramRequest) -> dict:
    tenant_id = getattr(request.state, "tenant_id", settings.tenant_id)
    vault = _get_vault()
    bot_token = payload.bot_token
    chat_id = payload.chat_id

    # Resolve token if not explicitly provided or if masked
    if not bot_token or "..." in bot_token or bot_token.startswith("****"):
        async with app.state.database.sessions() as session:
            record = await session.get(SystemSettingsRecord, tenant_id)
            if record and record.encrypted_telegram_bot_token and vault:
                try:
                    bot_token = vault.decrypt(record.encrypted_telegram_bot_token).get("token")
                except Exception:
                    pass
        if not bot_token:
            bot_token = settings.telegram_bot_token

    # Resolve chat_id if not provided
    if not chat_id:
        async with app.state.database.sessions() as session:
            record = await session.get(SystemSettingsRecord, tenant_id)
            if record and record.telegram_chat_id:
                chat_id = record.telegram_chat_id
        if not chat_id:
            chat_id = settings.telegram_chat_id

    if not bot_token:
        raise HTTPException(status_code=400, detail="Telegram Bot Token is not configured or provided")
    if not chat_id:
        raise HTTPException(status_code=400, detail="Telegram Chat ID is not configured or provided")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": "🤖 <b>Trading Platform Integration Test</b>\n\nYour Telegram bot notifications are properly configured and operational!",
                    "parse_mode": "HTML",
                }
            )
            data = resp.json()
            if resp.status_code != 200 or not data.get("ok"):
                return {
                    "success": False,
                    "error": data.get("description", f"Telegram API returned HTTP {resp.status_code}")
                }
            return {"success": True, "message": "Test notification delivered to Telegram successfully!"}
    except Exception as exc:
        return {"success": False, "error": f"Connection failed: {str(exc)}"}


@app.post("/system/settings/test-ai", tags=["system settings"])
async def test_ai(request: Request, payload: TestAIRequest) -> dict:
    tenant_id = getattr(request.state, "tenant_id", settings.tenant_id)
    vault = _get_vault()
    api_key = payload.api_key
    provider = payload.provider.lower().strip()

    # Resolve api_key if masked or missing
    if not api_key or "..." in api_key or api_key.startswith("****"):
        async with app.state.database.sessions() as session:
            record = await session.get(SystemSettingsRecord, tenant_id)
            if record and record.encrypted_ai_api_key and vault:
                try:
                    api_key = vault.decrypt(record.encrypted_ai_api_key).get("key")
                except Exception:
                    pass
        if not api_key:
            if provider == "anthropic":
                api_key = settings.anthropic_api_key
            elif provider == "gemini":
                api_key = settings.gemini_api_key
            else:
                api_key = settings.openai_api_key

    if not api_key and provider != "ollama":
        raise HTTPException(status_code=400, detail=f"API key for {provider.capitalize()} is not provided or configured")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            if provider == "openai":
                resp = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {api_key}"}
                )
                if resp.status_code == 200:
                    return {"success": True, "message": "Successfully verified OpenAI API key connection!"}
                data = resp.json()
                return {"success": False, "error": data.get("error", {}).get("message", f"OpenAI error {resp.status_code}")}

            elif provider == "anthropic":
                resp = await client.get(
                    "https://api.anthropic.com/v1/models",
                    headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"}
                )
                if resp.status_code == 200:
                    return {"success": True, "message": "Successfully verified Anthropic API key connection!"}
                data = resp.json()
                return {"success": False, "error": data.get("error", {}).get("message", f"Anthropic error {resp.status_code}")}

            elif provider == "gemini":
                resp = await client.get(
                    f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
                )
                if resp.status_code == 200:
                    return {"success": True, "message": "Successfully verified Google Gemini API key connection!"}
                data = resp.json()
                return {"success": False, "error": data.get("error", {}).get("message", f"Gemini error {resp.status_code}")}

            elif provider == "ollama":
                host = api_key if (api_key and api_key.startswith("http")) else "http://localhost:11434"
                resp = await client.get(f"{host.rstrip('/')}/api/tags")
                if resp.status_code == 200:
                    return {"success": True, "message": f"Successfully connected to Ollama at {host}!"}
                return {"success": False, "error": f"Ollama returned HTTP {resp.status_code}"}

            else:
                return {"success": False, "error": f"Unsupported provider: {provider}"}
    except Exception as exc:
        return {"success": False, "error": f"Network error during verification: {str(exc)}"}


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    db_ok = await app.state.database.ping()
    redis_ok = await app.state.cache.ping()
    if not db_ok:
        raise HTTPException(status_code=503, detail="Postgres is unavailable")
    if not redis_ok:
        raise HTTPException(status_code=503, detail="Redis is unavailable")
    return {"status": "ok", "postgres": "ok", "redis": "ok"}


@app.get("/health/ready", tags=["health"])
async def readiness() -> dict[str, str]:
    """Dependency and notification-worker health for an external uptime monitor."""
    db_ok = await app.state.database.ping()
    redis_ok = await app.state.cache.ping()
    worker = None
    if redis_ok:
        worker = await app.state.cache.redis.get("notifications:worker:heartbeat")
    if isinstance(worker, bytes):
        worker = worker.decode("ascii", errors="ignore")
    worker_ok = worker in {"push-ready", "telegram-only"}
    components = {
        "postgres": "ok" if db_ok else "unavailable",
        "redis": "ok" if redis_ok else "unavailable",
        "notification_worker": "ok" if worker_ok else "unavailable",
    }
    if not db_ok or not redis_ok or not worker_ok:
        raise HTTPException(status_code=503, detail={"status": "degraded", "components": components})
    return {"status": "ok", **components}


@app.get("/", tags=["health"])
async def root() -> dict[str, str]:
    return {"service": "trading-platform-api", "docs": "/docs"}


@app.get("/connectors/status", tags=["connectors"])
async def connector_status() -> dict:
    crypto = getattr(app.state, "crypto", None)
    mt5 = app.state.mt5
    return {
        "crypto": {
            "exchange": settings.crypto_exchange.lower(),
            "rest": "connected" if crypto is not None else "unavailable",
            "websocket": bool(crypto and crypto.websocket_available),
            "error": getattr(app.state, "crypto_error", None),
            "sandbox": settings.crypto_sandbox,
        },
        "mt5": {"state": mt5.state, "detail": mt5.detail},
        "telegram": {"state": app.state.telegram.state, "error": getattr(app.state, "telegram_error", None)},
        "news_calendar": app.state.calendar.status(),
    }


async def _mt5_agent_for_request(request: Request) -> MT5AgentRecord:
    header = request.headers.get("authorization", "")
    token = header.removeprefix("Bearer ") if header.startswith("Bearer ") else ""
    if not token or len(token) > 256:
        raise HTTPException(status_code=401, detail="Valid MT5 agent bearer token required")
    async with app.state.database.sessions() as session:
        agent = await session.scalar(select(MT5AgentRecord).where(
            MT5AgentRecord.token_hash == token_digest(token),
        ))
        if agent is None:
            raise HTTPException(status_code=401, detail="Invalid MT5 agent token")
        return agent


@app.post("/mt5/agents", status_code=201, tags=["MT5 agents"])
async def create_mt5_agent(payload: MT5AgentCreateRequest, request: Request) -> dict:
    token, digest = make_agent_token()
    account_id, agent_id = str(uuid4()), str(uuid4())
    async with app.state.database.sessions() as session:
        account = BrokerAccountRecord(
            id=account_id, tenant_id=request.state.tenant_id, name=payload.name,
            broker="mt5", encrypted_credentials="",
            configuration={"transport": "windows_agent", "agent_id": agent_id, "demo_only": True},
            enabled=False,
        )
        agent = MT5AgentRecord(
            id=agent_id, tenant_id=request.state.tenant_id, broker_account_id=account_id,
            name=payload.name, token_hash=digest, account_metadata={}, allow_live_orders=False,
        )
        session.add_all([account, agent])
        session.add(AuditRecord(tenant_id=request.state.tenant_id, actor=request.state.user_id,
                                action="mt5_agent_created", entity_id=agent_id,
                                details={"broker_account_id": account_id, "live_orders_allowed": False}))
        await session.commit()
    return {"id": agent_id, "broker_account_id": account_id, "name": payload.name,
            "token": token, "account_enabled": False,
            "setup": "Install the Windows agent on the MT5 terminal computer and provide this token once."}


@app.get("/mt5/agents", tags=["MT5 agents"])
async def list_mt5_agents(request: Request) -> list[dict]:
    async with app.state.database.sessions() as session:
        rows = await session.execute(select(MT5AgentRecord, BrokerAccountRecord.enabled).join(
            BrokerAccountRecord, BrokerAccountRecord.id == MT5AgentRecord.broker_account_id,
        ).where(MT5AgentRecord.tenant_id == request.state.tenant_id)
          .order_by(desc(MT5AgentRecord.created_at)))
        return [{"id": agent.id, "broker_account_id": agent.broker_account_id, "name": agent.name,
                 "online": agent_is_online(agent), "last_seen_at": agent.last_seen_at.isoformat() if agent.last_seen_at else None,
                 "trade_mode": agent.trade_mode, "account_currency": agent.account_currency,
                 "account_equity": agent.account_equity, "terminal_version": agent.terminal_version,
                 "agent_version": agent.agent_version, "account_enabled": enabled,
                 "allow_live_orders": agent.allow_live_orders} for agent, enabled in rows]


@app.post("/mt5/agent/heartbeat", tags=["MT5 agent protocol"])
async def mt5_agent_heartbeat(payload: MT5AgentHeartbeatRequest, request: Request) -> dict:
    agent = await _mt5_agent_for_request(request)
    account = payload.account
    mode = str(account.get("trade_mode", "")).lower()
    equity = float(account.get("equity", 0) or 0)
    if mode not in {"demo", "contest", "real", "unknown"} or equity < 0 or equity > 1e15:
        raise HTTPException(status_code=422, detail="Invalid MT5 account status")
    async with app.state.database.sessions() as session:
        current = await session.get(MT5AgentRecord, agent.id)
        current.last_seen_at = datetime.now(timezone.utc)
        current.trade_mode = mode
        current.account_currency = str(account.get("currency", ""))[:8]
        current.account_equity = equity
        current.terminal_version = str(account.get("terminal_version", ""))[:64]
        current.agent_version = payload.agent_version
        current.account_metadata = {key: account.get(key) for key in
            ("login", "server", "company", "trade_allowed", "trade_expert") if key in account}
        await session.commit()
    return {"ok": True, "allow_live_orders": bool(agent.allow_live_orders)}


@app.get("/mt5/agent/subscriptions", tags=["MT5 agent protocol"])
async def mt5_agent_subscriptions(request: Request) -> dict:
    agent = await _mt5_agent_for_request(request)
    async with app.state.database.sessions() as session:
        strategies = await session.scalars(select(StrategyRecord).where(
            StrategyRecord.tenant_id == agent.tenant_id,
            StrategyRecord.broker_account_id == agent.broker_account_id,
            StrategyRecord.enabled.is_(True),
        ))
        output: dict[tuple[str, str], None] = {}
        for strategy in strategies:
            try:
                rule = StrategyRule.model_validate(strategy.rule)
                output[(rule.symbol, rule.timeframe)] = None
            except Exception:
                logger.exception("Invalid rule in MT5 subscription for strategy %s", strategy.id)
    return {"subscriptions": [{"symbol": symbol, "timeframe": timeframe} for symbol, timeframe in output]}


@app.post("/mt5/agent/market-data", tags=["MT5 agent protocol"])
async def mt5_agent_market_data(payload: MT5AgentMarketDataRequest, request: Request) -> dict:
    agent = await _mt5_agent_for_request(request)
    # MarketEvent drives the same strategy runtime and account routing as other feed connectors.
    candles = payload.candles
    if any(not all(key in candle for key in ("timestamp", "open", "high", "low", "close", "volume"))
           for candle in candles):
        raise HTTPException(status_code=422, detail="Candle data is incomplete")
    if payload.kind == "history":
        body = {"candles": candles}
    else:
        if len(candles) != 1:
            raise HTTPException(status_code=422, detail="A candle event must contain exactly one candle")
        body = candles[0]
    await app.state.strategy_runtime.on_event(MarketEvent(
        payload.kind, "mt5", payload.symbol, body, payload.timeframe,
    ))
    return {"accepted": len(candles)}


@app.post("/mt5/agent/commands/claim", tags=["MT5 agent protocol"])
async def mt5_agent_claim_command(request: Request) -> dict | None:
    agent = await _mt5_agent_for_request(request)
    async with app.state.database.sessions() as session:
        now = datetime.now(timezone.utc)
        command = await session.scalar(select(MT5AgentCommandRecord).where(
            MT5AgentCommandRecord.agent_id == agent.id,
            MT5AgentCommandRecord.status == "queued",
            MT5AgentCommandRecord.expires_at > now,
        ).order_by(MT5AgentCommandRecord.created_at).with_for_update(skip_locked=True).limit(1))
        if command is None:
            await session.execute(update(MT5AgentCommandRecord).where(
                MT5AgentCommandRecord.agent_id == agent.id,
                MT5AgentCommandRecord.status == "queued",
                MT5AgentCommandRecord.expires_at <= now,
            ).values(status="failed", error="Command expired before it was claimed", finished_at=now))
            await session.commit()
            return None
        if command.command_type == "execute_order" and (
            not settings.live_trading_enabled or settings.engine_paper_mode
            or await app.state.cache.is_kill_switch_active(agent.tenant_id)
        ):
            command.status = "failed"
            command.error = "Trading execution is disabled by server safety settings"
            command.finished_at = now
            await session.commit()
            return None
        command.status = "leased"
        command.attempts += 1
        command.lease_until = now + timedelta(seconds=30)
        await session.commit()
        return {"id": command.id, "type": command.command_type, "payload": command.payload,
                "allow_live_orders": bool(agent.allow_live_orders),
                "lease_until": command.lease_until.isoformat()}


@app.post("/mt5/agent/commands/{command_id}/result", tags=["MT5 agent protocol"])
async def mt5_agent_command_result(command_id: str, payload: MT5AgentResultRequest, request: Request) -> dict:
    agent = await _mt5_agent_for_request(request)
    async with app.state.database.sessions() as session:
        command = await session.get(MT5AgentCommandRecord, command_id)
        if command is None or command.agent_id != agent.id:
            raise HTTPException(status_code=404, detail="Command not found")
        if command.status in {"completed", "failed", "unknown"}:
            if command.status == payload.status:
                return {"ok": True, "duplicate": True}
            # A worker can finish after its server-side wait timed out. Record
            # that late definitive result while keeping the trade intent itself
            # in unknown until normal reconciliation adopts broker state.
            if command.status == "unknown" and payload.status in {"completed", "failed"}:
                command.status = payload.status
                command.response = payload.response
                command.error = payload.error
                command.finished_at = datetime.now(timezone.utc)
                await session.commit()
                return {"ok": True, "late_result": True}
            raise HTTPException(status_code=409, detail="Command already has a final result")
        if command.status != "leased":
            raise HTTPException(status_code=409, detail="Command was not leased to this agent")
        command.status = payload.status
        command.response = payload.response
        command.error = payload.error
        command.finished_at = datetime.now(timezone.utc)
        await session.commit()
    return {"ok": True}


@app.post("/broker-accounts", status_code=201, tags=["broker accounts"])
async def create_broker_account(context: Request, request: BrokerAccountCreateRequest) -> dict:
    try:
        encrypted = CredentialVault().encrypt(request.credentials)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    account_id = str(uuid4())
    async with app.state.database.sessions() as session:
        record = BrokerAccountRecord(
            id=account_id, tenant_id=context.state.tenant_id, name=request.name, broker=request.broker,
            encrypted_credentials=encrypted, configuration=request.configuration, enabled=False,
        )
        session.add(record)
        session.add(AuditRecord(tenant_id=context.state.tenant_id, actor=context.state.user_id, action="broker_account_created",
                                entity_id=account_id, details={"broker": request.broker, "enabled": False}))
        await session.commit()
    return {"id": account_id, "name": request.name, "broker": request.broker, "enabled": False,
            "configuration": request.configuration, "credentials_stored_encrypted": True}


@app.get("/broker-accounts", tags=["broker accounts"])
async def list_broker_accounts(request: Request) -> list[dict]:
    async with app.state.database.sessions() as session:
        rows = await session.execute(select(BrokerAccountRecord).where(
            BrokerAccountRecord.tenant_id == request.state.tenant_id,
        ).order_by(desc(BrokerAccountRecord.created_at)).limit(200))
        return [{"id": row.id, "name": row.name, "broker": row.broker, "enabled": row.enabled,
                 "configuration": row.configuration, "created_at": row.created_at.isoformat()}
                for row in rows.scalars()]


@app.patch("/broker-accounts/{account_id}", tags=["broker accounts"])
async def set_broker_account_enabled(account_id: str, payload: BrokerAccountEnabledRequest, request: Request) -> dict:
    if payload.enabled and not settings.live_trading_enabled:
        raise HTTPException(status_code=503, detail="LIVE_TRADING_ENABLED is false")
    async with app.state.database.sessions() as session:
        account = await session.scalar(select(BrokerAccountRecord).where(
            BrokerAccountRecord.id == account_id,
            BrokerAccountRecord.tenant_id == request.state.tenant_id,
        ))
        if account is None:
            raise HTTPException(status_code=404, detail="Broker account not found")
        account.enabled = payload.enabled
        session.add(AuditRecord(tenant_id=request.state.tenant_id, actor=request.state.user_id,
                                action="broker_account_enabled_changed", entity_id=account_id,
                                details={"enabled": payload.enabled}))
        await session.commit()
        if payload.enabled:
            try:
                await app.state.live_positions.seed_account(account)
            except Exception as exc:
                account.enabled = False
                await session.commit()
                raise HTTPException(status_code=503, detail="Broker account equity could not be verified") from exc
        return {"id": account.id, "broker": account.broker, "enabled": account.enabled}


@app.delete("/broker-accounts/{account_id}", tags=["broker accounts"])
async def delete_broker_account(account_id: str, request: Request) -> dict:
    async with app.state.database.sessions() as session:
        account = await session.scalar(select(BrokerAccountRecord).where(
            BrokerAccountRecord.id == account_id,
            BrokerAccountRecord.tenant_id == request.state.tenant_id,
        ))
        if account is None:
            raise HTTPException(status_code=404, detail="Broker account not found")
        await session.delete(account)
        session.add(AuditRecord(
            tenant_id=request.state.tenant_id,
            actor=request.state.user_id,
            action="broker_account_deleted",
            entity_id=account_id,
            details={"broker": account.broker, "name": account.name}
        ))
        await session.commit()
        return {"status": "deleted", "id": account_id}


@app.post("/execution/orders", status_code=201, tags=["execution"])
async def submit_protected_order(payload: LiveOrderRequest, request: Request) -> dict:
    """Explicit, tenant-scoped live execution with durable idempotency.

    The live manager records an intent before contacting the broker and never
    retries an uncertain submission.  Send an ``Idempotency-Key`` header to make
    client retries safe; a key is generated when the header is absent.
    """
    if not settings.live_trading_enabled:
        raise HTTPException(status_code=503, detail="Live execution is disabled by LIVE_TRADING_ENABLED")
    if await app.state.cache.is_kill_switch_active(request.state.tenant_id):
        raise HTTPException(status_code=423, detail="Emergency kill-switch is active")
    if settings.news_calendar_required and not app.state.calendar.fresh:
        raise HTTPException(status_code=503, detail="Required high-impact news calendar is unavailable or stale")
    async with app.state.database.sessions() as session:
        account = await session.scalar(select(BrokerAccountRecord).where(
            BrokerAccountRecord.id == payload.account_id,
            BrokerAccountRecord.tenant_id == request.state.tenant_id,
            BrokerAccountRecord.enabled.is_(True),
        ))
        if account is None:
            raise HTTPException(status_code=404, detail="Enabled broker account not found")
        broker = account.broker
    idempotency_key = (
        request.headers.get("Idempotency-Key")
        or payload.client_order_id
        or ""
    ).strip()
    if len(idempotency_key) > 80:
        raise HTTPException(status_code=422, detail="Idempotency-Key must be at most 80 characters")
    if not idempotency_key:
        idempotency_key = f"manual-{uuid4()}"
    risk = app.state.strategy_runtime.risk_for(request.state.tenant_id)
    try:
        result = await app.state.live_positions.execute_strategy_order(
            tenant_id=request.state.tenant_id,
            account_id=payload.account_id,
            strategy_id="manual",
            symbol=payload.symbol,
            side=payload.side,
            risk_pct=payload.risk_pct,
            stop_distance=max(abs(payload.stop_loss - payload.take_profit), 1e-9),
            target_distance=max(abs(payload.take_profit - payload.stop_loss), 1e-9),
            stop_loss_price=payload.stop_loss,
            take_profit_price=payload.take_profit,
            risk_manager=risk,
            events=app.state.strategy_runtime.current_events(),
            feed_broker=None,
            calendar_fresh=app.state.calendar.fresh,
            fixed_quantity=payload.quantity,
            max_tiers=1,
            client_order_id=idempotency_key,
        )
    except RuntimeError as exc:
        message = str(exc)
        status_code = 502 if "unknown" in message.lower() else 422
        raise HTTPException(status_code=status_code, detail=message) from exc
    except Exception as exc:
        logger.exception("Manual protected order failed")
        raise HTTPException(
            status_code=502,
            detail=f"Broker execution failed or requires reconciliation: {type(exc).__name__}",
        ) from exc
    async with app.state.database.sessions() as session:
        session.add(AuditRecord(
            tenant_id=request.state.tenant_id, actor=request.state.user_id,
            action="live_order_submitted", entity_id=result["id"],
            details={"broker": broker, "symbol": payload.symbol, "side": payload.side,
                     "quantity": result.get("quantity"), "broker_order_id": result.get("order_id"),
                     "client_order_id": idempotency_key},
        ))
        await session.commit()
    return {"id": result["id"], **result, "mode": "live"}


@app.get("/engine/status", tags=["engine"])
async def engine_status(request: Request) -> dict:
    return {
        "enabled": settings.engine_enabled,
        "paper_mode": settings.engine_paper_mode,
        "engine": app.state.engine.status() if app.state.engine else {"running": False},
        "engines": [engine.status() for engine in app.state.engines],
        "engine_error": app.state.engine_error,
        "runtime": app.state.strategy_runtime.status(),
        "kill_switch": await app.state.cache.is_kill_switch_active(request.state.tenant_id),
    }


async def _call_ai_service(
    provider: str,
    preferred_model: str,
    api_key: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.2,
    response_json: bool = True,
) -> tuple[str, str]:
    """Robust AI call with candidate fallback for Gemini."""
    provider = provider.lower().strip()
    if provider == "gemini":
        preferred = preferred_model if preferred_model.startswith("gemini") else settings.ai_gemini_default_model
        models_to_try = [preferred]
        if settings.ai_gemini_default_model not in models_to_try:
            models_to_try.append(settings.ai_gemini_default_model)

        payload = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "temperature": float(temperature),
            },
        }
        if response_json:
            payload["generationConfig"]["responseMimeType"] = "application/json"

        last_err = ""
        async with httpx.AsyncClient(timeout=30.0) as client:
            for model_candidate in models_to_try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_candidate}:generateContent?key={api_key}"
                try:
                    resp = await client.post(url, json=payload, timeout=20.0)
                    if resp.status_code == 200:
                        data = resp.json()
                        candidates = data.get("candidates", [])
                        if (
                            candidates
                            and "content" in candidates[0]
                            and "parts" in candidates[0]["content"]
                            and candidates[0]["content"]["parts"]
                        ):
                            text = candidates[0]["content"]["parts"][0].get("text", "")
                            if text:
                                return text, model_candidate
                        last_err = f"{model_candidate} returned no content"
                    else:
                        err_msg = resp.text
                        try:
                            err_msg = resp.json().get("error", {}).get("message", err_msg)
                        except Exception:
                            pass
                        last_err = f"{model_candidate} HTTP {resp.status_code}: {err_msg}"
                except httpx.TimeoutException:
                    last_err = f"{model_candidate} request timed out"
                    continue
                except Exception as ex:
                    last_err = f"{model_candidate} error: {str(ex).strip() or type(ex).__name__}"
                    continue

        raise HTTPException(
            status_code=502,
            detail=f"Gemini API unavailable across candidate models. {last_err}",
        )

    elif provider == "anthropic":
        url = "https://api.anthropic.com/v1/messages"
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        payload = {
            "model": preferred_model if preferred_model.startswith("claude") else settings.ai_anthropic_default_model,
            "max_tokens": 1200,
            "temperature": float(temperature),
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code != 200:
                raise HTTPException(status_code=502, detail=f"Anthropic error ({resp.status_code}): {resp.text}")
            data = resp.json()
            return data["content"][0]["text"], preferred_model

    elif provider == "openai":
        url = "https://api.openai.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "model": preferred_model if preferred_model.startswith(("gpt", "o1", "o3", "o4")) else settings.ai_openai_default_model,
            "temperature": float(temperature),
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if response_json:
            payload["response_format"] = {"type": "json_object"}
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code != 200:
                raise HTTPException(status_code=502, detail=f"OpenAI error ({resp.status_code}): {resp.text}")
            data = resp.json()
            return data["choices"][0]["message"]["content"], preferred_model

    elif provider == "ollama":
        host = api_key if (api_key and api_key.startswith("http")) else "http://localhost:11434"
        url = f"{host.rstrip('/')}/api/generate"
        payload = {
            "model": preferred_model or "llama3.3",
            "system": system_prompt,
            "prompt": user_prompt,
            "stream": False,
        }
        if response_json:
            payload["format"] = "json"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code != 200:
                raise HTTPException(status_code=502, detail=f"Ollama error ({resp.status_code}): {resp.text}")
            return resp.json().get("response", "{}"), preferred_model

    else:
        raise HTTPException(status_code=400, detail=f"Unsupported AI provider: {provider}")


@app.post("/strategies/ai-generate", tags=["strategies"])
async def ai_generate_strategy(context: Request, request: AIGenerateStrategyRequest) -> dict:
    tenant_id = getattr(context.state, "tenant_id", settings.tenant_id)
    vault = _get_vault()

    async with app.state.database.sessions() as session:
        record = await session.get(SystemSettingsRecord, tenant_id)

    ai_provider = (record.ai_provider if record else "openai").lower()
    ai_model = record.ai_model if record else settings.ai_openai_default_model
    ai_temp = record.ai_temperature if record else 0.2
    ai_directives = record.ai_system_prompt if record else ""

    raw_ai_key = ""
    if record and record.encrypted_ai_api_key and vault:
        try:
            raw_ai_key = vault.decrypt(record.encrypted_ai_api_key).get("key", "")
        except Exception:
            pass
    if not raw_ai_key:
        if ai_provider == "anthropic" and settings.anthropic_api_key:
            raw_ai_key = settings.anthropic_api_key
        elif ai_provider == "gemini" and settings.gemini_api_key:
            raw_ai_key = settings.gemini_api_key
        elif settings.openai_api_key:
            raw_ai_key = settings.openai_api_key

    if not raw_ai_key and ai_provider != "ollama":
        raise HTTPException(
            status_code=400,
            detail=f"{ai_provider.upper()} API key is not configured. Please configure it under Settings > AI Model & API.",
        )

    active_sym = request.symbol or "BTC/USDT"
    active_tf = request.timeframe or "1m"

    system_instruction = (
        "You are an expert quantitative trading strategy architect. Your job is to translate the user's trading idea "
        "into a valid deterministic trading strategy rule that compiles without error on the platform's grammar engine.\n\n"
        "GRAMMAR SPECIFICATION:\n"
        "Format: [BUY|SELL] [SYMBOL] on [TIMEFRAME] when [CONDITIONS joined by 'and'] risk [RISK_PCT]% stop loss [SL] pips take profit [TP] pips\n\n"
        "ALLOWED INDICATORS IN CONDITIONS:\n"
        "1. RSI: RSI(period) < number OR RSI(period) > number (e.g. RSI(14) < 30, RSI(14) > 70)\n"
        "2. Moving Average Crosses: EMA(fast) crosses above EMA(slow) OR EMA(fast) crosses below EMA(slow) (e.g. EMA(9) crosses above EMA(21))\n"
        "3. Moving Average Threshold: SMA(period) > number OR SMA(period) < number (e.g. SMA(20) > 65000)\n"
        "4. Price Threshold: CLOSE > number OR CLOSE < number\n\n"
        "ALLOWED RISK RULES:\n"
        "- 'risk 0.5% stop loss 20 pips take profit 40 pips' (risk between 0.1% and 1.0%; anything above 1.0% is rejected)\n\n"
        f"Active symbol: {active_sym}. Active timeframe: {active_tf}.\n"
        f"User Custom Directives: {ai_directives or 'None'}\n\n"
        "You MUST respond ONLY with a raw JSON object in this exact schema (no markdown fences, no extra text):\n"
        "{\n"
        f'  "strategy_prompt": "BUY {active_sym} on {active_tf} when RSI(14) < 30 and EMA(9) crosses above EMA(21) risk 0.5% stop loss 20 pips take profit 40 pips",\n'
        '  "explanation": "Brief 1-2 sentence explanation of the market rationale and entry/exit signals",\n'
        '  "indicators": ["RSI(14)", "EMA(9)", "EMA(21)"],\n'
        '  "risk_summary": "Risk 0.5%, SL 20 pips, TP 40 pips (1:2 R:R)"\n'
        "}"
    )

    try:
        raw_text, used_model = await _call_ai_service(
            provider=ai_provider,
            preferred_model=ai_model,
            api_key=raw_ai_key,
            system_prompt=system_instruction,
            user_prompt=request.user_prompt,
            temperature=ai_temp,
            response_json=True,
        )

        clean_text = raw_text.strip()
        if clean_text.startswith("```"):
            clean_text = re.sub(r"^```(?:json)?\s*", "", clean_text)
            clean_text = re.sub(r"\s*```$", "", clean_text)

        parsed_json = json.loads(clean_text)
        strategy_prompt = parsed_json.get("strategy_prompt", "").strip()

        rule = parse_strategy_prompt(strategy_prompt)
        if rule.symbol.replace("/", "").replace("-", "").upper() != active_sym.replace("/", "").replace("-", "").upper():
            raise HTTPException(
                status_code=422,
                detail=f"AI returned symbol {rule.symbol}, but the active chart is {active_sym}. Please retry.",
            )
        if rule.timeframe != active_tf:
            raise HTTPException(
                status_code=422,
                detail=f"AI returned timeframe {rule.timeframe}, but the active chart is {active_tf}. Please retry.",
            )

        return {
            "success": True,
            "provider": ai_provider,
            "model": used_model,
            "strategy_prompt": strategy_prompt,
            "explanation": parsed_json.get("explanation", "Synthesized by AI Copilot"),
            "indicators": parsed_json.get("indicators", []),
            "risk_summary": parsed_json.get("risk_summary", ""),
            "rule": rule.model_dump(mode="json"),
        }

    except StrategyParseError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"AI generated prompt failed grammar validation: {str(exc)}. Try rephrasing your request.",
        ) from exc
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="AI returned invalid JSON formatting. Please retry.") from exc
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="AI provider request timed out. Please retry.")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("AI strategy generation failed")
        err_detail = str(exc).strip() or type(exc).__name__
        raise HTTPException(status_code=502, detail=f"AI Copilot generation failed: {err_detail}") from exc


@app.post("/copilot/chat", tags=["copilot"])
async def copilot_chat(context: Request, request: CopilotChatRequest) -> dict:
    tenant_id = getattr(context.state, "tenant_id", settings.tenant_id)
    vault = _get_vault()

    async with app.state.database.sessions() as session:
        record = await session.get(SystemSettingsRecord, tenant_id)
        trade_rows = await session.execute(
            select(TradeRecord)
            .where(TradeRecord.tenant_id == tenant_id)
            .order_by(desc(TradeRecord.created_at))
            .limit(10)
        )
        recent_trades = trade_rows.scalars().all()
        strat_count = await session.scalar(
            select(func.count(StrategyRecord.id)).where(StrategyRecord.tenant_id == tenant_id)
        )

    ai_provider = (record.ai_provider if record else "openai").lower()
    ai_model = record.ai_model if record else settings.ai_openai_default_model
    ai_temp = record.ai_temperature if record else 0.2

    raw_ai_key = ""
    if record and record.encrypted_ai_api_key and vault:
        try:
            raw_ai_key = vault.decrypt(record.encrypted_ai_api_key).get("key", "")
        except Exception:
            pass
    if not raw_ai_key:
        if ai_provider == "anthropic" and settings.anthropic_api_key:
            raw_ai_key = settings.anthropic_api_key
        elif ai_provider == "gemini" and settings.gemini_api_key:
            raw_ai_key = settings.gemini_api_key
        elif settings.openai_api_key:
            raw_ai_key = settings.openai_api_key

    if not raw_ai_key and ai_provider != "ollama":
        raise HTTPException(
            status_code=400,
            detail=f"{ai_provider.upper()} API key is not configured. Please configure it in Settings.",
        )

    kill_switch_active = await app.state.cache.is_kill_switch_active(tenant_id)
    active_sym = request.symbol or "BTC/USDT"
    active_tf = request.timeframe or "1m"
    current_page = request.current_page or "/"

    total_pnl = sum((t.pnl or 0) for t in recent_trades)
    open_trades_count = sum(1 for t in recent_trades if t.status in ["proposed", "open", "submitted"])

    system_instruction = (
        "You are the autonomous AI Trading Copilot for an institutional algorithmic trading platform.\n"
        "You help the trader monitor risk, analyze live markets, draft and backtest strategies, and command the terminal.\n\n"
        "LIVE SYSTEM SNAPSHOT:\n"
        f"- Active Terminal Screen: {current_page}\n"
        f"- Active Market: {active_sym} on {active_tf}\n"
        f"- Kill Switch Status: {'HALTED (Emergency Active)' if kill_switch_active else 'DISARMED (Normal Operation)'}\n"
        f"- Execution Mode: {'Paper Mode' if settings.engine_paper_mode else 'Live Broker Protected'}\n"
        f"- Recent Trades: {len(recent_trades)} recorded | Open/Pending: {open_trades_count} | Net P&L: ${total_pnl:.2f}\n"
        f"- Total Saved Strategies: {strat_count or 0}\n\n"
        "COMMAND CAPABILITIES & GRAMMAR ENGINE:\n"
        "1. DRAFT_STRATEGY: Formulate a deterministic strategy from the trader's idea.\n"
        "   Rule syntax format: [BUY|SELL] [SYMBOL] on [TIMEFRAME] when [CONDITIONS joined by 'and'] risk [RISK_PCT]% stop loss [SL] pips take profit [TP] pips\n"
        "   Allowed indicators: RSI(period) < number, EMA(fast) crosses above EMA(slow), SMA(period) > number, CLOSE > number.\n"
        "2. BACKTEST: If the client asks to backtest or test a strategy, provide action_type 'BACKTEST' with the valid strategy_prompt.\n"
        "3. PORTFOLIO: If the client asks for P&L, balance, or positions, summarize concisely and use action_type 'PORTFOLIO'.\n"
        "4. KILL_SWITCH: If the client wants to halt or stop all trading, provide action_type 'KILL_SWITCH'.\n"
        "5. NAVIGATE: If client asks to open/go to a page (e.g. /backtest, /trades, /strategies, /settings, /accounts), set action_type 'NAVIGATE'.\n"
        "6. GENERAL: For general market questions or analysis, set action_type 'NONE'.\n\n"
        "RESPONSE FORMAT:\n"
        "You MUST respond ONLY with a JSON object in this exact schema (no markdown fences, no extra text):\n"
        "{\n"
        "  \"reply\": \"Concise, professional markdown response. Use bolding and bullets where appropriate.\",\n"
        "  \"action_type\": \"STRATEGY_CARD\" | \"BACKTEST\" | \"PORTFOLIO\" | \"KILL_SWITCH\" | \"NAVIGATE\" | \"NONE\",\n"
        "  \"action_data\": {\n"
        "     \"strategy_prompt\": \"BUY BTC/USDT on 1m when ...\",\n"
        "     \"target_url\": \"/trades\",\n"
        "     \"symbol\": \"BTC/USDT\",\n"
        "     \"timeframe\": \"1m\"\n"
        "  }\n"
        "}"
    )

    user_content = request.message
    if request.history:
        history_snippets = "\n".join([f"{m.role.upper()}: {m.content}" for m in request.history[-4:]])
        user_content = f"CONVERSATION HISTORY:\n{history_snippets}\n\nLATEST TRADER COMMAND:\n{request.message}"

    preferred_model = ai_model
    requested_model = (request.model or "").strip()
    compatible_prefixes = {
        "openai": ("gpt", "o1", "o3", "o4"),
        "anthropic": ("claude",),
        "gemini": ("gemini",),
    }
    if requested_model and (
        ai_provider == "ollama"
        or requested_model.startswith(compatible_prefixes.get(ai_provider, ()))
    ):
        preferred_model = requested_model

    try:
        raw_text, used_model = await _call_ai_service(
            provider=ai_provider,
            preferred_model=preferred_model,
            api_key=raw_ai_key,
            system_prompt=system_instruction,
            user_prompt=user_content,
            temperature=ai_temp,
            response_json=True,
        )

        clean_text = raw_text.strip()
        if clean_text.startswith("```"):
            clean_text = re.sub(r"^```(?:json)?\s*", "", clean_text)
            clean_text = re.sub(r"\s*```$", "", clean_text)

        parsed = json.loads(clean_text)
        reply_value = parsed.get("reply", "Copilot command processed.")
        reply = reply_value if isinstance(reply_value, str) else "Copilot command processed."
        action_type = (parsed.get("action_type") or "NONE").upper()
        action_data_value = parsed.get("action_data")
        action_data = action_data_value if isinstance(action_data_value, dict) else {}

        # If action is BACKTEST, automatically execute historical backtest on current candles!
        # Auto-detect strategy formulation if action_type was missed or empty
        if action_type not in ("STRATEGY_CARD", "BACKTEST", "PORTFOLIO", "KILL_SWITCH", "NAVIGATE"):
            strategy_prompt = extract_copilot_strategy_prompt(reply)
            if strategy_prompt:
                action_type = "STRATEGY_CARD"
                action_data["strategy_prompt"] = strategy_prompt
                action_data["prompt"] = strategy_prompt

        if action_type == "BACKTEST" and action_data.get("strategy_prompt"):
            strat_p = action_data["strategy_prompt"].strip()
            action_data["prompt"] = strat_p
            try:
                rule = parse_strategy_prompt(strat_p)
                connector = await _crypto_connector(None)
                b_sym = action_data.get("symbol") or rule.symbol or active_sym
                b_tf = action_data.get("timeframe") or rule.timeframe or active_tf
                candles = await connector.candles(b_sym, b_tf, limit=300)
                if len(candles) >= 3:
                    bt_res = await asyncio.to_thread(run_backtest, rule, candles, 10_000.0, 5.0)
                    action_data["backtest_result"] = {
                        "trade_count": bt_res.get("trade_count", 0),
                        "win_rate_pct": bt_res.get("win_rate_pct", 0.0),
                        "profit_factor": bt_res.get("profit_factor"),
                        "max_drawdown_pct": bt_res.get("max_drawdown_pct", 0.0),
                        "total_pnl": bt_res.get("total_pnl", 0.0),
                        "total_return_pct": bt_res.get("total_return_pct", 0.0),
                    }
                    action_data["rule"] = rule.model_dump(mode="json")
            except Exception as e:
                action_data["backtest_error"] = str(e)

        elif action_type == "STRATEGY_CARD":
            strat_p = action_data.get("strategy_prompt") or action_data.get("prompt")
            if not strat_p:
                strat_p = extract_copilot_strategy_prompt(reply)
            if strat_p:
                action_data["strategy_prompt"] = strat_p
                action_data["prompt"] = strat_p
                try:
                    rule = parse_strategy_prompt(strat_p.strip())
                    action_data["rule"] = rule.model_dump(mode="json")
                except Exception as e:
                    action_data["rule_error"] = str(e)

        elif action_type == "PORTFOLIO":
            action_data["recent_trades"] = [
                {
                    "id": t.id,
                    "symbol": t.symbol,
                    "side": t.side,
                    "status": t.status,
                    "pnl": t.pnl,
                    "created_at": t.created_at.isoformat(),
                }
                for t in recent_trades[:5]
            ]
            action_data["total_pnl"] = total_pnl
            action_data["open_trades_count"] = open_trades_count

        elif action_type == "KILL_SWITCH":
            action_data["currently_active"] = kill_switch_active

        return {
            "success": True,
            "reply": reply,
            "action_type": action_type,
            "action_data": action_data,
            "model": used_model,
        }

    except HTTPException:
        raise
    except json.JSONDecodeError:
        return {
            "success": True,
            "reply": raw_text if "raw_text" in locals() else "Command executed.",
            "action_type": "NONE",
            "action_data": {},
            "model": ai_model,
        }
    except Exception as exc:
        err_msg = str(exc).strip() or type(exc).__name__
        raise HTTPException(status_code=500, detail=f"Copilot error: {err_msg}") from exc


@app.post("/strategies/compile", tags=["strategies"])
async def compile_strategy(request: CompileRequest) -> dict:
    try:
        rule = parse_strategy_prompt(request.prompt)
    except StrategyParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"deterministic": True, "live_execution": False, "rule": rule.model_dump(mode="json")}


@app.post("/strategies", status_code=201, tags=["strategies"])
async def create_strategy(context: Request, request: StrategyCreateRequest) -> dict:
    try:
        rule = parse_strategy_prompt(request.prompt)
    except StrategyParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not settings.engine_paper_mode and (not settings.live_trading_enabled or not request.broker_account_id):
        raise HTTPException(status_code=422, detail="Live strategies require live execution to be enabled and a broker account selected")
    if request.broker_account_id:
        async with app.state.database.sessions() as session:
            account = await session.scalar(select(BrokerAccountRecord).where(
                BrokerAccountRecord.id == request.broker_account_id,
                BrokerAccountRecord.tenant_id == context.state.tenant_id,
                BrokerAccountRecord.enabled.is_(True),
            ))
            if account is None:
                raise HTTPException(status_code=422, detail="Selected broker account is not enabled in this tenant")
    strategy_id = str(uuid4())
    record = StrategyRecord(
        id=strategy_id, tenant_id=context.state.tenant_id, name=request.name, prompt=request.prompt, rule=rule.model_dump(mode="json"),
        enabled=request.enabled, broker_account_id=request.broker_account_id,
    )
    async with app.state.database.sessions() as session:
        session.add(record)
        session.add(AuditRecord(tenant_id=context.state.tenant_id, actor=context.state.user_id, action="strategy_created", entity_id=strategy_id,
                                details={"enabled": request.enabled, "broker_account_id": request.broker_account_id,
                                         "mode": "paper" if settings.engine_paper_mode else "live"}))
        await session.commit()
    return {"id": strategy_id, "name": request.name, "enabled": request.enabled,
            "paper_only": settings.engine_paper_mode, "mode": "paper" if settings.engine_paper_mode else "live",
            "broker_account_id": request.broker_account_id, "rule": rule.model_dump(mode="json")}


@app.get("/strategies", tags=["strategies"])
async def list_strategies(request: Request) -> list[dict]:
    async with app.state.database.sessions() as session:
        rows = await session.execute(select(StrategyRecord).where(
            StrategyRecord.tenant_id == request.state.tenant_id,
        ).order_by(desc(StrategyRecord.created_at)).limit(200))
        return [{"id": item.id, "name": item.name, "prompt": item.prompt, "rule": item.rule,
                 "enabled": item.enabled, "broker_account_id": item.broker_account_id,
                 "created_at": item.created_at.isoformat()} for item in rows.scalars()]


@app.patch("/strategies/{strategy_id}", tags=["strategies"])
async def set_strategy_enabled(strategy_id: str, request: StrategyEnabledRequest, context: Request) -> dict:
    async with app.state.database.sessions() as session:
        record = await session.scalar(select(StrategyRecord).where(
            StrategyRecord.id == strategy_id, StrategyRecord.tenant_id == context.state.tenant_id,
        ))
        if record is None:
            raise HTTPException(status_code=404, detail="Strategy not found")
        record.enabled = request.enabled
        session.add(AuditRecord(tenant_id=context.state.tenant_id, actor=context.state.user_id, action="strategy_enabled_changed", entity_id=strategy_id,
                                details={"enabled": request.enabled, "paper_only": settings.engine_paper_mode,
                                         "broker_account_id": record.broker_account_id}))
        await session.commit()
    return {"id": strategy_id, "enabled": request.enabled, "paper_only": settings.engine_paper_mode,
            "mode": "paper" if settings.engine_paper_mode else "live"}


@app.delete("/strategies/{strategy_id}", tags=["strategies"])
async def delete_strategy(strategy_id: str, context: Request) -> dict:
    async with app.state.database.sessions() as session:
        record = await session.scalar(select(StrategyRecord).where(
            StrategyRecord.id == strategy_id, StrategyRecord.tenant_id == context.state.tenant_id,
        ))
        if record is None:
            raise HTTPException(status_code=404, detail="Strategy not found")
        await session.delete(record)
        session.add(AuditRecord(
            tenant_id=context.state.tenant_id,
            actor=context.state.user_id,
            action="strategy_deleted",
            entity_id=strategy_id,
            details={"name": record.name}
        ))
        await session.commit()
    return {"id": strategy_id, "deleted": True}


@app.post("/backtests", tags=["backtesting"])
async def backtest(request: BacktestRequest) -> dict:
    try:
        rule = parse_strategy_prompt(request.prompt)
        risk = app.state.strategy_runtime.risk_for(settings.tenant_id)
        return await asyncio.to_thread(
            run_backtest, rule, request.candles, request.starting_balance, request.fee_bps,
            spread_bps=request.spread_bps, slippage_bps=request.slippage_bps,
            funding_bps_per_day=request.funding_bps_per_day, max_leverage=request.max_leverage,
            account_currency=request.account_currency.upper(),
            max_trade_risk_pct=risk.max_trade_risk_pct, max_open_risk_pct=risk.max_open_risk_pct,
        )
    except (StrategyParseError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/backtests/walk-forward", tags=["backtesting"])
async def walk_forward_backtest(request: WalkForwardRequest) -> dict:
    try:
        rule = parse_strategy_prompt(request.prompt)
        return await asyncio.to_thread(
            run_walk_forward, rule, request.candles,
            folds=request.folds, starting_balance=request.starting_balance, fee_bps=request.fee_bps,
            spread_bps=request.spread_bps, slippage_bps=request.slippage_bps,
            funding_bps_per_day=request.funding_bps_per_day,
            account_currency=request.account_currency.upper(),
        )
    except (StrategyParseError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/indicators", tags=["market data"])
async def indicators(request: IndicatorRequest) -> dict:
    try:
        return {"indicators": calculate_indicators(request.candles)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/risk/assess", tags=["risk"])
async def assess_risk(request: TradeProposal, context: Request) -> dict:
    try:
        risk = app.state.strategy_runtime.risk_for(context.state.tenant_id)
        exposure = await load_exposure(
            app.state.database, context.state.tenant_id, "paper",
            app.state.strategy_runtime.account_currency,
        )
        assessment = risk.validate(
            request, kill_switch=await app.state.cache.is_kill_switch_active(context.state.tenant_id),
            events=app.state.strategy_runtime.current_events(),
            open_risk_amount=exposure.open_risk_amount,
            open_notional=exposure.open_notional,
            open_positions=exposure.open_positions,
            trades_today=exposure.trades_today,
        )
        if settings.news_calendar_required and not app.state.calendar.fresh:
            assessment["approved"] = False
            assessment["reasons"].append("Required high-impact news calendar is unavailable or stale")
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=f"Risk calendar configuration invalid: {exc}") from exc
    return {**assessment, "drawdown_halted": risk.halted}


@app.post("/paper/trades", status_code=201, tags=["paper trading"])
async def propose_paper_trade(request: PaperTradeRequest, context: Request) -> dict:
    exposure = await load_exposure(
        app.state.database, context.state.tenant_id, "paper",
        app.state.strategy_runtime.account_currency,
    )
    assessment = app.state.strategy_runtime.risk_for(context.state.tenant_id).validate(
        request, kill_switch=await app.state.cache.is_kill_switch_active(context.state.tenant_id),
        events=app.state.strategy_runtime.current_events(),
        open_risk_amount=exposure.open_risk_amount,
        open_notional=exposure.open_notional,
        open_positions=exposure.open_positions,
        trades_today=exposure.trades_today,
    )
    if settings.news_calendar_required and not app.state.calendar.fresh:
        assessment["approved"] = False
        assessment["reasons"].append("Required high-impact news calendar is unavailable or stale")
    if not assessment["approved"]:
        raise HTTPException(status_code=422, detail=assessment["reasons"])
    trade_id = str(uuid4())
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=app.state.strategy_runtime.proposal_ttl_seconds)
    trade = TradeRecord(
        id=trade_id, tenant_id=context.state.tenant_id, symbol=request.symbol, side=request.side.lower(),
        entry_price=request.entry_price, exit_price=None, quantity=assessment["quantity"],
        stop_loss=float(request.stop_loss), take_profit=float(request.take_profit), pnl=None,
        status="open" if app.state.strategy_runtime.execution_mode == "full_auto" else "proposed",
        mode="paper", client_order_id=f"tp-{trade_id}",
        expires_at=expires_at if app.state.strategy_runtime.execution_mode == "semi_auto" else None,
        metadata_json={
            "risk_amount": assessment["risk_amount"], "risk_pct": request.risk_pct,
            "notional": assessment["notional"], "contract_size": assessment["contract_size"],
            "quote_currency": assessment["quote_currency"],
            "conversion_rate": assessment["conversion_rate"],
            "quantity_unit": assessment["quantity_unit"],
            "account_currency": assessment["account_currency"],
            "per_trade_risk_pct": assessment["per_trade_risk_pct"],
            "client_order_id": f"tp-{trade_id}",
            "expires_at": expires_at.isoformat() if app.state.strategy_runtime.execution_mode == "semi_auto" else None,
            "warnings": assessment["warnings"],
        },
    )
    async with app.state.database.sessions() as session:
        session.add(trade)
        session.add(AuditRecord(tenant_id=context.state.tenant_id, actor=context.state.user_id, action="paper_trade_proposed", entity_id=trade_id,
                                details={"symbol": request.symbol, "side": request.side, "paper_only": True}))
        if app.state.strategy_runtime.execution_mode != "full_auto":
            await app.state.telegram.send_trade_alert({
                "id": trade_id, "symbol": request.symbol, "side": request.side,
                "entry_price": request.entry_price, "quantity": assessment["quantity"],
                "risk_amount": assessment["risk_amount"], "risk_pct": request.risk_pct,
                "stop_loss": request.stop_loss, "take_profit": request.take_profit,
                "quantity_unit": assessment["quantity_unit"], "tenant_id": context.state.tenant_id,
                "mode": "paper", "expires_at": expires_at.isoformat(),
            }, session=session)
        else:
            await app.state.telegram.send_event(
                tenant_id=context.state.tenant_id, kind="paper_trade_opened",
                title="Paper trade opened",
                body=f"{request.side.upper()} {request.symbol} was opened in paper mode.",
                url="/trades", dedupe_key=f"paper-trade-opened:{trade_id}", session=session,
            )
        await session.commit()
    if app.state.strategy_runtime.execution_mode == "full_auto":
        app.state.strategy_runtime.risk_for(context.state.tenant_id).record_trade_opened()
    return {"id": trade_id, "status": trade.status, "paper_only": True, **assessment}


@app.post("/paper/trades/{trade_id}/decision", tags=["paper trading"])
async def decide_paper_trade(trade_id: str, request: TradeDecisionRequest, context: Request) -> dict:
    changed = await app.state.strategy_runtime.approve_paper_trade(trade_id, request.approved, tenant_id=context.state.tenant_id)
    if not changed:
        raise HTTPException(status_code=404, detail="Pending paper trade not found")
    return {"id": trade_id, "status": "open" if request.approved else "skipped", "paper_only": True}


@app.get("/paper/trades", tags=["paper trading"])
async def list_paper_trades(request: Request, limit: int = Query(50, ge=1, le=500)) -> list[dict]:
    async with app.state.database.sessions() as session:
        rows = await session.execute(select(TradeRecord).where(
            TradeRecord.tenant_id == request.state.tenant_id,
        ).order_by(desc(TradeRecord.created_at)).limit(limit))
        return [{"id": row.id, "symbol": row.symbol, "side": row.side, "entry_price": row.entry_price,
                 "exit_price": row.exit_price, "quantity": row.quantity, "stop_loss": row.stop_loss,
                 "take_profit": row.take_profit, "pnl": row.pnl, "status": row.status, "mode": row.mode,
                 "created_at": row.created_at.isoformat()} for row in rows.scalars()]


@app.get("/analytics/equity", tags=["analytics"])
async def equity_curve(request: Request, limit: int = Query(1000, ge=1, le=5000)) -> dict:
    async with app.state.database.sessions() as session:
        total_count = await session.scalar(select(func.count(TradeRecord.id)).where(
            TradeRecord.tenant_id == request.state.tenant_id, TradeRecord.mode == "paper",
            TradeRecord.closed_at.is_not(None), TradeRecord.pnl.is_not(None),
        ))
        total_pnl = await session.scalar(select(func.coalesce(func.sum(TradeRecord.pnl), 0)).where(
            TradeRecord.tenant_id == request.state.tenant_id, TradeRecord.mode == "paper",
            TradeRecord.closed_at.is_not(None), TradeRecord.pnl.is_not(None),
        ))
        total_wins = await session.scalar(select(func.count(TradeRecord.id)).where(
            TradeRecord.tenant_id == request.state.tenant_id, TradeRecord.mode == "paper",
            TradeRecord.closed_at.is_not(None), TradeRecord.pnl > 0,
        ))
        gross_wins = await session.scalar(select(func.coalesce(func.sum(TradeRecord.pnl), 0)).where(
            TradeRecord.tenant_id == request.state.tenant_id, TradeRecord.mode == "paper",
            TradeRecord.closed_at.is_not(None), TradeRecord.pnl > 0,
        ))
        gross_losses = await session.scalar(select(func.coalesce(func.sum(TradeRecord.pnl), 0)).where(
            TradeRecord.tenant_id == request.state.tenant_id, TradeRecord.mode == "paper",
            TradeRecord.closed_at.is_not(None), TradeRecord.pnl < 0,
        ))
        rows = await session.execute(select(TradeRecord).where(
            TradeRecord.tenant_id == request.state.tenant_id, TradeRecord.mode == "paper",
            TradeRecord.closed_at.is_not(None), TradeRecord.pnl.is_not(None),
        ).order_by(desc(TradeRecord.closed_at)).limit(limit))
        closed = list(rows.scalars())
    closed.reverse()
    starting_equity = app.state.strategy_runtime.paper_equity
    selected_pnl = sum(float(trade.pnl or 0.0) for trade in closed)
    equity = starting_equity + float(total_pnl or 0) - selected_pnl
    points = [{"timestamp": None, "equity": equity, "pnl": 0.0}]
    for trade in closed:
        equity += float(trade.pnl or 0.0)
        points.append({"timestamp": trade.closed_at.isoformat(), "equity": equity, "pnl": float(trade.pnl or 0.0)})
    return {"starting_equity": starting_equity, "ending_equity": equity,
            "trade_count": int(total_count or 0),
            "win_rate_pct": float(total_wins or 0) / int(total_count or 1) * 100 if total_count else 0,
            "profit_factor": float(gross_wins or 0) / abs(float(gross_losses or 0))
            if gross_losses else ("infinity" if gross_wins else 0),
            "curve": points}


@app.get("/market/cache/tick", tags=["market data"])
async def cached_tick(
    exchange: str = Query("binance", min_length=2, max_length=20),
    symbol: str = Query(..., min_length=3, max_length=32),
) -> dict:
    tick = await app.state.cache.get_tick(exchange, symbol)
    if tick is None:
        raise HTTPException(status_code=404, detail="No cached tick; enable ENGINE_ENABLED and wait for a feed update")
    return tick


@app.get("/market/crypto/ticker", tags=["market data"])
async def crypto_ticker(
    symbol: str = Query(..., min_length=3, max_length=32, description="Unified CCXT symbol, e.g. BTC/USDT"),
    exchange: str | None = Query(None, description="binance, bybit, or okx; defaults to CRYPTO_EXCHANGE"),
) -> dict:
    connector = await _crypto_connector(exchange)
    try:
        return await connector.ticker(symbol)
    except Exception as exc:
        status, message = classify_ccxt_error(exc)
        raise HTTPException(status_code=status, detail=message) from exc


@app.get("/market/crypto/candles", tags=["market data"])
async def crypto_candles(
    symbol: str = Query(..., min_length=3, max_length=32),
    timeframe: str = Query("1m", pattern=r"^(1m|3m|5m|15m|30m|1h|2h|4h|6h|12h|1d|1w)$"),
    limit: int = Query(500, ge=1, le=1000),
    exchange: str | None = Query(None),
) -> dict:
    connector = await _crypto_connector(exchange)
    try:
        return {"exchange": connector.exchange_id, "symbol": symbol, "timeframe": timeframe,
                "candles": await connector.candles(symbol, timeframe, limit)}
    except Exception as exc:
        status, message = classify_ccxt_error(exc)
        raise HTTPException(status_code=status, detail=message) from exc


@app.get("/market/mt5/{symbol}/ticker", tags=["market data"])
async def mt5_ticker(symbol: str) -> dict:
    try:
        return await app.state.mt5.quote(symbol)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/market/mt5/{symbol}/candles", tags=["market data"])
async def mt5_candles(
    symbol: str,
    timeframe: str = Query("1m"),
    limit: int = Query(500, ge=1, le=5000),
) -> dict:
    try:
        candles = await app.state.mt5.candles(symbol, timeframe, limit)
        return {"broker": "mt5", "symbol": symbol, "timeframe": timeframe, "candles": candles}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.websocket("/ws/crypto/ticker")
async def crypto_ticker_stream(websocket: WebSocket) -> None:
    symbol = websocket.query_params.get("symbol", "")
    exchange = websocket.query_params.get("exchange")
    try:
        connector = await _crypto_connector(exchange)
    except HTTPException:
        await websocket.close(code=1008, reason="Unsupported or unavailable exchange")
        return
    if not symbol:
        await websocket.close(code=1008, reason="symbol query parameter is required")
        return
    await websocket.accept()
    try:
        while True:
            if connector.websocket_available:
                ticker = await connector.watch_ticker(symbol)
            else:
                ticker = await connector.ticker(symbol)
                await asyncio.sleep(2.5)
            await websocket.send_json(ticker)
    except WebSocketDisconnect:
        return
    except Exception:
        await websocket.close(code=1011, reason="Exchange WebSocket stream failed")


async def _crypto_connector(exchange: str | None) -> CryptoMarketData:
    selected = (exchange or settings.crypto_exchange).lower()
    if selected not in SUPPORTED_EXCHANGES:
        raise HTTPException(status_code=422, detail="Exchange must be binance, bybit, or okx.")
    current = getattr(app.state, "crypto", None)
    if current is not None and current.exchange_id == selected:
        return current
    cached = app.state.crypto_connectors.get(selected)
    if cached is not None:
        return cached
    try:
        use_credentials = selected == settings.crypto_exchange.lower()
        connector = CryptoMarketData(
            exchange_id=selected,
            api_key=settings.crypto_api_key if use_credentials else "",
            secret=settings.crypto_api_secret if use_credentials else "",
            password=settings.crypto_api_password if use_credentials else "",
            sandbox=settings.crypto_sandbox,
            default_type=settings.crypto_default_type,
        )
        app.state.crypto_connectors[selected] = connector
        return connector
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Exchange adapter could not be initialized.") from exc
