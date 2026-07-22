# GUI name: "Portfolio Heat Index". Canonical scheduled-job names live in scheduler_manifest.JOB_GRAPH.
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from config import load_config
from database import get_connection
from xray_engine import assemble_xray_report, simulate_scope_with_hypothetical_holding

logger = logging.getLogger(__name__)

TIER_GREEN = "GREEN"
TIER_YELLOW = "YELLOW"
TIER_RED = "RED"

SCOPE_ALL = "all"


def _sub_score(value: Optional[float], yellow: float, red: float) -> float:
    """Normalize a raw metric to 0-100: 0-50 below `yellow`, 50-100 between `yellow` and `red`, clamped at 100 beyond."""
    if value is None:
        return 0.0
    value = abs(value)
    if red <= yellow:
        return 100.0 if value >= yellow else 0.0
    if value <= yellow:
        return 50.0 * (value / yellow) if yellow > 0 else 0.0
    if value >= red:
        return 100.0
    return 50.0 + 50.0 * (value - yellow) / (red - yellow)


def _tier_for(sub_score: float, yellow: float = 40.0, red: float = 75.0) -> str:
    if sub_score >= red:
        return TIER_RED
    if sub_score >= yellow:
        return TIER_YELLOW
    return TIER_GREEN


_TIER_RANK = {TIER_GREEN: 0, TIER_YELLOW: 1, TIER_RED: 2}


def _thresholds(config: dict) -> dict:
    return config.get("SCHEDULING", {}).get("RISK_ORCHESTRATOR", {}).get("THRESHOLDS", {
        "PHI_YELLOW": 40, "PHI_RED": 75,
        "VAR_PCT_YELLOW": 2.0, "VAR_PCT_RED": 4.0,
        "MAX_CORR_YELLOW": 0.5, "MAX_CORR_RED": 0.75,
        "DRAWDOWN_PCT_YELLOW": 5.0, "DRAWDOWN_PCT_RED": 10.0,
    })


def _weights(config: dict) -> dict:
    return config.get("SCHEDULING", {}).get("RISK_ORCHESTRATOR", {}).get("WEIGHTS", {
        "VAR": 0.4, "CORRELATION": 0.3, "DRAWDOWN": 0.3,
    })


def compute_portfolio_heat(scope: str, scope_label: str, config: Optional[dict] = None) -> Optional[dict]:
    """Derives a 0-100 Portfolio Heat Index for `scope` from its X-ray VaR/correlation/drawdown."""
    config = config or load_config()
    thresholds = _thresholds(config)
    weights = _weights(config)

    try:
        report = assemble_xray_report(scope)
    except RuntimeError as e:
        logger.warning("Risk Orchestrator: skipping scope %s (%s)", scope, e)
        return None

    risk_metrics = report.get("risk_metrics", {})
    total_value = report.get("portfolio_total_value") or 0.0
    var_95_1d = risk_metrics.get("var_95_1d")
    var_pct_of_equity = round(var_95_1d / total_value * 100, 3) if var_95_1d and total_value else None
    max_correlation = risk_metrics.get("max_pairwise_correlation")
    drawdown_raw = risk_metrics.get("max_drawdown")
    drawdown_pct = round(abs(drawdown_raw) * 100, 3) if drawdown_raw is not None else None

    var_sub = _sub_score(var_pct_of_equity, thresholds["VAR_PCT_YELLOW"], thresholds["VAR_PCT_RED"])
    corr_sub = _sub_score(max_correlation, thresholds["MAX_CORR_YELLOW"], thresholds["MAX_CORR_RED"])
    dd_sub = _sub_score(drawdown_pct, thresholds["DRAWDOWN_PCT_YELLOW"], thresholds["DRAWDOWN_PCT_RED"])

    phi_score = round(
        weights["VAR"] * var_sub + weights["CORRELATION"] * corr_sub + weights["DRAWDOWN"] * dd_sub, 2
    )
    phi_score = max(0.0, min(phi_score, 100.0))
    tier = _tier_for(phi_score, thresholds["PHI_YELLOW"], thresholds["PHI_RED"])
    var_tier = _tier_for(var_sub, thresholds["PHI_YELLOW"], thresholds["PHI_RED"])
    correlation_tier = _tier_for(corr_sub, thresholds["PHI_YELLOW"], thresholds["PHI_RED"])
    drawdown_tier = _tier_for(dd_sub, thresholds["PHI_YELLOW"], thresholds["PHI_RED"])

    breakdown = [
        f"VaR (95%, 1d): {var_pct_of_equity}% of equity ({var_tier})" if var_pct_of_equity is not None else "VaR: not available",
        f"Max pairwise correlation: {max_correlation} ({correlation_tier})" if max_correlation is not None else "Max correlation: not available",
        f"Max drawdown: {drawdown_pct}% ({drawdown_tier})" if drawdown_pct is not None else "Max drawdown: not available",
    ]

    return {
        "scope": scope,
        "scope_label": scope_label,
        "phi_score": phi_score,
        "tier": tier,
        "var_pct_of_equity": var_pct_of_equity,
        "var_tier": var_tier,
        "max_correlation": max_correlation,
        "correlation_tier": correlation_tier,
        "drawdown_pct": drawdown_pct,
        "drawdown_tier": drawdown_tier,
        "breakdown_json": json.dumps(breakdown),
        "holdings": report.get("holdings", []),
    }


