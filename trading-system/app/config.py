from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "development"
    database_url: str = "postgresql+psycopg://trading:trading@localhost:5432/trading"

    # Risk defaults — deliberately conservative. See PART 1 item 3 of the spec:
    # portfolio-level caps apply across ALL agents/strategies combined, not per-strategy.
    risk_per_trade_pct: float = 0.0025
    portfolio_risk_cap_pct: float = 0.03
    correlation_group_risk_cap_pct: float = 0.015
    meme_bucket_cap_pct: float = 0.05
    daily_loss_limit_pct: float = 0.03
    weekly_loss_limit_pct: float = 0.06
    consecutive_loss_breaker: int = 4
    feed_stale_seconds: int = 60

    mt5_login: str = ""
    mt5_password: str = ""
    mt5_server: str = ""
    mt5_path: str = ""

    alpha_vantage_api_key: str = ""

    anthropic_api_key: str = ""

    api_auth_secret: str = "change-me-to-a-long-random-value"
    api_totp_secret: str = ""


settings = Settings()
