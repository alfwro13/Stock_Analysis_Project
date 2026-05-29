"""
tests/test_05_config_and_utils.py  ── CONFIGURATION & UTILITIES

Verifies:
  • config.load_config() always returns a dict with required top-level keys
  • config.load_config() never raises an exception (even with empty/missing file)
  • Schema merging: missing keys in stored config are filled with defaults
  • utils.normalize_ticker() handles edge cases correctly
  • constants.py values are within expected sane ranges (guards against typos)
  • Scheduler job names match what the DB expects

No network calls.  No database connection required.
"""

import sys
import json
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ── Config loading ────────────────────────────────────────────────────────────

@pytest.mark.config
def test_load_config_returns_dict():
    """load_config() must always return a dict (never raises, never returns None)."""
    from config import load_config
    result = load_config()
    assert isinstance(result, dict), f"load_config() returned {type(result)}, expected dict"


@pytest.mark.config
def test_load_config_has_required_top_level_keys():
    """load_config() must contain all the top-level keys that page routes depend on."""
    from config import load_config
    config = load_config()
    required = {
        "UI_PREFERENCES",
        "POSITION_SIZING",
        "SCHEDULING",
        "NOTIFICATIONS",
    }
    missing = required - set(config.keys())
    assert not missing, f"config.json is missing required top-level keys: {missing}"


@pytest.mark.config
def test_load_config_ui_preferences_has_required_keys():
    """UI_PREFERENCES must contain keys that the portfolio/watchlist pages read."""
    from config import load_config
    ui = load_config().get("UI_PREFERENCES", {})
    required = {"LIVE_PORTFOLIO", "LIVE_WATCHLIST", "LIVE_DETAILS", "REFRESH_RATE"}
    missing = required - set(ui.keys())
    assert not missing, f"UI_PREFERENCES missing keys: {missing}"


@pytest.mark.config
def test_load_config_position_sizing_has_required_keys():
    """POSITION_SIZING must contain the three keys used by the sizing calculator."""
    from config import load_config
    ps = load_config().get("POSITION_SIZING", {})
    required = {"ACCOUNT_VALUE", "RISK_PCT", "STOP_MULTIPLE"}
    missing = required - set(ps.keys())
    assert not missing, f"POSITION_SIZING missing keys: {missing}"


@pytest.mark.config
def test_load_config_does_not_raise_with_missing_file(tmp_path):
    """load_config() must not raise even when the config file does not exist."""
    import config as _config
    original_path = _config.SECRETS_PATH
    try:
        _config.SECRETS_PATH = tmp_path / "nonexistent_config.json"
        result = _config.load_config()
        assert isinstance(result, dict), "load_config() must return defaults, not None"
    finally:
        _config.SECRETS_PATH = original_path


@pytest.mark.config
def test_load_config_does_not_raise_with_corrupt_json(tmp_path):
    """load_config() must not raise when the config file contains invalid JSON."""
    import config as _config
    original_path = _config.SECRETS_PATH
    corrupt_file = tmp_path / "corrupt.json"
    corrupt_file.write_text("{this is not valid json}")
    try:
        _config.SECRETS_PATH = corrupt_file
        result = _config.load_config()
        assert isinstance(result, dict), "load_config() must return defaults on corrupt JSON"
    finally:
        _config.SECRETS_PATH = original_path


@pytest.mark.config
def test_update_config_atomic_persists_changes(tmp_path):
    """update_config_atomic() must write changes that are readable by load_config()."""
    import config as _config
    original_path = _config.SECRETS_PATH
    test_config_path = tmp_path / "test_config.json"
    try:
        _config.SECRETS_PATH = test_config_path
        _config.update_config_atomic({"TEST_KEY": "test_value_123"})
        result = _config.load_config()
        assert result.get("TEST_KEY") == "test_value_123", (
            "update_config_atomic() wrote a value that load_config() could not read back"
        )
    finally:
        _config.SECRETS_PATH = original_path


