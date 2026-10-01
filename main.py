import asyncio
import logging
import os
import re
import secrets
import sys
import threading
from contextlib import suppress
from time import perf_counter
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
from scheduler_engine import start_scheduler, shutdown_scheduler, reload_scheduler, resume_interrupted_scans, start_model_compatibility_guard
from log_config import configure_file_logging
from utils import _request_stages

from api_routes import api_router
from page_routes import page_router
from data_engine import run_yfinance_smoke_test

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

configure_file_logging(load_config())

_TIMED_ROUTES = {
    "/portfolio", "/watchlist", "/stock/{ticker}",
    "/api/intraday-chart/refresh",
    "/api/accounts/portfolio-totals", "/api/accounts/list-with-metrics",
    "/api/accounts/holdings-list", "/api/accounts/other-accounts-list",
}


class _EventLoopLagSampler:
    def __init__(self, loop_thread_id):
        self._loop_thread_id = loop_thread_id
        self._condition = threading.Condition()
        self._generation = 0
        self._deadline = 0.0
        self._sample = None
        self._stopped = False
        self._thread = threading.Thread(target=self._run, name="event-loop-lag-sampler", daemon=True)

    def start(self):
        self._thread.start()

    def arm(self, deadline):
        with self._condition:
            self._generation += 1
            self._deadline = deadline
            self._sample = None
            self._condition.notify()
            return self._generation

    def sample(self, generation):
        with self._condition:
            return self._sample if generation == self._generation else None

    def stop(self):
        with self._condition:
            self._stopped = True
            self._condition.notify()
        self._thread.join(timeout=1)

    def _run(self):
        project_root = os.path.dirname(os.path.abspath(__file__)) + os.sep
        while True:
            with self._condition:
                while not self._stopped:
                    generation = self._generation
                    if not generation:
                        self._condition.wait()
                        continue
                    remaining = self._deadline + 0.1 - perf_counter()
                    if remaining > 0:
                        self._condition.wait(timeout=remaining)
                        continue
                    break
                if self._stopped:
                    return
            frame = sys._current_frames().get(self._loop_thread_id)
            location = None
            while frame is not None:
                filename = os.path.abspath(frame.f_code.co_filename)
                if filename.startswith(project_root):
                    relative = filename[len(project_root):]
                    if not relative.startswith(("venv" + os.sep, ".venv" + os.sep)):
                        location = f"{relative}:{frame.f_code.co_name}:{frame.f_lineno}"
                        break
                frame = frame.f_back
            with self._condition:
                if generation == self._generation:
                    self._sample = location
                    self._condition.wait_for(lambda: generation != self._generation or self._stopped)


async def _watch_event_loop_lag():
    loop = asyncio.get_running_loop()
    sampler = _EventLoopLagSampler(threading.get_ident())
    sampler.start()
    try:
        while True:
            target = loop.time() + 0.25
            generation = sampler.arm(perf_counter() + 0.25)
            await asyncio.sleep(0.25)
            lag_ms = max(0.0, (loop.time() - target) * 1000)
            if lag_ms >= 100:
                logger.warning(
                    "event_loop_lag lag_ms=%.1f suspected_blocker=%s",
                    lag_ms, sampler.sample(generation) or "unavailable",
                )
    finally:
        sampler.stop()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing application lifecycle...")
    init_db()
    from utils import ensure_workflow_assets, notify_requirements_drift
    ensure_workflow_assets()
    notify_requirements_drift()
    run_yfinance_smoke_test()
    start_scheduler()
    reload_scheduler()
    start_model_compatibility_guard()
    threading.Thread(target=resume_interrupted_scans, daemon=True).start()
    lag_task = asyncio.create_task(_watch_event_loop_lag())
    try:
        yield
    finally:
        lag_task.cancel()
        with suppress(asyncio.CancelledError):
            await lag_task
        shutdown_scheduler()
    logger.info("Application lifecycle terminated safely.")


app = FastAPI(title="Quantamental Dashboard", lifespan=lifespan)


@app.middleware("http")
async def request_timing_middleware(request: Request, call_next):
    path = request.url.path
    route_path = "/stock/{ticker}" if path.startswith("/stock/") and path.count("/") == 2 else path
    if route_path not in _TIMED_ROUTES:
        return await call_next(request)
    started = perf_counter()
    stages = []
    response = None
    token = _request_stages.set(stages)
    try:
        response = await call_next(request)
        return response
    finally:
        _request_stages.reset(token)
        duration_ms = (perf_counter() - started) * 1000
        totals = {}
        for name, elapsed_ms in stages:
            totals[name] = totals.get(name, 0.0) + elapsed_ms
        if response is not None:
            response.headers["Server-Timing"] = ", ".join(
                [f"app;dur={duration_ms:.1f}"]
                + [f"{name};dur={value:.1f}" for name, value in sorted(totals.items())]
            )
        logger.log(
            logging.INFO if duration_ms >= 1000 else logging.DEBUG,
            "request_timing route=%s method=%s status=%s duration_ms=%.1f stages_ms=%s",
            route_path, request.method, response.status_code if response else 500, duration_ms,
            ",".join(f"{name}:{value:.1f}" for name, value in sorted(totals.items())),
        )


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
