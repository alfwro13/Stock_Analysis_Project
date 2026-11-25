# GUI name: "Google News Fetcher". Canonical scheduled-job names live in scheduler_manifest.JOB_GRAPH.
import logging

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse

from api.deps import _error_500, job_busy_response, limiter
from config import load_config
from news.gfetcher import health_status
from scheduler_engine import get_all_job_last_runs, run_gfetcher_job, run_manual_job

logger = logging.getLogger(__name__)

gfetcher_router = APIRouter()

JOB_ID = "gfetcher_job"
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
