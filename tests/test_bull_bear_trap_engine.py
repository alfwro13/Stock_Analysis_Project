"""
tests/test_bull_bear_trap_engine.py — Market Trap & Recovery Monitor Tests

Covers:
  • _phase_severity()    — severity index is monotonically ordered
  • _derive_phase()      — priority ordering for all 7 lifecycle labels
  • _detect_bull_trap()  — SAFE / ACTIVE_SELLOFF / SEVERE_TRAP_RISK / ELEVATED_RISK paths
  • _analyse_ticker()    — full pipeline returns a valid result dict
  • _save_results()      — DB upsert round-trip; second write overwrites first
  • _load_history()      — auto-fetches and writes Parquet when the file is absent;
                           returns None gracefully when the fetch fails or returns nothing
"""

import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest
import ta

sys.path.insert(0, str(Path(__file__).parent.parent))

import database as db
from bull_bear_trap_engine import TrapEngine, _phase_severity, _PHASE_ORDER

# ── minimal config ─────────────────────────────────────────────────────────────

_CFG = {
    "NOTIFICATIONS": {"TRAP_MONITOR_ALERTS": {}},
    "SCHEDULING": {"TRAP_MONITORS": {}},
}


# ── DataFrame helpers ──────────────────────────────────────────────────────────

def _make_bull_trap_df(
    n_decline: int = 20,
    n_bounce: int = 5,
    base: float = 100.0,
    decline_frac: float = 0.15,
    down_vol: float = 2_000_000.0,
    up_vol: float = 300_000.0,
) -> pd.DataFrame:
    """
    Price declines ~15 % over n_decline bars then bounces weakly on low volume.
    EMA-20 trails well above the current close (price still below EMA).
    vol_ratio = up_vol / down_vol — caller controls severity tier.
    """
    bottom = base * (1.0 - decline_frac)
    decline = np.linspace(base, bottom, n_decline)
    bounce  = np.linspace(bottom + 0.5, bottom + 1.5, n_bounce)
    prices  = np.concatenate([decline, bounce])
    vols    = np.concatenate([np.full(n_decline, down_vol), np.full(n_bounce, up_vol)])
    return pd.DataFrame({
        "Open": prices, "High": prices + 1.0,
        "Low":  prices - 1.0, "Close": prices,
        "Volume": vols,
    })


def _make_rising_df(n: int = 25, base: float = 80.0, top: float = 100.0) -> pd.DataFrame:
    """Steadily rising prices — close will be above EMA-20 → SAFE."""
    prices = np.linspace(base, top, n)
    return pd.DataFrame({
        "Open": prices, "High": prices + 1.0,
        "Low":  prices - 1.0, "Close": prices,
        "Volume": np.full(n, 1_000_000.0),
    })


def _make_active_selloff_df() -> pd.DataFrame:
    """
    8 stable bars at 100, then a zigzag decline (every 3rd step bounces slightly).
    Guarantees: close << EMA (EMA anchored from stable period), at least some up
    days in the recent window (so the vol-ratio branch is reached), and the last
    bar is always a down day → ACTIVE_SELLOFF.
    """
    stable = list(np.full(8, 100.0))
    prices = stable[:]
    p = 100.0
    for i in range(17):
        p = p + 0.5 if i % 3 == 1 else p - 2.5
        prices.append(p)
    prices = np.array(prices)
    prices[-1] = prices[-2] - 1.0  # force last bar down
    return pd.DataFrame({
        "Open": prices, "High": prices + 0.5,
        "Low":  prices - 0.5, "Close": prices,
        "Volume": np.full(25, 1_000_000.0),
    })


def _make_fetch_df(n: int = 60) -> pd.DataFrame:
    """Minimal yfinance-style DataFrame with a tz-aware DatetimeIndex."""
    prices = np.linspace(90.0, 100.0, n)
    idx = pd.date_range("2026-01-01", periods=n, freq="B", tz="UTC")
    return pd.DataFrame({
        "Open": prices, "High": prices + 1.0,
        "Low":  prices - 1.0, "Close": prices,
        "Volume": np.full(n, 1_000_000.0),
    }, index=idx)


