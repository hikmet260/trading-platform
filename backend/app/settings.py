from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    log_level: str = "info"
    database_url: str = "postgresql+psycopg://trader:change-me-local-only@localhost:5432/trading"
    redis_url: str = "redis://localhost:6379/0"
    cors_origins: str = "http://localhost:3000"
    api_access_token: str = ""
    jwt_secret_key: str = ""
    bootstrap_admin_email: str = ""
    bootstrap_admin_password: str = ""
    access_token_ttl_seconds: int = 1800
    broker_credentials_key: str = ""
    tenant_id: str = "local"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_allowed_user_ids: str = ""
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    gemini_api_key: str = ""
    binance_api_key: str = ""
    binance_api_secret: str = ""
    mt5_login: str = ""
    mt5_password: str = ""
    mt5_server: str = ""
    mt5_enabled: bool = False
    mt5_terminal_path: str = ""
    crypto_exchange: str = "binance"
    crypto_api_key: str = ""
    crypto_api_secret: str = ""
    crypto_api_password: str = ""
    crypto_sandbox: bool = True
    crypto_default_type: str = "spot"
    live_trading_enabled: bool = False
    engine_enabled: bool = False
    engine_symbols: str = "BTC/USDT"
    engine_mt5_symbols: str = "EURUSD,XAUUSD"
    engine_timeframe: str = "1m"
    engine_paper_mode: bool = True
    execution_mode: Literal["semi_auto", "full_auto"] = "semi_auto"
    engine_max_daily_drawdown_pct: float = 3.0
    engine_news_blackout_minutes: int = 15
    account_currency: str = "USD"
    max_trade_risk_pct: float = 1.0
    max_open_risk_pct: float = 2.0
    max_notional_multiple: float = 3.0
    max_positions_per_tenant: int = 5
    max_daily_trades: int = 20
    proposal_ttl_seconds: int = 900
    live_readiness_ack: bool = False
    trust_proxy_headers: bool = False
    ws_auth_required: bool = False
    ai_openai_default_model: str = "gpt-4o"
    ai_anthropic_default_model: str = "claude-sonnet-4-6"
    ai_gemini_default_model: str = "gemini-3.8-flash"
    ai_request_timeout_seconds: float = 30.0
    news_events_json: str = "[]"
    news_calendar_api_key: str = ""
    news_calendar_poll_seconds: int = 900
    news_calendar_required: bool = False
    paper_account_equity: float = 10_000.0
    telegram_enabled: bool = False
    telegram_role: Literal["inline", "publisher", "worker"] = "inline"
    telegram_timezone: str = "UTC"
    webpush_public_key: str = ""
    webpush_private_key: str = ""
    webpush_subject: str = "mailto:admin@example.com"
    frontend_api_url: str = "http://localhost:8000"
    postgres_pool_size: int = 10
    postgres_max_overflow: int = 20
    postgres_pool_timeout_seconds: int = 30
    redis_market_data_ttl_seconds: int = 60

    @property
    def engine_symbol_list(self) -> list[str]:
        return [symbol.strip() for symbol in self.engine_symbols.split(",") if symbol.strip()]

    @property
    def engine_mt5_symbol_list(self) -> list[str]:
        return [symbol.strip() for symbol in self.engine_mt5_symbols.split(",") if symbol.strip()]

    @property
    def telegram_user_id_list(self) -> set[str]:
        configured = {value.strip() for value in self.telegram_allowed_user_ids.split(",") if value.strip()}
        return configured or ({self.telegram_chat_id} if self.telegram_chat_id else set())

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
