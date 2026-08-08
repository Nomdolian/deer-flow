import hmac

import pyotp
from fastapi import Header, HTTPException, status

from app.config import settings


def require_api_key(x_api_key: str = Header(...)) -> None:
    """Minimal auth for a system that controls real money (Phase 8, item 4):
    a static bearer key plus, when API_TOTP_SECRET is configured, a required
    TOTP code header for anything beyond read-only GET requests. This is a
    starting point, not a finished auth system — put this API behind a VPN or
    add OAuth/session auth before connecting a live broker account.
    """
    if not settings.api_auth_secret or settings.api_auth_secret == "change-me-to-a-long-random-value":
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "API_AUTH_SECRET not configured")
    if not hmac.compare_digest(x_api_key, settings.api_auth_secret):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid API key")


def require_totp(x_totp_code: str = Header(...)) -> None:
    """Required in addition to require_api_key on trade/kill-switch-authority
    endpoints when 2FA is configured (Phase 8, item 4)."""
    if not settings.api_totp_secret:
        return  # 2FA not configured — operator has explicitly opted out
    totp = pyotp.TOTP(settings.api_totp_secret)
    if not totp.verify(x_totp_code, valid_window=1):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid TOTP code")