# ── _phase_severity() ──────────────────────────────────────────────────────────

class TestPhaseSeverity:
    def test_active_selloff_is_most_severe(self):
        assert _phase_severity("ACTIVE_SELLOFF") == 0

    def test_neutral_is_least_severe(self):
        assert _phase_severity("NEUTRAL") == len(_PHASE_ORDER) - 1

    def test_ordering_is_monotonic(self):
        scores = [_phase_severity(p) for p in _PHASE_ORDER]
        assert scores == sorted(scores), "Severity must increase along _PHASE_ORDER"

    def test_unknown_phase_returns_sentinel(self):
        assert _phase_severity("UNKNOWN") == len(_PHASE_ORDER)


# ── _derive_phase() ────────────────────────────────────────────────────────────

class TestDerivePhase:
    def setup_method(self):
        self.engine = TrapEngine(_CFG)

    def _derive(
        self,
        bull_level: str = "SAFE",
        bear_level: str = "SAFE",
        cap_level:  str = "NONE",
        wyk_level:  str = "NONE",
        ema_dist:   float = 0.0,
    ) -> str:
        return self.engine._derive_phase(
            {"level": bull_level}, {"level": bear_level},
            {"level": cap_level},  {"level": wyk_level},
            ema_dist,
        )

    def test_active_selloff_beats_everything(self):
        # Even when capitulation is also signalling, ACTIVE_SELLOFF wins
        assert self._derive(bull_level="ACTIVE_SELLOFF", cap_level="CAPITULATION_FORMING") == "ACTIVE_SELLOFF"

    def test_capitulation_forming_beats_bull_trap(self):
        assert self._derive(bull_level="SEVERE_TRAP_RISK", cap_level="CAPITULATION_FORMING") == "CAPITULATION_FORMING"

    def test_bull_trap_risk_from_severe(self):
        assert self._derive(bull_level="SEVERE_TRAP_RISK") == "BULL_TRAP_RISK"

    def test_bull_trap_risk_from_elevated(self):
        assert self._derive(bull_level="ELEVATED_RISK") == "BULL_TRAP_RISK"

    def test_bull_trap_beats_bear_trap(self):
        assert self._derive(bull_level="ELEVATED_RISK", bear_level="CONFIRMED_BEAR_TRAP") == "BULL_TRAP_RISK"

    def test_bear_trap_risk_from_confirmed(self):
        assert self._derive(bear_level="CONFIRMED_BEAR_TRAP") == "BEAR_TRAP_RISK"

    def test_bear_trap_risk_from_possible(self):
        assert self._derive(bear_level="POSSIBLE_BEAR_TRAP") == "BEAR_TRAP_RISK"

    def test_accumulation_from_wyckoff_phase(self):
        assert self._derive(wyk_level="ACCUMULATION_PHASE") == "ACCUMULATION"

    def test_caution_from_capitulation_watch(self):
        assert self._derive(cap_level="WATCH") == "CAUTION"

    def test_caution_from_wyckoff_squeeze(self):
        assert self._derive(wyk_level="SQUEEZE_FORMING") == "CAUTION"

    def test_neutral_when_all_safe(self):
        assert self._derive() == "NEUTRAL"


# ── _detect_bull_trap() ────────────────────────────────────────────────────────