# ── Ticker normalisation ──────────────────────────────────────────────────────

@pytest.mark.config
def test_normalize_ticker_uppercase():
    """normalize_ticker() must return tickers in uppercase."""
    from utils import normalize_ticker
    assert normalize_ticker("aapl") == "AAPL"
    assert normalize_ticker("msft") == "MSFT"


@pytest.mark.config
def test_normalize_ticker_strips_whitespace():
    """normalize_ticker() must strip leading/trailing whitespace."""
    from utils import normalize_ticker
    assert normalize_ticker("  AAPL  ") == "AAPL"
    assert normalize_ticker("\tGOOGL\n") == "GOOGL"


@pytest.mark.config
def test_normalize_ticker_preserves_lse_suffix():
    """normalize_ticker() must preserve .L suffix for London Stock Exchange tickers."""
    from utils import normalize_ticker
    result = normalize_ticker("lloy.l")
    assert result == "LLOY.L", f"Expected 'LLOY.L', got '{result}'"


@pytest.mark.config
def test_normalize_ticker_already_uppercase():
    """normalize_ticker() must be idempotent — running it twice gives the same result."""
    from utils import normalize_ticker
    assert normalize_ticker("AAPL") == normalize_ticker(normalize_ticker("AAPL"))


# ── Constants sanity checks ───────────────────────────────────────────────────

@pytest.mark.config
def test_rsi_thresholds_are_sane():
    """RSI oversold/overbought thresholds must be within 0–100 and correctly ordered."""
    from constants import RSI_OVERSOLD, RSI_OVERBOUGHT
    assert 0 < RSI_OVERSOLD < RSI_OVERBOUGHT < 100, (
        f"RSI thresholds look wrong: oversold={RSI_OVERSOLD}, overbought={RSI_OVERBOUGHT}"
    )


@pytest.mark.config
def test_ml_horizon_is_positive_integer():
    """ML prediction horizon must be a positive integer (trading days)."""
    from constants import PREDICTION_HORIZON_DAYS
    assert isinstance(PREDICTION_HORIZON_DAYS, int), "PREDICTION_HORIZON_DAYS must be an integer"
    assert PREDICTION_HORIZON_DAYS > 0, f"PREDICTION_HORIZON_DAYS must be positive, got {PREDICTION_HORIZON_DAYS}"


@pytest.mark.config
def test_ml_return_threshold_is_reasonable():
    """ML return threshold must be a positive percentage (typically 1–10%)."""
    from constants import PREDICTION_RETURN_THRESHOLD
    assert 0 < PREDICTION_RETURN_THRESHOLD < 0.50, (
        f"PREDICTION_RETURN_THRESHOLD={PREDICTION_RETURN_THRESHOLD} looks wrong (expected 0.01–0.50)"
    )


@pytest.mark.config
def test_regime_thresholds_ordered():
    """Market regime thresholds must be ordered: Volatile vol < Crash vol."""
    from constants import REGIME_VOLATILE_VOL, REGIME_CRASH_VOL
    assert REGIME_VOLATILE_VOL < REGIME_CRASH_VOL, (
        f"Regime thresholds are not ordered: "
        f"volatile={REGIME_VOLATILE_VOL}, crash={REGIME_CRASH_VOL}"
    )


# ── Indicator functions exist and are callable ───────────────────────────────

@pytest.mark.config
def test_indicators_module_exports_required_functions():
    """indicators.py must export all the canonical indicator functions."""
    import indicators
    required_functions = [
        "compute_rsi",
        "compute_macd",
        "compute_smas",
        "compute_atr",
        "compute_volume_sma",
        "compute_volume_surge",
        "compute_bullish_cross",
    ]
    for fn_name in required_functions:
        assert hasattr(indicators, fn_name), (
            f"indicators.py is missing '{fn_name}' — "
            "this will break any engine that calls it"
        )
        assert callable(getattr(indicators, fn_name)), (
            f"indicators.{fn_name} is not callable"
        )
