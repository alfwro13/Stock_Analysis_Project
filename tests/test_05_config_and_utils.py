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


@pytest.mark.config
def test_update_config_atomic_strips_sensitive_keys(tmp_path):
    """update_config_atomic() must never write SENSITIVE_KEYS into config.json at all."""
    import config as _config
    original_path = _config.SECRETS_PATH
    test_config_path = tmp_path / "test_config.json"
    try:
        _config.SECRETS_PATH = test_config_path
        _config.update_config_atomic({"API_TOKEN": "secret-credential-value", "PORT": 9999})
        raw = json.loads(test_config_path.read_text())
        for key in _config.SENSITIVE_KEYS:
            assert key not in raw, f"Sensitive key '{key}' must never appear in config.json"
        assert raw.get("PORT") == 9999, "Non-sensitive key must still be written"
    finally:
        _config.SECRETS_PATH = original_path


@pytest.mark.config
def test_load_config_strips_deprecated_schedule_keys(tmp_path):
    """load_config() must silently drop DEPRECATED_SCHEDULE_KEYS from a saved config."""
    import config as _config
    original_path = _config.SECRETS_PATH
    stale_config_path = tmp_path / "stale.json"
    stale = {"SCHEDULING": {"UNIVERSE_FUNDAMENTALS": {"ENABLED": True}, "QUANT_ANALYSIS": {"TIME": "18:00"}}}
    stale_config_path.write_text(json.dumps(stale))
    try:
        _config.SECRETS_PATH = stale_config_path
        result = _config.load_config()
        scheduling = result.get("SCHEDULING", {})
        assert "UNIVERSE_FUNDAMENTALS" not in scheduling, (
            "Deprecated UNIVERSE_FUNDAMENTALS key must be stripped during load"
        )
        assert "QUANT_ANALYSIS" in scheduling, "Valid scheduling keys must be preserved"
    finally:
        _config.SECRETS_PATH = original_path


@pytest.mark.config
def test_load_config_migrates_et_as_utc_defaults(tmp_path):
    """load_config() must replace the legacy ET-as-UTC time defaults with correct UTC windows."""
    import config as _config
    original_path = _config.SECRETS_PATH
    legacy_config_path = tmp_path / "legacy.json"
    legacy = {
        "SCHEDULING": {
            "SENTIMENT_ENGINE": {"START_TIME": "09:30", "END_TIME": "16:00"},
            "CRASH_ALERTS":     {"START_TIME": "09:30", "END_TIME": "16:00"},
        }
    }
    legacy_config_path.write_text(json.dumps(legacy))
    try:
        _config.SECRETS_PATH = legacy_config_path
        result = _config.load_config()
        for key in ("SENTIMENT_ENGINE", "CRASH_ALERTS"):
            block = result["SCHEDULING"][key]
            assert block["START_TIME"] == "08:00", (
                f"SCHEDULING.{key}.START_TIME must be migrated from '09:30' to '08:00'"
            )
            assert block["END_TIME"] == "21:00", (
                f"SCHEDULING.{key}.END_TIME must be migrated from '16:00' to '21:00'"
            )
    finally:
        _config.SECRETS_PATH = original_path


