import os
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.util import get_remote_address
from pydantic import BaseModel

from config import BASE_DIR, load_config, update_config_atomic
from database import get_connection

logger = logging.getLogger(__name__)

auth_router = APIRouter()
_auth_limiter = Limiter(key_func=get_remote_address)


def require_confirm_token(x_confirm_token: str = Header(..., alias="X-Confirm-Token")):
    import secrets as _secrets
    expected = os.environ.get("ADMIN_CONFIRM_TOKEN", "")
    if not expected or not _secrets.compare_digest(x_confirm_token.encode(), expected.encode()):
        raise HTTPException(status_code=403, detail="Invalid or missing confirmation token.")


def _error_500(e: Exception) -> JSONResponse:
    return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})


class LoginRequest(BaseModel):
    username: str
    password: str
    remember_me: bool = False


@auth_router.post("/login")
@_auth_limiter.limit("10/minute")
async def login(request: Request, body: LoginRequest, response: Response):
    import secrets as _secrets
    from auth import create_session_token, cookie_kwargs, verify_password
    valid_user = _secrets.compare_digest(
        body.username.encode(), os.environ.get("DASHBOARD_USERNAME", "").encode()
    )
    stored_hash = os.environ.get("DASHBOARD_PASSWORD_HASH", "")
    if stored_hash:
        valid_pass = verify_password(body.password, stored_hash)
    else:
        valid_pass = _secrets.compare_digest(
            body.password.encode(), os.environ.get("DASHBOARD_PASSWORD", "").encode()
        )
    if not (valid_user and valid_pass):
        raise HTTPException(status_code=401, detail="Invalid username or password.")
    token = create_session_token(body.username, body.remember_me)
    response.set_cookie(value=token, **cookie_kwargs(body.remember_me))
    return {"status": "ok"}


@auth_router.post("/generate-api-key", dependencies=[Depends(require_confirm_token)])
async def generate_api_key():
    import secrets as _secrets
    from dotenv import set_key
    new_key = _secrets.token_hex(32)
    set_key(str(BASE_DIR / ".env"), "API_KEY", new_key)
    os.environ["API_KEY"] = new_key
    return {"api_key": new_key}


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str
    confirm_password: str


@auth_router.post("/change-password", dependencies=[Depends(require_confirm_token)])
async def change_password(body: ChangePasswordRequest):
    import secrets as _secrets
    from dotenv import set_key
    from auth import hash_password, verify_password

    stored_hash = os.environ.get("DASHBOARD_PASSWORD_HASH", "")
    if stored_hash:
        valid_current = verify_password(body.current_password, stored_hash)
    else:
        current_plain = os.environ.get("DASHBOARD_PASSWORD", "")
        valid_current = _secrets.compare_digest(body.current_password.encode(), current_plain.encode())
    if not valid_current:
        raise HTTPException(status_code=400, detail="Current password is incorrect.")
    if body.new_password != body.confirm_password:
        raise HTTPException(status_code=400, detail="Passwords do not match.")
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")
    if body.new_password == "changeme":
        raise HTTPException(status_code=400, detail="Please choose a different password.")

    new_hash = hash_password(body.new_password)
    env_path = str(BASE_DIR / ".env")
    set_key(env_path, "DASHBOARD_PASSWORD_HASH", new_hash)
    set_key(env_path, "DASHBOARD_PASSWORD", "")
    os.environ["DASHBOARD_PASSWORD_HASH"] = new_hash
    os.environ["DASHBOARD_PASSWORD"] = ""
    return {"status": "ok"}


class PasswordResetRequestBody(BaseModel):
    email: str


class PasswordResetBody(BaseModel):
    token: str
    new_password: str
    confirm_password: str


