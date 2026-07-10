# Tier A = live Ghostfolio (holdings, allocations); Tier C = yfinance/SQLite cache (beta, vol, VaR); benchmark SWDA.L always fetched independently.

import json
import logging
import datetime
import numpy as np
import pandas as pd
import requests
import urllib3
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote

from config import GHOSTFOLIO_URL, GHOSTFOLIO_TOKEN, load_config
from data_engine import load_or_fetch_daily_history
from database import get_connection
from fundamentals_helpers import get_instrument_type as _get_instrument_type
from accounts_engine import derive_account_holdings, market_values_for_xray, get_combined_holdings
from utils import ignored_tickers_set, is_excluded_from_yahoo_fetch

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

BENCHMARK_SYMBOL = "SWDA.L"   # iShares MSCI World — always fetched independently
LOOKBACK_DAYS = 252            # 1-year trailing window
AMBER_THRESHOLD = 0.10         # position weight colouring
RED_THRESHOLD = 0.20

# ISO 4217 codes Ghostfolio uses as cash symbols — module-level so cash detection is centralised.
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

# Built-in accounts have no Ghostfolio-style look-through country/continent breakdown — only a
# single yfinance `country` string (asset_profiles.country). This maps that string to the
# (ISO 3166-1 alpha-2 code, continent) pair the rest of this module expects from Ghostfolio.
_COUNTRY_NAME_TO_CODE_CONTINENT: Dict[str, Tuple[str, str]] = {
    "United States": ("US", "North America"), "Canada": ("CA", "North America"),
    "Mexico": ("MX", "North America"), "Bermuda": ("BM", "North America"),
    "Cayman Islands": ("KY", "North America"), "British Virgin Islands": ("VG", "North America"),
    "Panama": ("PA", "North America"), "Bahamas": ("BS", "North America"),
    "United Kingdom": ("GB", "Europe"), "Germany": ("DE", "Europe"), "France": ("FR", "Europe"),
    "Netherlands": ("NL", "Europe"), "Switzerland": ("CH", "Europe"), "Sweden": ("SE", "Europe"),
    "Denmark": ("DK", "Europe"), "Norway": ("NO", "Europe"), "Finland": ("FI", "Europe"),
    "Ireland": ("IE", "Europe"), "Belgium": ("BE", "Europe"), "Luxembourg": ("LU", "Europe"),
    "Spain": ("ES", "Europe"), "Italy": ("IT", "Europe"), "Poland": ("PL", "Europe"),
    "Greece": ("GR", "Europe"), "Austria": ("AT", "Europe"), "Portugal": ("PT", "Europe"),
    "Czech Republic": ("CZ", "Europe"), "Hungary": ("HU", "Europe"), "Cyprus": ("CY", "Europe"),
    "Isle of Man": ("IM", "Europe"), "Guernsey": ("GG", "Europe"), "Jersey": ("JE", "Europe"),
    "Monaco": ("MC", "Europe"), "Iceland": ("IS", "Europe"), "Malta": ("MT", "Europe"),
    "Lithuania": ("LT", "Europe"), "Latvia": ("LV", "Europe"), "Estonia": ("EE", "Europe"),
    "Romania": ("RO", "Europe"), "Bulgaria": ("BG", "Europe"), "Croatia": ("HR", "Europe"),
    "Slovenia": ("SI", "Europe"), "Slovakia": ("SK", "Europe"), "Russia": ("RU", "Europe"),
    "Georgia": ("GE", "Europe"), "Azerbaijan": ("AZ", "Europe"),
    "China": ("CN", "Asia"), "Japan": ("JP", "Asia"), "South Korea": ("KR", "Asia"),
    "Hong Kong": ("HK", "Asia"), "Taiwan": ("TW", "Asia"), "Singapore": ("SG", "Asia"),
    "India": ("IN", "Asia"), "Indonesia": ("ID", "Asia"), "Malaysia": ("MY", "Asia"),
    "Thailand": ("TH", "Asia"), "Philippines": ("PH", "Asia"), "Vietnam": ("VN", "Asia"),
    "Israel": ("IL", "Asia"), "United Arab Emirates": ("AE", "Asia"), "Saudi Arabia": ("SA", "Asia"),
    "Qatar": ("QA", "Asia"), "Kuwait": ("KW", "Asia"), "Macau": ("MO", "Asia"),
    "Australia": ("AU", "Oceania"), "New Zealand": ("NZ", "Oceania"),
    "Brazil": ("BR", "South America"), "Chile": ("CL", "South America"),
    "Colombia": ("CO", "South America"), "Peru": ("PE", "South America"),
    "Argentina": ("AR", "South America"), "Uruguay": ("UY", "South America"),
    "South Africa": ("ZA", "Africa"), "Egypt": ("EG", "Africa"), "Nigeria": ("NG", "Africa"),
}


