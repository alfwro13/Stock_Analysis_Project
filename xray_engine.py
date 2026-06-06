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
import urllib3
from typing import Dict, List, Optional, Tuple
from yahoo_engine import yahoo_engine
from urllib.parse import quote

from config import GHOSTFOLIO_URL, GHOSTFOLIO_TOKEN, PORTFOLIO_PATH, load_config
from database import get_connection

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

BENCHMARK_SYMBOL = "SWDA.L"   # iShares MSCI World — always fetched independently
LOOKBACK_DAYS = 252            # 1-year trailing window
AMBER_THRESHOLD = 0.10         # position weight colouring
RED_THRESHOLD = 0.20

# ISO 4217 currency codes Ghostfolio uses as cash-holding symbols.
# Module-level so any future cash-detection caller can reuse the same set.
_CURRENCY_SYMBOLS: frozenset = frozenset({
    "AED", "AUD", "BRL", "CAD", "CHF", "CNY", "CZK", "DKK", "EUR",
    "GBP", "GBX", "GBp", "HKD", "HUF", "IDR", "ILS", "INR", "JPY",
    "KRW", "MXN", "MYR", "NOK", "NZD", "PHP", "PLN", "RON", "RUB",
    "SAR", "SEK", "SGD", "THB", "TRY", "TWD", "USD", "ZAR",
})