def _send_reset_notification(reset_url: str) -> bool:
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    smtp_host = os.environ.get("SMTP_HOST", "")
    account_email = os.environ.get("ACCOUNT_EMAIL", "")
    if smtp_host and account_email:
        smtp_port = int(os.environ.get("SMTP_PORT", "587"))
        smtp_user = os.environ.get("SMTP_USER", "")
        smtp_pass = os.environ.get("SMTP_PASS", "")
        smtp_from = os.environ.get("SMTP_FROM", smtp_user) or "noreply@quantamental"
        body_text = (
            f"Click the link below to reset your Quantamental password.\n"
            f"This link expires in 1 hour.\n\n{reset_url}\n\n"
            f"If you did not request a reset, ignore this message."
        )
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "Quantamental — Password Reset"
        msg["From"] = smtp_from
        msg["To"] = account_email
        msg.attach(MIMEText(body_text, "plain"))
        try:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as s:
                s.ehlo()
                s.starttls()
                if smtp_user and smtp_pass:
                    s.login(smtp_user, smtp_pass)
                s.sendmail(smtp_from, [account_email], msg.as_string())
            logger.info("Password reset email sent to %s", account_email)
            return True
        except Exception as e:
            logger.warning("SMTP send failed: %s", e)

    nextcloud_url = os.environ.get("NEXTCLOUD_URL", "")
    if nextcloud_url:
        try:
            from nextcloud_talk import send_nextcloud_message
            send_nextcloud_message(f"Password reset link (expires 1h):\n{reset_url}")
            logger.info("Password reset link sent via Nextcloud Talk")
            return True
        except Exception as e:
            logger.warning("Nextcloud reset notification failed: %s", e)

    return False


@auth_router.post("/request-password-reset")
async def request_password_reset(body: PasswordResetRequestBody):
    import secrets
    import hashlib as _hl
    from datetime import datetime, timezone, timedelta

    account_email = os.environ.get("ACCOUNT_EMAIL", "")
    if not account_email or body.email.lower().strip() != account_email.lower().strip():
        return {"status": "ok", "message": "If the email matches, a reset link has been sent."}

    token = secrets.token_urlsafe(32)
    token_hash = _hl.sha256(token.encode()).hexdigest()
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")

    conn = None
    try:
        conn = get_connection()
        conn.execute(
            "INSERT INTO password_reset_tokens (token_hash, expires_at, used) VALUES (?, ?, 0)",
            (token_hash, expires_at),
        )
        conn.commit()
    finally:
        if conn:
            conn.close()

    server_url = load_config().get("SERVER_URL", "http://localhost")
    port = load_config().get("PORT", 8090)
    base = server_url.rstrip("/")
    if not (base.endswith(":8090") or base.endswith(f":{port}")):
        base = f"{base}:{port}"
    reset_url = f"{base}/reset-password?token={token}"

    sent = _send_reset_notification(reset_url)
    if not sent:
        logger.info("Password reset URL (no delivery channel configured): %s", reset_url)
        return {"status": "ok", "message": "Reset link logged to server — check application logs."}

    return {"status": "ok", "message": "If the email matches, a reset link has been sent."}


@auth_router.post("/reset-password")
async def reset_password(body: PasswordResetBody):
    import hashlib as _hl
    from datetime import datetime, timezone
    from auth import hash_password as _hash
    from dotenv import set_key

    if body.new_password != body.confirm_password:
        raise HTTPException(status_code=400, detail="Passwords do not match.")
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")
    if body.new_password == "changeme":
        raise HTTPException(status_code=400, detail="Please choose a different password.")

    token_hash = _hl.sha256(body.token.encode()).hexdigest()
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    conn = None
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT expires_at, used FROM password_reset_tokens WHERE token_hash = ?",
            (token_hash,),
        ).fetchone()
        if not row or row["used"] or row["expires_at"] < now_utc:
            raise HTTPException(status_code=400, detail="Reset link is invalid or has expired.")
        conn.execute(
            "UPDATE password_reset_tokens SET used = 1 WHERE token_hash = ?",
            (token_hash,),
        )
        conn.commit()
    finally:
        if conn:
            conn.close()

    new_hash = _hash(body.new_password)
    env_path = str(BASE_DIR / ".env")
    set_key(env_path, "DASHBOARD_PASSWORD_HASH", new_hash)
    set_key(env_path, "DASHBOARD_PASSWORD", "")
    os.environ["DASHBOARD_PASSWORD_HASH"] = new_hash
    os.environ["DASHBOARD_PASSWORD"] = ""
    return {"status": "ok"}


