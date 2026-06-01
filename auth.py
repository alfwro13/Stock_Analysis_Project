import base64
import hashlib
import hmac
import json
import os
import time


COOKIE_NAME = "session"
_REMEMBER_MAX_AGE = 30 * 24 * 3600  # 30 days


def _secret() -> bytes:
    return os.environ.get("APP_SECRET_KEY", "fallback-insecure").encode()


def create_session_token(username: str, remember: bool) -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps({"u": username, "t": int(time.time()), "r": remember}).encode()
    ).decode()
    sig = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify_session_token(token: str) -> bool:
    try:
        payload, sig = token.rsplit(".", 1)
        expected = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expected)
    except Exception:
        return False


def cookie_kwargs(remember: bool) -> dict:
    return {
        "key": COOKIE_NAME,
        "httponly": True,
        "samesite": "lax",
        "max_age": _REMEMBER_MAX_AGE if remember else None,
    }
