from contextlib import asynccontextmanager
from uuid import uuid4

import asyncio
import hashlib
import json
import logging
import secrets
from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from starlette.responses import JSONResponse

from app.connectors.crypto import CryptoMarketData, SUPPORTED_EXCHANGES, classify_ccxt_error
from app.core.auth import hash_password, issue_access_token, verify_access_token, verify_password
from app.core.security import CredentialVault
from app.connectors.mt5 import MT5Connector
from app.engine.runner import MarketEngine, MT5MarketEngine
from app.engine.live_positions import LivePositionManager
from app.engine.indicators import calculate_indicators
from app.engine.strategy_runtime import StrategyRuntime
from app.risk.controls import RiskManager, TradeProposal
from app.risk.news import EconomicCalendar
from app.settings import settings
from app.storage.cache import MarketCache
from app.storage.database import Database
from app.storage.models import AuditRecord, BrokerAccountRecord, StrategyRecord, TradeRecord, UserRecord
from app.strategies.backtest import run_backtest
from app.strategies.rules import StrategyParseError, parse_strategy_prompt
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


class BacktestRequest(CompileRequest):
    candles: list[dict] = Field(min_length=3, max_length=100_000)
    starting_balance: float = Field(default=10_000, gt=0)
    fee_bps: float = Field(default=5, ge=0, le=1_000)


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
    tenant_id: str = Field(min_length=1, max_length=36)


class UserCreateRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=12, max_length=1024)
    tenant_id: str = Field(min_length=1, max_length=36)
    is_platform_admin: bool = False


class BrokerAccountCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    broker: str = Field(pattern=r"^(binance|bybit|okx|mt5)$")
    credentials: dict[str, str]
    configuration: dict = Field(default_factory=dict)


class BrokerAccountEnabledRequest(BaseModel):
    enabled: bool


class LiveOrderRequest(BaseModel):
    account_id: str = Field(min_length=1, max_length=36)
    symbol: str = Field(min_length=3, max_length=40)
    side: str = Field(pattern=r"^(buy|sell)$")
    quantity: float = Field(gt=0)
    stop_loss: float = Field(gt=0)
    take_profit: float = Field(gt=0)
    risk_pct: float = Field(gt=0, le=1.0)


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
    try:
        await app.state.telegram.start()
    except Exception as exc:
        app.state.telegram.state = "error"
        app.state.telegram_error = str(exc)
    app.state.strategy_runtime = StrategyRuntime(app.state.database, app.state.cache, app.state.telegram)
    await app.state.strategy_runtime.initialize()
    app.state.live_positions = LivePositionManager(app.state.database, app.state.cache)
    await app.state.live_positions.reconcile_once()
    await app.state.live_positions.start()
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
        crypto_feeds: dict[tuple[str, str], set[str]] = {}
        if app.state.crypto is not None:
            crypto_feeds[(exchange_id, settings.crypto_default_type)] = set(settings.engine_symbol_list)
        mt5_symbols = set(settings.engine_mt5_symbol_list)
        async with app.state.database.sessions() as session:
            routes = await session.execute(
                select(StrategyRecord.rule, BrokerAccountRecord.broker)
                .join(BrokerAccountRecord, BrokerAccountRecord.id == StrategyRecord.broker_account_id)
                .where(StrategyRecord.enabled.is_(True), BrokerAccountRecord.enabled.is_(True))
            )
            for rule_data, broker in routes:
                symbol = str(rule_data.get("symbol", ""))
                if broker in SUPPORTED_EXCHANGES and symbol:
                    crypto_feeds.setdefault((broker, "swap"), set()).add(symbol)
                elif broker == "mt5" and symbol:
                    mt5_symbols.add(symbol.replace("/", ""))
        for (feed_exchange, feed_type), feed_symbols in crypto_feeds.items():
            try:
                connector = app.state.crypto if (
                    app.state.crypto is not None and feed_exchange == app.state.crypto.exchange_id
                    and feed_type == settings.crypto_default_type
                ) else None
                if connector is None:
                    connector = CryptoMarketData(
                        exchange_id=feed_exchange, sandbox=settings.crypto_sandbox, default_type=feed_type,
                    )
                    app.state.crypto_connectors[f"feed:{feed_exchange}:{feed_type}"] = connector
                crypto_engine = MarketEngine(
                    connector, app.state.cache, sorted(feed_symbols), settings.engine_timeframe,
                    on_event=app.state.strategy_runtime.on_event,
                )
                await crypto_engine.start()
                app.state.engines.append(crypto_engine)
            except Exception as exc:
                errors.append(f"{feed_exchange}/{feed_type}: {exc}")
        if app.state.mt5.state == "connected":
            try:
                mt5_engine = MT5MarketEngine(
                    app.state.mt5, app.state.cache, sorted(mt5_symbols), settings.engine_timeframe,
                    on_event=app.state.strategy_runtime.on_event,
                )
                await mt5_engine.start()
                app.state.engines.append(mt5_engine)
            except Exception as exc:
                errors.append(f"mt5: {exc}")
        if app.state.engines:
            app.state.engine = app.state.engines[0]
        else:
            app.state.engine_error = "; ".join(errors) or "No market-data adapter is available for the runner."
    try:
        yield
    finally:
        for engine in app.state.engines:
            await engine.stop()
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
    return {"id": getattr(request.state, "user_id", None),
            "tenant_id": getattr(request.state, "tenant_id", settings.tenant_id),
            "is_platform_admin": getattr(request.state, "is_platform_admin", False)}


