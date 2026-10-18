from typing import Any

from market_pulse import get_all_cached_pulse, get_pulse_index_tickers
from regime_engine import crash_veto_stress_threshold, current_hmm_regime, macro_conditions_snapshot, market_stress_snapshot

from ..envelope import NIGHTLY_STALE_AFTER_SECONDS, date_epoch, dated_part, envelope, iso_utc
from ..server import READ_ONLY, server

_REGIME_UNITS = {
    "market_regime.label": "Market Regime (HMM): Bull, Chop or Crash, fitted on the S&P 500 (SPY)",
    "market_regime.probability": "fraction 0-1: model confidence in the label",
    "market_regime.last_change": "the most recent label change within the last 60 stored days; null when the label has not changed in that window",
    "market_regime.turbulence_class": "Normal, Volatile or Crash from the EWMA realized-volatility Turbulence Index of the S&P 500 (us) and FTSE 100 (uk); a different classifier from the HMM label",
    "market_stress.score": "0-1 Isolation Forest anomaly score across VIX, high-yield credit, 10Y yield and S&P 500 (higher is more abnormal)",
    "market_stress.crash_veto_threshold": "score at or above which the Meta Score vetoes every signal (Settings, default 0.75)",
    "market_stress.features": "the six inputs of the score: VIX level and its ratio to its average, HYG and SPY returns in percent, 10Y yield change in basis points, S&P 500 volatility z-score",
    "macro_threat.*.threat_level": "GREEN, YELLOW or RED from 3-day yield velocity and yield level; US uses the 10Y (^TNX), UK the gilt",
    "macro_threat.*.yield_velocity_bps": "basis points: yield change over about 3 days",
    "macro_threat.*.yield_pct": "percent: the sovereign yield behind the level (us 10Y, us 30Y, uk gilt)",
    "macro_threat.us.dollar_index": "index points: the US Dollar Index (DXY); context only, it does not feed the threat level",
    "macro_threat.uk.gbp_usd": "US dollars per pound (GBP/USD); context only, it does not feed the threat level",
    "treasury_auctions.healthy": "false when any of the recent auctions fired a weak-demand alert; null when there were no auctions in the last 30 days (not the same as healthy)",
    "treasury_auctions.recent": "up to the 6 latest auctions of the last 30 days; bid_to_cover is a ratio, tail_bp basis points",
    "fear_greed.value": "0-100: CNN-style Fear & Greed index. Extreme Fear below 25, Fear below 45, Neutral below 56, Greed below 76, Extreme Greed otherwise",
    "market_pulse.tiles": "the Market Pulse tiles in registry order, each in its own instrument units (index points, a rate, a price, a yield in percent); change_pct is percent since the previous regular close",
    "market_pulse.tiles.extended": "pre- or post-market move, kept apart from price and change_pct; null outside extended hours",
    "<part>.as_of": "dated parts: the data date (YYYY-MM-DD); Market Pulse: UTC time of the oldest tile price. stale is true when a dated part is older than 5 days (a missed nightly run) or a Market Pulse tile is past its refresh interval; treasury_auctions never ages (auctions are irregular, as_of is the latest one)",
}
_MISSING_REGIME = "Market Regime (HMM) has not been computed yet"
_MISSING_STRESS = "Market Stress score has not been computed yet"
_MISSING_MACRO = "Macro threat levels have not been computed yet"
_MISSING_AUCTIONS = "Treasury auction health is unknown: no auctions in the last 30 days"
_MISSING_FEAR_GREED = "Fear & Greed has not been fetched yet (the Market Sentiment refresh has not run since the app started)"
_NO_TILES = "No Market Pulse tile has a cached quote yet"


def _regime_part(missing, epochs):
    hmm = current_hmm_regime()
    current = hmm["current"]
    if current is None:
        missing.append(_MISSING_REGIME)
        return None
    for key, label in (("us_regime_label", "US"), ("uk_regime_label", "UK")):
        if current[key] is None:
            missing.append(f"{label} turbulence class is not available for {current['as_of']}")
    epoch = date_epoch(current["as_of"])
    epochs.append(epoch)
    return dated_part(epoch, {
        "label": current["label"], "probability": current["probability"],
        "turbulence_class": {"us": current["us_regime_label"], "uk": current["uk_regime_label"]},
        "last_change": hmm["last_change"],
    }, as_of=current["as_of"])