@pytest.mark.config
def test_load_config_fills_missing_xray_and_file_logging_sub_keys_from_defaults(tmp_path):
    """load_config() must 2-level-merge XRAY_TARGETS, REGIME_TARGETS, FILE_LOGGING, and
    REPORTS_DEFAULTS so that a partial stored value does not silently discard unmentioned
    defaults (regression guard for the fix that added these keys to the merge list)."""
    import config as _config
    original_path = _config.SECRETS_PATH
    partial_path = tmp_path / "partial.json"
    partial_path.write_text(json.dumps({
        "FILE_LOGGING": {"ENABLED": True},
        "REPORTS_DEFAULTS": {"MR_MAX_RSI": 25},
        "XRAY_TARGETS": {"concentration_targets": {"max_single_position_pct": 20.0}},
    }))
    try:
        _config.SECRETS_PATH = partial_path
        result = _config.load_config()
        fl = result["FILE_LOGGING"]
        assert fl.get("ENABLED") is True, "Stored FILE_LOGGING.ENABLED must be applied"
        assert "LEVEL" in fl, "Default FILE_LOGGING.LEVEL must be preserved"
        assert "DAYS_TO_KEEP" in fl, "Default FILE_LOGGING.DAYS_TO_KEEP must be preserved"
        rd = result["REPORTS_DEFAULTS"]
        assert rd.get("MR_MAX_RSI") == 25, "Stored REPORTS_DEFAULTS.MR_MAX_RSI must be applied"
        assert "DIV_MIN_YIELD" in rd, "Default REPORTS_DEFAULTS.DIV_MIN_YIELD must be preserved"
        xt = result["XRAY_TARGETS"]
        assert xt["concentration_targets"]["max_single_position_pct"] == 20.0
        assert "sector_targets" in xt, "Default XRAY_TARGETS.sector_targets must be preserved"
    finally:
        _config.SECRETS_PATH = original_path


@pytest.mark.config
def test_load_config_fills_missing_scheduling_sub_keys_from_defaults(tmp_path):
    """load_config() must fill in missing keys within a SCHEDULING sub-block from defaults.

    If config.json stores only {"SCHEDULING": {"QUANT_ANALYSIS": {"TIME": "20:00"}}},
    the merged result must still include all default QUANT_ANALYSIS keys (ENABLED,
    FREQUENCY, etc.) so that scheduler_engine never gets a KeyError.
    """
    import config as _config
    original_path = _config.SECRETS_PATH
    partial_config_path = tmp_path / "partial.json"
    partial_config_path.write_text(json.dumps({
        "SCHEDULING": {
            "QUANT_ANALYSIS": {"TIME": "20:00"}
        }
    }))
    try:
        _config.SECRETS_PATH = partial_config_path
        result = _config.load_config()
        qa = result["SCHEDULING"]["QUANT_ANALYSIS"]
        assert qa.get("TIME") == "20:00", "Stored TIME must be applied"
        assert "ENABLED" in qa, "Default ENABLED key must be filled in from defaults"
        assert "FREQUENCY" in qa, "Default FREQUENCY key must be filled in from defaults"
    finally:
        _config.SECRETS_PATH = original_path


@pytest.mark.config
def test_update_config_atomic_deep_merge_preserves_sibling_keys(tmp_path):
    """update_config_atomic() deep_merge must not clobber sibling sub-keys.

    Updating only {"SCHEDULING": {"QUANT_ANALYSIS": {"TIME": "22:00"}}} must leave
    all other SCHEDULING entries (e.g. MAINTENANCE) untouched.
    """
    import config as _config
    original_path = _config.SECRETS_PATH
    test_config_path = tmp_path / "deep.json"
    try:
        _config.SECRETS_PATH = test_config_path
        # Write an initial full config via the first atomic write.
        _config.update_config_atomic({"PORT": 8090})
        # Now update only one nested key.
        _config.update_config_atomic({"SCHEDULING": {"QUANT_ANALYSIS": {"TIME": "22:00"}}})
        result = _config.load_config()
        assert result["SCHEDULING"]["QUANT_ANALYSIS"]["TIME"] == "22:00", "Updated key must be applied"
        assert "MAINTENANCE" in result["SCHEDULING"], "Sibling MAINTENANCE block must be preserved"
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


@pytest.mark.config
def test_is_daily_bar_still_forming_true_when_daily_matches_live_date_and_is_today():
    """A daily bar dated the same as (or after) the live feed's last tick, and matching today's
    real calendar date, is still-forming, not a completed close."""
    from datetime import datetime, timezone
    from utils import is_daily_bar_still_forming
    today = datetime.now(timezone.utc).date()
    assert is_daily_bar_still_forming(today, today) is True


