import base64
import hashlib
import hmac
import json
import logging
import math
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Optional

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


def check_dashboard_password(password: str) -> bool:
    stored_hash = os.environ.get("DASHBOARD_PASSWORD_HASH", "")
    if stored_hash:
        return verify_password(password, stored_hash)
    return hmac.compare_digest(password.encode(), os.environ.get("DASHBOARD_PASSWORD", "").encode())


COOKIE_NAME = "session"
STEP_UP_COOKIE_NAME = "stepup"
STEP_UP_MAX_AGE = 5 * 60
STEP_UP_COOKIE_PATH = "/api"
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


def _sign(payload: str, purpose: bytes = b"") -> str:
    return hmac.new(_secret(), purpose + payload.encode(), hashlib.sha256).hexdigest()


def _session_epoch() -> int:
    return int(os.environ.get("SESSION_EPOCH") or 0)


def bump_session_epoch() -> int:
    from dotenv import set_key
    from config import BASE_DIR
    new_epoch = _session_epoch() + 1
    set_key(str(BASE_DIR / ".env"), "SESSION_EPOCH", str(new_epoch))
    os.environ["SESSION_EPOCH"] = str(new_epoch)
    return new_epoch


def create_session_token(username: str, remember: bool) -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps({"u": username, "t": int(time.time()), "r": remember, "e": _session_epoch()}).encode()
    ).decode()
    return f"{payload}.{_sign(payload)}"


_SESSION_MAX_AGE = 24 * 3600  # non-remember sessions expire after 24 hours


def session_claims(token: str) -> Optional[dict]:
    try:
        payload, sig = token.rsplit(".", 1)
        if not hmac.compare_digest(sig, _sign(payload)):
            return None
        data = json.loads(base64.urlsafe_b64decode(payload + "=="))
        max_age = _REMEMBER_MAX_AGE if data.get("r", False) else _SESSION_MAX_AGE
        if (time.time() - data.get("t", 0)) >= max_age or data.get("e", 0) != _session_epoch():
            return None
        return data
    except Exception:
        return None


def verify_session_token(token: str) -> bool:
    return session_claims(token) is not None


def _session_binding(session_token: str) -> str:
    return hashlib.sha256(session_token.encode()).hexdigest()


def create_step_up_token(session_token: str) -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps({"t": int(time.time()), "s": _session_binding(session_token)}).encode()
    ).decode()
    return f"{payload}.{_sign(payload, b'stepup:')}"


def verify_step_up_token(token: str, session_token: str) -> bool:
    try:
        payload, sig = token.rsplit(".", 1)
        if not hmac.compare_digest(sig, _sign(payload, b"stepup:")):
            return False
        data = json.loads(base64.urlsafe_b64decode(payload + "=="))
        age = time.time() - data.get("t", 0)
        return 0 <= age < STEP_UP_MAX_AGE and hmac.compare_digest(
            str(data.get("s", "")), _session_binding(session_token)
        )
    except Exception:
        return False


def verify_embed_token(token: str) -> bool:
    expected = os.environ.get("EMBED_TOKEN", "")
    return bool(expected) and bool(token) and hmac.compare_digest(token.encode(), expected.encode())


def cookie_secure() -> bool:
    from config import load_config
    cfg = load_config()
    setting = str(cfg.get("COOKIE_SECURE", "auto")).lower()
    if setting in ("true", "false"):
        return setting == "true"
    return cfg.get("SERVER_URL", "").startswith("https://")


def cookie_kwargs(remember: bool) -> dict:
    return {
        "key": COOKIE_NAME,
        "httponly": True,
        "samesite": "lax",
        "secure": cookie_secure(),
        "max_age": _REMEMBER_MAX_AGE if remember else None,
    }


def step_up_cookie_kwargs() -> dict:
    return {
        "key": STEP_UP_COOKIE_NAME,
        "httponly": True,
        "samesite": "strict",
        "secure": cookie_secure(),
        "max_age": STEP_UP_MAX_AGE,
        "path": STEP_UP_COOKIE_PATH,
    }


MIN_PASSWORD_LENGTH = 12
_COMMON_PASSWORDS_PATH = Path(__file__).resolve().parent / "tools" / "common_passwords.txt"


@lru_cache(maxsize=1)
def _common_passwords() -> frozenset:
    lines = _COMMON_PASSWORDS_PATH.read_text(encoding="utf-8").splitlines()
    return frozenset(line.strip().lower() for line in lines if line.strip())


def password_policy_error(password: str) -> Optional[str]:
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    if password.lower() in _common_passwords():
        return "That password is too common. Please choose a different password."
    return None


LOGIN_FAILURE_THRESHOLD = 5
_LOCKOUT_BASE_SECONDS = 30
_LOCKOUT_MAX_SECONDS = 15 * 60
_THROTTLE_TS_FORMAT = "%Y-%m-%d %H:%M:%S"


def login_lockout_remaining() -> int:
    from database import get_connection
    conn = None
    try:
        conn = get_connection()
        row = conn.execute("SELECT locked_until FROM login_throttle WHERE id = 1").fetchone()
    finally:
        if conn:
            conn.close()
    if not row or not row["locked_until"]:
        return 0
    locked_until = datetime.strptime(row["locked_until"], _THROTTLE_TS_FORMAT).replace(tzinfo=timezone.utc)
    return max(0, math.ceil((locked_until - datetime.now(timezone.utc)).total_seconds()))


def record_login_failure(context: str, client: str, known_user: bool) -> int:
    from database import get_connection
    now = datetime.now(timezone.utc)
    conn = None
    try:
        conn = get_connection()
        conn.execute("INSERT OR IGNORE INTO login_throttle (id) VALUES (1)")
        conn.execute(
            "UPDATE login_throttle SET failed_count = failed_count + 1, last_failure_at = ? WHERE id = 1",
            (now.strftime(_THROTTLE_TS_FORMAT),),
        )
        count = conn.execute("SELECT failed_count FROM login_throttle WHERE id = 1").fetchone()["failed_count"]
        lock_seconds = 0
        if count >= LOGIN_FAILURE_THRESHOLD:
            lock_seconds = min(_LOCKOUT_BASE_SECONDS * 2 ** (count - LOGIN_FAILURE_THRESHOLD), _LOCKOUT_MAX_SECONDS)
            conn.execute(
                "UPDATE login_throttle SET locked_until = ? WHERE id = 1",
                ((now + timedelta(seconds=lock_seconds)).strftime(_THROTTLE_TS_FORMAT),),
            )
        conn.commit()
    finally:
        if conn:
            conn.close()

    logger.warning(
        "Failed dashboard password attempt (%s): consecutive=%d client=%s username_matched=%s lockout_seconds=%d",
        context, count, client, known_user, lock_seconds,
    )
    if count == LOGIN_FAILURE_THRESHOLD:
        from notification_engine import notify
        notify(
            "failed_login_alert", "Warning",
            f"{count} consecutive failed dashboard password attempts (last: {context} from {client}). "
            f"Sign-in is locked for {lock_seconds} seconds and the lockout doubles with each further failure.",
            level="warning",
        )
    return count


def clear_login_failures() -> None:
    from database import get_connection
    conn = None
    try:
        conn = get_connection()
        conn.execute(
            "UPDATE login_throttle SET failed_count = 0, locked_until = NULL WHERE id = 1 AND failed_count > 0"
        )
        conn.commit()
    finally:
        if conn:
            conn.close()
