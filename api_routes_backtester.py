import logging
from typing import List, Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Path as PathParam, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator
from starlette.concurrency import run_in_threadpool

from accounts_engine import list_scope_accounts_with_values
from api_deps import _error_500, limiter
from config import BASE_CURRENCY
from strategy_backtest_data import (
    COST_PRESETS,
    DEFAULT_BENCHMARKS,
    EXTENDED_PERIOD,
    MAX_TICKERS,
    MIN_TICKERS,
    history_status,
    list_backtest_candidates,
    list_shortlist_baskets,
    request_history_preparation,
)
from strategy_backtest_engine import CADENCES, MIN_TEST_SESSIONS
from strategy_backtest_reads import get_allocations, get_run, list_runs
from strategy_backtest_runs import DEFAULTS, MAX_SAVED_RUNS, create_run, delete_run, execute_run
from strategy_backtest_strategies import STRATEGIES

logger = logging.getLogger(__name__)

backtester_router = APIRouter()

RUN_ID_PATTERN = r"^[0-9a-f]{12}$"


class StrategyBacktestRunRequest(BaseModel):
    basket_type: Literal["account", "shortlist"] = "account"
    account_id: str = "all"
    include_tickers: List[str] = Field(default_factory=list, max_length=MAX_TICKERS)
    shortlist_signal: Optional[Literal["ml_upside", "quant_score"]] = None
    shortlist_scope: Optional[Literal["portfolio", "watchlist"]] = None
    currency: Optional[str] = Field(None, max_length=8)
    convert_currency: bool = False
    strategies: List[str] = Field(min_length=1, max_length=len(STRATEGIES))
    cadence: Literal["monthly", "quarterly", "annual"] = DEFAULTS["cadence"]
    lookback: int = Field(DEFAULTS["lookback"], ge=20, le=756)
    initial_capital: float = Field(DEFAULTS["initial_capital"], gt=0, le=1e9)
    cost_preset: Literal["none", "low", "typical", "custom"] = DEFAULTS["cost_preset"]
    commission_bps: float = Field(0.0, ge=0, le=500)
    spread_bps: float = Field(0.0, ge=0, le=500)
    slippage_bps: float = Field(0.0, ge=0, le=500)
    cash_rate: float = Field(DEFAULTS["cash_rate"], ge=-0.05, le=0.25)
    band_pp: float = Field(DEFAULTS["band_pp"], gt=0, le=50)
    max_weight: float = Field(DEFAULTS["max_weight"], gt=0, le=1)
    cash_reserve: float = Field(DEFAULTS["cash_reserve"], ge=0, lt=1)
    benchmark: str = Field("auto", max_length=20)
    history: Literal["standard", "extended"] = DEFAULTS["history"]

    @model_validator(mode="after")
    def _shortlist_needs_list(self):
        if self.basket_type == "shortlist" and not (self.shortlist_signal and self.shortlist_scope):
            raise ValueError("A Stable Shortlist basket needs shortlist_signal and shortlist_scope.")
        return self


class PrepareHistoryRequest(BaseModel):
    tickers: List[str] = Field(min_length=1, max_length=MAX_TICKERS + 1)
    convert_currency: bool = False


@backtester_router.get("/strategy-backtester/meta")
@limiter.limit("30/minute")
def api_backtester_meta(request: Request):
    return JSONResponse(content={
        "status": "success",
        "strategies": [
            {"id": s.id, "label": s.label, "description": s.description, "schedule": s.schedule,
             "needs_current_weights": s.needs_current_weights}
            for s in STRATEGIES.values()
        ],
        "cadences": list(CADENCES), "defaults": DEFAULTS, "cost_presets": COST_PRESETS,
        "default_benchmarks": DEFAULT_BENCHMARKS, "base_currency": BASE_CURRENCY,
        "limits": {
            "max_tickers": MAX_TICKERS, "min_tickers": MIN_TICKERS, "max_saved_runs": MAX_SAVED_RUNS,
            "min_test_sessions": MIN_TEST_SESSIONS, "extended_period": EXTENDED_PERIOD,
        },
    })


