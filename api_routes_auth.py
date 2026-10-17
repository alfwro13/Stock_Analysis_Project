import os
import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api_deps import enforce_login_lockout, limiter, record_password_attempt, require_step_up, _error_500
from config import BASE_DIR, load_config, update_config_atomic
from database import get_connection

logger = logging.getLogger(__name__)

auth_router = APIRouter()


def _smtp_send(smtp_host, smtp_port, smtp_user, smtp_pass, smtp_from, recipients, msg):
    import smtplib
    if smtp_port == 465:
        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=10) as s:
            s.ehlo()
            if smtp_user and smtp_pass:
                s.login(smtp_user, smtp_pass)
            s.sendmail(smtp_from, recipients, msg.as_string())
    elif smtp_port == 25:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as s:
            s.ehlo()
            if smtp_user and smtp_pass:
                s.login(smtp_user, smtp_pass)
            s.sendmail(smtp_from, recipients, msg.as_string())
    else:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as s:
            s.ehlo()
            s.starttls()
            if smtp_user and smtp_pass:
                s.login(smtp_user, smtp_pass)
            s.sendmail(smtp_from, recipients, msg.as_string())


class LoginRequest(BaseModel):
    username: str
    password: str
    remember_me: bool = False


@auth_router.post("/login")
@limiter.limit("10/minute")
def login(request: Request, body: LoginRequest, response: Response):
    import secrets as _secrets
    from auth import check_dashboard_password, create_session_token, cookie_kwargs
    enforce_login_lockout()
    valid_user = _secrets.compare_digest(
        body.username.encode(), os.environ.get("DASHBOARD_USERNAME", "").encode()
    )
    valid_pass = check_dashboard_password(body.password)
    record_password_attempt(request, "login", valid_user and valid_pass, known_user=valid_user)
    if not (valid_user and valid_pass):
        raise HTTPException(status_code=401, detail="Invalid username or password.")
    token = create_session_token(body.username, body.remember_me)
    response.set_cookie(value=token, **cookie_kwargs(body.remember_me))
    return {"status": "ok"}


class StepUpRequest(BaseModel):
    password: str


@auth_router.post("/step-up")
@limiter.limit("5/minute")
def step_up(request: Request, body: StepUpRequest, response: Response):
    from auth import (
        COOKIE_NAME, STEP_UP_MAX_AGE, check_dashboard_password, create_step_up_token,
        step_up_cookie_kwargs, verify_session_token,
    )
    session = request.cookies.get(COOKIE_NAME, "")
    if not session or not verify_session_token(session):
        raise HTTPException(status_code=401, detail="A signed-in session is required.")
    enforce_login_lockout()
    password_ok = check_dashboard_password(body.password)
    record_password_attempt(request, "step-up", password_ok)
    if not password_ok:
        raise HTTPException(status_code=401, detail="Incorrect password.")
    response.set_cookie(value=create_step_up_token(session), **step_up_cookie_kwargs())
    return {"status": "ok", "expires_in": STEP_UP_MAX_AGE}


def _secret_update(value: str, clear: bool) -> Optional[str]:
    if clear:
        return ""
    return value if value.strip() else None


def _generate_env_secret(name: str) -> str:
    import secrets as _secrets
    from dotenv import set_key
    value = _secrets.token_hex(32)
    set_key(str(BASE_DIR / ".env"), name, value)
    os.environ[name] = value
    return value


@auth_router.post("/generate-api-key", dependencies=[Depends(require_step_up)])
def generate_api_key():
    return {"api_key": _generate_env_secret("API_KEY")}


@auth_router.post("/generate-embed-token", dependencies=[Depends(require_step_up)])
def generate_embed_token():
    return {"embed_token": _generate_env_secret("EMBED_TOKEN")}


@auth_router.post("/generate-mcp-key", dependencies=[Depends(require_step_up)])
@limiter.limit("5/minute")
def generate_mcp_key(request: Request):
    return {"mcp_api_key": _generate_env_secret("MCP_API_KEY")}


class McpServerToggle(BaseModel):
    enabled: bool


@auth_router.post("/mcp-server/enabled")
@limiter.limit("10/minute")
def set_mcp_server_enabled(request: Request, body: McpServerToggle):
    if body.enabled:
        require_step_up(request)
    update_config_atomic({"MCP_SERVER_ENABLED": body.enabled})
    return {"status": "success", "enabled": body.enabled}


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str
    confirm_password: str