def _country_code_continent(country_name: Optional[str]) -> Tuple[str, str]:
    return _COUNTRY_NAME_TO_CODE_CONTINENT.get(country_name or "", ("", "Other"))

# Serialised into the API response so the frontend never hard-codes tooltip definitions.
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
            logger.error("Ghostfolio X-ray auth failed: %s", e)
            return False

    def get_holdings(self, account_ids: List[str]) -> Tuple[List[Dict], float]:
        # Never call without explicit account_ids — omitting leaks excluded accounts.
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
            logger.error("Ghostfolio portfolio/details failed: %s", e)
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


class XRayRiskComputer:
    # Benchmark SWDA.L is always fetched independently — never assumed to be a portfolio holding.

    def _fetch_returns(self, symbols: List[str]) -> pd.DataFrame:
        """1-year daily close returns, read from each symbol's own historical parquet (only hits Yahoo for a symbol with no parquet cached yet)."""
        if not symbols:
            return pd.DataFrame()
        closes: Dict[str, pd.Series] = {}
        for t in symbols:
            df = load_or_fetch_daily_history(t)
            if df is not None and "Close" in df.columns:
                closes[t] = df["Close"].tail(252)
        if not closes:
            return pd.DataFrame()
        prices = pd.DataFrame(closes)
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
        portfolio_symbols = list({h["symbol"] for h in holdings if h.get("symbol")})
        if not portfolio_symbols:
            logger.warning("X-ray risk compute: no portfolio symbols found.")
            return False

        # Benchmark is always fetched independently — not assumed to be a holding
        all_symbols = list(set(portfolio_symbols + [BENCHMARK_SYMBOL]))
        logger.info("X-ray risk compute: fetching %s symbols (incl. benchmark %s)", len(all_symbols), BENCHMARK_SYMBOL)

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
            logger.warning("Benchmark %s not available from yfinance — beta will be None for all tickers.", BENCHMARK_SYMBOL)

        today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
        conn = None
        try:
            conn = get_connection()

            for sym in portfolio_symbols:
                if sym not in returns_df.columns:
                    logger.warning("No returns data for %s — skipping.", sym)
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

            # Per-ticker series (incl. benchmark) so assemble_xray_report can derive a weighted
            # portfolio return series for any account scope, not just a precomputed global one.
            for sym in all_symbols:
                if sym not in returns_df.columns:
                    continue
                sym_rets = returns_df[sym].dropna()
                if sym_rets.empty:
                    continue
                conn.execute(
                    """INSERT OR REPLACE INTO xray_returns_cache
                       (ticker, benchmark, last_updated, dates_json, returns_json)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        sym, BENCHMARK_SYMBOL, today,
                        json.dumps(sym_rets.index.strftime("%Y-%m-%d").tolist()),
                        json.dumps(sym_rets.tolist()),
                    ),
                )

            available = [s for s in portfolio_symbols if s in returns_df.columns]
            if len(available) >= 2:
                # Pairwise correlation — each pair uses its own overlapping window
                # (min_periods=30 per pair; NaN means insufficient overlap for that pair).
                corr_raw = returns_df[available].corr(min_periods=30).values
                # Replace NaN pairs (insufficient overlap) with 0.0 (uncorrelated assumption).
                corr_raw = np.where(np.isnan(corr_raw), 0.0, corr_raw)
                np.fill_diagonal(corr_raw, 1.0)
                # Project to nearest PSD by clipping negative eigenvalues so portfolio
                # variance is always well-defined, regardless of how unequal the
                # overlapping periods are across holdings.
                eigvals, eigvecs = np.linalg.eigh(corr_raw)
                eigvals = np.maximum(eigvals, 1e-8)
                psd = eigvecs @ np.diag(eigvals) @ eigvecs.T
                diag_sqrt = np.sqrt(np.diag(psd))
                diag_sqrt[diag_sqrt == 0] = 1.0
                psd = psd / np.outer(diag_sqrt, diag_sqrt)
                np.fill_diagonal(psd, 1.0)
                conn.execute(
                    """INSERT OR REPLACE INTO xray_correlation_matrix
                       (benchmark, last_updated, tickers_json, matrix_json)
                       VALUES (?, ?, ?, ?)""",
                    (
                        BENCHMARK_SYMBOL, today,
                        json.dumps(available),
                        json.dumps(psd.tolist()),
                    ),
                )

            conn.commit()
            logger.info("X-ray risk cache updated: %s tickers, benchmark %s.", len(available), BENCHMARK_SYMBOL)
            return True

        except Exception as e:
            logger.error("X-ray risk cache write failed: %s", e)
            if conn:
                conn.rollback()
            return False
        finally:
            if conn:
                conn.close()


def cache_xray_dividends(holdings: List[Dict], client: GhostfolioXRayClient) -> None:
    # One HTTP call per holding — must only be called from the scheduler job, never on page load.
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
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
        logger.info("X-ray dividend cache updated for %s holdings.", len(holdings))
    except Exception as e:
        logger.error("X-ray dividend cache write failed: %s", e)
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()



def run_xray_precompute() -> bool:
    logger.info("X-ray pre-compute job started.")
    ignored_tickers = ignored_tickers_set(load_config())
    symbols = {
        sym for sym in get_combined_holdings().keys()
        if not is_excluded_from_yahoo_fetch(sym, ignored_tickers)
    }
    holdings = [{"symbol": sym, "data_source": "YAHOO", "currency": ""} for sym in symbols]
    if not holdings:
        logger.warning("X-ray pre-compute: no tickers found in portfolio or built-in Trading accounts.")
        return False

    risk_ok = XRayRiskComputer().compute_and_cache(holdings)

    # Dividend cache requires live Ghostfolio (no equivalent for built-in holdings); non-fatal
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
        logger.warning("X-ray dividend cache step failed (non-fatal): %s", e)

    logger.info("X-ray pre-compute finished. Risk cache: %s.", "OK" if risk_ok else "FAILED")
    return risk_ok



def get_scope_return_series(
    holdings: List[Dict], total_value: float
) -> Tuple[Optional[pd.Series], Optional[pd.Series], List[str]]:
    """Weighted daily-return series (portfolio, benchmark) for an already-resolved scope,
    derived from xray_returns_cache. Single source of truth for assemble_xray_report() and
    performance_analytics_engine.py — None/None if fewer than 30 overlapping cached days."""
    data_warnings: List[str] = []
    portfolio_symbols = [h["symbol"] for h in holdings if h.get("weight", 0) > 0]
    if not portfolio_symbols:
        return None, None, data_warnings
    returns_tickers = list(set(portfolio_symbols + [BENCHMARK_SYMBOL]))

    conn = None
    returns_cache: Dict[str, Tuple[List[str], List[float]]] = {}
    try:
        conn = get_connection()
        rt_placeholders = ",".join("?" * len(returns_tickers))
        returns_rows = conn.execute(
            f"SELECT ticker, dates_json, returns_json FROM xray_returns_cache "
            f"WHERE benchmark = ? AND ticker IN ({rt_placeholders})",
            [BENCHMARK_SYMBOL] + returns_tickers,
        ).fetchall()
        returns_cache = {
            row["ticker"]: (json.loads(row["dates_json"]), json.loads(row["returns_json"]))
            for row in returns_rows
        }
    except Exception as e:
        logger.error("X-ray return series DB read failed: %s", e)
        raise
    finally:
        if conn:
            conn.close()

    if BENCHMARK_SYMBOL not in returns_cache:
        return None, None, data_warnings

    series_map: Dict[str, pd.Series] = {}
    for h in holdings:
        sym = h["symbol"]
        if sym in returns_cache and h.get("weight", 0) > 0:
            dates, rets = returns_cache[sym]
            series_map[sym] = pd.Series(rets, index=pd.to_datetime(dates))
    bench_dates, bench_rets_raw = returns_cache[BENCHMARK_SYMBOL]
    series_map[BENCHMARK_SYMBOL] = pd.Series(bench_rets_raw, index=pd.to_datetime(bench_dates))

    if len(series_map) < 2:
        return None, None, data_warnings

    combined_df = pd.DataFrame(series_map).dropna(how="any")
    if len(combined_df) < 30:
        return None, None, data_warnings

    weights = pd.Series({
        h["symbol"]: h["weight"] for h in holdings if h["symbol"] in combined_df.columns
    })
    port_cols = [c for c in weights.index if c != BENCHMARK_SYMBOL]
    if not port_cols or weights[port_cols].sum() <= 0:
        return None, None, data_warnings

    weights = weights[port_cols] / weights[port_cols].sum()
    port_rets_series = (combined_df[port_cols] * weights[port_cols]).sum(axis=1)
    bench_rets_series = combined_df[BENCHMARK_SYMBOL]
    return port_rets_series, bench_rets_series, data_warnings


def annualized_return(returns: pd.Series) -> float:
    """CAGR-style annualisation of a daily return series (252 trading days/year)."""
    return float((1 + returns).prod() ** (252 / len(returns)) - 1)


def native_max_drawdown(returns: pd.Series) -> Tuple[float, pd.Series]:
    """Peak-to-trough max drawdown and the full dated drawdown series, derived from a daily
    return series — the only max-drawdown source available for built-in-account-only scopes
    (Ghostfolio's performance-chart endpoint has no equivalent for those)."""
    cumulative = (1 + returns).cumprod()
    running_max = cumulative.cummax()
    drawdown_series = cumulative / running_max - 1
    return float(drawdown_series.min()), drawdown_series



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

    md_targets = targets.get("market_development", {})
    for label, weight in [("Developed Markets", dev_weight * 100), ("Emerging Markets", em_weight * 100)]:
        t = md_targets.get(label, {})
        item = _rec_item(label, weight, t.get("min"), t.get("max"))
        if item:
            result["market_development"].append(item)

    rc_targets = targets.get("regional_clusters", {})
    for label, weight in regional.items():
        t = rc_targets.get(label, {})
        item = _rec_item(label, weight * 100, t.get("min"), t.get("max"))
        if item:
            result["regional_clusters"].append(item)
    result["regional_clusters"].sort(key=lambda x: x["current_value"], reverse=True)

    cc_targets = targets.get("country_concentration", {})
    for country_name, t in cc_targets.items():
        weight = country_totals.get(country_name, 0.0) * 100
        item = _rec_item(country_name, weight, t.get("min"), t.get("max"))
        if item:
            result["country_concentration"].append(item)

    sector_targets = targets.get("sector_targets", {})
    sector_map = {s["name"].lower(): s["weight"] for s in sector_allocation}
    for sector_name, t in sector_targets.items():
        weight = sector_map.get(sector_name.lower(), 0.0) * 100
        if weight == 0.0 and t.get("min") is None:
            continue
        item = _rec_item(sector_name, weight, t.get("min"), t.get("max"))
        if item:
            result["sector"].append(item)

    ac_targets = targets.get("asset_class_targets", {})
    ac_map = {a["name"].lower(): a["weight"] for a in asset_class_allocation}
    for ac_name, t in ac_targets.items():
        weight = ac_map.get(ac_name.lower(), 0.0) * 100
        if weight == 0.0 and t.get("min") is None:
            continue
        item = _rec_item(ac_name, weight, t.get("min"), t.get("max"))
        if item:
            result["asset_class"].append(item)

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

    inc_t = targets.get("income_targets", {})
    div_yield = income.get("weighted_dividend_yield", 0.0) or 0.0
    item = _rec_item("Dividend Yield", div_yield * 100, inc_t.get("dividend_yield_min_pct"), None)
    if item:
        result["income"].append(item)

    return result


def _psd_fix_corr(raw: List) -> List[List[float]]:
    """Sanitise a stored correlation matrix: null→0.0, diagonal→1.0, clip negative eigenvalues to 1e-8 (Higham nearest-PSD), re-normalise."""
    n = len(raw)
    arr = np.zeros((n, n), dtype=float)
    for i, row in enumerate(raw):
        for j, v in enumerate(row):
            if v is None or (isinstance(v, float) and not np.isfinite(v)):
                arr[i, j] = 0.0
            else:
                arr[i, j] = float(v)
    np.fill_diagonal(arr, 1.0)

    try:
        eigvals, eigvecs = np.linalg.eigh(arr)
    except np.linalg.LinAlgError:
        logger.warning("eigh failed on stored correlation matrix; using identity fallback.")
        return np.eye(n).tolist()

    if float(eigvals.min()) < 0:
        logger.warning(
            "Stored correlation matrix is non-PSD (min eigenvalue %.4f). "
            "Projecting to nearest valid matrix.",
            float(eigvals.min()),
        )
        eigvals = np.maximum(eigvals, 1e-8)
        arr = eigvecs @ np.diag(eigvals) @ eigvecs.T
        diag_sqrt = np.sqrt(np.diag(arr))
        diag_sqrt[diag_sqrt == 0] = 1.0
        arr = arr / np.outer(diag_sqrt, diag_sqrt)
        np.fill_diagonal(arr, 1.0)

    return arr.tolist()


def _sanitize_floats(obj):
    # Recursively replace nan/inf with None so the report is always JSON-safe.
    if isinstance(obj, float):
        return None if not np.isfinite(obj) else obj
    if isinstance(obj, dict):
        return {k: _sanitize_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_floats(v) for v in obj]
    return obj


def _asset_profile_map(tickers: List[str]) -> Dict[str, Dict]:
    if not tickers:
        return {}
    conn = None
    try:
        conn = get_connection()
        placeholders = ",".join("?" * len(tickers))
        rows = conn.execute(
            f"SELECT ticker, sector, country, quote_type FROM asset_profiles WHERE ticker IN ({placeholders})",
            tickers,
        ).fetchall()
        return {row["ticker"]: dict(row) for row in rows}
    except Exception as e:
        logger.error("X-ray asset_profiles lookup failed: %s", e)
        return {}
    finally:
        if conn:
            conn.close()


def _builtin_account_holdings(account_id: Optional[int]) -> List[Dict]:
    # No Ghostfolio look-through data exists for built-in holdings — sector/country come from
    # asset_profiles as a single 100%-weight bucket per holding, not a weighted breakdown.
    rows = market_values_for_xray(account_id)
    if not rows:
        return []
    profiles = _asset_profile_map([r["ticker"] for r in rows])
    holdings: List[Dict] = []
    for r in rows:
        profile = profiles.get(r["ticker"], {})
        sector = profile.get("sector") or "Unknown"
        country_name = profile.get("country")
        code, continent = _country_code_continent(country_name)
        asset_class = (profile.get("quote_type") or "EQUITY").upper()
        value = r["market_value"]
        quantity = r["shares"]
        holdings.append({
            "symbol": r["ticker"],
            "name": r["company_name"] or r["ticker"],
            "asset_class": asset_class,
            "asset_sub_class": "",
            "currency": r["currency"] or "",
            "data_source": "YAHOO",
            "value": value,
            "investment": r["total_investment"],
            "quantity": quantity,
            "market_price": r["market_price"] or 0.0,
            "gross_perf": round(value - r["total_investment"], 2),
            "gross_perf_pct": round((value / r["total_investment"] - 1), 4) if r["total_investment"] else 0.0,
            "sectors": [{"name": sector, "weight": 1.0}],
            "countries": [{"name": country_name or "Unknown", "code": code, "continent": continent, "weight": 1.0}],
            "weight": 0.0,
        })
    return holdings


def _merge_holdings(primary: List[Dict], secondary: List[Dict]) -> List[Dict]:
    # Used to combine Ghostfolio + built-in account holdings for the "all" scope — same ticker
    # from both sources is summed (mirrors accounts_engine._merge_into's coexistence model).
    merged: Dict[str, Dict] = {h["symbol"]: dict(h) for h in primary}
    for h in secondary:
        sym = h["symbol"]
        if sym not in merged:
            merged[sym] = dict(h)
            continue
        existing = merged[sym]
        existing["value"] += h["value"]
        existing["investment"] += h["investment"]
        existing["quantity"] += h["quantity"]
        existing["gross_perf"] = existing["value"] - existing["investment"]
        existing["gross_perf_pct"] = (
            round(existing["gross_perf"] / existing["investment"], 4) if existing["investment"] else 0.0
        )
    return list(merged.values())


def _classify_scope(account_id: str, active_ids: List[str]) -> Tuple[List[str], Optional[int], bool]:
    # Returns (ghost_scope_ids, builtin_account_id, include_builtin) for the given account_id.
    if account_id.startswith("acct:"):
        return [], int(account_id.split(":", 1)[1]), True
    if account_id in active_ids:
        return [account_id], None, False
    # "all" (or an unrecognised id, preserving the previous fallback-to-global behaviour) —
    # combine every configured source, mirroring accounts_engine.get_combined_holdings().
    # GHOSTFOLIO_ACCOUNTS.active is only ever populated when Ghostfolio is configured and
    # enabled (set via Settings → Ghostfolio discovery), so no separate enabled check is needed.
    return active_ids, None, True


def resolve_scope_holdings(account_id: str) -> Tuple[List[Dict], float]:
    """Canonical holdings resolver for any account scope — Ghostfolio (optional) + Built-in Accounts.

    account_id: "all" = every configured source; a Ghostfolio UUID = that account only;
    "acct:{id}" = that one built-in Trading account only (db_accounts namespacing convention).
    Returns (holdings with "weight" populated, total_value). Raises RuntimeError if the
    resolved scope has no holdings, or if Ghostfolio is in scope but unreachable.
    """
    config = load_config()
    active_ids: List[str] = config.get("GHOSTFOLIO_ACCOUNTS", {}).get("active", [])
    ghost_scope_ids, builtin_account_id, include_builtin = _classify_scope(account_id, active_ids)

    ghost_holdings: List[Dict] = []
    if ghost_scope_ids:
        client = GhostfolioXRayClient()
        if not client.is_configured:
            raise RuntimeError("Ghostfolio is not configured (check GHOSTFOLIO_URL / GHOSTFOLIO_TOKEN).")
        if not client.authenticate():
            raise RuntimeError("Ghostfolio authentication failed.")
        ghost_holdings, _ = client.get_holdings(ghost_scope_ids)

    builtin_holdings = _builtin_account_holdings(builtin_account_id) if include_builtin else []

    holdings = _merge_holdings(ghost_holdings, builtin_holdings)
    if not holdings:
        raise RuntimeError("No holdings found for this scope (Ghostfolio and/or built-in accounts may be empty).")
    total_value = sum(h["value"] for h in holdings)
    for h in holdings:
        h["weight"] = h["value"] / total_value if total_value else 0.0

    return holdings, total_value


def assemble_xray_report(account_id: str) -> Dict:
    # Combines live Ghostfolio (Tier A) + built-in Trading accounts with SQLite risk cache (Tier C).
    config = load_config()
    base_currency: str = config.get("BASE_CURRENCY", "GBP")

    holdings, total_value = resolve_scope_holdings(account_id)

    holdings_sorted = sorted(holdings, key=lambda h: h["weight"], reverse=True)

    top5_weight = sum(h["weight"] for h in holdings_sorted[:5])
    top10_weight = sum(h["weight"] for h in holdings_sorted[:10])
    hhi = sum(h["weight"] ** 2 for h in holdings)

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

    risk_cache: Dict[str, Dict] = {}
    corr_tickers: List[str] = []
    corr_matrix: List[List[float]] = []
    _raw_matrix: Optional[List] = None
    cache_date: Optional[str] = None
    div_cache: Dict[str, Dict] = {}

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
            _raw_matrix = json.loads(corr_row["matrix_json"])

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
    except Exception as e:
        logger.error("X-ray DB read failed: %s", e)
        raise
    finally:
        if conn:
            conn.close()

    # --- PSD fix for stored correlation matrix -----------------------------------
    # Runs outside the DB try/except so numpy LinAlgError is never swallowed by
    # the "X-ray DB read failed" handler.
    if _raw_matrix is not None:
        corr_matrix = _psd_fix_corr(_raw_matrix)

    for h in holdings_sorted:
        sym = h["symbol"]
        rc = risk_cache.get(sym, {})
        h["beta"] = rc.get("beta")
        h["vol"] = rc.get("vol")
        dc = div_cache.get(sym, {})
        h["dividend_yield_pct"] = dc.get("yield_pct", 0.0)
        h["dividend_income"] = dc.get("income", 0.0)

    # Weighted portfolio/benchmark return series, derived for THIS scope from per-ticker
    # cached series (xray_returns_cache) — works for any account scope.
    port_rets_series, bench_rets_series, series_warnings = get_scope_return_series(
        holdings_sorted, total_value
    )

    # --- Data warnings (initialised early so risk blocks can append to it) ------
    data_warnings: List[str] = list(series_warnings)

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

    weighted_div_yield = round(
        sum(h["weight"] * (h.get("dividend_yield_pct") or 0) for h in holdings_sorted), 4
    )
    projected_annual_income = round(
        sum(h.get("dividend_income") or 0 for h in holdings_sorted), 2
    )

    fx_map: Dict[str, float] = {}
    for h in holdings_sorted:
        ccy = (h.get("currency") or "").upper() or "UNKNOWN"
        fx_map[ccy] = fx_map.get(ccy, 0.0) + h["weight"]
    fx_exposure = sorted(
        [{"currency": k, "weight": round(v, 4)} for k, v in fx_map.items()],
        key=lambda x: x["weight"], reverse=True,
    )

    # Euler decomposition (MRC_i = Σ·w_i/σ_p·w_i·√252): sum of all MRC_i = portfolio annualised vol.
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
    for h in holdings_sorted:
        h.setdefault("marginal_risk_contribution", None)

    historical_var_95_1d: Optional[float] = None
    cvar_95_1d: Optional[float] = None
    tracking_error: Optional[float] = None
    sharpe_ratio: Optional[float] = None
    calmar_ratio: Optional[float] = None
    skewness: Optional[float] = None
    excess_kurtosis: Optional[float] = None
    max_drawdown: Optional[float] = None

    if port_rets_series is not None and len(port_rets_series) >= 30:
        try:
            import scipy.stats as _stats
            pr = np.array(port_rets_series)

            var_threshold = float(np.percentile(pr, 5))
            historical_var_95_1d = round(abs(var_threshold) * total_value, 2)
            tail = pr[pr <= var_threshold]
            if len(tail) > 0:
                cvar_95_1d = round(abs(float(tail.mean())) * total_value, 2)

            skewness = round(float(_stats.skew(pr)), 3)
            excess_kurtosis = round(float(_stats.kurtosis(pr)), 3)  # Fisher = excess

            ann_return = annualized_return(port_rets_series)
            max_drawdown = round(native_max_drawdown(port_rets_series)[0], 4)

            if bench_rets_series is not None and len(bench_rets_series) == len(port_rets_series):
                br = np.array(bench_rets_series)
                active_rets = pr - br
                tracking_error = round(float(active_rets.std() * np.sqrt(252)), 4)

            # Sharpe ratio — risk-free rate from config, default 4.5%
            rf_rate: float = float(config.get("RISK_FREE_RATE", 0.045))
            if portfolio_vol and portfolio_vol > 0:
                sharpe_ratio = round((ann_return - rf_rate) / portfolio_vol, 3)

            if max_drawdown < 0:
                calmar_ratio = round(ann_return / abs(max_drawdown), 3)

        except Exception as e:
            logger.warning("Portfolio return stats computation failed: %s", e)
    elif risk_cache:
        data_warnings.append(
            "Historical VaR, CVaR, Sharpe/Calmar ratio, tracking error and skewness need at least "
            "30 overlapping cached trading days across this scope's holdings — not yet available. "
            "Wait for the next nightly risk cache run, or trigger it from Settings → Scheduler."
        )

    if not risk_cache:
        data_warnings.append(
            "Risk metrics (beta, volatility, correlation) are not yet available. "
            "Trigger the X-ray risk cache job from Settings → Scheduler, "
            "or wait for the next scheduled run (daily at 19:00)."
        )
    elif cache_date:
        try:
            days_old = (
                datetime.datetime.now(datetime.timezone.utc).date() - datetime.date.fromisoformat(cache_date)
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

    report = {
        "account_id": account_id,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
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
    return _sanitize_floats(report)