@backtester_router.get("/strategy-backtester/accounts")
@limiter.limit("10/minute")
async def api_backtester_accounts(request: Request):
    try:
        accounts, total = await run_in_threadpool(list_scope_accounts_with_values)
        if not accounts:
            return JSONResponse(content={"status": "error", "message": "No accounts with holdings configured."})
        return JSONResponse(content={"status": "success", "accounts": accounts, "total": total})
    except Exception as e:
        return _error_500(e)


@backtester_router.get("/strategy-backtester/candidates")
@limiter.limit("10/minute")
async def api_backtester_candidates(request: Request, account_id: str = "all"):
    """Held tickers (pre-checked) and Watchlist tickers (opt-in) with quote currency and price-history coverage."""
    try:
        return JSONResponse(content=await run_in_threadpool(list_backtest_candidates, account_id))
    except Exception as e:
        return _error_500(e)


@backtester_router.get("/strategy-backtester/shortlists")
@limiter.limit("10/minute")
async def api_backtester_shortlists(request: Request):
    """Latest Stable Shortlist snapshot members that can be backtested as a basket."""
    try:
        return JSONResponse(content={"status": "success", "baskets": await run_in_threadpool(list_shortlist_baskets)})
    except Exception as e:
        return _error_500(e)


@backtester_router.get("/strategy-backtester/history-status")
@limiter.limit("30/minute")
async def api_backtester_history_status(request: Request, tickers: str = Query(..., max_length=2000)):
    try:
        symbols = [t for t in tickers.split(",") if t.strip()][:MAX_TICKERS]
        return JSONResponse(content={"status": "success", "tickers": await run_in_threadpool(history_status, symbols)})
    except Exception as e:
        return _error_500(e)


@backtester_router.post("/strategy-backtester/prepare-history")
@limiter.limit("6/minute")
async def api_backtester_prepare_history(request: Request, req: PrepareHistoryRequest):
    """Starts the background download of extended daily history (separate cache) for the given tickers."""
    try:
        return JSONResponse(content=await run_in_threadpool(request_history_preparation, req.tickers, req.convert_currency))
    except Exception as e:
        return _error_500(e)


@backtester_router.post("/strategy-backtester/run")
@limiter.limit("10/minute")
async def api_backtester_run(request: Request, req: StrategyBacktestRunRequest, background_tasks: BackgroundTasks):
    """Validates the request, saves a queued run and computes it in the background; poll GET /runs/{run_id}."""
    try:
        created = await run_in_threadpool(create_run, req.model_dump())
        if created["status"] == "success":
            background_tasks.add_task(execute_run, created["run_id"])
        return JSONResponse(content=created)
    except Exception as e:
        return _error_500(e)


@backtester_router.get("/strategy-backtester/runs")
@limiter.limit("30/minute")
async def api_backtester_runs(request: Request):
    try:
        return JSONResponse(content={"status": "success", "runs": await run_in_threadpool(list_runs)})
    except Exception as e:
        return _error_500(e)


@backtester_router.get("/strategy-backtester/runs/{run_id}")
@limiter.limit("60/minute")
async def api_backtester_run_detail(request: Request, run_id: str = PathParam(..., pattern=RUN_ID_PATTERN)):
    try:
        return JSONResponse(content=await run_in_threadpool(get_run, run_id))
    except Exception as e:
        return _error_500(e)


@backtester_router.get("/strategy-backtester/runs/{run_id}/allocations")
@limiter.limit("60/minute")
async def api_backtester_run_allocations(
    request: Request, run_id: str = PathParam(..., pattern=RUN_ID_PATTERN),
    strategy: str = Query(..., pattern=r"^[a-z_]{1,30}$"),
):
    try:
        return JSONResponse(content=await run_in_threadpool(get_allocations, run_id, strategy))
    except Exception as e:
        return _error_500(e)


@backtester_router.delete("/strategy-backtester/runs/{run_id}")
@limiter.limit("20/minute")
async def api_backtester_run_delete(request: Request, run_id: str = PathParam(..., pattern=RUN_ID_PATTERN)):
    try:
        return JSONResponse(content=await run_in_threadpool(delete_run, run_id))
    except Exception as e:
        return _error_500(e)