class TestDetectBullTrap:
    def setup_method(self):
        self.engine = TrapEngine(_CFG)

    def _run(self, df: pd.DataFrame) -> dict:
        close = df["Close"]
        vol   = df["Volume"]
        ema20 = ta.trend.EMAIndicator(close=close, window=20).ema_indicator()
        rsi14 = ta.momentum.RSIIndicator(close=close, window=14).rsi()
        return self.engine._detect_bull_trap(df, close, vol, ema20, rsi14)

    def test_safe_when_price_above_ema(self):
        result = self._run(_make_rising_df())
        assert result["level"] == "SAFE"

    def test_active_selloff_when_price_still_declining_below_ema(self):
        result = self._run(_make_active_selloff_df())
        assert result["level"] == "ACTIVE_SELLOFF"

    def test_severe_trap_risk_on_very_low_volume_bounce(self):
        # vol_ratio = 300 K / 2 M = 0.15, well below the 0.75 severe threshold
        result = self._run(_make_bull_trap_df(down_vol=2_000_000.0, up_vol=300_000.0))
        assert result["level"] == "SEVERE_TRAP_RISK"

    def test_elevated_risk_on_moderate_volume_bounce(self):
        # vol_ratio ≈ 0.82: below 0.90 elevated threshold, above 0.75 severe threshold
        result = self._run(_make_bull_trap_df(down_vol=1_000_000.0, up_vol=820_000.0))
        assert result["level"] == "ELEVATED_RISK"

    def test_vol_ratio_present_on_elevated_or_severe(self):
        result = self._run(_make_bull_trap_df())
        assert "vol_ratio" in result
        assert 0.0 < result["vol_ratio"] < 1.0

    def test_notes_non_empty_on_severe(self):
        result = self._run(_make_bull_trap_df())
        assert result.get("notes"), "Expected notes string on SEVERE_TRAP_RISK"


# ── _analyse_ticker() integration ─────────────────────────────────────────────

class TestAnalyseTicker:
    def setup_method(self):
        self.engine = TrapEngine(_CFG)

    def test_returns_none_for_insufficient_data(self):
        # 12 rows < 22 minimum
        df = _make_bull_trap_df(n_decline=10, n_bounce=2)
        assert self.engine._analyse_ticker("TEST", df) is None

    def test_result_has_required_keys(self):
        result = self.engine._analyse_ticker("FAKE", _make_bull_trap_df())
        assert result is not None
        for key in ("ticker", "phase", "bull_trap_level", "bear_trap_level",
                    "cap_level", "wyckoff_level", "ema_distance", "rsi", "scan_ts"):
            assert key in result, f"Missing key in result: {key}"

    def test_ticker_preserved_in_result(self):
        result = self.engine._analyse_ticker("NVDA", _make_bull_trap_df())
        assert result["ticker"] == "NVDA"

    def test_bull_trap_scenario_sets_correct_phase_and_level(self):
        # down_vol 2 M vs up_vol 300 K → vol_ratio 0.15 → SEVERE_TRAP_RISK → phase BULL_TRAP_RISK
        result = self.engine._analyse_ticker("TEST", _make_bull_trap_df(
            down_vol=2_000_000.0, up_vol=300_000.0,
        ))
        assert result["phase"] == "BULL_TRAP_RISK"
        assert result["bull_trap_level"] == "SEVERE_TRAP_RISK"


# ── _save_results() / DB round-trip ───────────────────────────────────────────