@app.middleware("http")
async def require_control_token(request, call_next):
    """Require a deployment token for all operator and account data routes when configured."""
    public_paths = {"/", "/health", "/auth/session", "/auth/login", "/docs", "/openapi.json", "/redoc"}
    expected = settings.api_access_token
    if request.method == "OPTIONS" or request.url.path in public_paths:
        return await call_next(request)
    supplied = request.headers.get("authorization", "")
    token = supplied.removeprefix("Bearer ") if supplied.startswith("Bearer ") else ""
    if expected and token and secrets.compare_digest(token, expected):
        request.state.user_id = "deployment-admin"
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
                request.state.tenant_id = user.tenant_id
                request.state.is_platform_admin = user.is_platform_admin
                return await call_next(request)
        except (ValueError, KeyError, TypeError):
            pass
    if not settings.api_access_token and not settings.jwt_secret_key and settings.app_env.lower() != "production":
        request.state.user_id = "local-development"
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
        "execution_mode": settings.execution_mode,
        "paper_mode": settings.engine_paper_mode,
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
    return {"max_daily_drawdown_pct": risk.max_daily_drawdown_pct,
            "news_blackout_minutes": int(risk.news_blackout.total_seconds() // 60),
            "halted": risk.halted, "daily_drawdown_pct": risk.daily_drawdown_pct}


@app.put("/risk/settings", tags=["risk"])
async def set_risk_settings(request: RiskSettingsRequest, context: Request) -> dict:
    risk = app.state.strategy_runtime.risk_for(context.state.tenant_id)
    risk.configure(request.max_daily_drawdown_pct, request.news_blackout_minutes)
    await app.state.cache.redis.set(f"risk:settings:{context.state.tenant_id}", json.dumps(request.model_dump()))
    async with app.state.database.sessions() as session:
        session.add(AuditRecord(tenant_id=context.state.tenant_id, actor=context.state.user_id, action="risk_settings_updated", details=request.model_dump()))
        await session.commit()
    return await get_risk_settings(context)


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    try:
        await app.state.database.ping()
        redis_ok = await app.state.cache.ping()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="A required state service is unavailable") from exc
    if not redis_ok:
        raise HTTPException(status_code=503, detail="Redis is unavailable")
    return {"status": "ok", "postgres": "ok", "redis": "ok"}


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


