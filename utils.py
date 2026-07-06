# Lightweight helpers with no heavy dependencies — safe to import from any module.
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

MERMAID_VERSION = "10.9.1"
MERMAID_URL = f"https://cdn.jsdelivr.net/npm/mermaid@{MERMAID_VERSION}/dist/mermaid.min.js"


def ensure_workflow_assets() -> None:
    """Fetch the vendored Mermaid bundle on first boot; it is gitignored, not committed."""
    target = os.path.join(os.path.dirname(__file__), "static", "js", "mermaid.min.js")
    if os.path.exists(target) and os.path.getsize(target) > 0:
        return
    try:
        import urllib.request
        with urllib.request.urlopen(MERMAID_URL, timeout=15) as resp:
            data = resp.read()
        with open(target, "wb") as f:
            f.write(data)
        logger.info("Fetched Mermaid %s into static/js/.", MERMAID_VERSION)
    except Exception as e:
        logger.warning("Could not fetch Mermaid bundle (%s); Workflow Monitor graph unavailable until fetched.", e)


def normalize_ticker(ticker: str) -> str:
    return str(ticker).strip().upper()


def clamp_beta(raw: Any, lo: float = 0.5, hi: float = 2.0, default: float = 1.0) -> float:
    """Guards against empty strings / SQLite None for the beta column, which raises ValueError inside float()."""
    try:
        if raw is None:
            return default
        return max(lo, min(hi, float(raw)))
    except (TypeError, ValueError):
        return default


def is_daily_bar_still_forming(last_daily_date: Any, last_live_date: Any) -> bool:
    """True when the daily feed's last date is on/after the live feed's last date — Yahoo's daily endpoint often returns today's still-forming session as the 'close' when queried mid-session."""
    return last_daily_date >= last_live_date