class TestSaveResults:
    def setup_method(self):
        self.engine = TrapEngine(_CFG)

    @staticmethod
    def _row(ticker: str, phase: str = "BULL_TRAP_RISK") -> dict:
        return {
            "ticker": ticker, "phase": phase,
            "bull_trap_level": "SEVERE_TRAP_RISK", "bull_trap_vol_ratio": 0.42,
            "bull_trap_notes": "Test note.",
            "bear_trap_level": "SAFE",   "bear_trap_notes": None,
            "cap_level": "NONE",         "cap_vol_zscore": 1.1, "cap_notes": None,
            "wyckoff_level": "NONE",     "wyckoff_bb_width": 3.5, "wyckoff_notes": None,
            "ema_distance": -5.2, "rsi": 38.0,
            "scan_ts": "2026-06-10 12:00:00",
        }

    def test_row_readable_after_save(self):
        self.engine._save_results([self._row("TSTT1")])
        conn = db.get_connection()
        try:
            saved = conn.execute(
                "SELECT * FROM trap_monitor_results WHERE ticker = 'TSTT1'"
            ).fetchone()
        finally:
            conn.execute("DELETE FROM trap_monitor_results WHERE ticker = 'TSTT1'")
            conn.commit()
            conn.close()
        assert saved is not None
        assert saved["phase"] == "BULL_TRAP_RISK"
        assert abs(saved["bull_trap_vol_ratio"] - 0.42) < 1e-6
        assert abs(saved["ema_distance"] - (-5.2)) < 1e-6

    def test_upsert_overwrites_existing_row(self):
        self.engine._save_results([self._row("TSTT2", phase="NEUTRAL")])
        self.engine._save_results([self._row("TSTT2", phase="ACCUMULATION")])
        conn = db.get_connection()
        try:
            rows = conn.execute(
                "SELECT phase FROM trap_monitor_results WHERE ticker = 'TSTT2'"
            ).fetchall()
        finally:
            conn.execute("DELETE FROM trap_monitor_results WHERE ticker = 'TSTT2'")
            conn.commit()
            conn.close()
        assert len(rows) == 1, "Upsert must not create duplicate rows"
        assert rows[0]["phase"] == "ACCUMULATION"

    def test_multiple_tickers_saved_in_one_call(self):
        rows = [self._row(f"TSTT{i}") for i in range(3, 6)]
        self.engine._save_results(rows)
        conn = db.get_connection()
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM trap_monitor_results "
                "WHERE ticker IN ('TSTT3', 'TSTT4', 'TSTT5')"
            ).fetchone()[0]
        finally:
            conn.execute(
                "DELETE FROM trap_monitor_results WHERE ticker IN ('TSTT3','TSTT4','TSTT5')"
            )
            conn.commit()
            conn.close()
        assert count == 3


# ── _load_history() — auto-fetch ──────────────────────────────────────────────

class TestLoadHistoryAutoFetch:
    def setup_method(self):
        self.engine = TrapEngine(_CFG)

    def test_returns_df_when_parquet_already_exists(self, tmp_path):
        df = _make_fetch_df()
        pq = tmp_path / "EXIST.parquet"
        df.to_parquet(pq, engine="pyarrow")
        with patch("bull_bear_trap_engine.HISTORICAL_DIR", tmp_path):
            result = self.engine._load_history("EXIST")
        assert result is not None
        assert len(result) <= 60

    def test_auto_fetches_and_writes_parquet_when_missing(self, tmp_path):
        fetch_df = _make_fetch_df()
        with (
            patch("bull_bear_trap_engine.HISTORICAL_DIR", tmp_path),
            patch("bull_bear_trap_engine.yahoo_engine.get_price_history",
                  return_value={"NEWT": fetch_df}),
        ):
            result = self.engine._load_history("NEWT")
        assert result is not None, "Expected DataFrame after auto-fetch"
        assert (tmp_path / "NEWT.parquet").exists(), "Parquet must be written to disk"

    def test_returns_none_when_fetch_returns_empty(self, tmp_path):
        with (
            patch("bull_bear_trap_engine.HISTORICAL_DIR", tmp_path),
            patch("bull_bear_trap_engine.yahoo_engine.get_price_history",
                  return_value={}),
        ):
            result = self.engine._load_history("NOPE")
        assert result is None

    def test_returns_none_when_fetch_raises(self, tmp_path):
        with (
            patch("bull_bear_trap_engine.HISTORICAL_DIR", tmp_path),
            patch("bull_bear_trap_engine.yahoo_engine.get_price_history",
                  side_effect=Exception("network error")),
        ):
            result = self.engine._load_history("FAIL")
        assert result is None


# ── _detect_bear_trap() ────────────────────────────────────────────────────────