@app.post("/execution/orders", status_code=201, tags=["execution"])
async def submit_protected_order(payload: LiveOrderRequest, request: Request) -> dict:
    """Explicit, tenant-scoped live execution. Disabled by default; requires venue-attached SL/TP."""
    if not settings.live_trading_enabled:
        raise HTTPException(status_code=503, detail="Live execution is disabled by LIVE_TRADING_ENABLED")
    if await app.state.cache.is_kill_switch_active(request.state.tenant_id):
        raise HTTPException(status_code=423, detail="Emergency kill-switch is active")
    async with app.state.database.sessions() as session:
        account = await session.scalar(select(BrokerAccountRecord).where(
            BrokerAccountRecord.id == payload.account_id,
            BrokerAccountRecord.tenant_id == request.state.tenant_id,
            BrokerAccountRecord.enabled.is_(True),
        ))
        if account is None:
            raise HTTPException(status_code=404, detail="Enabled broker account not found")
        account_id, broker = account.id, account.broker
        configuration = dict(account.configuration or {})
        try:
            credentials = CredentialVault().decrypt(account.encrypted_credentials)
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=503, detail="Broker credentials are unavailable") from exc

    connector = None
    try:
        if broker == "mt5":
            connector = MT5Connector(login=credentials.get("login", ""), password=credentials.get("password", ""),
                                     server=credentials.get("server", ""),
                                     terminal_path=configuration.get("terminal_path", ""))
            await connector.connect(enabled=True)
            existing_positions = await connector.positions_for_symbol(payload.symbol)
            if existing_positions:
                raise HTTPException(status_code=409, detail="A position already exists for this MT5 symbol; managed live entries require an isolated symbol")
            equity = await connector.account_equity()
            live_guard = await app.state.live_positions.guard_equity(
                request.state.tenant_id, account_id, equity,
                app.state.strategy_runtime.risk_for(request.state.tenant_id).max_daily_drawdown_pct,
            )
            if live_guard["tripped"]:
                raise HTTPException(status_code=423, detail="Live account daily drawdown limit reached")
            quote = await connector.quote(payload.symbol)
            entry = float(quote["ask"] if payload.side == "buy" else quote["bid"])
            proposal = TradeProposal(symbol=payload.symbol, side=payload.side, entry_price=entry,
                                     stop_loss=payload.stop_loss, take_profit=payload.take_profit,
                                     risk_pct=payload.risk_pct, account_equity=equity)
            risk = app.state.strategy_runtime.risk_for(request.state.tenant_id)
            assessment = risk.validate(proposal, kill_switch=False, events=app.state.strategy_runtime.current_events())
            if settings.news_calendar_required and not app.state.calendar.fresh:
                assessment["approved"] = False
                assessment["reasons"].append("Required high-impact news calendar is unavailable or stale")
            try:
                risk_per_lot = await connector.risk_per_lot(payload.symbol, entry, payload.stop_loss)
            except RuntimeError:
                risk_per_lot = 0
            if not assessment["approved"] or risk_per_lot <= 0:
                raise HTTPException(status_code=422, detail=assessment["reasons"] or ["Broker risk metadata unavailable"])
            actual_risk = risk_per_lot * payload.quantity
            if actual_risk > assessment["risk_amount"]:
                raise HTTPException(status_code=422, detail="Requested MT5 volume exceeds the configured risk budget")
            result = await connector.execute_protected_market_order(
                payload.symbol, payload.side, payload.quantity, payload.stop_loss, payload.take_profit,
            )
        else:
            if broker not in SUPPORTED_EXCHANGES:
                raise HTTPException(status_code=422, detail="Unsupported broker")
            connector = CryptoMarketData(
                exchange_id=broker, api_key=credentials.get("apiKey", ""),
                secret=credentials.get("secret", ""), password=credentials.get("password", ""),
                sandbox=bool(configuration.get("sandbox", True)),
                default_type=str(configuration.get("default_type", "spot")),
            )
            equity = await connector.account_equity(str(configuration.get("risk_currency", "USDT")))
            await connector.rest.load_markets()
            live_guard = await app.state.live_positions.guard_equity(
                request.state.tenant_id, account_id, equity,
                app.state.strategy_runtime.risk_for(request.state.tenant_id).max_daily_drawdown_pct,
            )
            if live_guard["tripped"]:
                raise HTTPException(status_code=423, detail="Live account daily drawdown limit reached")
            market_info = connector.rest.market(payload.symbol)
            risk_currency = str(configuration.get("risk_currency", "USDT")).upper()
            if market_info.get("linear") is not True or str(market_info.get("quote", "")).upper() != risk_currency:
                raise HTTPException(status_code=422, detail="Live risk sizing requires a linear contract quoted in the configured risk currency")
            existing_positions = await connector.positions_for_symbol(payload.symbol)
            if existing_positions:
                raise HTTPException(status_code=409, detail="A position already exists for this derivatives symbol; managed live entries require an isolated symbol")
            quote = await connector.quote(payload.symbol)
            market_price = quote.get("ask") if payload.side == "buy" else quote.get("bid")
            entry = float(market_price or quote.get("last") or 0)
            proposal = TradeProposal(symbol=payload.symbol, side=payload.side, entry_price=entry,
                                     stop_loss=payload.stop_loss, take_profit=payload.take_profit,
                                     risk_pct=payload.risk_pct, account_equity=equity)
            assessment = app.state.strategy_runtime.risk_for(request.state.tenant_id).validate(
                proposal, kill_switch=False, events=app.state.strategy_runtime.current_events(),
            )
            if settings.news_calendar_required and not app.state.calendar.fresh:
                assessment["approved"] = False
                assessment["reasons"].append("Required high-impact news calendar is unavailable or stale")
            contract_size = float(market_info.get("contractSize") or 1)
            estimated_risk = abs(entry - payload.stop_loss) * payload.quantity * contract_size
            if not assessment["approved"] or estimated_risk > assessment["risk_amount"]:
                raise HTTPException(status_code=422, detail=assessment["reasons"] or ["Requested amount exceeds the configured risk budget"])
            result = await connector.execute_protected_market_order(
                payload.symbol, payload.side, payload.quantity, payload.stop_loss, payload.take_profit,
            )
    except HTTPException:
        raise
    except Exception as exc:
        # Do not retry an uncertain order submission; reconcile it at the venue first.
        raise HTTPException(status_code=502, detail=f"Broker execution failed or requires reconciliation: {type(exc).__name__}") from exc
    finally:
        if connector is not None:
            try:
                await connector.close()
            except Exception:
                logger.exception("Broker connector cleanup failed after manual order")

    trade_id = str(uuid4())
    async with app.state.database.sessions() as session:
        trade = TradeRecord(
            id=trade_id, tenant_id=request.state.tenant_id, broker_account_id=account_id,
            symbol=payload.symbol, side=payload.side, entry_price=result["entry_price"],
            exit_price=None, quantity=result["quantity"], stop_loss=payload.stop_loss,
            take_profit=payload.take_profit, pnl=None, status="open", mode="live",
            metadata_json={"broker": broker, "broker_order_id": result["order_id"], "broker_status": result["status"],
                           "position_ticket": result.get("position_ticket"), "risk_pct": payload.risk_pct,
                           "position_side": payload.side},
        )
        session.add(trade)
        session.add(AuditRecord(tenant_id=request.state.tenant_id, actor=request.state.user_id,
                                action="live_order_submitted", entity_id=trade_id,
                                details={"broker": broker, "symbol": payload.symbol, "side": payload.side,
                                         "quantity": result["quantity"], "broker_order_id": result["order_id"]}))
        await session.commit()
    return {"id": trade_id, **result, "mode": "live"}


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


