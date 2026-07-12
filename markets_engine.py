import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import market_pulse
import time_engine
from config import load_config
from database import get_ticker_registry, get_ticker_registry_row_by_exchange, get_registry_spot_future_tickers

logger = logging.getLogger(__name__)

_DYNAMIC_REGIONS = ("US", "Europe", "Asia")
_STATIC_REGION_ORDER = ["Europe", "US", "Asia", "Commodities_FX"]
_TIER_RANK = {"open": 0, "partial": 1, "pre": 2, "post": 3, "closed": 4}

def resolve_benchmark_for_holdings(top_holdings: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Registry index for the most-represented exchange (via time_engine's suffix registry, not a
    second hardcoded map); unrecognised suffixes and exchanges with no registry index are skipped."""
    if not top_holdings:
        return None

    weight_by_exchange: Dict[str, float] = {}
    for holding in top_holdings:
        symbol = holding.get("symbol") or ""
        weight = holding.get("weight") or 0.0
        if "." not in symbol:
            exchange = "NYSE"
        else:
            exchange = time_engine.ticker_exchange_or_none(symbol)
            if exchange is None:
                continue
        weight_by_exchange[exchange] = weight_by_exchange.get(exchange, 0.0) + weight

    for exchange in sorted(weight_by_exchange, key=weight_by_exchange.get, reverse=True):
        row = get_ticker_registry_row_by_exchange(exchange)
        if row:
            return row
    return None


def get_exchange_state(exchange: str) -> str:
    """4-state open/pre/post/closed for one exchange — see
    market_pulse.get_exchange_session_state() (holiday-aware where a proxy ticker exists,
    weekday+hours heuristic otherwise; 'post' only available for proxy-mapped exchanges)."""
    return market_pulse.get_exchange_session_state(exchange)


def get_region_exchanges(region: str) -> List[str]:
    """Derived from the registry, not a second hardcoded map — a new exchange added to a
    region via Settings changes region membership with no code change."""
    rows = get_ticker_registry(enabled_only=True)
    exchanges = {r["exchange"] for r in rows if r["region"] == region and r["exchange"]}
    return sorted(exchanges)


def _seconds_since_open(exchange: str, now_utc: datetime) -> float:
    open_t, _ = time_engine.market_window_utc(exchange)
    open_dt = datetime.combine(now_utc.date(), open_t, tzinfo=timezone.utc)
    return max((now_utc - open_dt).total_seconds(), 0.0)


def _seconds_until_open(exchange: str, now_utc: datetime) -> float:
    """Approximates the next trading day's open using today's UTC open-time-of-day — off by at
    most an hour around a DST transition, which doesn't matter for a UI ordering tie-break."""
    open_t, _ = time_engine.market_window_utc(exchange)
    for offset in range(0, 8):
        candidate_date = now_utc.date() + timedelta(days=offset)
        if candidate_date.weekday() < 5:
            candidate_dt = datetime.combine(candidate_date, open_t, tzinfo=timezone.utc)
            if candidate_dt > now_utc:
                return (candidate_dt - now_utc).total_seconds()
    return 0.0


def get_region_state(region: str) -> Dict[str, Any]:
    """Aggregate open/partial/pre/post/closed state for a region plus a recency_seconds
    tie-break value: seconds since the most-recently-opened constituent exchange opened (when
    any are open), or seconds until the soonest constituent opens (when pre/post/closed). "open"
    requires every constituent exchange to be open; "partial" covers the mixed case (e.g. Hong
    Kong still open while Tokyo has closed for the day) so the region badge doesn't overstate
    how live the section actually is. "post" ranks below "pre" (see _TIER_RANK) since
    after-hours trading is a lower-priority session than the pre-market one still to come."""
    exchanges = get_region_exchanges(region)
    now = datetime.now(timezone.utc)
    if not exchanges:
        return {"state": "open", "recency_seconds": 0.0}

    ex_states = {ex: get_exchange_state(ex) for ex in exchanges}
    open_exchanges = [ex for ex, s in ex_states.items() if s == "open"]
    if open_exchanges:
        recency = min(_seconds_since_open(ex, now) for ex in open_exchanges)
        state = "open" if len(open_exchanges) == len(exchanges) else "partial"
        return {"state": state, "recency_seconds": recency}

    pre_exchanges = [ex for ex, s in ex_states.items() if s == "pre"]
    if pre_exchanges:
        recency = min(_seconds_until_open(ex, now) for ex in pre_exchanges)
        return {"state": "pre", "recency_seconds": recency}

    post_exchanges = [ex for ex, s in ex_states.items() if s == "post"]
    if post_exchanges:
        recency = min(_seconds_until_open(ex, now) for ex in post_exchanges)
        return {"state": "post", "recency_seconds": recency}

    recency = min(_seconds_until_open(ex, now) for ex in exchanges)
    return {"state": "closed", "recency_seconds": recency}


def dynamic_region_order() -> List[str]:
    """US/Europe/Asia ranked open > pre > closed, ties broken by recency (most recently
    opened first within the open tier; soonest-to-open first within pre/closed) — this is what
    flips US above a longer-running Europe session the instant NYSE opens, and Europe above Asia
    during their own overlap window. Commodities_FX is always visible, pinned directly beneath
    whichever region currently ranks first."""
    states = {region: get_region_state(region) for region in _DYNAMIC_REGIONS}
    ordered = sorted(
        _DYNAMIC_REGIONS,
        key=lambda r: (_TIER_RANK[states[r]["state"]], states[r]["recency_seconds"]),
    )
    ordered.insert(1, "Commodities_FX")
    return ordered


def static_region_order() -> List[str]:
    return list(_STATIC_REGION_ORDER)


def resolve_tile(row: Dict[str, Any]) -> Tuple[str, str, bool]:
    """(ticker, display_name, is_future) for one registry row. Spot/future auto-swap: shows
    spot while the row's own exchange is in its regular session, future during pre-market and
    closed — gated on the row's own exchange, not aggregated region state, so it stays precise
    per-ticker even when a region straddles an open/closed tier boundary."""
    if not row.get("future_ticker"):
        return row["ticker"], row["display_name"], False
    if row.get("exchange") and market_pulse.is_exchange_open(row["exchange"], include_premarket=False):
        return row["ticker"], row["display_name"], False
    return row["future_ticker"], row["future_display_name"] or row["display_name"], True


def _rows_by_region(rows: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["region"], []).append(row)
    for region_rows in grouped.values():
        region_rows.sort(key=lambda r: r["sort_order"])
    return grouped


def assemble_markets_payload(view: str) -> Dict[str, Any]:
    """Data for GET /api/markets: registry rows grouped by region in the resolved order, with
    spot/future resolved per tile and live price/sentiment/sparkline pulled from the shared
    market_pulse cache (reused, not re-derived — AGENTS.md rule 16)."""
    view = "static" if view == "static" else "dynamic"
    region_order = static_region_order() if view == "static" else dynamic_region_order()
    region_states = {region: get_region_state(region) for region in set(region_order)}

    rows = get_ticker_registry(enabled_only=True)
    grouped = _rows_by_region(rows)

    resolved_by_region: Dict[str, List[Tuple[Dict[str, Any], str, str, bool]]] = {}
    lookup_tickers: List[str] = []
    for region in region_order:
        resolved = []
        for row in grouped.get(region, []):
            ticker, display_name, is_future = resolve_tile(row)
            resolved.append((row, ticker, display_name, is_future))
            lookup_tickers.append(ticker)
            # Also warm the *other* dual-instrument ticker (spot or future, whichever isn't
            # resolved right now) so a consumer needing both simultaneously (e.g. independent
            # spot + futures sensors in the Home Assistant integration) always has live data for
            # both, not just whichever one this tile currently displays.
            if row.get("future_ticker"):
                lookup_tickers.append(row["ticker"])
                lookup_tickers.append(row["future_ticker"])
        resolved_by_region[region] = resolved

    config_data = load_config()
    refresh_rate = int(config_data.get("UI_PREFERENCES", {}).get("REFRESH_RATE", 60))
    cache_data = market_pulse.get_cached_pulse_from_db(lookup_tickers, refresh_rate)
    cache_by_ticker = {item["ticker"]: item for item in cache_data["indexes"] + cache_data["assets"]}

    regions_payload = []
    for region in region_order:
        tiles = []
        for row, ticker, display_name, is_future in resolved_by_region[region]:
            cached = cache_by_ticker.get(ticker, {})
            is_stale = cached.get("is_stale", True)
            # Own exchange, not the aggregated region state — a Hong Kong tile must read "open"
            # even while its region badge reads "Some Open" because Tokyo has already closed.
            market_state = get_exchange_state(row["exchange"]) if row.get("exchange") else "open"

            dual_instrument = None
            if row.get("future_ticker"):
                spot_cached = cache_by_ticker.get(row["ticker"], {})
                future_cached = cache_by_ticker.get(row["future_ticker"], {})
                dual_instrument = {
                    "spot": {
                        "ticker": row["ticker"], "display_name": row["display_name"],
                        "price": spot_cached.get("price", 0.0),
                        "change_pts": spot_cached.get("change_pts", 0.0),
                        "change_pct": spot_cached.get("change_pct", 0.0),
                        "is_positive": spot_cached.get("is_positive", True),
                        "is_active": not is_future,
                    },
                    "future": {
                        "ticker": row["future_ticker"],
                        "display_name": row.get("future_display_name") or row["display_name"],
                        "price": future_cached.get("price", 0.0),
                        "change_pts": future_cached.get("change_pts", 0.0),
                        "change_pct": future_cached.get("change_pct", 0.0),
                        "is_positive": future_cached.get("is_positive", True),
                        "is_active": is_future,
                    },
                }

            tiles.append({
                "ticker": ticker,
                # Stable identity for consumers that must not churn when resolve_tile() swaps a
                # dual-instrument index between its spot and future ticker across the trading
                # day (e.g. ^GSPC <-> ES=F) — "ticker" above is whichever one is resolved right
                # now, "registry_ticker" is always the registry row's own primary ticker.
                "registry_ticker": row["ticker"],
                "display_name": display_name,
                "region": region,
                "exchange": row.get("exchange"),
                "is_future": is_future,
                "price": cached.get("price", 0.0),
                "currency": row["currency"],
                "change_pts": cached.get("change_pts", 0.0),
                "change_pct": cached.get("change_pct", 0.0),
                "is_positive": cached.get("is_positive", True),
                "invert_color": bool(row["invert_color"]),
                "asset_type": row["asset_type"],
                "sentiment_score": cached.get("sentiment_score"),
                "market_state": market_state,
                # Only flagged as a data problem when the market is supposed to be live —
                # staleness while the market is closed is expected (no new prints), not an error.
                "is_stale": is_stale,
                "stale_data": is_stale and market_state == "open",
                "needs_refresh": cached.get("needs_refresh", True),
                "sparkline": market_pulse.get_intraday_points(ticker),
                "dual_instrument": dual_instrument,
            })
        regions_payload.append({
            "region": region,
            "state": region_states.get(region, {}).get("state", "open"),
            "tiles": tiles,
        })

    return {"view": view, "regions": regions_payload}


def registry_lookup_tickers() -> List[str]:
    """Resolved (spot-or-future, per current session) ticker for every active registry row,
    plus both the spot and future ticker for any row with a dual-instrument pairing, regardless
    of region order — used to warm market_pulse_cache for the whole Markets page (e.g. from the
    Home Assistant refresh-now hook), not just whatever's in view right now. Both tickers of a
    dual-instrument pair are included (not just the resolved one) so consumers needing both
    prices simultaneously — the Home Assistant integration's independent spot/futures sensors —
    always have live data for the one not currently on display."""
    rows = get_ticker_registry(enabled_only=True)
    tickers: List[str] = [resolve_tile(row)[0] for row in rows]
    tickers.extend(get_registry_spot_future_tickers())
    return tickers


def select_pulse_tickers(dynamic: bool, desktop_count: int = 10, mobile_count: int = 8) -> Dict[str, List[str]]:
    """Which tickers render as Market Pulse tiles. Static mode reproduces today's exact
    is_pulse_tile picked list; dynamic mode flattens dynamic_region_order()'s tile ordering,
    mirroring the Markets page's own logic. Mobile is always a sub-filter of the desktop
    selection (is_pulse_mobile=1 rows only), not an independently-ranked list, so the two counts
    never disagree about which tickers are "in scope" today."""
    rows = get_ticker_registry(enabled_only=True)
    rows_by_ticker = {r["ticker"]: r for r in rows}

    if not dynamic:
        pulse_tickers = market_pulse.get_pulse_index_tickers()
        ordered_rows = [rows_by_ticker[t] for t in pulse_tickers.keys() if t in rows_by_ticker]
    else:
        region_order = dynamic_region_order()
        grouped = _rows_by_region(rows)
        ordered_rows = [row for region in region_order for row in grouped.get(region, [])]

    desktop_rows = ordered_rows[:desktop_count]
    desktop = [resolve_tile(row)[0] for row in desktop_rows]
    mobile_rows = [row for row in desktop_rows if row["is_pulse_mobile"]][:mobile_count]
    mobile = [resolve_tile(row)[0] for row in mobile_rows]
    return {"desktop": desktop, "mobile": mobile}
