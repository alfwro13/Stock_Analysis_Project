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
from fastapi.exceptions import RequestValidationError
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager

from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette_csrf import CSRFMiddleware

from config import ALLOWED_HOSTS, BASE_DIR, BIND_HOST, FRAME_ANCESTORS, MCP_PUBLIC_HOST, PORT, SERVER_URL, load_config
from api_deps import API_KEY_ROUTES
from auth import COOKIE_NAME, cookie_secure, verify_session_token, verify_embed_token
from api_routes import limiter
from database import init_db
from scheduler_engine import start_scheduler, shutdown_scheduler, reload_scheduler, resume_interrupted_scans, start_model_compatibility_guard
from log_config import LOG_FORMAT, SafeFormatter, configure_file_logging, uvicorn_log_config
from utils import _request_counts, _request_stages, harden_runtime_permissions

from api_routes import api_router
from page_routes import page_router
from data_engine import run_yfinance_smoke_test
import mcp_server

_console_handler = logging.StreamHandler()
_console_handler.setFormatter(SafeFormatter(LOG_FORMAT))
logging.basicConfig(level=logging.INFO, handlers=[_console_handler])
logger = logging.getLogger(__name__)

configure_file_logging(load_config())

_TRUSTED_HOSTS = sorted({*ALLOWED_HOSTS, "localhost", "127.0.0.1", *([MCP_PUBLIC_HOST] if MCP_PUBLIC_HOST else [])})

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
            locations = []
            while frame is not None and len(locations) < 12:
                filename = os.path.abspath(frame.f_code.co_filename)
                if filename.startswith(project_root):
                    relative = filename[len(project_root):]
                    if not relative.startswith(("venv" + os.sep, ".venv" + os.sep)):
                        locations.append(f"{relative}:{frame.f_code.co_name}:{frame.f_lineno}")
                frame = frame.f_back
            with self._condition:
                if generation == self._generation:
                    self._sample = locations
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
                locations = sampler.sample(generation) or []
                logger.warning(
                    "event_loop_lag lag_ms=%.1f suspected_blocker=%s callers=%s",
                    lag_ms,
                    locations[0] if locations else "unavailable",
                    ">".join(locations[1:]) if len(locations) > 1 else "unavailable",
                )
    finally:
        sampler.stop()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing application lifecycle...")
    harden_runtime_permissions(BASE_DIR)
    init_db()
    from utils import notify_requirements_drift
    notify_requirements_drift()
    run_yfinance_smoke_test()
    start_scheduler()
    reload_scheduler()
    start_model_compatibility_guard()
    threading.Thread(target=resume_interrupted_scans, daemon=True).start()
    lag_task = asyncio.create_task(_watch_event_loop_lag())
    try:
        async with mcp_server.run(_TRUSTED_HOSTS):
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
    path = request.scope["path"]
    route_path = "/stock/{ticker}" if path.startswith("/stock/") and path.count("/") == 2 else path
    if route_path not in _TIMED_ROUTES:
        return await call_next(request)
    started = perf_counter()
    stages = []
    counts = {}
    response = None
    token = _request_stages.set(stages)
    counts_token = _request_counts.set(counts)
    try:
        response = await call_next(request)
        return response
    finally:
        _request_stages.reset(token)
        _request_counts.reset(counts_token)
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
            logging.INFO if duration_ms >= 1000 or counts else logging.DEBUG,
            "request_timing route=%s method=%s status=%s duration_ms=%.1f stages_ms=%s counts=%s",
            route_path, request.method, response.status_code if response else 500, duration_ms,
            ",".join(f"{name}:{value:.1f}" for name, value in sorted(totals.items())),
            ",".join(f"{name}:{value}" for name, value in sorted(counts.items())),
        )


app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