def _store_new_password(new_password: str) -> None:
    from dotenv import set_key
    from auth import bump_session_epoch, clear_login_failures, hash_password

    new_hash = hash_password(new_password)
    env_path = str(BASE_DIR / ".env")
    set_key(env_path, "DASHBOARD_PASSWORD_HASH", new_hash)
    set_key(env_path, "DASHBOARD_PASSWORD", "")
    os.environ["DASHBOARD_PASSWORD_HASH"] = new_hash
    os.environ["DASHBOARD_PASSWORD"] = ""
    bump_session_epoch()
    clear_login_failures()


def _check_new_password(new_password: str, confirm_password: str) -> None:
    from auth import password_policy_error

    if new_password != confirm_password:
        raise HTTPException(status_code=400, detail="Passwords do not match.")
    policy_error = password_policy_error(new_password)
    if policy_error:
        raise HTTPException(status_code=400, detail=policy_error)


@auth_router.post("/change-password")
@limiter.limit("5/minute")
def change_password(request: Request, body: ChangePasswordRequest, response: Response):
    from auth import COOKIE_NAME, check_dashboard_password, cookie_kwargs, create_session_token, session_claims

    enforce_login_lockout()
    current_ok = check_dashboard_password(body.current_password)
    record_password_attempt(request, "change-password", current_ok)
    if not current_ok:
        raise HTTPException(status_code=400, detail="Current password is incorrect.")
    _check_new_password(body.new_password, body.confirm_password)

    claims = session_claims(request.cookies.get(COOKIE_NAME, ""))
    _store_new_password(body.new_password)
    if claims:
        remember = claims.get("r") is True
        token = create_session_token(os.environ.get("DASHBOARD_USERNAME", ""), remember)
        response.set_cookie(value=token, **cookie_kwargs(remember))
    return {"status": "ok"}


class PasswordResetRequestBody(BaseModel):
    email: str


class PasswordResetBody(BaseModel):
    token: str
    new_password: str
    confirm_password: str


def _send_reset_notification(reset_url: str) -> bool:
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
            _smtp_send(smtp_host, smtp_port, smtp_user, smtp_pass, smtp_from, [account_email], msg)
            logger.info("Password reset email sent to %s", account_email)
            return True
        except Exception as e:
            logger.warning("SMTP send failed: %s", e)

    if os.environ.get("NEXTCLOUD_URL"):
        from nextcloud_talk import send_text_message
        if send_text_message(f"Password reset link (expires 1h):\n{reset_url}", {}):
            logger.info("Password reset link sent via Nextcloud Talk")
            return True
        logger.warning("Nextcloud reset notification failed")

    return False


_RESET_REQUEST_REPLY = {"status": "ok", "message": "If the email matches, a reset link has been sent."}


def _issue_reset_link(email: str) -> None:
    import secrets
    import hashlib as _hl
    from datetime import datetime, timezone, timedelta

    account_email = os.environ.get("ACCOUNT_EMAIL", "")
    if not account_email or email.lower().strip() != account_email.lower().strip():
        return

    token = secrets.token_urlsafe(32)
    token_hash = _hl.sha256(token.encode()).hexdigest()
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")

    conn = None
    try:
        conn = get_connection()
        conn.execute("UPDATE password_reset_tokens SET used = 1 WHERE used = 0")
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

    if not _send_reset_notification(f"{base}/reset-password?token={token}"):
        logger.warning("Password reset link could not be delivered: no working SMTP or Nextcloud Talk channel")


@auth_router.post("/request-password-reset")
@limiter.limit("3/hour")
def request_password_reset(request: Request, body: PasswordResetRequestBody, background_tasks: BackgroundTasks):
    background_tasks.add_task(_issue_reset_link, body.email)
    return _RESET_REQUEST_REPLY


@auth_router.post("/reset-password")
@limiter.limit("10/hour")
def reset_password(request: Request, body: PasswordResetBody):
    import hashlib as _hl
    from datetime import datetime, timezone

    _check_new_password(body.new_password, body.confirm_password)

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

    _store_new_password(body.new_password)
    return {"status": "ok"}


class SaveAccountEmailRequest(BaseModel):
    email: str


@auth_router.post("/save-account-email", dependencies=[Depends(require_step_up)])
def save_account_email(body: SaveAccountEmailRequest):
    from dotenv import set_key
    set_key(str(BASE_DIR / ".env"), "ACCOUNT_EMAIL", body.email.strip())
    os.environ["ACCOUNT_EMAIL"] = body.email.strip()
    return {"status": "ok"}