def compute_ticker_risk_contributions(all_scope_result: dict, config: Optional[dict] = None) -> list:
    """Per-ticker risk tier: marginal VaR contribution, own max correlation, ATR stop distance."""
    config = config or load_config()
    thresholds = _thresholds(config)
    holdings = all_scope_result.get("holdings", [])
    if not holdings:
        return []

    tickers = [h["symbol"] for h in holdings]
    conn = None
    price_map = {}
    stop_map = {}
    try:
        conn = get_connection()
        placeholders = ",".join("?" * len(tickers))
        rows = conn.execute(
            f"SELECT ticker, current_price, atr_stop_loss FROM stock_signals WHERE ticker IN ({placeholders})",
            tickers,
        ).fetchall()
        for row in rows:
            price_map[row["ticker"]] = row["current_price"]
            stop_map[row["ticker"]] = row["atr_stop_loss"]
    finally:
        if conn:
            conn.close()

    total_var_contribution = sum(
        abs(h.get("marginal_risk_contribution") or 0.0) for h in holdings
    ) or None

    out = []
    for h in holdings:
        ticker = h["symbol"]
        mrc = h.get("marginal_risk_contribution")
        mrc_pct = round(abs(mrc) / total_var_contribution * 100, 3) if mrc is not None and total_var_contribution else None
        max_corr = h.get("max_pairwise_correlation")

        current_price = price_map.get(ticker)
        atr_stop = stop_map.get(ticker)
        stop_distance_pct = None
        if current_price and atr_stop and current_price > 0:
            stop_distance_pct = round((current_price - atr_stop) / current_price * 100, 3)

        mrc_sub = _sub_score(mrc_pct, thresholds["VAR_PCT_YELLOW"], thresholds["VAR_PCT_RED"])
        corr_sub = _sub_score(max_corr, thresholds["MAX_CORR_YELLOW"], thresholds["MAX_CORR_RED"])
        # A smaller (or negative) stop distance is riskier — invert so "closer to/through stop" scores hotter.
        stop_sub = _sub_score(
            (thresholds["DRAWDOWN_PCT_RED"] - stop_distance_pct) if stop_distance_pct is not None else None,
            thresholds["DRAWDOWN_PCT_YELLOW"], thresholds["DRAWDOWN_PCT_RED"],
        ) if stop_distance_pct is not None else 0.0

        weights = _weights(config)
        risk_score = round(
            weights["VAR"] * mrc_sub + weights["CORRELATION"] * corr_sub + weights["DRAWDOWN"] * stop_sub, 2
        )
        risk_score = max(0.0, min(risk_score, 100.0))
        tier = _tier_for(risk_score, thresholds["PHI_YELLOW"], thresholds["PHI_RED"])

        out.append({
            "ticker": ticker,
            "risk_score": risk_score,
            "risk_tier": tier,
            "marginal_var_contribution_pct": mrc_pct,
            "max_pairwise_correlation": max_corr,
            "stop_distance_pct": stop_distance_pct,
        })
    return out


