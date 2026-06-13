import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time

logger = logging.getLogger(__name__)

_PBKDF2_ITERS = 600_000


def hash_password(password: str) -> str:
    salt = os.urandom(32)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERS)
    return f"pbkdf2:sha256:{_PBKDF2_ITERS}:{salt.hex()}:{dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, hash_name, iters, salt_hex, hash_hex = stored.split(":")
        if algo != "pbkdf2" or hash_name != "sha256":
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iters))
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False


COOKIE_NAME = "session"
_REMEMBER_MAX_AGE = 30 * 24 * 3600  # 30 days


def _secret() -> bytes:
    key = os.environ.get("APP_SECRET_KEY", "")
    if not key:
        key = secrets.token_hex(32)
        try:
            from dotenv import set_key
            from config import BASE_DIR
            env_path = str(BASE_DIR / ".env")
            set_key(env_path, "APP_SECRET_KEY", key)
            logger.info("APP_SECRET_KEY was not set — generated and persisted a new secret to .env")
        except Exception as e:
            logger.warning("APP_SECRET_KEY not set and could not persist to .env (%s) — session is ephemeral this restart", e)
        os.environ["APP_SECRET_KEY"] = key
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
