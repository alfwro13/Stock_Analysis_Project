import logging
import os
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

from config import PORT, SERVER_URL
from auth import COOKIE_NAME, verify_session_token
from api_routes import limiter
from database import init_db
from scheduler_engine import start_scheduler, shutdown_scheduler, reload_scheduler

from api_routes import api_router
from page_routes import page_router
from data_engine import run_yfinance_smoke_test

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing application lifecycle...")
    init_db()
    run_yfinance_smoke_test()
    start_scheduler()
    reload_scheduler()
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
    cookie_httponly=False,           # JS must be able to read it
    cookie_samesite="lax",
)

# Paths that never require a session
_EXEMPT = {"/login", "/api/login"}
_EXEMPT_PREFIXES = ("/static/", "/assets/", "/rss/")

# Paths accessible with a valid session even when password is still default
_CHANGE_PW_PATHS = {"/change-password", "/api/change-password"}


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path

    # Static files and login routes are always public
    if path in _EXEMPT or any(path.startswith(p) for p in _EXEMPT_PREFIXES):
        return await call_next(request)

    # API key authentication (for scripts / curl)
    api_key = request.headers.get("X-API-Key", "")
    if api_key:
        expected = os.environ.get("API_KEY", "")
        if expected and secrets.compare_digest(api_key.encode(), expected.encode()):
            return await call_next(request)
        return JSONResponse({"detail": "Invalid API key"}, status_code=401)

    # Session cookie authentication
    token = request.cookies.get(COOKIE_NAME, "")
    if not token or not verify_session_token(token):
        next_path = request.url.path
        return RedirectResponse(f"/login?next={next_path}", status_code=302)

    # Force password change on first login
    if path not in _CHANGE_PW_PATHS and os.environ.get("DASHBOARD_PASSWORD") == "changeme":
        return RedirectResponse("/change-password", status_code=302)

    return await call_next(request)


app.mount("/assets", StaticFiles(directory="assets"), name="assets")
app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(api_router)
app.include_router(page_router)


if __name__ == "__main__":
    logger.info(f"Starting Quantamental Web Server at {SERVER_URL}:{PORT}...")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
