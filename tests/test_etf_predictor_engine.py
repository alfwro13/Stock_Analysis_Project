"""Tests for ETF predictor engine pure functions."""
import sys
import pytest
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import date

sys.path.insert(0, str(Path(__file__).parent.parent))

from etf_predictor_engine import (
    detect_fx_pair,
    get_next_open_date,
    _compute_holdings_prediction,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_daily_df(tickers_closes: dict, days: int = 5) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=days, freq="D")
    data = {}
    for t, values in tickers_closes.items():
        if isinstance(values, (int, float)):
            data[t] = [values] * days
        else:
            data[t] = list(values) + [values[-1]] * max(0, days - len(values))
    return pd.DataFrame(data, index=idx)


def _equal_weight_holdings(tickers):
    n = len(tickers)
    return [{"ticker": t, "weight": 1.0 / n} for t in tickers]


# ── detect_fx_pair ────────────────────────────────────────────────────────────

class TestDetectFxPair:
    def test_same_currency_returns_none(self):
        assert detect_fx_pair("USD", ["USD", "USD", "USD"]) is None

    def test_gbp_usd_returns_gbpusd(self):
        pair = detect_fx_pair("GBP", ["USD", "USD", "USD"])
        assert pair == "GBPUSD=X"

    def test_eur_usd_returns_eurusd(self):
        pair = detect_fx_pair("EUR", ["USD", "USD"])
        assert pair == "EURUSD=X"

    def test_gbp_pence_normalised(self):
        # GBp (pence) should be normalised to GBP
        pair = detect_fx_pair("GBp", ["USD", "USD"])
        assert pair == "GBPUSD=X"

    def test_gbx_normalised(self):
        pair = detect_fx_pair("GBX", ["USD", "USD"])
        assert pair == "GBPUSD=X"

    def test_most_common_constituent_currency_used(self):
        # 2 USD, 1 EUR → most common is USD
        pair = detect_fx_pair("GBP", ["USD", "USD", "EUR"])
        assert pair == "GBPUSD=X"

    def test_empty_constituents_returns_none(self):
        assert detect_fx_pair("GBP", []) is None

    def test_usd_etf_eur_constituents(self):
        pair = detect_fx_pair("USD", ["EUR", "EUR"])
        assert pair == "USDEUR=X"


# ── get_next_open_date ────────────────────────────────────────────────────────

class TestGetNextOpenDate:
    def test_returns_a_date(self):
        result = get_next_open_date("NYSE")
        assert isinstance(result, date)

    def test_returns_a_date_for_lse(self):
        result = get_next_open_date("LSE")
        assert isinstance(result, date)

    def test_next_open_is_not_in_the_far_past(self):
        from datetime import datetime, timezone
        result = get_next_open_date("NYSE")
        today = datetime.now(timezone.utc).date()
        assert result >= today


# ── _compute_holdings_prediction ─────────────────────────────────────────────

class TestComputeHoldingsPrediction:
    def test_basic_no_fx(self):
        """All holdings up 10%, no FX → predicted change ~10%."""
        tickers = ["A", "B", "C"]
        df = _make_daily_df({t: [100.0, 110.0] for t in tickers}, days=2)
        result = _compute_holdings_prediction(
            df, _equal_weight_holdings(tickers),
            fx_rate=1.0, last_etf_close=100.0,
            intraday_returns=None, fx_pair=None,
        )
        assert result is not None
        assert abs(result["predicted_change_pct"] - 10.0) < 0.01

    def test_uses_intraday_returns_when_provided(self):
        tickers = ["A", "B", "C"]
        df = _make_daily_df({t: [100.0, 100.0] for t in tickers}, days=2)
        intraday = {t: 0.05 for t in tickers}
        result = _compute_holdings_prediction(
            df, _equal_weight_holdings(tickers),
            fx_rate=1.0, last_etf_close=100.0,
            intraday_returns=intraday, fx_pair=None,
        )
        assert result is not None
        assert abs(result["predicted_change_pct"] - 5.0) < 0.01

    def test_returns_none_when_fewer_than_3_holdings(self):
        holdings = [{"ticker": t, "weight": 0.5} for t in ["A", "B"]]
        df = _make_daily_df({"A": [100.0, 105.0], "B": [100.0, 105.0]}, days=2)
        result = _compute_holdings_prediction(
            df, holdings, fx_rate=1.0, last_etf_close=100.0,
            intraday_returns=None, fx_pair=None,
        )
        assert result is None

    def test_fx_weakening_etf_currency_raises_price(self):
        """ETF currency weakens vs constituent currency → constituent assets worth more → price rises."""
        tickers = ["A", "B", "C"]
        fx_pair = "GBPUSD=X"
        # 0% equity change, GBP weakened 5% (pair falls from 1.25 to 1.1875)
        df = _make_daily_df({
            fx_pair: [1.25, 1.1875],
            **{t: [100.0, 100.0] for t in tickers}
        }, days=2)
        result = _compute_holdings_prediction(
            df, _equal_weight_holdings(tickers),
            fx_rate=1.1875, last_etf_close=100.0,
            intraday_returns=None, fx_pair=fx_pair,
        )
        assert result is not None
        assert result["fx_adjustment_pct"] > 0

    def test_no_fx_pair_in_df_gives_zero_adjustment(self):
        tickers = ["A", "B", "C"]
        df = _make_daily_df({t: [100.0, 100.0] for t in tickers}, days=2)
        result = _compute_holdings_prediction(
            df, _equal_weight_holdings(tickers),
            fx_rate=1.26, last_etf_close=100.0,
            intraday_returns=None, fx_pair="GBPUSD=X",
        )
        # fx_pair not in df → fx_prev is None → fx_adjustment = 0
        assert result is not None
        assert abs(result["fx_adjustment_pct"]) < 0.001

    def test_n_holdings_used_counts_available_tickers(self):
        tickers = ["A", "B", "C", "D"]
        df = _make_daily_df({"A": [100.0, 105.0], "B": [100.0, 105.0], "C": [100.0, 105.0]}, days=2)
        # D has no data in df
        holdings = [{"ticker": t, "weight": 0.25} for t in tickers]
        result = _compute_holdings_prediction(
            df, holdings, fx_rate=1.0, last_etf_close=100.0,
            intraday_returns=None, fx_pair=None,
        )
        assert result is not None
        assert result["n_holdings_used"] == 3

    def test_contributions_sorted_by_absolute_contribution(self):
        holdings = [
            {"ticker": "A", "weight": 0.8},
            {"ticker": "B", "weight": 0.1},
            {"ticker": "C", "weight": 0.1},
        ]
        df = _make_daily_df({"A": [100.0, 110.0], "B": [100.0, 105.0], "C": [100.0, 101.0]}, days=2)
        result = _compute_holdings_prediction(
            df, holdings, fx_rate=1.0, last_etf_close=100.0,
            intraday_returns=None, fx_pair=None,
        )
        assert result is not None
        contribs = result["contributions"]
        abs_contribs = [abs(c["contribution_pct"]) for c in contribs]
        assert abs_contribs == sorted(abs_contribs, reverse=True)