def _make_bear_trap_df(
    n: int = 30,
    breakdown_low: float = 97.0,
    breakdown_close: float = 100.5,
    vol_ratio: float = 0.8,
) -> pd.DataFrame:
    """
    Prices flat at 100 for n-1 bars (lows at 99.9); one breakdown bar where the low
    clearly breaches the prior-20-day low (99.9) and the close recovers above it.
    The BB lower band (~99.8) serves as the computed support level.
    """
    base_vol      = 1_000_000.0
    stable_prices = np.full(n - 1, 100.0)
    stable_lows   = np.full(n - 1, 99.9)
    prices = np.append(stable_prices, breakdown_close)
    lows   = np.append(stable_lows, breakdown_low)
    vols   = np.append(np.full(n - 1, base_vol), base_vol * vol_ratio)
    return pd.DataFrame({
        "Open": prices, "High": prices + 0.1,
        "Low":  lows, "Close": prices, "Volume": vols,
    })


class TestDetectBearTrap:
    def setup_method(self):
        self.engine = TrapEngine(_CFG)

    def _run(self, df: pd.DataFrame) -> dict:
        close = df["Close"]
        vol   = df["Volume"]
        low   = df["Low"]
        bb    = ta.volatility.BollingerBands(close=close, window=20, window_dev=2)
        rsi14 = ta.momentum.RSIIndicator(close=close, window=14).rsi()
        return self.engine._detect_bear_trap(df, close, vol, low, bb, rsi14)

    def test_safe_when_no_support_breach(self):
        # Rising prices, low always above prior 20-day low → no breakdown
        result = self._run(_make_rising_df(n=35))
        assert result["level"] == "SAFE"

    def test_safe_with_insufficient_data(self):
        df = _make_rising_df(n=15)
        result = self._run(df)
        assert result["level"] == "SAFE"

    def test_confirmed_bear_trap_on_very_low_volume(self):
        # vol_ratio=0.5, below default bear_vol_ratio=1.20 → CONFIRMED
        result = self._run(_make_bear_trap_df(vol_ratio=0.5))
        assert result["level"] == "CONFIRMED_BEAR_TRAP"

    def test_possible_bear_trap_on_moderate_volume(self):
        # vol_ratio=1.5, above 1.20 threshold → POSSIBLE
        result = self._run(_make_bear_trap_df(vol_ratio=1.5))
        assert result["level"] == "POSSIBLE_BEAR_TRAP"

    def test_safe_when_close_did_not_recover_above_support(self):
        # close = support - 0.5 (still below support)
        result = self._run(_make_bear_trap_df(close_recovery=-0.5))
        assert result["level"] == "SAFE"

    def test_notes_present_on_confirmed(self):
        result = self._run(_make_bear_trap_df(vol_ratio=0.5))
        assert result.get("notes"), "Expected notes string on CONFIRMED_BEAR_TRAP"


# ── _detect_capitulation() ─────────────────────────────────────────────────────

def _make_capitulation_df(
    n: int = 40,
    vol_multiplier: float = 5.0,  # today's vol vs 20d mean
    rsi_seed_low: bool = True,     # drive RSI below 30 via big decline
    close_in_upper_half: bool = True,
) -> pd.DataFrame:
    """Declining prices ending in a volume-climax bar with extreme RSI."""
    # Big decline to force RSI deeply oversold
    prices = np.linspace(100.0, 60.0, n - 1) if rsi_seed_low else np.linspace(95.0, 90.0, n - 1)
    base_vol = 1_000_000.0
    vols = np.full(n - 1, base_vol)
    # Capitulation bar
    cap_high  = prices[-1] + 5.0
    cap_low   = prices[-1] - 10.0
    cap_close = (cap_high if close_in_upper_half else prices[-1] - 4.0)
    prices = np.append(prices, cap_close)
    vols   = np.append(vols, base_vol * vol_multiplier)
    highs  = np.append(prices[:-1] + 1.0, cap_high)
    lows   = np.append(prices[:-1] - 1.0, cap_low)
    return pd.DataFrame({
        "Open": prices, "High": highs, "Low": lows,
        "Close": prices, "Volume": vols,
    })