class SaveNextcloudSettingsRequest(BaseModel):
    NEXTCLOUD_URL: str
    BOT_USERNAME: str
    APP_PASSWORD: str = ""
    CONVERSATION_TOKEN: str = ""
    CLEAR_APP_PASSWORD: bool = False
    CLEAR_CONVERSATION_TOKEN: bool = False


@auth_router.post("/save-nextcloud-settings", dependencies=[Depends(require_step_up)])
def save_nextcloud_settings(body: SaveNextcloudSettingsRequest):
    from dotenv import set_key
    env_path = str(BASE_DIR / ".env")
    mapping = {
        "NEXTCLOUD_URL": body.NEXTCLOUD_URL,
        "NEXTCLOUD_BOT_USERNAME": body.BOT_USERNAME,
        "NEXTCLOUD_APP_PASSWORD": _secret_update(body.APP_PASSWORD, body.CLEAR_APP_PASSWORD),
        "NEXTCLOUD_CONVERSATION_TOKEN": _secret_update(body.CONVERSATION_TOKEN, body.CLEAR_CONVERSATION_TOKEN),
    }
    for key, value in mapping.items():
        if value is None:
            continue
        set_key(env_path, key, value)
        os.environ[key] = value
    return {"status": "ok"}


@auth_router.post("/test-nextcloud-message", dependencies=[Depends(require_step_up)])
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
    GHOSTFOLIO_TOKEN: str = ""
    CLEAR_GHOSTFOLIO_TOKEN: bool = False


@auth_router.post("/save-ghostfolio-settings", dependencies=[Depends(require_step_up)])
def save_ghostfolio_settings(body: SaveGhostfolioSettingsRequest):
    from dotenv import set_key
    env_path = str(BASE_DIR / ".env")
    set_key(env_path, "GHOSTFOLIO_URL", body.GHOSTFOLIO_URL)
    os.environ["GHOSTFOLIO_URL"] = body.GHOSTFOLIO_URL
    token = _secret_update(body.GHOSTFOLIO_TOKEN, body.CLEAR_GHOSTFOLIO_TOKEN)
    if token is not None:
        set_key(env_path, "GHOSTFOLIO_TOKEN", token)
        os.environ["GHOSTFOLIO_TOKEN"] = token
    return {"status": "ok"}


class SaveFredApiKeyRequest(BaseModel):
    FRED_API_KEY: str = ""
    CLEAR_FRED_API_KEY: bool = False


@auth_router.post("/save-fred-api-key", dependencies=[Depends(require_step_up)])
def save_fred_api_key(body: SaveFredApiKeyRequest):
    from dotenv import set_key
    env_path = str(BASE_DIR / ".env")
    key = _secret_update(body.FRED_API_KEY, body.CLEAR_FRED_API_KEY)
    if key is not None:
        set_key(env_path, "FRED_API_KEY", key)
        os.environ["FRED_API_KEY"] = key
    return {"status": "ok"}


class SaveHFTokenRequest(BaseModel):
    HF_TOKEN: str = ""
    CLEAR_HF_TOKEN: bool = False


@auth_router.post("/save-hf-token", dependencies=[Depends(require_step_up)])
def save_hf_token(body: SaveHFTokenRequest):
    from dotenv import set_key
    env_path = str(BASE_DIR / ".env")
    token = _secret_update(body.HF_TOKEN, body.CLEAR_HF_TOKEN)
    if token is not None:
        set_key(env_path, "HF_TOKEN", token)
        os.environ["HF_TOKEN"] = token
    return {"status": "ok", "message": "HF Token saved."}


class TestHFTokenRequest(BaseModel):
    HF_TOKEN: str = ""