class SaveAccountEmailRequest(BaseModel):
    email: str


@auth_router.post("/save-account-email", dependencies=[Depends(require_confirm_token)])
async def save_account_email(body: SaveAccountEmailRequest):
    from dotenv import set_key
    set_key(str(BASE_DIR / ".env"), "ACCOUNT_EMAIL", body.email.strip())
    os.environ["ACCOUNT_EMAIL"] = body.email.strip()
    return {"status": "ok"}


class AdminResetPasswordBody(BaseModel):
    new_password: str
    confirm_password: str


@auth_router.post("/admin-reset-password")
async def admin_reset_password(body: AdminResetPasswordBody):
    from dotenv import set_key
    from auth import hash_password as _hash

    cfg = load_config()
    if not cfg.get("FORCE_PASSWORD_RESET", False):
        raise HTTPException(status_code=403, detail="Admin reset is not enabled.")

    if body.new_password != body.confirm_password:
        raise HTTPException(status_code=400, detail="Passwords do not match.")
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")
    if body.new_password == "changeme":
        raise HTTPException(status_code=400, detail="Please choose a different password.")

    new_hash = _hash(body.new_password)
    env_path = str(BASE_DIR / ".env")
    set_key(env_path, "DASHBOARD_PASSWORD_HASH", new_hash)
    set_key(env_path, "DASHBOARD_PASSWORD", "")
    os.environ["DASHBOARD_PASSWORD_HASH"] = new_hash
    os.environ["DASHBOARD_PASSWORD"] = ""

    update_config_atomic({"FORCE_PASSWORD_RESET": False})
    return {"status": "ok"}


class SaveNextcloudSettingsRequest(BaseModel):
    NEXTCLOUD_URL: str
    BOT_USERNAME: str
    APP_PASSWORD: str
    CONVERSATION_TOKEN: str


@auth_router.post("/save-nextcloud-settings", dependencies=[Depends(require_confirm_token)])
async def save_nextcloud_settings(body: SaveNextcloudSettingsRequest):
    from dotenv import set_key
    env_path = str(BASE_DIR / ".env")
    mapping = {
        "NEXTCLOUD_URL": body.NEXTCLOUD_URL,
        "NEXTCLOUD_BOT_USERNAME": body.BOT_USERNAME,
        "NEXTCLOUD_APP_PASSWORD": body.APP_PASSWORD,
        "NEXTCLOUD_CONVERSATION_TOKEN": body.CONVERSATION_TOKEN,
    }
    for key, value in mapping.items():
        set_key(env_path, key, value)
        os.environ[key] = value
    return {"status": "ok"}


@auth_router.post("/test-nextcloud-message", dependencies=[Depends(require_confirm_token)])
def test_nextcloud_message():
    from notification_engine import send_test_message
    url = os.environ.get("NEXTCLOUD_URL", "")
    token = os.environ.get("NEXTCLOUD_CONVERSATION_TOKEN", "")
    user = os.environ.get("NEXTCLOUD_BOT_USERNAME", "")
    pwd = os.environ.get("NEXTCLOUD_APP_PASSWORD", "")
    if not all([url, token, user, pwd]):
        missing = [k for k, v in {"NEXTCLOUD_URL": url, "NEXTCLOUD_CONVERSATION_TOKEN": token, "NEXTCLOUD_BOT_USERNAME": user, "NEXTCLOUD_APP_PASSWORD": pwd}.items() if not v]
        return JSONResponse(status_code=400, content={"status": "error", "message": f"Missing credentials: {', '.join(missing)}"})
    ok = send_test_message()
    if ok:
        return JSONResponse(content={"status": "success", "message": "Test message sent successfully."})
    return JSONResponse(status_code=500, content={"status": "error", "message": "Send failed. Check server logs for the HTTP error detail."})