class TestDetectCapitulation:
    def setup_method(self):
        self.engine = TrapEngine(_CFG)

    def _run(self, df: pd.DataFrame) -> dict:
        close = df["Close"]
        high  = df["High"]
        low   = df["Low"]
        vol   = df["Volume"]
        ema20 = ta.trend.EMAIndicator(close=close, window=20).ema_indicator()
        rsi14 = ta.momentum.RSIIndicator(close=close, window=14).rsi()
        return self.engine._detect_capitulation(df, close, high, low, vol, ema20, rsi14)

    def test_none_on_insufficient_data(self):
        df = _make_capitulation_df(n=20)
        result = self._run(df)
        assert result["level"] == "NONE"

    def test_none_when_volume_not_elevated(self):
        # vol_multiplier=1 → z-score near 0, gate_vol fails
        result = self._run(_make_capitulation_df(vol_multiplier=1.0))
        assert result["level"] == "NONE"

    def test_capitulation_forming_on_all_gates_met_upper_close(self):
        # Large volume spike + oversold RSI + close in upper half of range
        result = self._run(_make_capitulation_df(vol_multiplier=5.0, close_in_upper_half=True))
        assert result["level"] == "CAPITULATION_FORMING"

    def test_watch_when_close_in_lower_half(self):
        result = self._run(_make_capitulation_df(vol_multiplier=5.0, close_in_upper_half=False))
        assert result["level"] == "WATCH"

    def test_vol_zscore_present_in_result(self):
        result = self._run(_make_capitulation_df(vol_multiplier=5.0))
        assert "vol_zscore" in result


# ── _detect_wyckoff() ─────────────────────────────────────────────────────────

def _make_wyckoff_df(n: int = 50) -> pd.DataFrame:
    """
    Downtrend for 2/3 of bars, then low-volatility consolidation (prices barely move,
    volume dries up, BB width contracts) — designed to trigger all three severity gates.
    """
    decline = np.linspace(100.0, 75.0, n // 2)
    base_p  = 75.0
    # Tight sideways: prices jitter ±0.1 so BB squeeze is extreme
    consolidate = base_p + np.sin(np.linspace(0, np.pi, n - n // 2)) * 0.1
    prices  = np.concatenate([decline, consolidate])
    # Volume: high during decline, very low during consolidation
    vols    = np.concatenate([
        np.full(n // 2, 2_000_000.0),
        np.full(n - n // 2, 200_000.0),
    ])
    return pd.DataFrame({
        "Open": prices, "High": prices + 0.05,
        "Low":  prices - 0.05, "Close": prices,
        "Volume": vols,
    })


class TestDetectWyckoff:
    def setup_method(self):
        self.engine = TrapEngine(_CFG)

    def _run(self, df: pd.DataFrame) -> dict:
        close = df["Close"]
        vol   = df["Volume"]
        bb    = ta.volatility.BollingerBands(close=close, window=20, window_dev=2)
        atr   = ta.volatility.AverageTrueRange(
            high=df["High"], low=df["Low"], close=close, window=14
        ).average_true_range()
        return self.engine._detect_wyckoff(df, close, vol, bb, atr)

    def test_none_on_insufficient_data(self):
        df = _make_rising_df(n=20)
        result = self._run(df)
        assert result["level"] == "NONE"

    def test_none_when_prices_rising_and_volatile(self):
        # Rising prices → BB width not at historical minimum → gate fails
        result = self._run(_make_rising_df(n=50))
        assert result["level"] == "NONE"

    def test_accumulation_phase_on_tight_consolidation_after_downtrend(self):
        result = self._run(_make_wyckoff_df())
        assert result["level"] in ("ACCUMULATION_PHASE", "SQUEEZE_FORMING"), (
            f"Expected accumulation signal, got {result['level']!r}"
        )

    def test_bb_width_present_in_result(self):
        result = self._run(_make_wyckoff_df())
        assert "bb_width" in result
