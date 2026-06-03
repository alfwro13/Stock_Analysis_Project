# xray_engine.py
"""
Portfolio X-ray Engine — risk / diagnostics analytics for the X-ray report.

Data tiers
----------
Tier A  Live Ghostfolio data (holdings, sector look-through, performance chart)
Tier C  yfinance via APScheduler → SQLite cache (beta, vol, correlation, VaR)

Benchmark: SWDA.L (iShares MSCI World) — fetched independently from Yahoo Finance.
           Never assumed to be a holding in the user's portfolio.
"""

import json
import logging
import datetime
import numpy as np
import pandas as pd
import requests
import yfinance as yf
import urllib3
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote

from config import GHOSTFOLIO_URL, GHOSTFOLIO_TOKEN, PORTFOLIO_PATH, load_config
from database import get_connection

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

BENCHMARK_SYMBOL = "SWDA.L"   # iShares MSCI World — always fetched independently
LOOKBACK_DAYS = 252            # 1-year trailing window
AMBER_THRESHOLD = 0.10         # position weight colouring
RED_THRESHOLD = 0.20

# Centralised glossary — single source of truth for all X-ray tooltips.
# Serialised into the API response so the frontend never hard-codes definitions.
XRAY_TOOLTIPS: Dict[str, str] = {
    "beta": (
        "Portfolio Beta: measures how sensitive your portfolio is to market moves. "
        "A beta of 1.4 means if the market drops 10% you'd expect to lose ~14%. "
        "Below 1 = less volatile than market; above 1 = more volatile."
    ),
    "vol": (
        "Annualised Volatility: the typical magnitude of your portfolio's annual price swings, "
        "derived from daily returns over the past year. "
        "Higher = larger day-to-day price changes."
    ),
    "max_drawdown": (
        "Max Drawdown: the worst peak-to-trough decline over the measured period. "
        "Shows the most you would have lost had you bought at the peak and held through the trough."
    ),
    "var": (
        "Value at Risk (95%, 1-day, parametric): on a typical bad day "
        "(the worst 5% of trading days, assuming returns are normally distributed), "
        "your portfolio could reasonably lose this amount or more."
    ),
    "hhi": (
        "HHI (Herfindahl-Hirschman Index): concentration score from 0 to 1. "
        "Below 0.15 = well diversified; 0.15–0.25 = moderately concentrated; "
        "above 0.25 = highly concentrated. Computed from position weights squared and summed."
    ),
    "top5": (
        "Top-5 Concentration: the percentage of your portfolio held in the five largest positions. "
        "High concentration amplifies both upside and downside from single positions."
    ),
    "avg_correlation": (
        "Average Pairwise Correlation (Diversification Score): the mean correlation between all "
        "pairs of holdings from trailing daily returns. "
        "Closer to 1 = holdings move in lockstep (limited diversification benefit). "
        "Closer to 0 = holdings move more independently (stronger diversification)."
    ),
    "dividend_yield": (
        "Weighted Dividend Yield: the blended income yield across all holdings, "
        "weighted by each holding's share of invested capital. "
        "Accumulating funds and non-dividend payers contribute 0. Cash excluded."
    ),
    "projected_income": (
        "Projected Annual Income: estimated total dividends over the next 12 months "
        "based on current per-holding yields and portfolio values. "
        "Accumulating funds show 0. Cash excluded."
    ),
    "sector_lookthrough": (
        "True Sector Exposure (look-through): ETFs are decomposed into their underlying "
        "sector weights so that, for example, a 30% tech-heavy global ETF contributes "
        "30% × its tech weight to your true technology exposure."
    ),
    "instrument_type": (
        "Instrument Type: top-level split by what you directly hold — equities, ETFs, "
        "commodities, etc. See 'True Sector Exposure' for the look-through of what ETFs contain."
    ),
    "weights_note": "All percentages = % of invested capital (cash excluded).",
    "benchmark": (
        f"Beta and correlation are computed against {BENCHMARK_SYMBOL} (iShares MSCI World ETF), "
        f"a globally-diversified equity benchmark. "
        f"It is always fetched independently from Yahoo Finance — never assumed to be in your portfolio."
    ),
}


# ---------------------------------------------------------------------------
# Ghostfolio client
# ---------------------------------------------------------------------------

