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
from unittest.mock import patch

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
def test_load_config_migrates_legacy_head_shoulders_scheduling(tmp_path):
    """A config.json predating the unified Pattern Detection tool (flat SCHEDULING.HEAD_SHOULDERS
    / NOTIFICATIONS.HEAD_SHOULDERS_ALERTS) must have its values folded into the new nested
    PATTERN_DETECTION / PATTERN_DETECTION_ALERTS shape, not silently reset to defaults."""
    import config as _config
    original_path = _config.SECRETS_PATH
    legacy_config_path = tmp_path / "legacy_config.json"
    legacy_config_path.write_text(json.dumps({
        "SCHEDULING": {
            "HEAD_SHOULDERS": {
                "ENABLED": True, "REGULAR_ENABLED": False, "INVERSE_ENABLED": True,
                "MONITOR_PORTFOLIO": True, "MONITOR_WATCHLIST": True,
                "DAYS": ["mon", "tue", "wed", "thu", "fri"], "TIME": "23:45",
            },
        },
        "NOTIFICATIONS": {
            "HEAD_SHOULDERS_ALERTS": {
                "COOLDOWN_MINUTES": 90, "RETRIGGER_PERCENT": 4.0, "REARM_PERCENT": 6.0,
                "PRIOR_TREND_MIN_PCT": 10.0, "VOLUME_CONFIRM_MULTIPLIER": 2.0,
            },
        },
    }))
    try:
        _config.SECRETS_PATH = legacy_config_path
        result = _config.load_config()

        pd_sched = result["SCHEDULING"]["PATTERN_DETECTION"]
        assert pd_sched["ENABLED"] is True
        assert pd_sched["MONITOR_WATCHLIST"] is True
        assert pd_sched["TIME"] == "23:45"
        assert pd_sched["HEAD_SHOULDERS"]["REGULAR_ENABLED"] is False
        assert pd_sched["HEAD_SHOULDERS"]["INVERSE_ENABLED"] is True
        assert "HEAD_SHOULDERS" not in result["SCHEDULING"]

        pd_alerts = result["NOTIFICATIONS"]["PATTERN_DETECTION_ALERTS"]
        assert pd_alerts["COOLDOWN_MINUTES"] == 90
        assert pd_alerts["HEAD_SHOULDERS"]["PRIOR_TREND_MIN_PCT"] == 10.0
        assert pd_alerts["HEAD_SHOULDERS"]["VOLUME_CONFIRM_MULTIPLIER"] == 2.0
        assert "HEAD_SHOULDERS_ALERTS" not in result["NOTIFICATIONS"]
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
    """load_config() must 2-level-merge XRAY_TARGETS, REGIME_TARGETS, FILE_LOGGING,
    REPORTS_DEFAULTS, and META_SCORING so that a partial stored value does not silently discard
    unmentioned defaults (regression guard for the fix that added these keys to the merge list)."""
    import config as _config
    original_path = _config.SECRETS_PATH
    partial_path = tmp_path / "partial.json"
    partial_path.write_text(json.dumps({
        "FILE_LOGGING": {"ENABLED": True},
        "REPORTS_DEFAULTS": {"MR_MAX_RSI": 25},
        "XRAY_TARGETS": {"concentration_targets": {"max_single_position_pct": 20.0}},
        "META_SCORING": {"CRASH_VETO": {"MARKET_STRESS_THRESHOLD": 0.9}},
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
        ms = result["META_SCORING"]
        assert ms["CRASH_VETO"]["MARKET_STRESS_THRESHOLD"] == 0.9
        assert "REGIME_WEIGHTS" in ms, "Default META_SCORING.REGIME_WEIGHTS must be preserved"
        assert "Bull" in ms["REGIME_WEIGHTS"] and "Chop" in ms["REGIME_WEIGHTS"]
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
def test_safe_ticker_filename_rejects_markup_traversal_and_trailing_newline():
    """safe_ticker_filename() is the guard behind every ticker-built parquet path; markup,
    separators and a trailing newline (which a '$'-anchored match let through) must be refused."""
    from utils import safe_ticker_filename
    for good in ("AAPL", "BRK-B", "LLOY.L", "^GSPC", "GC=F", "EURUSD=X", "0P0000XYZ.L"):
        assert safe_ticker_filename(good) == good
    for bad in ("", None, "../X", "A/B", "A\\B", "<img src=x>", "A B", "AAPL\n", "&lt;SPAN&gt;"):
        assert safe_ticker_filename(bad) is None, bad
    assert safe_ticker_filename("A" * 64) == "A" * 64
    assert safe_ticker_filename("A" * 65) is None


@pytest.mark.config
def test_is_valid_ticker_is_safe_ticker_filename_as_a_predicate_and_keeps_case():
    """Entry points that store a ticker use one pattern with the path builders; a lowercase
    Ghostfolio asset UUID is a stored ticker and must pass unchanged."""
    from utils import is_valid_ticker
    uuid_ticker = "9a1c1fdc-7d0e-4d3b-8a75-1e2d3c4b5a69"
    for good in ("AAPL", "BRK-B", "^FTSE", "GC=F", "PENSION-12", "TBILL-7", uuid_ticker):
        assert is_valid_ticker(good), good
    for bad in ("", None, 5, "../X", "/X/Y", "../../Z", "A/B", "A B", "AAPL\n", "<b>", "A" * 65):
        assert not is_valid_ticker(bad), bad


@pytest.mark.config
def test_csv_safe_cell_prefixes_formula_starters_and_leaves_numbers_alone():
    from utils import csv_safe_cell
    for text in ("=1+1", "+1", "-1", "@SUM(A1)", "\t=1", "\r=1"):
        assert csv_safe_cell(text) == "'" + text, text
    for untouched in ("AAPL", "", "a=b", "'=1", " =1", -600.0, 0, None):
        assert csv_safe_cell(untouched) == untouched, untouched


@pytest.mark.config
def test_attachment_content_disposition_plain_name_has_no_filename_star():
    from utils import attachment_content_disposition
    assert attachment_content_disposition("My ISA", "_transactions.csv") == 'attachment; filename="My_ISA_transactions.csv"'


@pytest.mark.config
def test_attachment_content_disposition_neutralises_header_breaking_characters():
    from urllib.parse import unquote
    from utils import attachment_content_disposition
    hostile = 'a"b;c\r\nSet-Cookie: x=1\x00\u202eevil/..\\z'
    header = attachment_content_disposition(hostile, "_transactions.csv")
    assert "\r" not in header and "\n" not in header and "\x00" not in header and "\u202e" not in header
    ascii_part, _, star_part = header.partition("; filename*=UTF-8''")
    assert ascii_part.startswith('attachment; filename="') and ascii_part.endswith('_transactions.csv"')
    assert ascii_part.count('"') == 2 and ";" not in ascii_part[len("attachment;"):]
    decoded = unquote(star_part)
    assert not any(ch in decoded for ch in '"\\/;\r\n\x00\u202e')
    assert decoded.endswith("_transactions.csv")


@pytest.mark.config
def test_attachment_content_disposition_non_ascii_name_uses_rfc5987_and_caps_length():
    from urllib.parse import unquote
    from utils import attachment_content_disposition
    header = attachment_content_disposition("Caf\u00e9 ISA", "_transactions.csv")
    assert header.startswith('attachment; filename="Caf_ISA_transactions.csv"; filename*=UTF-8\'\'')
    assert unquote(header.split("filename*=UTF-8''")[1]) == "Caf\u00e9_ISA_transactions.csv"
    header.encode("latin-1")

    long_header = attachment_content_disposition("x" * 500, "_transactions.csv")
    assert long_header == 'attachment; filename="' + "x" * 64 + '_transactions.csv"'


@pytest.mark.config
def test_attachment_content_disposition_falls_back_when_nothing_ascii_remains():
    from utils import attachment_content_disposition
    assert attachment_content_disposition("\u65e5\u672c", "_transactions.csv").startswith('attachment; filename="download_transactions.csv"; filename*=')
    assert attachment_content_disposition("...", "_transactions.csv") == 'attachment; filename="download_transactions.csv"'
    assert attachment_content_disposition(".hidden", "_t.csv") == 'attachment; filename="hidden_t.csv"'


def test_safe_local_path_accepts_only_same_origin_relative_paths():
    """safe_local_path() backs the score-history Back link and the login redirect: a tab/CR/LF
    is dropped by browsers before URL parsing, so '/<TAB>/host' would become '//host'."""
    from utils import safe_local_path
    for good in ("/", "/portfolio", "/stock/AAPL?embed=true&embed_token=abc", "/score-history?filter=all"):
        assert safe_local_path(good) == good
    for bad in (
        None, "", "portfolio", "javascript:alert(1)", "https://evil.example/", "//evil.example",
        "/\\evil.example", "/a\\b", "/\t/evil.example", "/\n/evil.example", "/\r/evil.example",
        "/ok\x00", "/ok\x7f",
    ):
        assert safe_local_path(bad) is None, repr(bad)


@pytest.mark.config
def test_is_currency_code_accepts_three_letters_and_keeps_pence_case():
    """is_currency_code() guards a currency before it is stored or interpolated into a pair name:
    'GBp' must pass, markup, padding, digits and a trailing newline must not."""
    from utils import is_currency_code
    for good in ("GBP", "GBp", "USD", "ZAc", "ILA", "usd"):
        assert is_currency_code(good) is True, good
    for bad in (None, "", "GB", "GBPP", "G1P", "<b>", "<img src=x onerror=alert(1)>", " GBP", "GBP\n", "GB P", "ÉUR", 123):
        assert is_currency_code(bad) is False, repr(bad)


@pytest.mark.config
def test_is_iso_date_accepts_only_real_zero_padded_calendar_dates():
    """is_iso_date() must refuse the forms date.fromisoformat() alone tolerates (compact, week
    dates), impossible dates, markup, non-ASCII digits and a trailing newline."""
    from utils import is_iso_date
    for good in ("2026-01-05", "2024-02-29", "1999-12-31"):
        assert is_iso_date(good) is True, good
    for bad in (
        None, "", "2026-1-5", "20260105", "2026-W02-1", "2026-02-30", "2026-13-01", "05/01/2026",
        "2026-01-05T00:00:00", "2026-01-05\n", " 2026-01-05", "2026-01-05<script>", "２０２６-０１-０５", 20260105,
    ):
        assert is_iso_date(bad) is False, repr(bad)


@pytest.mark.config
def test_ignored_tickers_set_normalizes_and_uses_supplied_config():
    """ignored_tickers_set() must uppercase/strip entries and honor an explicitly-passed config
    dict rather than always reloading from disk — callers that already hold a loaded config
    (and tests that patch their module's own load_config) rely on this."""
    from utils import ignored_tickers_set
    result = ignored_tickers_set({"IGNORED_TICKERS": [" tsla ", "gme"]})
    assert result == {"TSLA", "GME"}


@pytest.mark.config
def test_ignored_tickers_set_defaults_to_load_config_when_no_config_passed():
    """With no config argument, ignored_tickers_set() must fall back to the real load_config()."""
    from unittest.mock import patch
    from utils import ignored_tickers_set
    with patch("config.load_config", return_value={"IGNORED_TICKERS": ["ZZDEFAULTED"]}):
        result = ignored_tickers_set()
    assert result == {"ZZDEFAULTED"}


@pytest.mark.config
def test_is_synthetic_ticker_true_for_tbill_and_pension():
    """TBILL-{txn_id} and PENSION-{account_id} have no Yahoo Finance listing and must be
    flagged as synthetic — the structural half of is_excluded_from_yahoo_fetch()."""
    from utils import is_synthetic_ticker
    assert is_synthetic_ticker("TBILL-606") is True
    assert is_synthetic_ticker("PENSION-12") is True


@pytest.mark.config
def test_is_synthetic_ticker_false_for_real_ticker():
    from utils import is_synthetic_ticker
    assert is_synthetic_ticker("AAPL") is False


@pytest.mark.config
def test_is_excluded_from_yahoo_fetch_true_for_synthetic_ticker_regardless_of_ignored_list():
    from utils import is_excluded_from_yahoo_fetch
    assert is_excluded_from_yahoo_fetch("TBILL-606", ignored=set()) is True


@pytest.mark.config
def test_is_excluded_from_yahoo_fetch_true_for_registry_series_sourced_outside_yahoo():
    """UK10YG is scraped from FT/BoE; Yahoo lists no such ticker."""
    from utils import is_excluded_from_yahoo_fetch
    assert is_excluded_from_yahoo_fetch("UK10YG", ignored=set()) is True


@pytest.mark.config
def test_is_excluded_from_yahoo_fetch_matches_registry_series_and_synthetic_tickers_case_insensitively():
    from utils import is_excluded_from_yahoo_fetch
    assert is_excluded_from_yahoo_fetch("uk10yg", ignored=set()) is True
    assert is_excluded_from_yahoo_fetch(" tbill-606 ", ignored=set()) is True


def test_is_excluded_from_yahoo_fetch_true_for_ignored_ticker():
    from utils import is_excluded_from_yahoo_fetch
    assert is_excluded_from_yahoo_fetch("gme", ignored={"GME"}) is True


@pytest.mark.config
def test_is_excluded_from_yahoo_fetch_false_for_normal_unignored_ticker():
    from utils import is_excluded_from_yahoo_fetch
    assert is_excluded_from_yahoo_fetch("AAPL", ignored={"GME"}) is False


@pytest.mark.config
def test_write_parquet_atomic_replaces_file_and_keeps_last_good_one_on_failure(tmp_path):
    import pandas as pd
    from unittest.mock import patch
    from utils import write_parquet_atomic

    path = tmp_path / "AAA.parquet"
    good = pd.DataFrame({"Close": [10.0]}, index=pd.to_datetime(["2026-07-01"]))
    write_parquet_atomic(good, path)
    with patch.object(pd.DataFrame, "to_parquet", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            write_parquet_atomic(pd.DataFrame({"Close": [11.0]}, index=pd.to_datetime(["2026-07-02"])), str(path))

    assert pd.read_parquet(path)["Close"].tolist() == [10.0]
    assert [p.name for p in tmp_path.iterdir()] == ["AAA.parquet"]


@pytest.mark.config
def test_write_parquet_atomic_creates_missing_folder(tmp_path):
    import pandas as pd
    from utils import write_parquet_atomic

    path = tmp_path / "nested" / "BBB.parquet"
    write_parquet_atomic(pd.DataFrame({"Close": [1.0]}), path)
    assert pd.read_parquet(path)["Close"].tolist() == [1.0]


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


@pytest.mark.config
def test_is_daily_bar_still_forming_false_when_exchange_confirmed_closed():
    """Regression test (found 2026-07-13): a same-day post-close fetch (e.g. the nightly Update
    Pipeline, run well after the relevant exchange has closed) produces the exact same date
    signature as a genuine mid-session fetch -- both daily and live feed dates equal today. An
    explicit exchange_currently_open=False must override the date-only heuristic so a fully
    final close is never mistaken for still-forming just because the fetch happened later the
    same UTC calendar day."""
    from datetime import datetime, timezone
    from utils import is_daily_bar_still_forming
    today = datetime.now(timezone.utc).date()
    assert is_daily_bar_still_forming(today, today, exchange_currently_open=False) is False


@pytest.mark.config
def test_is_daily_bar_still_forming_true_when_exchange_confirmed_open():
    """exchange_currently_open=True (or omitted) preserves the existing date-only behaviour for
    callers that already only run while the exchange is confirmed open."""
    from datetime import datetime, timezone
    from utils import is_daily_bar_still_forming
    today = datetime.now(timezone.utc).date()
    assert is_daily_bar_still_forming(today, today, exchange_currently_open=True) is True


def test_trading_days_forward_weekday_offset():
    """2024-01-02 is a Tuesday; 10 business days forward is 2024-01-16 (a Tuesday)."""
    from utils import trading_days_forward
    assert trading_days_forward("2024-01-02", 10) == "2024-01-16"


def test_trading_days_forward_weekend_anchor_rolls_forward_first():
    """2024-01-06 is a Saturday; np.busday_offset rolls forward to the next business day
    (Monday 2024-01-08) before counting 10 business days."""
    from utils import trading_days_forward
    assert trading_days_forward("2024-01-06", 10) == "2024-01-22"


def test_trading_days_forward_small_horizon():
    # 2024-01-02 is a Tuesday; 1 business day forward is 2024-01-03 (Wednesday).
    from utils import trading_days_forward
    assert trading_days_forward("2024-01-02", 1) == "2024-01-03"


# ── Constants sanity checks ───────────────────────────────────────────────────

@pytest.mark.config
def test_earnings_drift_horizons_are_positive_and_ascending():
    from constants import EARNINGS_DRIFT_HORIZONS
    assert list(EARNINGS_DRIFT_HORIZONS) == sorted(EARNINGS_DRIFT_HORIZONS)
    assert all(isinstance(h, int) and h > 0 for h in EARNINGS_DRIFT_HORIZONS)


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


# ── Requirements drift detection ──────────────────────────────────────────────

def _installed_version(pkg):
    import importlib.metadata
    return importlib.metadata.version(pkg)


@pytest.mark.utils
def test_check_requirements_drift_no_mismatch_for_satisfied_exact_pin(tmp_path):
    from utils import check_requirements_drift
    version = _installed_version("pytest")
    req_file = tmp_path / "requirements.txt"
    req_file.write_text(f"pytest=={version}\n")
    assert check_requirements_drift(str(req_file)) == []


@pytest.mark.utils
def test_check_requirements_drift_detects_exact_pin_mismatch(tmp_path):
    from utils import check_requirements_drift
    req_file = tmp_path / "requirements.txt"
    req_file.write_text("pytest==0.0.0.dev0\n")
    mismatches = check_requirements_drift(str(req_file))
    assert len(mismatches) == 1
    assert "pytest" in mismatches[0]
    assert "0.0.0.dev0" in mismatches[0]


@pytest.mark.utils
def test_check_requirements_drift_no_mismatch_for_satisfied_floor(tmp_path):
    from utils import check_requirements_drift
    req_file = tmp_path / "requirements.txt"
    req_file.write_text("pytest>=0.0.1\n")
    assert check_requirements_drift(str(req_file)) == []


@pytest.mark.utils
def test_check_requirements_drift_detects_floor_violation(tmp_path):
    from utils import check_requirements_drift
    req_file = tmp_path / "requirements.txt"
    req_file.write_text("pytest>=9999.0.0\n")
    mismatches = check_requirements_drift(str(req_file))
    assert len(mismatches) == 1
    assert "pytest" in mismatches[0]
    assert "9999.0.0" in mismatches[0]


@pytest.mark.utils
def test_check_requirements_drift_detects_missing_package(tmp_path):
    from utils import check_requirements_drift
    req_file = tmp_path / "requirements.txt"
    req_file.write_text("this-package-does-not-exist-anywhere==1.0.0\n")
    mismatches = check_requirements_drift(str(req_file))
    assert len(mismatches) == 1
    assert "not installed" in mismatches[0]


@pytest.mark.utils
def test_check_requirements_drift_ignores_unpinned_package(tmp_path):
    from utils import check_requirements_drift
    req_file = tmp_path / "requirements.txt"
    req_file.write_text("pytest\n")
    assert check_requirements_drift(str(req_file)) == []


@pytest.mark.utils
def test_check_requirements_drift_ignores_comments_and_blank_lines(tmp_path):
    from utils import check_requirements_drift
    version = _installed_version("pytest")
    req_file = tmp_path / "requirements.txt"
    req_file.write_text(f"# a comment\n\npytest=={version}  # inline comment\n")
    assert check_requirements_drift(str(req_file)) == []


@pytest.mark.utils
def test_check_requirements_drift_missing_file_returns_empty_list(tmp_path):
    from utils import check_requirements_drift
    assert check_requirements_drift(str(tmp_path / "does_not_exist.txt")) == []


@pytest.mark.utils
def test_notify_requirements_drift_sends_notification_on_mismatch(monkeypatch):
    import shlex
    import utils
    monkeypatch.setattr(utils, "check_requirements_drift", lambda: ["pytest: installed 1.0, requirements.lock pins ==2.0"])
    calls = []
    monkeypatch.setattr("notification_engine.notify", lambda *a, **kw: calls.append((a, kw)))
    utils.notify_requirements_drift()
    assert len(calls) == 1
    assert calls[0][0][0] == "system_update_status"
    assert "pytest" in calls[0][0][2]
    assert shlex.join(utils.pip_install_command()) in calls[0][0][2]


@pytest.mark.utils
def test_pip_install_command_uses_the_running_interpreter_and_hashed_lock():
    """The hand-run fallback must not depend on venv/bin/pip or `activate`, which stay stale when a venv is moved."""
    import utils
    command = utils.pip_install_command()
    assert command[:6] == [sys.executable, "-m", "pip", "install", "--require-hashes", "-r"]
    assert command[6] == str(Path(utils.__file__).resolve().parent / "requirements.lock")


@pytest.mark.utils
def test_check_requirements_drift_reads_hashed_lock_syntax(tmp_path):
    """Continuation lines, --hash options and `# via` comments of a --generate-hashes lock must not be mistaken for packages."""
    from utils import check_requirements_drift
    version = _installed_version("pytest")
    lock = tmp_path / "requirements.lock"
    lock.write_text(
        "# This file was autogenerated by uv\n"
        f"pytest=={version} \\\n"
        "    --hash=sha256:" + "0" * 64 + " \\\n"
        "    --hash=sha256:" + "1" * 64 + "\n"
        "    # via -r requirements.txt\n"
    )
    assert check_requirements_drift(str(lock)) == []
    lock.write_text(f"pytest==0.0.0.dev0 \\\n    --hash=sha256:{'0' * 64}\n")
    mismatches = check_requirements_drift(str(lock))
    assert len(mismatches) == 1
    assert "requirements.lock pins ==0.0.0.dev0" in mismatches[0]


@pytest.mark.utils
def test_check_requirements_drift_honours_environment_markers(tmp_path):
    """A pin whose marker is false here (another platform or interpreter) is not a missing package."""
    from utils import check_requirements_drift
    lock = tmp_path / "requirements.lock"
    lock.write_text(
        "this-package-does-not-exist-anywhere==1.0.0 ; sys_platform == 'win32'\n"
        "this-package-also-does-not-exist==1.0.0 ; python_full_version < '3.0'\n"
    )
    assert check_requirements_drift(str(lock)) == []
    lock.write_text("this-package-does-not-exist-anywhere==1.0.0 ; python_full_version >= '3.0'\n")
    assert "not installed" in check_requirements_drift(str(lock))[0]


@pytest.mark.utils
def test_requirements_lock_covers_every_requirement_and_is_fully_hashed():
    """The lock is what CI, prod and drift detection install, so it must satisfy requirements.txt and carry a hash for every pin."""
    import re
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name
    from packaging.version import Version
    import utils

    lock_text = utils.requirements_lock_path().read_text()
    pins = {}
    for block in re.split(r"\n(?=[A-Za-z0-9])", lock_text):
        m = re.match(r"^([A-Za-z0-9_.\-]+)==([^\s;]+)", block)
        if not m:
            continue
        assert "--hash=sha256:" in block, f"{m.group(1)} has no hash in requirements.lock"
        pins[canonicalize_name(m.group(1))] = Version(m.group(2))

    requirements_txt = Path(utils.__file__).resolve().parent / "requirements.txt"
    for raw in requirements_txt.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        req = Requirement(line)
        name = canonicalize_name(req.name)
        assert name in pins, f"{req.name} is in requirements.txt but not in requirements.lock; regenerate the lock"
        assert req.specifier.contains(pins[name], prereleases=True), (
            f"requirements.lock pins {req.name}=={pins[name]}, which requirements.txt ({req.specifier}) excludes; regenerate the lock"
        )


@pytest.mark.utils
def test_notify_requirements_drift_noop_when_no_mismatch(monkeypatch):
    import utils
    monkeypatch.setattr(utils, "check_requirements_drift", lambda: [])
    calls = []
    monkeypatch.setattr("notification_engine.notify", lambda *a, **kw: calls.append((a, kw)))
    utils.notify_requirements_drift()
    assert calls == []


@pytest.mark.config
def test_performance_settings_merge_and_reload(tmp_path, monkeypatch):
    import config

    config_path = tmp_path / "config.json"
    monkeypatch.setattr(config, "SECRETS_PATH", config_path)
    config_path.write_text(json.dumps({"PERFORMANCE": {"FX_FRESH_SECONDS": 120}}))
    first = config.load_config()["PERFORMANCE"]
    assert first["FX_FRESH_SECONDS"] == 120
    assert first["FX_MAX_USABLE_SECONDS"] == 604800
    config_path.write_text(json.dumps({"PERFORMANCE": {"FX_FRESH_SECONDS": 300, "FX_MAX_USABLE_SECONDS": 3600}}))
    assert config.load_config()["PERFORMANCE"]["FX_FRESH_SECONDS"] == 300
    assert config.load_config()["PERFORMANCE"]["FX_MAX_USABLE_SECONDS"] == 3600


@pytest.mark.parametrize("settings", [{"FX_FRESH_SECONDS": -1}, {"HA_FX_MAX_AGE_SECONDS": "3600"}, {"FX_FRESH_SECONDS": "600"}, {"FX_FRESH_SECONDS": 100, "FX_MAX_USABLE_SECONDS": 50}])
def test_invalid_performance_settings_reset_only_performance_section(tmp_path, monkeypatch, settings):
    import config

    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"BASE_CURRENCY": "USD", "USER_TIMEZONE": "America/New_York", "PERFORMANCE": settings}))
    monkeypatch.setattr(config, "SECRETS_PATH", config_path)
    loaded = config.load_config()
    assert loaded["PERFORMANCE"] == config.DEFAULT_CONFIG["PERFORMANCE"]
    assert loaded["BASE_CURRENCY"] == "USD"
    assert loaded["USER_TIMEZONE"] == "America/New_York"


@pytest.mark.config
def test_sanitize_floats_nan_replaced_with_none():
    from utils import sanitize_floats
    assert sanitize_floats(float("nan")) is None


@pytest.mark.config
def test_sanitize_floats_inf_replaced_with_none():
    from utils import sanitize_floats
    assert sanitize_floats(float("inf")) is None


@pytest.mark.config
def test_sanitize_floats_negative_inf_replaced_with_none():
    from utils import sanitize_floats
    assert sanitize_floats(float("-inf")) is None


@pytest.mark.config
def test_sanitize_floats_finite_float_unchanged():
    from utils import sanitize_floats
    assert sanitize_floats(3.14) == pytest.approx(3.14)


@pytest.mark.config
def test_sanitize_floats_dict_values_sanitized():
    from utils import sanitize_floats
    result = sanitize_floats({"a": float("nan"), "b": 1.5})
    assert result["a"] is None
    assert result["b"] == pytest.approx(1.5)


@pytest.mark.config
def test_sanitize_floats_list_values_sanitized():
    from utils import sanitize_floats
    result = sanitize_floats([float("nan"), 2.0, float("inf")])
    assert result[0] is None
    assert result[1] == pytest.approx(2.0)
    assert result[2] is None


@pytest.mark.config
def test_sanitize_floats_nested_structure_sanitized():
    from utils import sanitize_floats
    result = sanitize_floats({"a": [float("nan"), {"b": float("inf")}]})
    assert result["a"][0] is None
    assert result["a"][1]["b"] is None


@pytest.mark.config
def test_sanitize_floats_non_float_passthrough():
    from utils import sanitize_floats
    assert sanitize_floats("string") == "string"
    assert sanitize_floats(42) == 42
    assert sanitize_floats(None) is None


# ── Runtime file permissions (SEC 2/27, S1-10) ────────────────────────────────

def _mode(path) -> int:
    import os
    import stat
    return stat.S_IMODE(os.lstat(path).st_mode)


def _make_project_tree(root):
    import os
    (root / "data" / "historical").mkdir(parents=True)
    (root / "models").mkdir()
    (root / "logs").mkdir()
    files = {
        "env": root / ".env",
        "config": root / "config.json",
        "parquet": root / "data" / "historical" / "AAPL.parquet",
        "db": root / "data" / "analysis.db",
        "model": root / "models" / "ml_ensemble.joblib",
        "log": root / "logs" / "app.log",
    }
    for path in files.values():
        path.write_text("x")
        os.chmod(path, 0o666)
    os.chmod(root, 0o775)
    for sub in (root / "data", root / "data" / "historical", root / "models", root / "logs"):
        os.chmod(sub, 0o775)
    return files


@pytest.mark.config
def test_harden_runtime_permissions_tightens_secrets_and_write_bits(tmp_path):
    from utils import harden_runtime_permissions
    files = _make_project_tree(tmp_path)

    harden_runtime_permissions(tmp_path)

    assert _mode(tmp_path) == 0o700
    assert _mode(files["env"]) == 0o600
    assert _mode(files["config"]) == 0o600
    assert _mode(files["parquet"]) == 0o644
    assert _mode(files["db"]) == 0o644
    assert _mode(files["model"]) == 0o644
    assert _mode(tmp_path / "data") == 0o755
    assert _mode(tmp_path / "data" / "historical") == 0o755
    assert _mode(tmp_path / "models") == 0o755
    assert _mode(files["log"]) == 0o666
    assert _mode(tmp_path / "logs") == 0o775


@pytest.mark.config
def test_harden_runtime_permissions_never_follows_symlinks(tmp_path):
    import os
    from utils import harden_runtime_permissions
    project = tmp_path / "project"
    outside = tmp_path / "outside.bin"
    project.mkdir()
    outside.write_text("x")
    os.chmod(outside, 0o666)
    (project / "data").mkdir()
    (project / "data" / "link.bin").symlink_to(outside)
    (project / "data" / "linkdir").symlink_to(tmp_path, target_is_directory=True)

    harden_runtime_permissions(project)

    assert _mode(outside) == 0o666


@pytest.mark.config
def test_harden_runtime_permissions_is_idempotent_and_tolerates_missing_paths(tmp_path):
    from utils import harden_runtime_permissions
    project = tmp_path / "empty_project"
    project.mkdir()

    harden_runtime_permissions(project)
    harden_runtime_permissions(project)

    assert _mode(project) == 0o700


@pytest.mark.config
def test_harden_runtime_permissions_reports_unchangeable_path_and_continues(tmp_path, monkeypatch, caplog):
    import os
    import logging
    import utils
    files = _make_project_tree(tmp_path)
    real_chmod = os.chmod

    def flaky_chmod(path, mode, *a, **kw):
        if str(path).endswith("analysis.db"):
            raise PermissionError(1, "Operation not permitted")
        return real_chmod(path, mode, *a, **kw)

    monkeypatch.setattr(utils.os, "chmod", flaky_chmod)
    with caplog.at_level(logging.WARNING, logger="utils"):
        utils.harden_runtime_permissions(tmp_path)

    assert _mode(files["db"]) == 0o666
    assert _mode(files["parquet"]) == 0o644
    assert any("Could not tighten permissions on 1 path" in r.getMessage() for r in caplog.records)


@pytest.mark.config
def test_config_json_is_created_private_under_a_loose_umask(tmp_path, monkeypatch):
    import os
    import config
    monkeypatch.setattr(config, "SECRETS_PATH", tmp_path / "config.json")
    previous = os.umask(0o000)
    try:
        config.load_config()
        assert _mode(tmp_path / "config.json") == 0o600

        config.update_config_atomic({"BASE_CURRENCY": "USD"})
        assert _mode(tmp_path / "config.json") == 0o600
        assert not (tmp_path / "config.tmp").exists()
    finally:
        os.umask(previous)


@pytest.mark.config
def test_set_key_creates_env_file_private_under_a_loose_umask(tmp_path):
    import os
    from dotenv import set_key
    previous = os.umask(0o000)
    try:
        set_key(str(tmp_path / ".env"), "API_KEY", "secret")
    finally:
        os.umask(previous)
    assert _mode(tmp_path / ".env") == 0o600


@pytest.mark.config
def test_lifespan_hardens_permissions_before_database_init():
    from pathlib import Path
    from unittest.mock import patch
    from fastapi.testclient import TestClient
    import config
    order = []
    with (
        patch("main.run_yfinance_smoke_test"),
        patch("main.start_scheduler"),
        patch("main.reload_scheduler"),
        patch("main.shutdown_scheduler"),
        patch("main.resume_interrupted_scans"),
        patch("main.start_model_compatibility_guard"),
        patch("main.init_db", side_effect=lambda: order.append("init_db")),
        patch("main.harden_runtime_permissions", side_effect=lambda base: order.append(("harden", Path(base)))),
    ):
        import main
        with TestClient(main.app, raise_server_exceptions=False):
            pass
    assert order == [("harden", config.BASE_DIR), "init_db"]


@pytest.mark.config
def test_safe_http_url_accepts_only_absolute_web_links():
    """safe_http_url() guards Yahoo's `website` before it becomes an href: a `javascript:` value is clickable, and browsers drop tab/CR/LF before reading the scheme."""
    from utils import safe_http_url
    for good in ("https://example.com", "http://example.com/a?b=1", "https://sub.example.co.uk:8443/x"):
        assert safe_http_url(good) == good
    assert safe_http_url("  https://example.com  ") == "https://example.com"
    for bad in (
        None, "", 5, "javascript:alert(1)", "JavaScript:alert(1)", "java\tscript:alert(1)",
        "data:text/html,x", "vbscript:x", "//example.com", "example.com", "/path", "https://",
        "https://exa\nmple.com", "ftp://example.com",
    ):
        assert safe_http_url(bad) is None, repr(bad)


class TestSettingsBlockShapeValidators:
    def test_xray_targets_accept_a_new_band_name_and_a_negative_unbounded_threshold(self):
        import config
        value = {
            "country_concentration": {"Germany": {"min": None, "max": 10}},
            "risk_metric_targets": {"sharpe_ratio_min": -0.5},
        }
        assert config.validate_xray_targets(value) is value

    def test_xray_targets_cap_the_number_of_band_names(self):
        import config
        too_many = {f"Country {i}": {"max": 1} for i in range(config.XRAY_BAND_NAMES_MAX + 1)}
        with pytest.raises(ValueError, match="more than"):
            config.validate_xray_targets({"country_concentration": too_many})

    def test_xray_targets_reject_non_finite_numbers(self):
        import config
        with pytest.raises(ValueError):
            config.validate_xray_targets({"sector_targets": {"Technology": {"max": float("inf")}}})

    def test_meta_scoring_accepts_the_default_block(self):
        import config
        assert config.validate_meta_scoring(config.DEFAULT_CONFIG["META_SCORING"])

    def test_meta_scoring_rejects_nan(self):
        import config
        with pytest.raises(ValueError):
            config.validate_meta_scoring({"CRASH_VETO": {"MARKET_STRESS_THRESHOLD": float("nan")}})

    def test_every_default_xray_target_passes_its_own_validator(self):
        import config
        assert config.validate_xray_targets(config.DEFAULT_CONFIG["XRAY_TARGETS"])


@pytest.fixture
def private_config(tmp_path):
    import config as _config
    original_path = _config.SECRETS_PATH
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"HOME_EXCHANGE": "NYSE"}))
    _config.SECRETS_PATH = path
    _config._clear_config_cache()
    yield _config, path
    _config.SECRETS_PATH = original_path
    _config._clear_config_cache()


