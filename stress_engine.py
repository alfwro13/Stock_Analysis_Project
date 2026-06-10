import datetime
import logging
from typing import Dict, List, Optional

from config import load_config
from database import get_connection

logger = logging.getLogger(__name__)

# Sector multipliers represent how much more/less than the market a sector moved during
# the crash. 1.0 = moved with the market; 1.7 = 70% worse; -0.6 = actually gained
# (e.g. Energy in 2022). Formula: holding_drop = market_drop × beta × sector_mult.
SCENARIOS: Dict[str, Dict] = {
    "gfc_2008": {
        "name": "Global Financial Crisis (2008–09)",
        "market_drop": -0.57,
        "duration_days": 517,
        "recovery_months": 49,
        "sector_multipliers": {
            "Financial Services": 1.8,
            "Real Estate": 1.6,
            "Consumer Cyclical": 1.3,
            "Technology": 1.1,
            "Energy": 1.0,
            "Consumer Defensive": 0.6,
            "Healthcare": 0.7,
            "Utilities": 0.6,
        },
        "description": "The worst financial crash since 1929, driven by the US subprime mortgage collapse.",
    },
    "covid_2020": {
        "name": "COVID-19 Crash (Feb–Mar 2020)",
        "market_drop": -0.34,
        "duration_days": 33,
        "recovery_months": 5,
        "sector_multipliers": {
            "Energy": 1.9,
            "Industrials": 1.4,
            "Financial Services": 1.3,
            "Consumer Cyclical": 1.2,
            "Real Estate": 1.2,
            "Technology": 0.7,
            "Healthcare": 0.6,
            "Consumer Defensive": 0.5,
        },
        "description": "The fastest 30% decline in S&P 500 history, driven by pandemic lockdowns.",
    },
    "inflation_2022": {
        "name": "Inflation & Rate Shock (2022)",
        "market_drop": -0.25,
        "duration_days": 282,
        "recovery_months": 12,
        "sector_multipliers": {
            "Technology": 1.7,
            "Consumer Cyclical": 1.5,
            "Communication Services": 1.6,
            "Real Estate": 1.4,
            "Financial Services": 0.9,
            "Energy": -0.6,
            "Healthcare": 0.8,
            "Utilities": 1.0,
        },
        "description": "Aggressive Fed tightening crushed growth stocks while energy surged.",
    },
    "dotcom_2000": {
        "name": "Dot-com Crash (2000–02)",
        "market_drop": -0.49,
        "duration_days": 929,
        "recovery_months": 84,
        "sector_multipliers": {
            "Technology": 2.2,
            "Communication Services": 1.9,
            "Consumer Cyclical": 1.1,
            "Industrials": 1.0,
            "Financial Services": 0.9,
            "Energy": 0.5,
            "Consumer Defensive": 0.4,
            "Healthcare": 0.5,
            "Utilities": 0.6,
        },
        "description": "The collapse of the internet bubble wiped 49% off the S&P and 78% off the NASDAQ over 2.5 years.",
    },
    "custom": {
        "name": "Custom Scenario",
        "market_drop": None,
        "duration_days": None,
        "recovery_months": None,
        "sector_multipliers": {},
        "description": "Define your own market shock — no sector-specific multipliers applied.",
    },
}


def _primary_sector(holding: Dict) -> str:
    sectors = holding.get("sectors") or []
    if not sectors:
        return "Unknown"
    best = max(sectors, key=lambda s: float(s.get("weight") or 0))
    return best.get("name") or "Unknown"


def _get_betas(tickers: List[str], conn) -> Dict[str, Optional[float]]:
    if not tickers:
        return {}
    placeholders = ",".join("?" * len(tickers))
    rows = conn.execute(
        f"SELECT ticker, beta FROM xray_risk_cache WHERE ticker IN ({placeholders})",
        tickers,
    ).fetchall()
    return {row["ticker"]: row["beta"] for row in rows}