class GhostfolioXRayClient:
    """Minimal Ghostfolio REST client scoped to X-ray data fetches."""

    def __init__(self) -> None:
        self.url: str = GHOSTFOLIO_URL.rstrip("/")
        self.token: str = GHOSTFOLIO_TOKEN
        self.headers: Dict[str, str] = {}
        self.is_configured: bool = bool(self.url and self.token)

    def authenticate(self) -> bool:
        try:
            resp = requests.post(
                f"{self.url}/api/v1/auth/anonymous",
                json={"accessToken": self.token},
                verify=False, timeout=10,
            )
            resp.raise_for_status()
            bearer = resp.json().get("authToken")
            if not bearer:
                logger.error("Ghostfolio X-ray auth: no authToken in response.")
                return False
            self.headers = {"Authorization": f"Bearer {bearer}"}
            return True
        except Exception as e:
            logger.error(f"Ghostfolio X-ray auth failed: {e}")
            return False

    def get_holdings(self, account_ids: List[str]) -> Tuple[List[Dict], float]:
        """
        Fetches /api/v1/portfolio/details for the given account scope.
        Filters cash, computes de-cashed weights from summed valueInBaseCurrency.
        Returns (holdings_list, total_invested_value).

        IMPORTANT: never call without explicit account_ids — passing the full active
        list avoids leaking excluded accounts (e.g. aggregate-only pension accounts).
        """
        accounts_param = ",".join(account_ids)
        url = (
            f"{self.url}/api/v1/portfolio/details"
            f"?accounts={accounts_param}&range=max&withMarkets=true&withSummary=true"
        )
        try:
            resp = requests.get(url, headers=self.headers, verify=False, timeout=30)
            resp.raise_for_status()
            raw_holdings: Dict = resp.json().get("holdings", {})
        except Exception as e:
            logger.error(f"Ghostfolio portfolio/details failed: {e}")
            return [], 0.0

        holdings: List[Dict] = []
        for symbol, h in raw_holdings.items():
            asset_class = (h.get("assetClass") or "").upper()
            # Exclude cash entries (CASH asset class OR bare 3-letter currency codes)
            if asset_class == "CASH":
                continue
            if len(symbol) <= 4 and symbol.isalpha() and symbol.isupper():
                continue
            value = float(h.get("valueInBaseCurrency") or 0)
            if value <= 0:
                continue

            holdings.append({
                "symbol": symbol,
                "name": h.get("name") or symbol,
                "asset_class": asset_class,
                "asset_sub_class": (h.get("assetSubClass") or "").upper(),
                "currency": h.get("currency") or "",
                "data_source": h.get("dataSource") or "YAHOO",
                "value": value,
                "investment": float(h.get("investment") or 0),
                "quantity": float(h.get("quantity") or 0),
                "market_price": float(h.get("marketPrice") or 0),
                "gross_perf": float(h.get("grossPerformance") or 0),
                "gross_perf_pct": float(h.get("grossPerformancePercent") or 0),
                "sectors": h.get("sectors") or [],
                "countries": h.get("countries") or [],
                "weight": 0.0,
            })

        # De-cash portfolio total — computed from sum of in-scope holdings only.
        # Do NOT trust summary.totalValueInBaseCurrency (contaminated by excluded accounts).
        total_value = sum(h["value"] for h in holdings)
        if total_value > 0:
            for h in holdings:
                h["weight"] = h["value"] / total_value

        return holdings, total_value

    def get_performance_chart(self, account_ids: List[str]) -> List[Dict]:
        """
        Returns [{date, value}...] from portfolio performance endpoint.
        Tries v2 (returns netWorth) then falls back to v1.
        """
        accounts_param = ",".join(account_ids)
        for version in ("v2", "v1"):
            url = (
                f"{self.url}/api/{version}/portfolio/performance"
                f"?range=max&accounts={accounts_param}"
            )
            try:
                resp = requests.get(url, headers=self.headers, verify=False, timeout=30)
                if resp.status_code != 200:
                    continue
                chart = resp.json().get("chart", [])
                result = []
                for pt in chart:
                    val = float(pt.get("netWorth") or pt.get("value") or 0)
                    if val > 0:
                        result.append({"date": pt.get("date", ""), "value": val})
                if result:
                    return result
            except Exception as e:
                logger.warning(f"Performance chart ({version}) failed: {e}")
        return []

    def get_dividend_yield(self, data_source: str, symbol: str) -> Dict:
        """Returns {dividend_yield_pct, dividend_in_base_currency} for one holding."""
        encoded_sym = quote(symbol, safe="")
        url = f"{self.url}/api/v1/portfolio/holding/{data_source}/{encoded_sym}"
        try:
            resp = requests.get(url, headers=self.headers, verify=False, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            return {
                "dividend_yield_pct": float(data.get("dividendYieldPercent") or 0),
                "dividend_in_base_currency": float(data.get("dividendInBaseCurrency") or 0),
            }
        except Exception:
            return {"dividend_yield_pct": 0.0, "dividend_in_base_currency": 0.0}


# ---------------------------------------------------------------------------
# Risk computer (Tier C — yfinance + SQLite cache)
# ---------------------------------------------------------------------------

class XRayRiskComputer:
    """
    Computes beta, annualised vol, and pairwise correlation from yfinance daily returns.
    Writes results to SQLite via INSERT OR REPLACE (idempotent).

    The benchmark (SWDA.L) is always fetched as a standalone ticker regardless of
    whether it appears in the user's portfolio.
    """

    def _fetch_returns(self, symbols: List[str]) -> pd.DataFrame:
        """Downloads 1-year adjusted daily close returns via yfinance."""
        if not symbols:
            return pd.DataFrame()
        try:
            raw = yf.download(
                symbols, period="1y", auto_adjust=True,
                progress=False, threads=True,
            )
            if raw.empty:
                return pd.DataFrame()
            if isinstance(raw.columns, pd.MultiIndex):
                prices = raw["Close"]
            else:
                # Single ticker — yfinance returns flat columns
                prices = raw[["Close"]].rename(columns={"Close": symbols[0]})
            # Drop cols that are entirely NaN (failed fetches)
            prices = prices.dropna(axis=1, how="all")
            return prices.pct_change().dropna(how="all")
        except Exception as e:
            logger.error(f"yfinance download failed: {e}")
            return pd.DataFrame()

    def _compute_beta(
        self, asset_rets: pd.Series, bench_rets: pd.Series
    ) -> Optional[float]:
        """Beta = Cov(r_asset, r_benchmark) / Var(r_benchmark). Requires ≥30 observations."""
        aligned = pd.concat([asset_rets, bench_rets], axis=1).dropna()
        if len(aligned) < 30:
            return None
        cov = aligned.cov()
        var_bench = float(cov.iloc[1, 1])
        if var_bench == 0:
            return None
        return float(cov.iloc[0, 1] / var_bench)

    def _compute_vol(self, returns: pd.Series) -> Optional[float]:
        """Annualised daily vol = std(daily_returns) × √252."""
        clean = returns.dropna()
        if len(clean) < 10:
            return None
        return float(clean.std() * np.sqrt(252))

    def compute_and_cache(self, holdings: List[Dict]) -> bool:
        """
        Main entry point for the APScheduler job.
        1. Assembles portfolio symbol list from holdings.
        2. Fetches daily returns for all portfolio tickers + benchmark (independently).
        3. Computes per-ticker beta vs SWDA.L and annualised vol.
        4. Computes the full pairwise correlation matrix for the portfolio.
        5. Writes all results to SQLite with INSERT OR REPLACE.
        """
        portfolio_symbols = list({h["symbol"] for h in holdings if h.get("symbol")})
        if not portfolio_symbols:
            logger.warning("X-ray risk compute: no portfolio symbols found.")
            return False

        # Benchmark is always fetched independently — not assumed to be a holding
        all_symbols = list(set(portfolio_symbols + [BENCHMARK_SYMBOL]))
        logger.info(
            f"X-ray risk compute: fetching {len(all_symbols)} symbols "
            f"(incl. benchmark {BENCHMARK_SYMBOL})"
        )

        returns_df = self._fetch_returns(all_symbols)
        if returns_df.empty:
            logger.error("X-ray risk compute: yfinance returned empty data.")
            return False

        bench_rets: Optional[pd.Series] = (
            returns_df[BENCHMARK_SYMBOL]
            if BENCHMARK_SYMBOL in returns_df.columns
            else None
        )
        if bench_rets is None:
            logger.warning(
                f"Benchmark {BENCHMARK_SYMBOL} not available from yfinance — "
                "beta will be None for all tickers."
            )

        today = datetime.date.today().isoformat()
        conn = None
        try:
            conn = get_connection()

            for sym in portfolio_symbols:
                if sym not in returns_df.columns:
                    logger.warning(f"No returns data for {sym} — skipping.")
                    continue
                asset_rets = returns_df[sym]
                beta = self._compute_beta(asset_rets, bench_rets) if bench_rets is not None else None
                vol = self._compute_vol(asset_rets)
                conn.execute(
                    """INSERT OR REPLACE INTO xray_risk_cache
                       (ticker, benchmark, last_updated, beta, annualized_vol)
                       VALUES (?, ?, ?, ?, ?)""",
                    (sym, BENCHMARK_SYMBOL, today, beta, vol),
                )

            # Correlation matrix for all portfolio tickers with available returns
            available = [s for s in portfolio_symbols if s in returns_df.columns]
            if len(available) >= 2:
                corr_df = returns_df[available].dropna(how="all").corr()
                conn.execute(
                    """INSERT OR REPLACE INTO xray_correlation_matrix
                       (benchmark, last_updated, tickers_json, matrix_json)
                       VALUES (?, ?, ?, ?)""",
                    (
                        BENCHMARK_SYMBOL, today,
                        json.dumps(available),
                        json.dumps(corr_df.values.tolist()),
                    ),
                )

            conn.commit()
            logger.info(
                f"X-ray risk cache updated: {len(available)} tickers, "
                f"benchmark {BENCHMARK_SYMBOL}."
            )
            return True

        except Exception as e:
            logger.error(f"X-ray risk cache write failed: {e}")
            if conn:
                conn.rollback()
            return False
        finally:
            if conn:
                conn.close()


# ---------------------------------------------------------------------------
# Dividend cache helper (APScheduler job use only)
# ---------------------------------------------------------------------------

def cache_xray_dividends(holdings: List[Dict], client: GhostfolioXRayClient) -> None:
    """
    Fetches dividend yield per holding via Ghostfolio and writes to xray_dividend_cache.
    One HTTP call per holding — must only be called from the scheduler job, never on page load.
    """
    today = datetime.date.today().isoformat()
    conn = None
    try:
        conn = get_connection()
        for h in holdings:
            sym = h["symbol"]
            ds = h.get("data_source") or "YAHOO"
            div = client.get_dividend_yield(ds, sym)
            conn.execute(
                """INSERT OR REPLACE INTO xray_dividend_cache
                   (ticker, data_source, last_updated, dividend_yield_pct, dividend_in_base_currency)
                   VALUES (?, ?, ?, ?, ?)""",
                (sym, ds, today, div["dividend_yield_pct"], div["dividend_in_base_currency"]),
            )
        conn.commit()
        logger.info(f"X-ray dividend cache updated for {len(holdings)} holdings.")
    except Exception as e:
        logger.error(f"X-ray dividend cache write failed: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()


# ---------------------------------------------------------------------------
# APScheduler entry point
# ---------------------------------------------------------------------------

def run_xray_precompute() -> bool:
    """
    APScheduler job entry point. Runs daily after market close.
    1. Reads portfolio tickers from portfolio.json.
    2. Computes and caches beta / vol / correlation via yfinance.
    3. Fetches and caches per-holding dividend yields via Ghostfolio.
    """
    logger.info("X-ray pre-compute job started.")
    try:
        with open(PORTFOLIO_PATH) as f:
            portfolio_json: Dict = json.load(f)
    except Exception as e:
        logger.error(f"X-ray pre-compute: could not read portfolio.json — {e}")
        return False

    holdings = [
        {"symbol": data["ticker"], "data_source": "YAHOO", "currency": ""}
        for data in portfolio_json.values()
        if data.get("ticker")
    ]
    if not holdings:
        logger.warning("X-ray pre-compute: portfolio.json has no tickers.")
        return False

    risk_ok = XRayRiskComputer().compute_and_cache(holdings)

    # Dividend cache — requires a live Ghostfolio call; treat failure as non-fatal
    try:
        client = GhostfolioXRayClient()
        if client.is_configured and client.authenticate():
            config = load_config()
            active_ids: List[str] = config.get("GHOSTFOLIO_ACCOUNTS", {}).get("active", [])
            if active_ids:
                live_holdings, _ = client.get_holdings(active_ids)
                if live_holdings:
                    cache_xray_dividends(live_holdings, client)
    except Exception as e:
        logger.warning(f"X-ray dividend cache step failed (non-fatal): {e}")

    logger.info(f"X-ray pre-compute finished. Risk cache: {'OK' if risk_ok else 'FAILED'}.")
    return risk_ok


# ---------------------------------------------------------------------------
# Helpers for report assembly
# ---------------------------------------------------------------------------

def _get_instrument_type(asset_class: str, asset_sub_class: str) -> str:
    sub = (asset_sub_class or "").upper()
    cls = (asset_class or "").upper()
    if sub == "ETF" or cls == "ETF":
        return "ETF"
    if cls == "EQUITY" or sub == "STOCK":
        return "Equity"
    if cls == "COMMODITY" or sub == "COMMODITY":
        return "Commodity"
    if cls == "FIXED_INCOME":
        return "Fixed Income"
    if cls:
        return cls.title()
    return "Other"


def _compute_max_drawdown(chart: List[Dict]) -> Optional[float]:
    """Peak-to-trough max drawdown from [{date, value}...] series."""
    values = [pt["value"] for pt in chart if (pt.get("value") or 0) > 0]
    if len(values) < 2:
        return None
    peak = values[0]
    max_dd = 0.0
    for v in values:
        if v > peak:
            peak = v
        dd = (v - peak) / peak
        if dd < max_dd:
            max_dd = dd
    return float(max_dd)


# ---------------------------------------------------------------------------
# Main report assembler (called by the /api/xray endpoint)
# ---------------------------------------------------------------------------

def assemble_xray_report(account_id: str) -> Dict:
    """
    Assembles the full X-ray report JSON.

    Combines live Ghostfolio data (Tier A: holdings, allocations, performance chart)
    with SQLite-cached risk stats (Tier C: beta, vol, correlation, VaR).

    account_id: "all" for global (all active accounts) or a Ghostfolio account UUID.
    """
    config = load_config()
    active_ids: List[str] = config.get("GHOSTFOLIO_ACCOUNTS", {}).get("active", [])
    base_currency: str = config.get("BASE_CURRENCY", "GBP")

    # Resolve scope using the app's active list — never use Ghostfolio's isExcluded flag
    if account_id == "all":
        scope_ids = active_ids
    elif account_id in active_ids:
        scope_ids = [account_id]
    else:
        scope_ids = active_ids  # fallback to global for unknown IDs

    if not scope_ids:
        raise RuntimeError("No active Ghostfolio accounts configured.")

    client = GhostfolioXRayClient()
    if not client.is_configured:
        raise RuntimeError("Ghostfolio is not configured (check GHOSTFOLIO_URL / GHOSTFOLIO_TOKEN).")
    if not client.authenticate():
        raise RuntimeError("Ghostfolio authentication failed.")

    holdings, total_value = client.get_holdings(scope_ids)
    if not holdings:
        raise RuntimeError("No holdings returned from Ghostfolio (all accounts may be empty or cash-only).")

    holdings_sorted = sorted(holdings, key=lambda h: h["weight"], reverse=True)

    # --- Concentration metrics ---------------------------------------------------
    top5_weight = sum(h["weight"] for h in holdings_sorted[:5])
    top10_weight = sum(h["weight"] for h in holdings_sorted[:10])
    hhi = sum(h["weight"] ** 2 for h in holdings)

    # --- Sector allocation (look-through) ----------------------------------------
    sector_map: Dict[str, float] = {}
    for h in holdings:
        for sec in h.get("sectors") or []:
            name = sec.get("name") or "Unknown"
            sector_map[name] = (
                sector_map.get(name, 0.0) + float(sec.get("weight") or 0) * h["weight"]
            )
    sector_allocation = sorted(
        [{"name": k, "weight": round(v, 4)} for k, v in sector_map.items()],
        key=lambda x: x["weight"], reverse=True,
    )

    # --- Instrument type allocation -----------------------------------------------
    instrument_map: Dict[str, float] = {}
    for h in holdings:
        itype = _get_instrument_type(h["asset_class"], h["asset_sub_class"])
        instrument_map[itype] = instrument_map.get(itype, 0.0) + h["weight"]
    asset_class_allocation = sorted(
        [{"name": k, "weight": round(v, 4)} for k, v in instrument_map.items()],
        key=lambda x: x["weight"], reverse=True,
    )

    # --- Geographic allocation (by continent) ------------------------------------
    geo_map: Dict[str, float] = {}
    for h in holdings:
        for country in h.get("countries") or []:
            continent = country.get("continent") or "Unknown"
            geo_map[continent] = (
                geo_map.get(continent, 0.0) + float(country.get("weight") or 0) * h["weight"]
            )
    geographic_allocation = sorted(
        [{"name": k, "weight": round(v, 4)} for k, v in geo_map.items()],
        key=lambda x: x["weight"], reverse=True,
    )

    # --- Load cached risk stats --------------------------------------------------
    risk_cache: Dict[str, Dict] = {}
    corr_tickers: List[str] = []
    corr_matrix: List[List[float]] = []
    cache_date: Optional[str] = None
    div_cache: Dict[str, Dict] = {}

    conn = None
    try:
        conn = get_connection()

        rows = conn.execute(
            "SELECT ticker, beta, annualized_vol, last_updated "
            "FROM xray_risk_cache WHERE benchmark = ?",
            (BENCHMARK_SYMBOL,),
        ).fetchall()
        dates = []
        for row in rows:
            risk_cache[row["ticker"]] = {
                "beta": row["beta"],
                "vol": row["annualized_vol"],
            }
            if row["last_updated"]:
                dates.append(row["last_updated"])
        if dates:
            cache_date = max(dates)

        corr_row = conn.execute(
            "SELECT tickers_json, matrix_json, last_updated "
            "FROM xray_correlation_matrix WHERE benchmark = ?",
            (BENCHMARK_SYMBOL,),
        ).fetchone()
        if corr_row:
            corr_tickers = json.loads(corr_row["tickers_json"])
            corr_matrix = json.loads(corr_row["matrix_json"])

        div_rows = conn.execute(
            "SELECT ticker, dividend_yield_pct, dividend_in_base_currency "
            "FROM xray_dividend_cache"
        ).fetchall()
        div_cache = {
            row["ticker"]: {
                "yield_pct": float(row["dividend_yield_pct"] or 0),
                "income": float(row["dividend_in_base_currency"] or 0),
            }
            for row in div_rows
        }
    finally:
        if conn:
            conn.close()

    # Enrich sorted holdings with cached risk + dividend data
    for h in holdings_sorted:
        sym = h["symbol"]
        rc = risk_cache.get(sym, {})
        h["beta"] = rc.get("beta")
        h["vol"] = rc.get("vol")
        dc = div_cache.get(sym, {})
        h["dividend_yield_pct"] = dc.get("yield_pct", 0.0)
        h["dividend_income"] = dc.get("income", 0.0)

    # --- Portfolio-level risk metrics from cached per-ticker data + live weights --
    portfolio_beta: Optional[float] = None
    portfolio_vol: Optional[float] = None
    var_95_1d: Optional[float] = None
    avg_pairwise_corr: Optional[float] = None

    beta_pairs = [(h["weight"], h["beta"]) for h in holdings_sorted if h.get("beta") is not None]
    if beta_pairs:
        portfolio_beta = round(sum(w * b for w, b in beta_pairs), 3)

    # Portfolio vol = sqrt(w^T * Sigma * w), Sigma_ij = vol_i * vol_j * rho_ij (daily)
    if len(corr_tickers) >= 2 and corr_matrix:
        w_list: List[float] = []
        dv_list: List[float] = []
        for sym in corr_tickers:
            h_match = next((h for h in holdings_sorted if h["symbol"] == sym), None)
            w = h_match["weight"] if h_match else 0.0
            ann_vol = (risk_cache.get(sym) or {}).get("vol") or 0.0
            w_list.append(w)
            dv_list.append(ann_vol / np.sqrt(252) if ann_vol else 0.0)

        w_arr = np.array(w_list)
        dv_arr = np.array(dv_list)
        corr_arr = np.array(corr_matrix)

        if np.any(dv_arr > 0) and np.sum(w_arr) > 0:
            sigma_daily = np.outer(dv_arr, dv_arr) * corr_arr
            port_var_daily = float(w_arr @ sigma_daily @ w_arr)
            if port_var_daily > 0:
                port_daily_vol = np.sqrt(port_var_daily)
                portfolio_vol = round(float(port_daily_vol * np.sqrt(252)), 4)
                # VaR = daily_vol * z_0.95 * portfolio_value (parametric, absolute loss)
                var_95_1d = round(float(port_daily_vol * 1.6449 * total_value), 2)

        n = len(corr_matrix)
        if n >= 2:
            off_diag = [
                corr_matrix[i][j]
                for i in range(n) for j in range(n)
                if i != j and corr_matrix[i][j] is not None
            ]
            if off_diag:
                avg_pairwise_corr = round(float(np.mean(off_diag)), 3)

    # --- Max drawdown from Ghostfolio performance chart --------------------------
    max_drawdown: Optional[float] = None
    try:
        perf_chart = client.get_performance_chart(scope_ids)
        if perf_chart:
            max_drawdown = _compute_max_drawdown(perf_chart)
            if max_drawdown is not None:
                max_drawdown = round(max_drawdown, 4)
    except Exception as e:
        logger.warning(f"Max drawdown computation failed: {e}")

    # --- Income ------------------------------------------------------------------
    weighted_div_yield = round(
        sum(h["weight"] * (h.get("dividend_yield_pct") or 0) for h in holdings_sorted), 4
    )
    projected_annual_income = round(
        sum(h.get("dividend_income") or 0 for h in holdings_sorted), 2
    )

    # --- Data warnings -----------------------------------------------------------
    data_warnings: List[str] = []
    if not risk_cache:
        data_warnings.append(
            "Risk metrics (beta, volatility, correlation) are not yet available. "
            "Trigger the X-ray risk cache job from Settings → Scheduler, "
            "or wait for the next scheduled run (daily at 19:00)."
        )
    elif cache_date:
        try:
            days_old = (
                datetime.date.today() - datetime.date.fromisoformat(cache_date)
            ).days
            if days_old > 3:
                data_warnings.append(
                    f"Risk metrics are {days_old} days old — "
                    "the scheduler may not have run recently."
                )
        except Exception:
            pass
    uncovered = [h["symbol"] for h in holdings_sorted if h.get("beta") is None and risk_cache]
    if uncovered:
        data_warnings.append(
            f"{len(uncovered)} holding(s) not in risk cache "
            f"(possibly added since last run): "
            + ", ".join(uncovered[:5])
            + (" …" if len(uncovered) > 5 else "")
        )

    # --- Final payload -----------------------------------------------------------
    holdings_payload = [
        {
            "symbol": h["symbol"],
            "name": h["name"],
            "weight": round(h["weight"], 4),
            "value": round(h["value"], 2),
            "asset_class": h["asset_class"],
            "asset_sub_class": h["asset_sub_class"],
            "instrument_type": _get_instrument_type(h["asset_class"], h["asset_sub_class"]),
            "currency": h["currency"],
            "gross_perf": round(h["gross_perf"], 2),
            "gross_perf_pct": round(h["gross_perf_pct"] * 100, 2),
            "sectors": h["sectors"],
            "countries": h["countries"],
            "beta": round(h["beta"], 3) if h.get("beta") is not None else None,
            "vol": round(h["vol"], 4) if h.get("vol") is not None else None,
            "dividend_yield_pct": h.get("dividend_yield_pct", 0.0),
            "dividend_income": h.get("dividend_income", 0.0),
        }
        for h in holdings_sorted
    ]

    return {
        "account_id": account_id,
        "generated_at": datetime.datetime.now().isoformat(),
        "portfolio_total_value": round(total_value, 2),
        "base_currency": base_currency,
        "holdings": holdings_payload,
        "concentration": {
            "hhi": round(hhi, 4),
            "top5_weight": round(top5_weight, 4),
            "top10_weight": round(top10_weight, 4),
            "amber_threshold": AMBER_THRESHOLD,
            "red_threshold": RED_THRESHOLD,
        },
        "sector_allocation": sector_allocation,
        "asset_class_allocation": asset_class_allocation,
        "geographic_allocation": geographic_allocation,
        "risk_metrics": {
            "portfolio_beta": portfolio_beta,
            "annualized_vol": portfolio_vol,
            "max_drawdown": max_drawdown,
            "var_95_1d": var_95_1d,
            "avg_pairwise_correlation": avg_pairwise_corr,
            "benchmark": BENCHMARK_SYMBOL,
            "lookback_days": LOOKBACK_DAYS,
            "cache_date": cache_date,
        },
        "income": {
            "weighted_dividend_yield": weighted_div_yield,
            "projected_annual_income": projected_annual_income,
        },
        "correlation_matrix": (
            {"tickers": corr_tickers, "matrix": corr_matrix}
            if len(corr_tickers) >= 2 else None
        ),
        "tooltips": XRAY_TOOLTIPS,
        "data_warnings": data_warnings,
    }