@pytest.mark.config
def test_load_config_hands_each_caller_an_independent_copy(private_config):
    """Mutating a returned config (top level or nested) must never reach the next caller."""
    _config, _ = private_config
    first = _config.load_config()
    expected = json.loads(json.dumps(first))
    first["HOME_EXCHANGE"] = "TAMPERED"
    first["SCHEDULING"]["TAMPERED"] = True
    first["IGNORED_TICKERS"].append("TAMPERED")
    second = _config.load_config()
    assert json.loads(json.dumps(second)) == expected


@pytest.mark.config
def test_load_config_parses_the_file_once_until_it_changes(private_config):
    _config, path = private_config
    with patch.object(_config, "_read_config", wraps=_config._read_config) as read:
        assert _config.load_config()["HOME_EXCHANGE"] == "NYSE"
        assert _config.load_config()["HOME_EXCHANGE"] == "NYSE"
        assert read.call_count == 1
        path.write_text(json.dumps({"HOME_EXCHANGE": "LSE", "SERVER_URL": "http://changed"}))
        assert _config.load_config()["HOME_EXCHANGE"] == "LSE"
        assert read.call_count == 2


@pytest.mark.config
def test_update_config_atomic_clears_the_cache_even_when_the_file_signature_repeats(private_config, monkeypatch):
    """A same-size rewrite that recycles the inode and mtime tick must not serve the previous config."""
    _config, _ = private_config
    monkeypatch.setattr(_config, "_config_signature", lambda: ("fixed",))
    _config.update_config_atomic({"HOME_EXCHANGE": "NYS"})
    assert _config.load_config()["HOME_EXCHANGE"] == "NYS"
    _config.update_config_atomic({"HOME_EXCHANGE": "LSE"})
    assert _config.load_config()["HOME_EXCHANGE"] == "LSE"


@pytest.mark.config
def test_load_config_does_not_cache_a_failed_read(private_config):
    _config, path = private_config
    path.write_text("{this is not valid json}")
    assert _config.load_config()["HOME_EXCHANGE"] == _config.DEFAULT_CONFIG["HOME_EXCHANGE"]
    assert _config._config_cache is None
    path.write_text(json.dumps({"HOME_EXCHANGE": "ASX", "SERVER_URL": "http://fixed"}))
    assert _config.load_config()["HOME_EXCHANGE"] == "ASX"


def test_count_request_event_counts_inside_a_request_and_ignores_outside_one():
    from utils import _request_counts, count_request_event
    count_request_event("outside")
    counts = {}
    token = _request_counts.set(counts)
    try:
        count_request_event("anchor_hit")
        count_request_event("anchor_hit")
        count_request_event("anchor_miss")
    finally:
        _request_counts.reset(token)
    assert counts == {"anchor_hit": 2, "anchor_miss": 1}