def run_stress_test(
    account_id: str,
    scenario_id: str,
    custom_drop: Optional[float] = None,
) -> Dict:
    if scenario_id not in SCENARIOS:
        raise ValueError(f"Unknown scenario: {scenario_id!r}")

    scenario = dict(SCENARIOS[scenario_id])

    if scenario_id == "custom":
        if custom_drop is None:
            raise ValueError("custom_drop is required for the custom scenario.")
        custom_pct = float(custom_drop)
        scenario["market_drop"] = custom_pct
        scenario["name"] = f"Custom Scenario ({custom_pct * 100:+.1f}%)"
        scenario["duration_days"] = None
        scenario["recovery_months"] = None

    market_drop: float = scenario["market_drop"]
    sector_mults: Dict[str, float] = scenario.get("sector_multipliers") or {}

    config = load_config()
    active_ids: List[str] = config.get("GHOSTFOLIO_ACCOUNTS", {}).get("active", [])
    base_currency: str = config.get("BASE_CURRENCY", "GBP")

    if account_id == "all":
        scope_ids = active_ids
    elif account_id in active_ids:
        scope_ids = [account_id]
    else:
        scope_ids = active_ids

    if not scope_ids:
        raise RuntimeError("No active Ghostfolio accounts configured.")

    from xray_engine import GhostfolioXRayClient
    client = GhostfolioXRayClient()
    if not client.is_configured:
        raise RuntimeError("Ghostfolio is not configured (check GHOSTFOLIO_URL / GHOSTFOLIO_TOKEN).")
    if not client.authenticate():
        raise RuntimeError("Ghostfolio authentication failed.")

    holdings, total_value = client.get_holdings(scope_ids)
    if not holdings:
        raise RuntimeError("No holdings returned from Ghostfolio.")

    tickers = [h["symbol"] for h in holdings]
    beta_map: Dict[str, Optional[float]] = {}
    conn = None
    try:
        conn = get_connection()
        beta_map = _get_betas(tickers, conn)
    except Exception as e:
        logger.warning("Failed to read xray_risk_cache: %s", e)
    finally:
        if conn:
            conn.close()

    missing_beta: List[str] = []
    holding_rows: List[Dict] = []
    sector_impact_map: Dict[str, Dict] = {}

    for h in sorted(holdings, key=lambda x: x["value"], reverse=True):
        symbol = h["symbol"]
        beta_raw = beta_map.get(symbol)
        if beta_raw is None:
            missing_beta.append(symbol)
            beta = 1.0
        else:
            beta = float(beta_raw)

        sector = _primary_sector(h)
        sector_mult = float(sector_mults.get(sector, 1.0))

        holding_drop = market_drop * beta * sector_mult
        holding_drop = max(holding_drop, -0.95)

        monetary_loss = h["value"] * holding_drop

        holding_rows.append({
            "symbol": symbol,
            "name": h["name"],
            "weight": round(h["weight"], 4),
            "value": round(h["value"], 2),
            "beta": round(beta, 3),
            "sector": sector,
            "sector_multiplier": round(sector_mult, 2),
            "estimated_drop_pct": round(holding_drop * 100, 2),
            "estimated_loss": round(monetary_loss, 2),
        })

        if sector not in sector_impact_map:
            sector_impact_map[sector] = {"sector": sector, "weight": 0.0, "estimated_loss": 0.0}
        sector_impact_map[sector]["weight"] = round(
            sector_impact_map[sector]["weight"] + h["weight"], 4
        )
        sector_impact_map[sector]["estimated_loss"] += monetary_loss

    holding_rows.sort(key=lambda r: r["estimated_loss"])

    total_loss = sum(r["estimated_loss"] for r in holding_rows)
    total_loss_pct = (total_loss / total_value * 100) if total_value > 0 else 0.0

    sector_impact = sorted(
        [
            {
                "sector": v["sector"],
                "weight": round(v["weight"], 4),
                "estimated_loss": round(v["estimated_loss"], 2),
            }
            for v in sector_impact_map.values()
        ],
        key=lambda x: x["estimated_loss"],
    )

    data_warnings: List[str] = []
    if missing_beta:
        suffix = (
            f" +{len(missing_beta) - 5} more" if len(missing_beta) > 5 else ""
        )
        data_warnings.append(
            f"Beta not cached for {len(missing_beta)} holding(s) "
            f"({', '.join(missing_beta[:5])}{suffix}) — assumed β=1.0. "
            "Run the X-ray engine to populate the risk cache."
        )

    scenario_out = {k: v for k, v in scenario.items() if v is not None}

    return {
        "scenario": scenario_out,
        "scenario_id": scenario_id,
        "account_id": account_id,
        "portfolio_value": round(total_value, 2),
        "portfolio_currency": base_currency,
        "estimated_loss": round(total_loss, 2),
        "estimated_loss_pct": round(total_loss_pct, 2),
        "holdings": holding_rows,
        "sector_impact": sector_impact,
        "data_warnings": data_warnings,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