@pytest.mark.config
def test_is_daily_bar_still_forming_false_when_daily_predates_live():
    """A daily bar dated before the live feed's last tick is a genuinely completed prior close."""
    from datetime import date
    from utils import is_daily_bar_still_forming
    assert is_daily_bar_still_forming(date(2026, 7, 2), date(2026, 7, 6)) is False


@pytest.mark.config
def test_is_daily_bar_still_forming_false_when_market_closed_and_both_dates_lag_today():
    """Regression test (found 2026-07-08): when the market is currently closed (pre-market,
    after-hours, weekend), the live feed's last available session can share daily's last date even
    though daily has already correctly caught up to a completed prior close -- e.g. querying at
    09:00 UTC before NYSE opens, when both feeds' last date is still yesterday. This must not be
    mistaken for an in-progress session just because the two dates match each other; it's only
    genuinely forming if that shared date is also today's real calendar date."""
    from datetime import datetime, timedelta, timezone
    from utils import is_daily_bar_still_forming
    yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
    assert is_daily_bar_still_forming(yesterday, yesterday) is False


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


@pytest.mark.config
def test_score_boundaries_are_strictly_ordered():
    """Score label boundaries must be strictly descending: STRONG_BUY > BULLISH > NEUTRAL > BEARISH > STRONG_SELL."""
    from constants import SCORE_STRONG_BUY, SCORE_BULLISH, SCORE_NEUTRAL, SCORE_BEARISH, SCORE_STRONG_SELL
    assert SCORE_STRONG_BUY > SCORE_BULLISH > SCORE_NEUTRAL > SCORE_BEARISH > SCORE_STRONG_SELL, (
        f"Score boundaries out of order: {SCORE_STRONG_BUY}, {SCORE_BULLISH}, "
        f"{SCORE_NEUTRAL}, {SCORE_BEARISH}, {SCORE_STRONG_SELL}"
    )


@pytest.mark.config
def test_freshness_thresholds_ordered():
    """Staleness warning threshold must be strictly less than stale threshold for both model and price data."""
    from constants import (
        FRESHNESS_MODEL_WARN_DAYS, FRESHNESS_MODEL_STALE_DAYS,
        FRESHNESS_PRICES_WARN_DAYS, FRESHNESS_PRICES_STALE_DAYS,
    )
    assert FRESHNESS_MODEL_WARN_DAYS < FRESHNESS_MODEL_STALE_DAYS, (
        f"Model freshness thresholds wrong: warn={FRESHNESS_MODEL_WARN_DAYS}, stale={FRESHNESS_MODEL_STALE_DAYS}"
    )
    assert FRESHNESS_PRICES_WARN_DAYS < FRESHNESS_PRICES_STALE_DAYS, (
        f"Price freshness thresholds wrong: warn={FRESHNESS_PRICES_WARN_DAYS}, stale={FRESHNESS_PRICES_STALE_DAYS}"
    )


@pytest.mark.config
def test_rsi_stressed_threshold_below_normal():
    """RSI_OVERBOUGHT_STRESSED must be strictly less than RSI_OVERBOUGHT (tighter gate under stress)."""
    from constants import RSI_OVERBOUGHT, RSI_OVERBOUGHT_STRESSED
    assert RSI_OVERBOUGHT_STRESSED < RSI_OVERBOUGHT, (
        f"Stressed overbought {RSI_OVERBOUGHT_STRESSED} must be below normal {RSI_OVERBOUGHT}"
    )


@pytest.mark.config
def test_rsi_healthy_band_ordered():
    """RSI_HEALTHY_MIN must be strictly less than RSI_HEALTHY_MAX."""
    from constants import RSI_HEALTHY_MIN, RSI_HEALTHY_MAX
    assert RSI_HEALTHY_MIN < RSI_HEALTHY_MAX, (
        f"RSI healthy band inverted: min={RSI_HEALTHY_MIN}, max={RSI_HEALTHY_MAX}"
    )