@app.post("/backtests", tags=["backtesting"])
async def backtest(request: BacktestRequest) -> dict:
    try:
        rule = parse_strategy_prompt(request.prompt)
        return await asyncio.to_thread(run_backtest, rule, request.candles, request.starting_balance, request.fee_bps)
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
        assessment = risk.validate(
            request, kill_switch=await app.state.cache.is_kill_switch_active(context.state.tenant_id),
            events=app.state.strategy_runtime.current_events(),
        )
        if settings.news_calendar_required and not app.state.calendar.fresh:
            assessment["approved"] = False
            assessment["reasons"].append("Required high-impact news calendar is unavailable or stale")
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=f"Risk calendar configuration invalid: {exc}") from exc
    return {**assessment, "drawdown_halted": risk.halted}


@app.post("/paper/trades", status_code=201, tags=["paper trading"])
async def propose_paper_trade(request: PaperTradeRequest, context: Request) -> dict:
    assessment = app.state.strategy_runtime.risk_for(context.state.tenant_id).validate(
        request, kill_switch=await app.state.cache.is_kill_switch_active(context.state.tenant_id),
        events=app.state.strategy_runtime.current_events(),
    )
    if settings.news_calendar_required and not app.state.calendar.fresh:
        assessment["approved"] = False
        assessment["reasons"].append("Required high-impact news calendar is unavailable or stale")
    if not assessment["approved"]:
        raise HTTPException(status_code=422, detail=assessment["reasons"])
    trade_id = str(uuid4())
    trade = TradeRecord(
        id=trade_id, tenant_id=context.state.tenant_id, symbol=request.symbol, side=request.side.lower(), entry_price=request.entry_price,
        exit_price=None, quantity=assessment["quantity"], stop_loss=float(request.stop_loss),
        take_profit=float(request.take_profit), pnl=None,
        status="open" if settings.execution_mode == "full_auto" else "proposed", mode="paper",
        metadata_json={"risk_amount": assessment["risk_amount"], "risk_pct": request.risk_pct},
    )
    async with app.state.database.sessions() as session:
        session.add(trade)
        session.add(AuditRecord(tenant_id=context.state.tenant_id, actor=context.state.user_id, action="paper_trade_proposed", entity_id=trade_id,
                                details={"symbol": request.symbol, "side": request.side, "paper_only": True}))
        await session.commit()
    if settings.execution_mode != "full_auto":
        await app.state.telegram.send_trade_alert({
        "id": trade_id, "symbol": request.symbol, "side": request.side, "entry_price": request.entry_price,
        "quantity": assessment["quantity"], "risk_amount": assessment["risk_amount"],
        "risk_pct": request.risk_pct, "stop_loss": request.stop_loss, "take_profit": request.take_profit,
        })
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
    selected_pnl = sum(float(trade.pnl or 0.0) for trade in closed)
    equity = settings.paper_account_equity + float(total_pnl or 0) - selected_pnl
    points = [{"timestamp": None, "equity": equity, "pnl": 0.0}]
    for trade in closed:
        equity += float(trade.pnl or 0.0)
        points.append({"timestamp": trade.closed_at.isoformat(), "equity": equity, "pnl": float(trade.pnl or 0.0)})
    return {"starting_equity": settings.paper_account_equity, "ending_equity": equity,
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
