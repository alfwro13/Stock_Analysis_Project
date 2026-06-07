import base64
import hashlib
import hmac
import json
import logging
import os
import time

logger = logging.getLogger(__name__)

COOKIE_NAME = "session"
_REMEMBER_MAX_AGE = 30 * 24 * 3600  # 30 days


def _secret() -> bytes:
    key = os.environ.get("APP_SECRET_KEY", "")
    if not key:
        logger.warning("APP_SECRET_KEY is not set — using insecure fallback; set it in .env")
        return b"fallback-insecure"
    return key.encode()


def create_session_token(username: str, remember: bool) -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps({"u": username, "t": int(time.time()), "r": remember}).encode()
    ).decode()
    sig = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


_SESSION_MAX_AGE = 24 * 3600  # non-remember sessions expire after 24 hours


def verify_session_token(token: str) -> bool:
    try:
        payload, sig = token.rsplit(".", 1)
        expected = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return False
        data = json.loads(base64.urlsafe_b64decode(payload + "=="))
        issued_at = data.get("t", 0)
        remember = data.get("r", False)
        max_age = _REMEMBER_MAX_AGE if remember else _SESSION_MAX_AGE
        return (time.time() - issued_at) < max_age
    except Exception:
        return False


def _is_https() -> bool:
    from config import load_config
    return load_config().get("SERVER_URL", "").startswith("https://")


def cookie_kwargs(remember: bool) -> dict:
    return {
        "key": COOKIE_NAME,
        "httponly": True,
        "samesite": "lax",
        "secure": _is_https(),
        "max_age": _REMEMBER_MAX_AGE if remember else None,
    }
