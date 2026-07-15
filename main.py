import logging
import os
import re
import secrets
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager

from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette_csrf import CSRFMiddleware

from config import PORT, SERVER_URL, load_config
from auth import COOKIE_NAME, verify_session_token, verify_embed_token
from api_routes import limiter
from database import init_db
from scheduler_engine import start_scheduler, shutdown_scheduler, reload_scheduler, resume_interrupted_scans
from log_config import configure_file_logging

from api_routes import api_router
from page_routes import page_router
from data_engine import run_yfinance_smoke_test

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

configure_file_logging(load_config())


@asynccontextmanager
async def lifespan(app: FastAPI):
    import threading
    logger.info("Initializing application lifecycle...")
    init_db()
    from utils import ensure_workflow_assets, notify_requirements_drift
    ensure_workflow_assets()
    notify_requirements_drift()
    run_yfinance_smoke_test()
    start_scheduler()
    reload_scheduler()
    threading.Thread(target=resume_interrupted_scans, daemon=True).start()
    yield
    shutdown_scheduler()
    logger.info("Application lifecycle terminated safely.")


app = FastAPI(title="Quantamental Dashboard", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(
    CSRFMiddleware,
    secret=os.environ.get("APP_SECRET_KEY") or secrets.token_hex(32),
    sensitive_cookies={"session"},   # only enforce when a session cookie is present
    exempt_urls=[re.compile(r"^/api/(login|request-password-reset|reset-password|admin-reset-password)$")],
    cookie_httponly=False,           # JS must be able to read it
    cookie_samesite="lax",
)

# Paths that never require a session
_EXEMPT = {"/login", "/api/login", "/reset-password", "/api/request-password-reset", "/api/reset-password"}
_EXEMPT_PREFIXES = ("/static/", "/assets/", "/rss/")

# Paths accessible with a valid session even when password is still default
_CHANGE_PW_PATHS = {"/change-password", "/api/change-password", "/admin-reset-password", "/api/admin-reset-password"}

# Pages embeddable via ?embed=true (e.g. Home Assistant iframe) — the embed token only bypasses login here
_EMBED_PATHS = {"/portfolio", "/watchlist"}
_EMBED_PATH_PREFIXES = ("/stock/",)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path

    # Static files, login, and password-reset routes are always public
    if path in _EXEMPT or any(path.startswith(p) for p in _EXEMPT_PREFIXES):
        return await call_next(request)

    # FORCE_PASSWORD_RESET flag in config.json allows unauthenticated access to the admin reset page
    if path in _CHANGE_PW_PATHS:
        from config import load_config
        if load_config().get("FORCE_PASSWORD_RESET", False):
            return await call_next(request)

    # API key authentication (for scripts / curl)
    api_key = request.headers.get("X-API-Key", "")
    if api_key:
        expected = os.environ.get("API_KEY", "")
        if expected and secrets.compare_digest(api_key.encode(), expected.encode()):
            return await call_next(request)
        return JSONResponse({"detail": "Invalid API key"}, status_code=401)

    # Embed token authentication — GET only, scoped to the embeddable pages, requires embed=true
    is_embed_path = path in _EMBED_PATHS or any(path.startswith(p) for p in _EMBED_PATH_PREFIXES)
    if (
        request.method == "GET"
        and is_embed_path
        and request.query_params.get("embed", "").lower() == "true"
        and verify_embed_token(request.query_params.get("embed_token", ""))
    ):
        return await call_next(request)

    # Session cookie authentication
    token = request.cookies.get(COOKIE_NAME, "")
    if not token or not verify_session_token(token):
        next_path = request.url.path
        return RedirectResponse(f"/login?next={next_path}", status_code=302)

    # Force password change when only the default "changeme" plaintext password is set
    if (
        path not in _CHANGE_PW_PATHS
        and not os.environ.get("DASHBOARD_PASSWORD_HASH")
        and os.environ.get("DASHBOARD_PASSWORD") == "changeme"
    ):
        return RedirectResponse("/change-password", status_code=302)

    return await call_next(request)


app.mount("/assets", StaticFiles(directory="assets"), name="assets")
app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(api_router)
app.include_router(page_router)


if __name__ == "__main__":
    logger.info(f"Starting Quantamental Web Server at {SERVER_URL}:{PORT}...")
    uvicorn.run(app, host="0.0.0.0", port=PORT, timeout_graceful_shutdown=15)