def persist_heat_index(result: dict) -> None:
    conn = None
    try:
        conn = get_connection()
        conn.execute(
            """
            INSERT INTO portfolio_heat_index
                (scope, scope_label, phi_score, tier, var_pct_of_equity, var_tier,
                 max_correlation, correlation_tier, drawdown_pct, drawdown_tier,
                 breakdown_json, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(scope) DO UPDATE SET
                scope_label=excluded.scope_label, phi_score=excluded.phi_score, tier=excluded.tier,
                var_pct_of_equity=excluded.var_pct_of_equity, var_tier=excluded.var_tier,
                max_correlation=excluded.max_correlation, correlation_tier=excluded.correlation_tier,
                drawdown_pct=excluded.drawdown_pct, drawdown_tier=excluded.drawdown_tier,
                breakdown_json=excluded.breakdown_json, last_updated=excluded.last_updated
            """,
            (
                result["scope"], result["scope_label"], result["phi_score"], result["tier"],
                result["var_pct_of_equity"], result["var_tier"], result["max_correlation"],
                result["correlation_tier"], result["drawdown_pct"], result["drawdown_tier"],
                result["breakdown_json"], datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        conn.commit()
    finally:
        if conn:
            conn.close()


def persist_ticker_contributions(rows: list) -> None:
    conn = None
    try:
        conn = get_connection()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("DELETE FROM ticker_risk_contribution")
        conn.executemany(
            """
            INSERT INTO ticker_risk_contribution
                (ticker, risk_score, risk_tier, marginal_var_contribution_pct,
                 max_pairwise_correlation, stop_distance_pct, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (r["ticker"], r["risk_score"], r["risk_tier"], r["marginal_var_contribution_pct"],
                 r["max_pairwise_correlation"], r["stop_distance_pct"], now)
                for r in rows
            ],
        )
        conn.commit()
    finally:
        if conn:
            conn.close()


def run_scan() -> dict:
    """Computes and persists the "all" scope PHI, one PHI per Trading account, and ticker tiers."""
    from accounts_engine import list_scope_accounts_with_values

    config = load_config()
    scopes_computed = 0
    scopes_skipped = 0

    all_result = compute_portfolio_heat(SCOPE_ALL, "All Accounts", config)
    if all_result:
        persist_heat_index(all_result)
        scopes_computed += 1
        contributions = compute_ticker_risk_contributions(all_result, config)
        persist_ticker_contributions(contributions)
    else:
        scopes_skipped += 1
        contributions = []

    accounts, _ = list_scope_accounts_with_values()
    for acc in accounts:
        result = compute_portfolio_heat(acc["id"], acc["name"], config)
        if result:
            persist_heat_index(result)
            scopes_computed += 1
        else:
            scopes_skipped += 1

    return {"scopes_computed": scopes_computed, "scopes_skipped": scopes_skipped, "tickers_scored": len(contributions)}


def get_critical_scopes() -> list:
    """Reads back the just-persisted portfolio_heat_index for Pillar C2's critical escalations —
    every scope's PHI tier and max-correlation tier, so the caller can alert on whichever are RED."""
    conn = None
    try:
        conn = get_connection()
        rows = conn.execute(
            "SELECT scope, scope_label, phi_score, tier, max_correlation, correlation_tier FROM portfolio_heat_index"
        ).fetchall()
    finally:
        if conn:
            conn.close()
    return [dict(r) for r in rows]


def _suggest_reduced_value(
    scope: str, ticker: str, additional_value: float, dd_sub: float,
    thresholds: dict, weights: dict, target_rank: int, iterations: int = 6,
) -> Optional[float]:
    """Binary-searches for the largest addition size whose what-if tier is at or below
    `target_rank` (0=GREEN, 1=YELLOW) — an advisory suggestion only, per Pillar A's design
    (this app has no trade execution to actually enforce a smaller size)."""
    lo, hi = 0.0, additional_value
    best: Optional[float] = None
    for _ in range(iterations):
        mid = (lo + hi) / 2
        if mid <= 0:
            break
        sim = simulate_scope_with_hypothetical_holding(scope, ticker, mid)
        var_sub = _sub_score(sim["var_pct_of_equity"], thresholds["VAR_PCT_YELLOW"], thresholds["VAR_PCT_RED"])
        corr_sub = _sub_score(sim["max_pairwise_correlation"], thresholds["MAX_CORR_YELLOW"], thresholds["MAX_CORR_RED"])
        phi_score = max(0.0, min(
            weights["VAR"] * var_sub + weights["CORRELATION"] * corr_sub + weights["DRAWDOWN"] * dd_sub, 100.0
        ))
        tier = _tier_for(phi_score, thresholds["PHI_YELLOW"], thresholds["PHI_RED"])
        if _TIER_RANK[tier] <= target_rank:
            best = mid
            lo = mid
        else:
            hi = mid
    return round(best, 2) if best else None


def evaluate_pretrade_check(
    scope: str, ticker: str, additional_value: float, config: Optional[dict] = None
) -> dict:
    """Pillar A pre-trade gatekeeper: advisory approve/warn/reject verdict for adding
    `additional_value` (BASE_CURRENCY) of `ticker` to `scope`, using the exact same
    normalize/tier thresholds and weights as the passive Portfolio Heat Index (compute_portfolio_heat).
    Max drawdown is reused from the scope's current (unmodified) state — see
    simulate_scope_with_hypothetical_holding()'s docstring for why it isn't re-simulated.
    Raises RuntimeError if the scope has no holdings or Ghostfolio is unreachable, mirroring
    assemble_xray_report()."""
    config = config or load_config()
    thresholds = _thresholds(config)
    weights = _weights(config)

    baseline_report = assemble_xray_report(scope)
    baseline_drawdown_raw = baseline_report.get("risk_metrics", {}).get("max_drawdown")
    drawdown_pct = round(abs(baseline_drawdown_raw) * 100, 3) if baseline_drawdown_raw is not None else None
    dd_sub = _sub_score(drawdown_pct, thresholds["DRAWDOWN_PCT_YELLOW"], thresholds["DRAWDOWN_PCT_RED"])
    dd_tier = _tier_for(dd_sub, thresholds["PHI_YELLOW"], thresholds["PHI_RED"])

    sim = simulate_scope_with_hypothetical_holding(scope, ticker, additional_value)
    var_sub = _sub_score(sim["var_pct_of_equity"], thresholds["VAR_PCT_YELLOW"], thresholds["VAR_PCT_RED"])
    corr_sub = _sub_score(sim["max_pairwise_correlation"], thresholds["MAX_CORR_YELLOW"], thresholds["MAX_CORR_RED"])
    phi_score = round(
        weights["VAR"] * var_sub + weights["CORRELATION"] * corr_sub + weights["DRAWDOWN"] * dd_sub, 2
    )
    phi_score = max(0.0, min(phi_score, 100.0))
    tier = _tier_for(phi_score, thresholds["PHI_YELLOW"], thresholds["PHI_RED"])
    var_tier = _tier_for(var_sub, thresholds["PHI_YELLOW"], thresholds["PHI_RED"])
    correlation_tier = _tier_for(corr_sub, thresholds["PHI_YELLOW"], thresholds["PHI_RED"])

    if tier == TIER_RED:
        verdict = "reject"
    elif tier == TIER_YELLOW:
        verdict = "warn"
    else:
        verdict = "approve"

    breached_constraint = None
    for label, sub_tier in (("VaR", var_tier), ("Correlation", correlation_tier), ("Drawdown", dd_tier)):
        if sub_tier == TIER_RED:
            breached_constraint = label
            break
    if breached_constraint is None:
        for label, sub_tier in (("VaR", var_tier), ("Correlation", correlation_tier), ("Drawdown", dd_tier)):
            if sub_tier == TIER_YELLOW:
                breached_constraint = label
                break

    suggested_reduced_value = None
    if verdict != "approve" and additional_value > 0:
        target_rank = max(0, _TIER_RANK[tier] - 1)
        suggested_reduced_value = _suggest_reduced_value(
            scope, ticker, additional_value, dd_sub, thresholds, weights, target_rank
        )

    return {
        "scope": scope,
        "ticker": ticker,
        "proposed_value": round(additional_value, 2),
        "verdict": verdict,
        "breached_constraint": breached_constraint,
        "phi_score": phi_score,
        "tier": tier,
        "var_pct_of_equity": sim["var_pct_of_equity"],
        "var_tier": var_tier,
        "max_correlation": sim["max_pairwise_correlation"],
        "correlation_tier": correlation_tier,
        "drawdown_pct": drawdown_pct,
        "drawdown_tier": dd_tier,
        "hypothetical_weight": sim["hypothetical_weight"],
        "new_portfolio_total_value": sim["portfolio_total_value"],
        "suggested_reduced_value": suggested_reduced_value,
        "data_warnings": sim["data_warnings"],
    }