@pytest.mark.config
def test_css_version_is_nonempty_string():
    """CSS_VERSION must be a non-empty string so cache-busting query parameters are always applied."""
    from constants import CSS_VERSION
    assert isinstance(CSS_VERSION, str) and CSS_VERSION.strip(), (
        f"CSS_VERSION must be a non-empty string, got {CSS_VERSION!r}"
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


# ── news_feed_engine: _label_from_score ───────────────────────────────────────

@pytest.mark.utils
def test_label_from_score_positive():
    """Scores above 0.15 must map to 'positive'."""
    from news_feed_engine import _label_from_score
    assert _label_from_score(0.16) == "positive"
    assert _label_from_score(0.5)  == "positive"
    assert _label_from_score(1.0)  == "positive"


@pytest.mark.utils
def test_label_from_score_negative():
    """Scores below -0.15 must map to 'negative'."""
    from news_feed_engine import _label_from_score
    assert _label_from_score(-0.16) == "negative"
    assert _label_from_score(-0.5)  == "negative"
    assert _label_from_score(-1.0)  == "negative"


@pytest.mark.utils
def test_label_from_score_neutral_boundary():
    """Scores in [-0.15, 0.15] inclusive boundaries must map to 'neutral'."""
    from news_feed_engine import _label_from_score
    assert _label_from_score(0.0)   == "neutral"
    assert _label_from_score(0.15)  == "neutral"
    assert _label_from_score(-0.15) == "neutral"
    assert _label_from_score(0.14)  == "neutral"
    assert _label_from_score(-0.14) == "neutral"


@pytest.mark.utils
def test_label_from_score_exact_boundary_positive():
    """0.151 is just over threshold — must be 'positive', 0.15 must be 'neutral'."""
    from news_feed_engine import _label_from_score
    assert _label_from_score(0.151) == "positive"
    assert _label_from_score(0.15)  == "neutral"


@pytest.mark.utils
def test_label_from_score_exact_boundary_negative():
    """-0.151 is just under threshold — must be 'negative', -0.15 must be 'neutral'."""
    from news_feed_engine import _label_from_score
    assert _label_from_score(-0.151) == "negative"
    assert _label_from_score(-0.15)  == "neutral"


# ---------------------------------------------------------------------------
# utils.clamp_beta
# ---------------------------------------------------------------------------

@pytest.mark.utils
def test_clamp_beta_normal_value():
    from utils import clamp_beta
    assert clamp_beta(1.2) == pytest.approx(1.2)


@pytest.mark.utils
def test_clamp_beta_clamps_high():
    from utils import clamp_beta
    assert clamp_beta(3.0) == pytest.approx(2.0)


@pytest.mark.utils
def test_clamp_beta_clamps_low():
    from utils import clamp_beta
    assert clamp_beta(0.1) == pytest.approx(0.5)


@pytest.mark.utils
def test_clamp_beta_none_returns_default():
    from utils import clamp_beta
    assert clamp_beta(None) == pytest.approx(1.0)


@pytest.mark.utils
def test_clamp_beta_empty_string_returns_default():
    from utils import clamp_beta
    assert clamp_beta("") == pytest.approx(1.0)


@pytest.mark.utils
def test_clamp_beta_non_numeric_string_returns_default():
    from utils import clamp_beta
    assert clamp_beta("bad") == pytest.approx(1.0)


@pytest.mark.utils
def test_clamp_beta_custom_bounds():
    from utils import clamp_beta
    assert clamp_beta(0.0, lo=0.2, hi=1.5) == pytest.approx(0.2)
    assert clamp_beta(2.0, lo=0.2, hi=1.5) == pytest.approx(1.5)


@pytest.mark.utils
def test_clamp_beta_exact_boundary_not_clamped():
    from utils import clamp_beta
    assert clamp_beta(0.5) == pytest.approx(0.5)
    assert clamp_beta(2.0) == pytest.approx(2.0)
