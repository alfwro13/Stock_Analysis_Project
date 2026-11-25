# GUI name: "Google News Fetcher". Canonical scheduled-job names live in scheduler_manifest.JOB_GRAPH.
from .pipeline import FetchSettings, RunResult, run_fetch

__all__ = ["FetchSettings", "RunResult", "run_fetch"]