@auth_router.post("/test-hf-token", dependencies=[Depends(require_step_up)])
def test_hf_token(body: TestHFTokenRequest):
    token = body.HF_TOKEN.strip() or os.environ.get("HF_TOKEN", "")
    if not token:
        raise HTTPException(status_code=400, detail="No HuggingFace token provided. Enter a token and try again.")
    try:
        import requests
        resp = requests.get(
            "https://huggingface.co/api/whoami-v2",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        resp.raise_for_status()
        info = resp.json()
        username = info.get("name") or info.get("fullname") or "unknown"
        return {"status": "ok", "message": f"Token is valid. Authenticated as: {username}"}
    except Exception as e:
        logger.warning("HuggingFace token test failed: %s", e)
        status = getattr(getattr(e, "response", None), "status_code", None)
        reason = "HuggingFace rejected the token." if status in (401, 403) else "HuggingFace could not be reached."
        raise HTTPException(status_code=400, detail=f"Token verification failed: {reason} See the server log for details.")


class ChangeUsernameRequest(BaseModel):
    new_username: str


@auth_router.post("/change-username", dependencies=[Depends(require_step_up)])
def change_username(body: ChangeUsernameRequest):
    from dotenv import set_key
    username = body.new_username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="Username cannot be empty.")
    env_path = str(BASE_DIR / ".env")
    set_key(env_path, "DASHBOARD_USERNAME", username)
    os.environ["DASHBOARD_USERNAME"] = username
    return {"status": "ok"}


@auth_router.post("/logout-everywhere", dependencies=[Depends(require_step_up)])
def logout_everywhere(response: Response):
    from auth import COOKIE_NAME, STEP_UP_COOKIE_NAME, STEP_UP_COOKIE_PATH, bump_session_epoch

    bump_session_epoch()
    response.delete_cookie(COOKIE_NAME)
    response.delete_cookie(STEP_UP_COOKIE_NAME, path=STEP_UP_COOKIE_PATH)
    logger.info("All dashboard sessions revoked (logout everywhere)")
    return {"status": "ok"}


@auth_router.post("/rotate-app-secret", dependencies=[Depends(require_step_up)])
def rotate_app_secret():
    import secrets as _secrets
    from dotenv import set_key
    new_secret = _secrets.token_hex(32)
    env_path = str(BASE_DIR / ".env")
    set_key(env_path, "APP_SECRET_KEY", new_secret)
    os.environ["APP_SECRET_KEY"] = new_secret
    return {"status": "ok"}


class SaveSmtpSettingsRequest(BaseModel):
    smtp_host: str
    smtp_port: str
    smtp_user: str
    smtp_from: str
    smtp_pass: str = ""
    clear_smtp_pass: bool = False


@auth_router.post("/save-smtp-settings", dependencies=[Depends(require_step_up)])
def save_smtp_settings(body: SaveSmtpSettingsRequest):
    from dotenv import set_key
    env_path = str(BASE_DIR / ".env")
    fields = {
        "SMTP_HOST": body.smtp_host.strip(),
        "SMTP_PORT": body.smtp_port.strip(),
        "SMTP_USER": body.smtp_user.strip(),
        "SMTP_PASS": _secret_update(body.smtp_pass.strip(), body.clear_smtp_pass),
        "SMTP_FROM": body.smtp_from.strip(),
    }
    for key, val in fields.items():
        if val is None:
            continue
        set_key(env_path, key, val)
        os.environ[key] = val
    return {"status": "ok", "message": "Mail server settings saved."}


@auth_router.post("/send-test-email", dependencies=[Depends(require_step_up)])
def send_test_email():
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    smtp_host = os.environ.get("SMTP_HOST", "").strip()
    account_email = os.environ.get("ACCOUNT_EMAIL", "").strip()
    if not smtp_host:
        raise HTTPException(status_code=400, detail="SMTP host is not configured.")
    if not account_email:
        raise HTTPException(status_code=400, detail="Account email is not set — configure it in User Account first.")

    smtp_port = int(os.environ.get("SMTP_PORT", "587") or "587")
    smtp_user = os.environ.get("SMTP_USER", "").strip()
    smtp_pass = os.environ.get("SMTP_PASS", "").strip()
    smtp_from = (os.environ.get("SMTP_FROM", "").strip() or smtp_user) or "noreply@quantamental"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Quantamental — Mail Server Test"
    msg["From"] = smtp_from
    msg["To"] = account_email
    msg.attach(MIMEText("This is a test email from your Quantamental dashboard. Mail server is configured correctly.", "plain"))
    try:
        _smtp_send(smtp_host, smtp_port, smtp_user, smtp_pass, smtp_from, [account_email], msg)
        logger.info("Test email sent to %s", account_email)
        return {"status": "ok", "message": "Test email sent to %s." % account_email}
    except Exception as e:
        logger.warning("Test email failed: %s", e)
        raise HTTPException(status_code=500, detail="Send failed. See the server log for details.")