# ISO 3166-1 alpha-2 country code classification (MSCI methodology)
_DEVELOPED_MARKET_CODES: frozenset = frozenset({
    "US", "CA", "GB", "DE", "FR", "CH", "NL", "SE", "DK", "NO", "FI",
    "BE", "AT", "NZ", "AU", "JP", "HK", "SG", "IL", "PT", "IE", "ES",
    "IT", "PL", "CZ", "HU", "GR",
})
_EMERGING_MARKET_CODES: frozenset = frozenset({
    "CN", "IN", "BR", "TW", "KR", "ZA", "MX", "SA", "RU", "TH", "MY",
    "ID", "PH", "EG", "PK", "PE", "CO", "CL", "QA", "AE", "KW",
})
# Pacific ex-Japan (used for "Asia-Pacific" regional cluster)
_APAC_CODES: frozenset = frozenset({
    "AU", "NZ", "HK", "SG", "KR", "TW", "TH", "MY", "ID", "PH",
})

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
    "historical_var": (
        "Historical VaR (95%, 1-day): the worst 5th-percentile daily loss observed in the "
        "trailing year, computed directly from the weighted portfolio return series — no "
        "normality assumption. More robust than parametric VaR for fat-tailed distributions."
    ),
    "cvar": (
        "CVaR / Expected Shortfall (95%, 1-day): the average loss on days that exceed the "
        "historical VaR threshold — i.e. the expected damage in a truly bad scenario. "
        "Always worse than VaR; a more complete tail-risk measure."
    ),
    "tracking_error": (
        f"Tracking Error (annualised): the standard deviation of the difference between your "
        f"portfolio's daily returns and those of {BENCHMARK_SYMBOL}. "
        "Low = portfolio moves similarly to the benchmark; High = significant active bets."
    ),
    "sharpe_ratio": (
        "Sharpe Ratio: (portfolio 1-year return − risk-free rate) ÷ annualised volatility. "
        "Measures return per unit of total risk. Above 1 = good; above 2 = excellent."
    ),
    "calmar_ratio": (
        "Calmar Ratio: (portfolio 1-year return) ÷ |max drawdown|. "
        "Measures return per unit of drawdown risk. Higher = better risk-adjusted compounding."
    ),
    "skewness": (
        "Return Skewness: asymmetry of the daily return distribution. "
        "Negative skew = more frequent small gains but rare large losses (crash-prone). "
        "Positive skew = right tail — occasional large gains."
    ),
    "fx_exposure": (
        "FX / Currency Exposure: percentage of invested capital denominated in each currency. "
        "USD-denominated holdings move with GBP/USD; FX swings add a hidden return driver. "
        "Only the direct holding currency is shown — not the look-through of ETF constituents."
    ),
    "marginal_risk_contribution": (
        "Marginal Risk Contribution: each holding's proportional contribution to total portfolio "
        "volatility, accounting for correlations. "
        "A 5% holding in a high-corr, high-vol stock can contribute 15%+ of portfolio risk. "
        "Contributions sum to portfolio annualised volatility."
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
        self.is_configured: bool = bool(self.url and self.token)
        self._session: requests.Session = requests.Session()
        self._session.verify = False

    def authenticate(self) -> bool:
        try:
            resp = self._session.post(
                f"{self.url}/api/v1/auth/anonymous",
                json={"accessToken": self.token},
                timeout=10,
            )
            resp.raise_for_status()
            bearer = resp.json().get("authToken")
            if not bearer:
                logger.error("Ghostfolio X-ray auth: no authToken in response.")
                return False
            self._session.headers.update({"Authorization": f"Bearer {bearer}"})
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
            resp = self._session.get(url, timeout=30)
            resp.raise_for_status()
            raw_holdings: Dict = resp.json().get("holdings", {})
        except Exception as e:
            logger.error(f"Ghostfolio portfolio/details failed: {e}")
            return [], 0.0

        holdings: List[Dict] = []
        for symbol, h in raw_holdings.items():
            # Ghostfolio moved static metadata (assetClass, assetSubClass, sectors,
            # countries, currency, dataSource, name) from the top-level holding to
            # holding["assetProfile"] in a breaking change that removed the deprecated
            # top-level duplicates.  Read from assetProfile first; fall back to the
            # top-level keys so the code works on both old and new Ghostfolio versions.
            profile: Dict = h.get("assetProfile") or {}

            asset_class = (
                profile.get("assetClass") or h.get("assetClass") or ""
            ).upper()

            # Primary: assetClass == "CASH" (reliable when assetProfile is present).
            # Secondary: explicit ISO 4217 set — safety net for when assetProfile
            # is absent and the symbol is literally a currency code (e.g. "GBP").
            # Never use a length heuristic — it silently drops 4-letter tickers (AAPL etc.)
            if asset_class == "CASH":
                continue
            if symbol in _CURRENCY_SYMBOLS:
                continue
            value = float(h.get("valueInBaseCurrency") or 0)
            if value <= 0:
                continue

            holdings.append({
                "symbol": symbol,
                "name": profile.get("name") or h.get("name") or symbol,
                "asset_class": asset_class,
                "asset_sub_class": (
                    profile.get("assetSubClass") or h.get("assetSubClass") or ""
                ).upper(),
                "currency": profile.get("currency") or h.get("currency") or "",
                "data_source": profile.get("dataSource") or h.get("dataSource") or "YAHOO",
                "value": value,
                "investment": float(h.get("investment") or 0),
                "quantity": float(h.get("quantity") or 0),
                "market_price": float(h.get("marketPrice") or 0),
                "gross_perf": float(h.get("grossPerformance") or 0),
                "gross_perf_pct": float(h.get("grossPerformancePercent") or 0),
                "sectors": profile.get("sectors") or h.get("sectors") or [],
                "countries": profile.get("countries") or h.get("countries") or [],
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
                resp = self._session.get(url, timeout=30)
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
            resp = self._session.get(url, timeout=10)
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
        """Downloads 1-year adjusted daily close returns via yahoo_engine."""
        if not symbols:
            return pd.DataFrame()
        ticker_dfs = yahoo_engine.get_price_history(symbols, period="1y", interval="1d")
        if not ticker_dfs:
            return pd.DataFrame()
        prices = pd.DataFrame({t: df["Close"] for t, df in ticker_dfs.items() if "Close" in df.columns})
        prices = prices.dropna(axis=1, how="all")
        return prices.pct_change().dropna(how="all")

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
                corr_df = returns_df[available].dropna(how="any").corr()
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


    def compute_and_cache_portfolio_returns(self, holdings_with_weights: List[Dict]) -> bool:
        """
        Computes the weighted daily portfolio return series and stores it alongside
        the benchmark (SWDA.L) returns in xray_portfolio_returns_cache.

        Called from run_xray_precompute with live Ghostfolio holdings (which have
        weights).  Never called on page load.
        """
        weighted = [(h["symbol"], h.get("weight", 0.0)) for h in holdings_with_weights
                    if h.get("symbol") and h.get("weight", 0.0) > 0]
        if not weighted:
            return False

        symbols = [s for s, _ in weighted]
        all_symbols = list(set(symbols + [BENCHMARK_SYMBOL]))
        returns_df = self._fetch_returns(all_symbols)
        if returns_df.empty:
            return False

        # Drop the pct_change first row (all-NaN) and align all tickers to common dates
        clean = returns_df.dropna(how="any")
        if len(clean) < 30:
            logger.warning("X-ray portfolio returns: fewer than 30 aligned trading days.")
            return False

        # Weighted portfolio daily return
        weight_series = pd.Series(
            {sym: w for sym, w in weighted if sym in clean.columns}
        )
        if weight_series.empty:
            return False
        weight_series /= weight_series.sum()  # re-normalise to 1.0
        available_cols = [s for s in weight_series.index if s in clean.columns]
        port_rets = (clean[available_cols] * weight_series[available_cols]).sum(axis=1)

        bench_rets = clean[BENCHMARK_SYMBOL] if BENCHMARK_SYMBOL in clean.columns else None

        today = datetime.date.today().isoformat()
        conn = None
        try:
            conn = get_connection()
            conn.execute(
                """INSERT OR REPLACE INTO xray_portfolio_returns_cache
                   (benchmark, last_updated, dates_json, returns_json, benchmark_returns_json)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    BENCHMARK_SYMBOL,
                    today,
                    json.dumps(port_rets.index.strftime("%Y-%m-%d").tolist()),
                    json.dumps(port_rets.tolist()),
                    json.dumps(bench_rets.tolist() if bench_rets is not None else []),
                ),
            )
            conn.commit()
            logger.info(f"X-ray portfolio returns cache updated: {len(port_rets)} trading days.")
            return True
        except Exception as e:
            logger.error(f"X-ray portfolio returns cache write failed: {e}")
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

    # Dividend + portfolio returns caches — require live Ghostfolio; treat as non-fatal
    try:
        client = GhostfolioXRayClient()
        if client.is_configured and client.authenticate():
            config = load_config()
            active_ids: List[str] = config.get("GHOSTFOLIO_ACCOUNTS", {}).get("active", [])
            if active_ids:
                live_holdings, _ = client.get_holdings(active_ids)
                if live_holdings:
                    cache_xray_dividends(live_holdings, client)
                    XRayRiskComputer().compute_and_cache_portfolio_returns(live_holdings)
    except Exception as e:
        logger.warning(f"X-ray dividend/returns cache step failed (non-fatal): {e}")

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
# Recommendations engine — compares current allocations against configured targets
# ---------------------------------------------------------------------------

def _rec_item(
    category: str,
    current: float,
    min_val: Optional[float],
    max_val: Optional[float],
    unit: str = "%",
) -> Optional[Dict]:
    """Build a single recommendation dict, or None if no bound is defined."""
    if min_val is None and max_val is None:
        return None

    pct_str = f"{round(current, 2)}{unit}"
    if max_val is not None and current > max_val:
        status = "exceeds"
        msg = f"The {category} contribution of your current investment ({pct_str}) exceeds {max_val}{unit}"
    elif min_val is not None and current < min_val:
        if max_val is not None:
            status = "below"
            msg = f"The {category} contribution of your current investment ({pct_str}) is below {min_val}{unit}"
        else:
            status = "below"
            msg = f"The {category} contribution of your current investment ({pct_str}) is below {min_val}{unit}"
    elif min_val is not None and max_val is not None:
        status = "within"
        msg = (
            f"The {category} contribution of your current investment ({pct_str}) "
            f"is within the range of {min_val}{unit} and {max_val}{unit}"
        )
    else:
        status = "ok"
        msg = f"The {category} contribution of your current investment ({pct_str}) meets the target"

    return {
        "category": category,
        "current_value": round(current, 4),
        "status": status,
        "min": min_val,
        "max": max_val,
        "unit": unit,
        "message": msg,
    }


def _generate_xray_recommendations(
    holdings: List[Dict],
    sector_allocation: List[Dict],
    asset_class_allocation: List[Dict],
    risk_metrics: Dict,
    concentration: Dict,
    income: Dict,
    targets: Dict,
) -> Dict[str, List[Dict]]:
    """Compare current allocations against configured targets and return recommendation messages."""

    result: Dict[str, List[Dict]] = {
        "market_development": [],
        "regional_clusters": [],
        "country_concentration": [],
        "sector": [],
        "asset_class": [],
        "concentration": [],
        "risk_metrics": [],
        "income": [],
    }

    # --- 1. Market development & regional clusters (country-level iteration) ---
    dev_weight = 0.0
    em_weight = 0.0
    regional: Dict[str, float] = {}
    country_totals: Dict[str, float] = {}

    for h in holdings:
        h_weight = h.get("weight", 0.0)
        for country in h.get("countries") or []:
            code = (country.get("code") or "").upper()
            name = country.get("name") or ""
            continent = country.get("continent") or ""
            c_weight = float(country.get("weight") or 0.0) * h_weight

            # Market development
            if code in _DEVELOPED_MARKET_CODES:
                dev_weight += c_weight
            elif code in _EMERGING_MARKET_CODES:
                em_weight += c_weight

            # Regional clusters
            if code == "JP":
                regional["Japan"] = regional.get("Japan", 0.0) + c_weight
            elif code in _APAC_CODES:
                regional["Asia-Pacific"] = regional.get("Asia-Pacific", 0.0) + c_weight
            elif continent == "North America":
                regional["North America"] = regional.get("North America", 0.0) + c_weight
            elif continent == "Europe":
                regional["Europe"] = regional.get("Europe", 0.0) + c_weight
            if code in _EMERGING_MARKET_CODES:
                regional["Emerging Markets"] = regional.get("Emerging Markets", 0.0) + c_weight

            # Country concentration
            if name:
                country_totals[name] = country_totals.get(name, 0.0) + c_weight

    # Market development recommendations
    md_targets = targets.get("market_development", {})
    for label, weight in [("Developed Markets", dev_weight * 100), ("Emerging Markets", em_weight * 100)]:
        t = md_targets.get(label, {})
        item = _rec_item(label, weight, t.get("min"), t.get("max"))
        if item:
            result["market_development"].append(item)

    # Regional cluster recommendations
    rc_targets = targets.get("regional_clusters", {})
    for label, weight in regional.items():
        t = rc_targets.get(label, {})
        item = _rec_item(label, weight * 100, t.get("min"), t.get("max"))
        if item:
            result["regional_clusters"].append(item)
    result["regional_clusters"].sort(key=lambda x: x["current_value"], reverse=True)

    # Country concentration recommendations
    cc_targets = targets.get("country_concentration", {})
    for country_name, t in cc_targets.items():
        weight = country_totals.get(country_name, 0.0) * 100
        item = _rec_item(country_name, weight, t.get("min"), t.get("max"))
        if item:
            result["country_concentration"].append(item)

    # --- 2. Sector targets ---
    sector_targets = targets.get("sector_targets", {})
    sector_map = {s["name"].lower(): s["weight"] for s in sector_allocation}
    for sector_name, t in sector_targets.items():
        weight = sector_map.get(sector_name.lower(), 0.0) * 100
        if weight == 0.0 and t.get("min") is None:
            continue
        item = _rec_item(sector_name, weight, t.get("min"), t.get("max"))
        if item:
            result["sector"].append(item)

    # --- 3. Asset class targets ---
    ac_targets = targets.get("asset_class_targets", {})
    ac_map = {a["name"].lower(): a["weight"] for a in asset_class_allocation}
    for ac_name, t in ac_targets.items():
        weight = ac_map.get(ac_name.lower(), 0.0) * 100
        if weight == 0.0 and t.get("min") is None:
            continue
        item = _rec_item(ac_name, weight, t.get("min"), t.get("max"))
        if item:
            result["asset_class"].append(item)

    # --- 4. Concentration targets ---
    conc_t = targets.get("concentration_targets", {})
    hhi = concentration.get("hhi", 0.0) or 0.0
    top5 = concentration.get("top5_weight", 0.0) or 0.0
    top10 = concentration.get("top10_weight", 0.0) or 0.0
    max_pos = concentration.get("max_single_position", 0.0) or 0.0

    for label, val, min_v, max_v, unit in [
        ("Max Single Position", max_pos * 100, None, conc_t.get("max_single_position_pct"), "%"),
        ("Top-5 Concentration", top5 * 100, None, conc_t.get("top5_weight_max_pct"), "%"),
        ("Top-10 Concentration", top10 * 100, None, conc_t.get("top10_weight_max_pct"), "%"),
        ("Portfolio HHI", hhi, None, conc_t.get("hhi_max"), ""),
    ]:
        item = _rec_item(label, val, min_v, max_v, unit)
        if item:
            result["concentration"].append(item)

    # --- 5. Risk metric targets ---
    rm_t = targets.get("risk_metric_targets", {})
    beta = risk_metrics.get("portfolio_beta")
    vol = risk_metrics.get("annualized_vol")
    sharpe = risk_metrics.get("sharpe_ratio")
    drawdown = risk_metrics.get("max_drawdown")
    avg_corr = risk_metrics.get("avg_pairwise_correlation")

    if beta is not None:
        item = _rec_item("Portfolio Beta", beta, rm_t.get("portfolio_beta_min"), rm_t.get("portfolio_beta_max"), "")
        if item:
            result["risk_metrics"].append(item)
    if vol is not None:
        item = _rec_item("Annualised Volatility", vol * 100, None, rm_t.get("annualized_vol_max_pct"))
        if item:
            result["risk_metrics"].append(item)
    if sharpe is not None:
        item = _rec_item("Sharpe Ratio", sharpe, rm_t.get("sharpe_ratio_min"), None, "")
        if item:
            result["risk_metrics"].append(item)
    if drawdown is not None:
        item = _rec_item("Max Drawdown", abs(drawdown) * 100, None, rm_t.get("max_drawdown_max_pct"))
        if item:
            result["risk_metrics"].append(item)
    if avg_corr is not None:
        item = _rec_item("Average Correlation", avg_corr, None, rm_t.get("avg_correlation_max"), "")
        if item:
            result["risk_metrics"].append(item)

    # --- 6. Income targets ---
    inc_t = targets.get("income_targets", {})
    div_yield = income.get("weighted_dividend_yield", 0.0) or 0.0
    item = _rec_item("Dividend Yield", div_yield * 100, inc_t.get("dividend_yield_min_pct"), None)
    if item:
        result["income"].append(item)

    return result


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

    # --- Instrument type allocation (cache itype on h for reuse in payload) ------
    instrument_map: Dict[str, float] = {}
    for h in holdings:
        itype = _get_instrument_type(h["asset_class"], h["asset_sub_class"])
        h["_instrument_type"] = itype  # cached so holdings_payload doesn't recompute
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
    port_rets_series: Optional[List[float]] = None
    bench_rets_series: Optional[List[float]] = None

    conn = None
    try:
        conn = get_connection()

        portfolio_symbols = [h["symbol"] for h in holdings_sorted]
        placeholders = ",".join("?" * len(portfolio_symbols))
        rows = conn.execute(
            f"SELECT ticker, beta, annualized_vol, last_updated "
            f"FROM xray_risk_cache WHERE benchmark = ? AND ticker IN ({placeholders})",
            [BENCHMARK_SYMBOL] + portfolio_symbols,
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

        port_ret_row = conn.execute(
            "SELECT returns_json, benchmark_returns_json "
            "FROM xray_portfolio_returns_cache WHERE benchmark = ?",
            (BENCHMARK_SYMBOL,),
        ).fetchone()
        if port_ret_row:
            port_rets_series = json.loads(port_ret_row["returns_json"])
            bench_rets_series = json.loads(port_ret_row["benchmark_returns_json"])
    except Exception as e:
        logger.error(f"X-ray DB read failed: {e}")
        raise
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

    # --- Data warnings (initialised early so risk blocks can append to it) ------
    data_warnings: List[str] = []

    # --- Portfolio-level risk metrics from cached per-ticker data + live weights --
    portfolio_beta: Optional[float] = None
    portfolio_vol: Optional[float] = None
    var_95_1d: Optional[float] = None
    avg_pairwise_corr: Optional[float] = None

    beta_pairs = [(h["weight"], h["beta"]) for h in holdings_sorted if h.get("beta") is not None]
    if beta_pairs:
        covered_weight = sum(w for w, _ in beta_pairs)
        portfolio_beta = round(sum(w * b for w, b in beta_pairs) / covered_weight, 3)

    # Portfolio vol = sqrt(w^T * Sigma * w), Sigma_ij = vol_i * vol_j * rho_ij (daily)
    holdings_by_symbol: Dict[str, Dict] = {h["symbol"]: h for h in holdings_sorted}
    if len(corr_tickers) >= 2 and corr_matrix:
        w_list: List[float] = []
        dv_list: List[float] = []
        for sym in corr_tickers:
            h_match = holdings_by_symbol.get(sym)
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
            else:
                logger.warning(
                    "Portfolio variance <= 0 (non-PSD correlation matrix). "
                    "vol and parametric VaR will be None. "
                    "Cause: pairwise returns had unequal overlapping periods."
                )
                data_warnings.append(
                    "Could not compute portfolio volatility or VaR: the cached correlation "
                    "matrix is non-positive-semidefinite (returns data has unequal overlapping "
                    "periods across holdings). Re-run the risk cache job to refresh."
                )

        # avg_pairwise_corr: only include tickers that are in current holdings
        current_syms = {h["symbol"] for h in holdings_sorted}
        active_indices = [
            i for i, t in enumerate(corr_tickers) if t in current_syms
        ]
        if len(active_indices) >= 2:
            off_diag = [
                corr_matrix[i][j]
                for i in active_indices for j in active_indices
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

    # --- FX / currency exposure --------------------------------------------------
    fx_map: Dict[str, float] = {}
    for h in holdings_sorted:
        ccy = (h.get("currency") or "").upper() or "UNKNOWN"
        fx_map[ccy] = fx_map.get(ccy, 0.0) + h["weight"]
    fx_exposure = sorted(
        [{"currency": k, "weight": round(v, 4)} for k, v in fx_map.items()],
        key=lambda x: x["weight"], reverse=True,
    )

    # --- Marginal Risk Contribution (MRC) per holding ----------------------------
    # MRC_i = (Sigma_daily · w)_i * w_i / sigma_p_daily * sqrt(252)
    # Euler decomposition: sum(MRC_i) = portfolio annualised vol exactly.
    if portfolio_vol is not None and len(corr_tickers) >= 2 and corr_matrix:
        sigma_daily_full = np.outer(dv_arr, dv_arr) * np.array(corr_matrix)
        marginal_contrib_daily = sigma_daily_full @ w_arr
        port_daily_vol_val = portfolio_vol / np.sqrt(252)
        for i, sym in enumerate(corr_tickers):
            h_match = holdings_by_symbol.get(sym)
            if h_match is not None and port_daily_vol_val > 0:
                mrc_ann = float(
                    marginal_contrib_daily[i] * w_arr[i] / port_daily_vol_val * np.sqrt(252)
                )
                h_match["marginal_risk_contribution"] = round(mrc_ann, 4)
    # Set None for any holding not in corr_tickers
    for h in holdings_sorted:
        h.setdefault("marginal_risk_contribution", None)

    # --- Stats from portfolio returns series (historical VaR, Sharpe, etc.) -----
    historical_var_95_1d: Optional[float] = None
    cvar_95_1d: Optional[float] = None
    tracking_error: Optional[float] = None
    sharpe_ratio: Optional[float] = None
    calmar_ratio: Optional[float] = None
    skewness: Optional[float] = None
    excess_kurtosis: Optional[float] = None

    if port_rets_series and len(port_rets_series) >= 30:
        try:
            import scipy.stats as _stats
            pr = np.array(port_rets_series)

            # Historical VaR & CVaR (95%)
            var_threshold = float(np.percentile(pr, 5))
            historical_var_95_1d = round(abs(var_threshold) * total_value, 2)
            tail = pr[pr <= var_threshold]
            if len(tail) > 0:
                cvar_95_1d = round(abs(float(tail.mean())) * total_value, 2)

            # Skewness and excess kurtosis
            skewness = round(float(_stats.skew(pr)), 3)
            excess_kurtosis = round(float(_stats.kurtosis(pr)), 3)  # Fisher = excess

            # 1-year annualised return from compounded daily returns
            ann_return = float((1 + pr).prod() ** (252 / len(pr)) - 1)

            # Tracking error vs benchmark
            if bench_rets_series and len(bench_rets_series) == len(port_rets_series):
                br = np.array(bench_rets_series)
                active_rets = pr - br
                tracking_error = round(float(active_rets.std() * np.sqrt(252)), 4)

            # Sharpe ratio — risk-free rate from config, default 4.5%
            rf_rate: float = float(config.get("RISK_FREE_RATE", 0.045))
            if portfolio_vol and portfolio_vol > 0:
                sharpe_ratio = round((ann_return - rf_rate) / portfolio_vol, 3)

            # Calmar ratio
            if max_drawdown and max_drawdown < 0:
                calmar_ratio = round(ann_return / abs(max_drawdown), 3)

        except Exception as e:
            logger.warning(f"Portfolio return stats computation failed: {e}")

    # --- Data warnings (continued) -----------------------------------------------
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
            logger.debug("Could not compute risk cache age, skipping staleness check", exc_info=True)
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
            "instrument_type": h.get("_instrument_type") or _get_instrument_type(h["asset_class"], h["asset_sub_class"]),
            "currency": h["currency"],
            "gross_perf": round(h["gross_perf"], 2),
            "gross_perf_pct": round(h["gross_perf_pct"] * 100, 2),
            "sectors": h["sectors"],
            "countries": h["countries"],
            "beta": round(h["beta"], 3) if h.get("beta") is not None else None,
            "vol": round(h["vol"], 4) if h.get("vol") is not None else None,
            "dividend_yield_pct": h.get("dividend_yield_pct", 0.0),
            "dividend_income": h.get("dividend_income", 0.0),
            "marginal_risk_contribution": h.get("marginal_risk_contribution"),
        }
        for h in holdings_sorted
    ]

    xray_targets = load_config().get("XRAY_TARGETS", {})
    recommendations = _generate_xray_recommendations(
        holdings=holdings,
        sector_allocation=sector_allocation,
        asset_class_allocation=asset_class_allocation,
        risk_metrics={
            "portfolio_beta": portfolio_beta,
            "annualized_vol": portfolio_vol,
            "max_drawdown": max_drawdown,
            "sharpe_ratio": sharpe_ratio,
            "avg_pairwise_correlation": avg_pairwise_corr,
        },
        concentration={
            "hhi": hhi,
            "top5_weight": top5_weight,
            "top10_weight": top10_weight,
            "max_single_position": max(h["weight"] for h in holdings) if holdings else 0.0,
        },
        income={"weighted_dividend_yield": weighted_div_yield},
        targets=xray_targets,
    )

    return {
        "account_id": account_id,
        "generated_at": datetime.datetime.now().isoformat(),
        "portfolio_total_value": round(total_value, 2),
        "portfolio_total_investment": round(sum(h["investment"] for h in holdings_sorted), 2),
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
            "historical_var_95_1d": historical_var_95_1d,
            "cvar_95_1d": cvar_95_1d,
            "avg_pairwise_correlation": avg_pairwise_corr,
            "tracking_error": tracking_error,
            "sharpe_ratio": sharpe_ratio,
            "calmar_ratio": calmar_ratio,
            "skewness": skewness,
            "excess_kurtosis": excess_kurtosis,
            "benchmark": BENCHMARK_SYMBOL,
            "lookback_days": LOOKBACK_DAYS,
            "cache_date": cache_date,
        },
        "income": {
            "weighted_dividend_yield": weighted_div_yield,
            "projected_annual_income": projected_annual_income,
        },
        "fx_exposure": fx_exposure,
        "correlation_matrix": {
            "tickers": corr_tickers,
            "matrix": corr_matrix,
        },
        "tooltips": XRAY_TOOLTIPS,
        "data_warnings": data_warnings,
        "recommendations": recommendations,
    }