def _stress_part(missing, epochs):
    current = market_stress_snapshot()["current"]
    if current is None:
        missing.append(_MISSING_STRESS)
        return None
    epoch = date_epoch(current["date"])
    epochs.append(epoch)
    return dated_part(epoch, {
        "score": current["score"], "crash_veto_threshold": crash_veto_stress_threshold(), "features": current["features"],
    }, as_of=current["date"])


def _macro_parts(macro, missing, epochs):
    if macro["as_of"] is None:
        missing.append(_MISSING_MACRO)
        threat = None
    else:
        epoch = date_epoch(macro["as_of"])
        epochs.append(epoch)
        for value, label in (
            (macro["tyx_close"], "US 30Y yield"), (macro["dxy_close"], "US Dollar Index (DXY)"),
            (macro["uk_threat_level"], "UK gilt yield and threat level"), (macro["gbpusd_close"], "GBP/USD"),
        ):
            if value is None:
                missing.append(f"{label} is not available for {macro['as_of']}")
        threat = dated_part(epoch, {
            "us": {"threat_level": macro["us_threat_level"], "yield_velocity_bps": macro["us_yield_velocity"],
                   "yield_pct": {"10y": macro["tnx_close"], "30y": macro["tyx_close"]}, "dollar_index": macro["dxy_close"]},
            "uk": {"threat_level": macro["uk_threat_level"], "yield_velocity_bps": macro["uk_yield_velocity"],
                   "yield_pct": macro["uk_gilt_close"], "gbp_usd": macro["gbpusd_close"]},
        }, as_of=macro["as_of"])

    auctions = macro["treasury_auction"]
    if auctions["healthy"] is None:
        missing.append(_MISSING_AUCTIONS)
        treasury = None
    else:
        treasury = dated_part(
            None, {"healthy": auctions["healthy"], "recent": auctions["recent"]}, as_of=auctions["recent"][0]["auction_date"], ages=False,
        )

    fear_greed = macro["fear_greed"]
    if fear_greed["value"] is None:
        missing.append(_MISSING_FEAR_GREED)
        sentiment = None
    else:
        epoch = date_epoch(fear_greed["as_of"])
        epochs.append(epoch)
        sentiment = dated_part(epoch, {"value": fear_greed["value"], "label": fear_greed["label"]}, as_of=fear_greed["as_of"])
    return threat, treasury, sentiment


def _pulse_part(missing, epochs):
    cached = get_all_cached_pulse()
    tiles = []
    tile_epochs = []
    for ticker, name in get_pulse_index_tickers().items():
        row = cached.get(ticker)
        if row is None or row["price"] is None:
            missing.append(f"Market Pulse {ticker}: no cached quote")
            continue
        if row["price_as_of"] is None:
            missing.append(f"Market Pulse {ticker}: quote time is not known")
        else:
            tile_epochs.append(row["price_as_of"])
        tiles.append({
            "ticker": ticker, "name": name, "price": row["price"], "change_pct": row["change_pct"],
            "as_of": None if row["price_as_of"] is None else iso_utc(row["price_as_of"]),
            "stale": row["is_stale"],
            "extended": None if row["extended_price"] is None else {
                "session": row["extended_session"], "price": row["extended_price"], "change_pct": row["extended_change_pct"],
            },
        })
    if not tiles:
        missing.append(_NO_TILES)
        return None
    oldest = min(tile_epochs) if tile_epochs else None
    epochs.append(oldest)
    return {"as_of": None if oldest is None else iso_utc(oldest), "stale": any(t["stale"] for t in tiles), "tiles": tiles}


@server.tool(name="get_market_regime", annotations=READ_ONLY)
def get_market_regime() -> dict[str, Any]:
    """Market-wide context: Market Regime (HMM) with US/UK turbulence class, Market Stress score, US/UK sovereign-yield threat levels, Treasury auction health, Fear & Greed and the Market Pulse tiles. Each part has its own as_of and stale flag; a part that is not available is null and listed in missing."""
    missing: list[str] = []
    epochs: list = []
    regime = _regime_part(missing, epochs)
    stress = _stress_part(missing, epochs)
    threat, treasury, sentiment = _macro_parts(macro_conditions_snapshot(), missing, epochs)
    pulse = _pulse_part(missing, epochs)
    known = [e for e in epochs if e is not None]
    return envelope(
        {
            "market_regime": regime, "market_stress": stress, "macro_threat": threat,
            "treasury_auctions": treasury, "fear_greed": sentiment, "market_pulse": pulse,
        },
        as_of=min(known) if known else None, units=_REGIME_UNITS, missing=missing,
        stale_after_seconds=NIGHTLY_STALE_AFTER_SECONDS,
    )
