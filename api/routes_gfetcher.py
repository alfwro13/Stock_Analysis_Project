# GUI name: "Google News Fetcher". Canonical scheduled-job names live in scheduler_manifest.JOB_GRAPH.
import logging
import re
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Path, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from api.deps import _error_500, job_busy_response, limiter
from config import load_config
from constants import GFETCHER_DOMAIN_MAX_CHARS
from news.gfetcher import health_status, publishers
from news.gfetcher.store import list_symbols
from scheduler_engine import get_all_job_last_runs, run_gfetcher_job, run_gfetcher_probe, run_manual_job

logger = logging.getLogger(__name__)

gfetcher_router = APIRouter()

JOB_ID = "gfetcher_job"
PROBE_ID = "gfetcher_probe_source"
DOMAIN_PATTERN = r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
DISABLED_MESSAGE = "The Google News Fetcher is disabled. Enable it in Settings > News & RSS and save first."


def _enabled() -> bool:
    return bool(load_config().get("SCHEDULING", {}).get("GFETCHER", {}).get("ENABLED", False))


@gfetcher_router.get("/gfetcher/status")
def get_gfetcher_status():
    try:
        last = get_all_job_last_runs().get(JOB_ID) or {}
        return JSONResponse(content={
            "status": "success",
            "enabled": _enabled(),
            "last_run": last.get("last_run"),
            "last_status": last.get("last_status"),
            **health_status(),
        })
    except Exception as e:
        return _error_500(e)


@gfetcher_router.post("/gfetcher/run-now")
@limiter.limit("6/minute")
def run_gfetcher_now(request: Request, background_tasks: BackgroundTasks):
    busy = job_busy_response(JOB_ID)
    if busy:
        return busy
    if not _enabled():
        return JSONResponse(status_code=409, content={"status": "disabled", "message": DISABLED_MESSAGE})
    background_tasks.add_task(run_manual_job, JOB_ID, run_gfetcher_job)
    return JSONResponse(content={"status": "success", "message": "Google News Fetcher run queued."})


class PublisherRuleBody(BaseModel):
    domain: str = Field(max_length=GFETCHER_DOMAIN_MAX_CHARS)
    policy: Literal["allow", "block_paywall", "block_bots"]


def _clean_domain(value: str) -> str:
    domain = value.strip().lower().rstrip(".")
    return domain[4:] if domain.startswith("www.") else domain


def _invalid_domain() -> JSONResponse:
    return JSONResponse(status_code=422, content={"status": "error", "message": "Enter a domain name such as example.com."})


@gfetcher_router.get("/gfetcher/symbols")
def get_gfetcher_symbols():
    try:
        return JSONResponse(content={"status": "success", "symbols": list_symbols()})
    except Exception as e:
        return _error_500(e)


@gfetcher_router.post("/gfetcher/probe")
@limiter.limit("3/minute")
def probe_gfetcher_symbols(request: Request, background_tasks: BackgroundTasks):
    busy = job_busy_response(PROBE_ID) or job_busy_response(JOB_ID)
    if busy:
        return busy
    if not _enabled():
        return JSONResponse(status_code=409, content={"status": "disabled", "message": DISABLED_MESSAGE})
    background_tasks.add_task(run_manual_job, PROBE_ID, run_gfetcher_probe)
    return JSONResponse(content={"status": "success", "message": "Symbol probe queued; the result arrives as a notification."})


@gfetcher_router.get("/gfetcher/publishers")
def get_gfetcher_publishers():
    try:
        return JSONResponse(content={"status": "success", "publishers": publishers.list_rules(datetime.now(timezone.utc))})
    except Exception as e:
        return _error_500(e)


@gfetcher_router.post("/gfetcher/publishers")
@limiter.limit("30/minute")
def set_gfetcher_publisher_rule(request: Request, body: PublisherRuleBody):
    domain = _clean_domain(body.domain)
    if not re.fullmatch(DOMAIN_PATTERN, domain):
        return _invalid_domain()
    try:
        row = publishers.set_rule(domain, body.policy, datetime.now(timezone.utc))
        return JSONResponse(content={"status": "success", "message": f"Rule saved for {domain}.", "publisher": row})
    except Exception as e:
        return _error_500(e)


@gfetcher_router.delete("/gfetcher/publishers/{domain}")
@limiter.limit("30/minute")
def clear_gfetcher_publisher_rule(request: Request, domain: str = Path(max_length=GFETCHER_DOMAIN_MAX_CHARS)):
    domain = _clean_domain(domain)
    if not re.fullmatch(DOMAIN_PATTERN, domain):
        return _invalid_domain()
    try:
        row = publishers.clear_rule(domain, datetime.now(timezone.utc))
        if row is None:
            return JSONResponse(status_code=404, content={"status": "error", "message": "No rule for that domain."})
        return JSONResponse(content={"status": "success", "message": f"{domain} follows what its downloads teach again.", "publisher": row})
    except Exception as e:
        return _error_500(e)
