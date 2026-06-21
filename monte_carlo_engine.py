import json
import logging

import numpy as np

from config import load_config
from database import get_connection

logger = logging.getLogger(__name__)

ASSET_CLASS_DRIFTS = {
    "Global Equity ETF": 0.07,
    "UK Equity": 0.065,
    "Bond/Fixed Income": 0.035,
}
DEFAULT_DRIFT = 0.06
DEFAULT_VOL = 0.20
N_SIMS = 1000


def _classify_asset_class(asset_class: str, currency: str) -> str:
    ac = (asset_class or "").upper()
    if "FIXED_INCOME" in ac or "BOND" in ac:
        return "Bond/Fixed Income"
    if "EQUITY" in ac and currency == "GBP":
        return "UK Equity"
    return "Global Equity ETF"


def _load_corr_and_vols(benchmark: str = "SPY") -> tuple:
    conn = None
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT tickers_json, matrix_json FROM xray_correlation_matrix WHERE benchmark = ?",
            (benchmark,),
        ).fetchone()
        if not row:
            return {}, {}
        tickers = json.loads(row["tickers_json"])
        matrix = np.array(json.loads(row["matrix_json"]))
        vol_rows = conn.execute(
            "SELECT ticker, annualized_vol FROM xray_risk_cache WHERE benchmark = ?",
            (benchmark,),
        ).fetchall()
        vol_map = {r["ticker"]: r["annualized_vol"] for r in vol_rows if r["annualized_vol"] is not None}
        return {"tickers": tickers, "matrix": matrix}, vol_map
    except Exception as e:
        logger.error("failed to load correlation/vol data: %s", e)
        return {}, {}
    finally:
        if conn:
            conn.close()


def run_simulation(
    portfolio_value: float,
    monthly_contribution: float,
    horizon_years: int,
    target_wealth: float,
    drift_overrides: dict,
    inflation_pct: float,
    n_sims: int = N_SIMS,
    seed: int | None = None,
) -> dict:
    rng = np.random.default_rng(seed)
    overrides = drift_overrides or {}

    try:
        from xray_engine import assemble_xray_report
        xray = assemble_xray_report()
        holdings = [h for h in xray.get("holdings", []) if (h.get("weight") or 0) > 0]
    except Exception as e:
        logger.error("assemble_xray_report failed, using scalar fallback: %s", e)
        holdings = []

    annual_contribution = 12.0 * monthly_contribution
    W = np.zeros((n_sims, horizon_years + 1))
    W[:, 0] = portfolio_value

    if not holdings:
        key = "Global Equity ETF"
        mu = overrides.get(key, ASSET_CLASS_DRIFTS[key] * 100) / 100.0
        sigma = DEFAULT_VOL
        for t in range(horizon_years):
            Z = rng.standard_normal(n_sims)
            W[:, t + 1] = W[:, t] * np.exp((mu - 0.5 * sigma ** 2) + sigma * Z) + annual_contribution
    else:
        tickers = [h["symbol"] for h in holdings]
        weights = np.array([h["weight"] for h in holdings], dtype=float)
        weights /= weights.sum()
        N = len(tickers)

        corr_data, vol_map = _load_corr_and_vols()
        corr_tickers = corr_data.get("tickers", [])
        corr_matrix = corr_data.get("matrix", None)
        corr_idx = {t: i for i, t in enumerate(corr_tickers)}

        sigma = np.array([vol_map.get(t, DEFAULT_VOL) for t in tickers])

        mu = np.array([
            overrides.get(
                _classify_asset_class(h.get("asset_class", ""), h.get("currency", "")),
                ASSET_CLASS_DRIFTS.get(
                    _classify_asset_class(h.get("asset_class", ""), h.get("currency", "")),
                    DEFAULT_DRIFT * 100,
                ),
            ) / 100.0
            for h in holdings
        ])

        sub_C = np.eye(N)
        if corr_matrix is not None:
            for i, ti in enumerate(tickers):
                for j, tj in enumerate(tickers):
                    if ti in corr_idx and tj in corr_idx:
                        sub_C[i, j] = corr_matrix[corr_idx[ti], corr_idx[tj]]

        sub_C += 1e-8 * np.eye(N)
        try:
            L = np.linalg.cholesky(sub_C)
        except np.linalg.LinAlgError:
            logger.error("Cholesky failed, falling back to identity correlation")
            L = np.eye(N)

        log_drift = mu - 0.5 * sigma ** 2

        for t in range(horizon_years):
            Z = rng.standard_normal((n_sims, N)) @ L.T
            log_r = log_drift + sigma * Z
            port_r = np.exp(log_r) @ weights
            W[:, t + 1] = W[:, t] * port_r + annual_contribution

    pct_keys = {"p5": 5, "p25": 25, "p50": 50, "p75": 75, "p95": 95}
    percentiles = {k: np.percentile(W, v, axis=0).tolist() for k, v in pct_keys.items()}

    inflation_deflator = np.array([(1.0 + inflation_pct / 100.0) ** (-t) for t in range(horizon_years + 1)])
    W_real = W * inflation_deflator[np.newaxis, :]
    percentiles_real = {k: np.percentile(W_real, v, axis=0).tolist() for k, v in pct_keys.items()}

    prob_success = float(np.mean(W[:, -1] >= target_wealth)) if target_wealth > 0 else None

    return {
        "status": "success",
        "percentiles": percentiles,
        "percentiles_real": percentiles_real,
        "probability_of_success": prob_success,
        "median_final": float(np.percentile(W[:, -1], 50)),
        "p5_final": float(np.percentile(W[:, -1], 5)),
        "horizon_years": horizon_years,
        "n_simulations": n_sims,
    }
