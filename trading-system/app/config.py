from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "development"
    database_url: str = "postgresql+psycopg://trading:trading@localhost:5432/trading"

    # Broker profile driving instrument contract specs (app/risk/instruments.py).
    # Specs are NOT universal: the same ticker has different minimums, steps and
    # contract sizes per broker and per account type, and those differences decide
    # position size. "exness" resolves to exness_standard; set account_type="cent"
    # for a Standard Cent account, whose lot is 1,000 units instead of 100,000.
    broker: str = "exness"
    account_type: str = "cent"

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

    anthropic_api_key: str = ""

    api_auth_secret: str = "change-me-to-a-long-random-value"
    api_totp_secret: str = ""


settings = Settings()