async def _validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # FastAPI's default handler echoes each rejected `input`; an inf/nan there makes JSONResponse raise, turning the 422 into a 500.
    detail = [{key: value for key, value in error.items() if key not in ("input", "ctx")} for error in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": detail})


app.add_exception_handler(RequestValidationError, _validation_error_handler)
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(
    CSRFMiddleware,
    secret=os.environ.get("APP_SECRET_KEY") or secrets.token_hex(32),
    sensitive_cookies={"session"},   # only enforce when a session cookie is present
    exempt_urls=[re.compile(r"^/api/(login|request-password-reset|reset-password)$")],
    cookie_httponly=False,           # JS must be able to read it
    cookie_samesite="lax",
    cookie_secure=cookie_secure(),
)

# Paths that never require a session
_EXEMPT = {"/login", "/api/login", "/reset-password", "/api/request-password-reset", "/api/reset-password"}
_EXEMPT_PREFIXES = ("/static/", "/assets/", "/rss/")

# Paths accessible with a valid session even when password is still default
_CHANGE_PW_PATHS = {"/change-password", "/api/change-password"}

# Pages embeddable via ?embed=true (e.g. Home Assistant iframe) — the embed token only bypasses login here
_EMBED_PATHS = {"/portfolio", "/watchlist"}
_EMBED_PATH_PREFIXES = ("/stock/",)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.scope["path"]

    # Static files, login, and password-reset routes are always public
    if path in _EXEMPT or any(path.startswith(p) for p in _EXEMPT_PREFIXES):
        return await call_next(request)

    if path == mcp_server.MCP_PATH or path.startswith(mcp_server.MCP_PATH + "/"):
        rejection = await run_in_threadpool(mcp_server.auth_failure, request)
        return rejection if rejection is not None else await call_next(request)

    # The OAuth routes authenticate themselves and answer 404 on any host but MCP_PUBLIC_HOST
    if path in mcp_server.OAUTH_PATHS:
        return await call_next(request)

    # API key: only the routes in API_KEY_ROUTES, so a leaked key cannot read pages or settings
    api_key = request.headers.get("X-API-Key", "")
    if api_key:
        expected = os.environ.get("API_KEY", "")
        if not (expected and secrets.compare_digest(api_key.encode(), expected.encode())):
            return JSONResponse({"detail": "Invalid API key"}, status_code=401)
        if (request.method, path) not in API_KEY_ROUTES:
            return JSONResponse({"detail": "The API key cannot be used on this route"}, status_code=403)
        return await call_next(request)

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
        return RedirectResponse(f"/login?next={path}", status_code=302)

    # Force password change when only the default "changeme" plaintext password is set
    if (
        path not in _CHANGE_PW_PATHS
        and not os.environ.get("DASHBOARD_PASSWORD_HASH")
        and os.environ.get("DASHBOARD_PASSWORD") == "changeme"
    ):
        return RedirectResponse("/change-password", status_code=302)

    return await call_next(request)


app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=_TRUSTED_HOSTS,
)

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-XSS-Protection": "0",
    "X-Robots-Tag": "none",
    "Content-Security-Policy": "; ".join([
        "default-src 'self'",
        "script-src 'self' 'unsafe-inline'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "font-src 'self' data:",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors " + " ".join(["'self'", *FRAME_ANCESTORS]),
    ]),
}


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    for name, value in _SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response


# Registered after every other middleware so it is the outermost layer: no cookie, header or key is read before it.
app.add_middleware(mcp_server.PublicHostGuard, public_host=MCP_PUBLIC_HOST)


app.mount("/assets", StaticFiles(directory="assets"), name="assets")
app.mount("/static", StaticFiles(directory="static"), name="static")

app.add_route(mcp_server.MCP_PATH, mcp_server.endpoint, include_in_schema=False)
for _oauth_path, _oauth_handler, _oauth_methods in mcp_server.OAUTH_ROUTES:
    app.add_route(_oauth_path, _oauth_handler, methods=_oauth_methods, include_in_schema=False)
app.include_router(api_router)
app.include_router(page_router)


if __name__ == "__main__":
    logger.info("Starting Quantamental Web Server at %s:%s...", SERVER_URL, PORT)
    uvicorn.run(app, host=BIND_HOST, port=PORT, timeout_graceful_shutdown=15, log_config=uvicorn_log_config())