class SaveGhostfolioSettingsRequest(BaseModel):
    GHOSTFOLIO_URL: str
    GHOSTFOLIO_TOKEN: str


@auth_router.post("/save-ghostfolio-settings", dependencies=[Depends(require_confirm_token)])
async def save_ghostfolio_settings(body: SaveGhostfolioSettingsRequest):
    from dotenv import set_key
    env_path = str(BASE_DIR / ".env")
    set_key(env_path, "GHOSTFOLIO_URL", body.GHOSTFOLIO_URL)
    set_key(env_path, "GHOSTFOLIO_TOKEN", body.GHOSTFOLIO_TOKEN)
    os.environ["GHOSTFOLIO_URL"] = body.GHOSTFOLIO_URL
    os.environ["GHOSTFOLIO_TOKEN"] = body.GHOSTFOLIO_TOKEN
    return {"status": "ok"}


class SaveFredApiKeyRequest(BaseModel):
    FRED_API_KEY: str


@auth_router.post("/save-fred-api-key", dependencies=[Depends(require_confirm_token)])
async def save_fred_api_key(body: SaveFredApiKeyRequest):
    from dotenv import set_key
    env_path = str(BASE_DIR / ".env")
    set_key(env_path, "FRED_API_KEY", body.FRED_API_KEY)
    os.environ["FRED_API_KEY"] = body.FRED_API_KEY
    return {"status": "ok"}


class SaveHFTokenRequest(BaseModel):
    HF_TOKEN: str


@auth_router.post("/save-hf-token", dependencies=[Depends(require_confirm_token)])
async def save_hf_token(body: SaveHFTokenRequest):
    from dotenv import set_key
    env_path = str(BASE_DIR / ".env")
    set_key(env_path, "HF_TOKEN", body.HF_TOKEN)
    os.environ["HF_TOKEN"] = body.HF_TOKEN
    return {"status": "ok", "message": "HF Token saved."}


class TestHFTokenRequest(BaseModel):
    HF_TOKEN: str = ""


@auth_router.post("/test-hf-token", dependencies=[Depends(require_confirm_token)])
def test_hf_token(body: TestHFTokenRequest):
    token = body.HF_TOKEN.strip() or os.environ.get("HF_TOKEN", "")
    if not token:
        raise HTTPException(status_code=400, detail="No HuggingFace token provided. Enter a token and try again.")
    try:
        from huggingface_hub import whoami
        info = whoami(token=token)
        username = info.get("name") or info.get("fullname") or "unknown"
        return {"status": "ok", "message": f"Token is valid. Authenticated as: {username}"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Token verification failed: {e}")


class ChangeUsernameRequest(BaseModel):
    new_username: str


@auth_router.post("/change-username", dependencies=[Depends(require_confirm_token)])
async def change_username(body: ChangeUsernameRequest):
    from dotenv import set_key
    username = body.new_username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="Username cannot be empty.")
    env_path = str(BASE_DIR / ".env")
    set_key(env_path, "DASHBOARD_USERNAME", username)
    os.environ["DASHBOARD_USERNAME"] = username
    return {"status": "ok"}


@auth_router.post("/rotate-app-secret", dependencies=[Depends(require_confirm_token)])
async def rotate_app_secret():
    import secrets as _secrets
    from dotenv import set_key
    new_secret = _secrets.token_hex(32)
    env_path = str(BASE_DIR / ".env")
    set_key(env_path, "APP_SECRET_KEY", new_secret)
    os.environ["APP_SECRET_KEY"] = new_secret
    return {"status": "ok"}


@auth_router.post("/rotate-confirm-token", dependencies=[Depends(require_confirm_token)])
async def rotate_confirm_token():
    import secrets as _secrets
    from dotenv import set_key
    new_token = _secrets.token_hex(16)
    env_path = str(BASE_DIR / ".env")
    set_key(env_path, "ADMIN_CONFIRM_TOKEN", new_token)
    os.environ["ADMIN_CONFIRM_TOKEN"] = new_token
    return {"status": "ok", "new_token": new_token}
