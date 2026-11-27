from datetime import datetime, timedelta, timezone

from time_engine import fmt_datetime

from . import store
from .client import configured_routes
from .pipeline import RunResult

LAYOUT_HINT = "Google may have changed the quote page layout (the selectors at the top of news/gfetcher/parse.py)."


def _when(dt) -> str:
    return fmt_datetime(dt) if dt else "unknown"


def run_notices(result: RunResult) -> list[tuple[str, str]]:
    """(message_type, text) pairs worth a notification after a run; a normal run gives none, and each event is reported once, except crashed downloads, which every affected run reports."""
    notices = []
    if result.status == "refused":
        notices.append(("Warning", f"Google News Fetcher: Google refused every route ({result.refused_reason}); "
                                   f"no request is sent until {_when(result.cooldown_until)}."))
    else:
        for refusal in result.refused_routes:
            notices.append(("Warning", f"Google News Fetcher: Google refused {refusal['route']} ({refusal['reason']}); "
                                       f"it is paused until {_when(refusal['until'])} and the run carried on over the other route."))
    if result.status == "unreachable":
        notices.append(("Error", "Google News Fetcher: no route to Google could connect "
                                 f"({', '.join(result.unreachable_routes) or 'none'}); check the network and the IPv6 address in Advanced Network."))
    if result.status == "budget_spent" and result.google_requests:
        notices.append(("Warning", "Google News Fetcher: the daily request budget is spent; requests resume as the 24-hour window frees them."))
    if result.layout_suspects:
        notices.append(("Warning", f"Google News Fetcher: a valid page showed no news three runs in a row for "
                                   f"{', '.join(result.layout_suspects)}. {LAYOUT_HINT}"))
    if result.crashed_downloads:
        notices.append(("Error", f"Google News Fetcher: {result.crashed_downloads} article downloads failed unexpectedly and "
                                 f"none returned text; {result.articles_held} are left for a later run. See app.log."))
    for route in result.recovered_routes:
        notices.append(("Info", f"Google News Fetcher: {route} answered Google requests again after its pause."))
    return notices


def health_status(now: datetime | None = None) -> dict:
    """Health of the fetcher for the Settings tile: routes and their cooldowns, the last 24 hours of requests, stored articles, publisher rules."""
    now = now or datetime.now(timezone.utc)
    states = store.get_states()
    routes = []
    for route in configured_routes():
        state = states.get(route.name)
        cooling = bool(state and state.cooldown_until and state.cooldown_until > now)
        routes.append({
            "route": route.name,
            "cooling": cooling,
            "cooldown_until": state.cooldown_until.isoformat() if cooling else None,
            "cooldown_reason": state.cooldown_reason if cooling else None,
            "consecutive_refusals": state.consecutive_refusals if state else 0,
            "last_success_at": state.last_success_at.isoformat() if state and state.last_success_at else None,
        })
    since = now - timedelta(days=1)
    requests = store.request_counts_since(since)
    return {
        "routes": routes,
        "requests_24h": sum(r["n"] for r in requests),
        "refusals_24h": sum(r["n"] for r in requests if r["outcome"] in ("rate_limited", "captcha")),
        "requests_by_route_24h": requests,
        "articles": store.google_article_counts(int(since.timestamp())),
        "publishers": store.publisher_policy_counts(),
    }
