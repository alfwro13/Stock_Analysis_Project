"""
tests/test_ghostfolio_sync.py  ── GHOSTFOLIO SYNC ENGINE

Comprehensive tests for every API contract point used by GhostfolioSyncEngine.
If any upstream Ghostfolio API endpoint changes its response shape, these tests
will catch it before the morning sync corrupts portfolio.json.

API endpoints under test:
  POST  /api/v1/auth/anonymous           → authenticate()
  GET   /api/v1/account                  → discover_accounts()
  GET   /api/v1/portfolio/holdings       → sync_portfolio()
  GET   /api/v1/watchlist                → sync_watchlist()
  POST  /api/v1/watchlist                → add_to_watchlist()
  DELETE /api/v1/watchlist/YAHOO/{sym}   → remove_from_watchlist()

FastAPI routes under test:
  POST /api/sync-ghostfolio
  POST /api/ghostfolio/discover
  POST /api/watchlist/add
  POST /api/watchlist/remove

Regression tests:
  BUG-2024-06-04  Ghostfolio API moved symbol/name/currency from top-level
                  holding object into nested assetProfile sub-object, causing
                  all holdings to collapse under a single empty-string key.
"""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

from ghostfolio_sync import GhostfolioSyncEngine


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _mock_resp(status_code: int, payload: dict) -> MagicMock:
    """Return a mock requests.Response with .status_code and .json()."""
    m = MagicMock()
    m.status_code = status_code
    m.json.return_value = payload
    m.raise_for_status = MagicMock()
    if status_code >= 400:
        m.raise_for_status.side_effect = requests.exceptions.HTTPError(f"HTTP {status_code}")
    return m


def _engine_with_bearer(monkeypatch) -> GhostfolioSyncEngine:
    """Return a pre-authenticated engine without hitting the network."""
    monkeypatch.setenv("GHOSTFOLIO_URL", "http://ghost.local")
    monkeypatch.setenv("GHOSTFOLIO_TOKEN", "test-token")
    with patch("ghostfolio_sync.GHOSTFOLIO_URL", "http://ghost.local"), \
         patch("ghostfolio_sync.GHOSTFOLIO_TOKEN", "test-token"):
        engine = GhostfolioSyncEngine()
    engine.headers = {"Authorization": "Bearer fake-bearer"}
    engine.active_account_ids = ["acc-1", "acc-2"]
    engine.discovered_accounts = [
        {"id": "acc-1", "name": "ISA"},
        {"id": "acc-2", "name": "FreeTrade"},
    ]
    return engine


# Minimal assetProfile-style holding (current API format as of June 2026)
HOLDING_AAPL = {
    "activitiesCount": 5,
    "marketPrice": 310.26,
    "quantity": 2.0,
    "investment": 500.0,
    "assetProfile": {
        "symbol": "AAPL",
        "name": "Apple Inc.",
        "currency": "USD",
        "dataSource": "YAHOO",
        "assetClass": "EQUITY",
        "assetSubClass": "STOCK",
    },
}

HOLDING_SMT = {
    "activitiesCount": 3,
    "marketPrice": 855.0,
    "quantity": 10.0,
    "investment": 8000.0,
    "assetProfile": {
        "symbol": "SMT.L",
        "name": "Scottish Mortgage Investment Trust",
        "currency": "GBp",  # pence
        "dataSource": "YAHOO",
        "assetClass": "EQUITY",
        "assetSubClass": "ETF",
    },
}

# Legacy flat-format holding (pre-June-2026; still accepted via fallback)
HOLDING_AAPL_FLAT = {
    "symbol": "AAPL",
    "name": "Apple Inc.",
    "currency": "USD",
    "quantity": 2.0,
    "investment": 500.0,
}


# ──────────────────────────────────────────────────────────────────────────────
# 0. __init__() — attribute defaults
# ──────────────────────────────────────────────────────────────────────────────

class TestInit:

    def test_active_account_ids_initialized(self, monkeypatch):
        """
        active_account_ids must be set in __init__ so sync_portfolio() can be
        called without a prior discover_accounts() call.
        """
        with patch("ghostfolio_sync.GHOSTFOLIO_URL", "http://ghost.local"), \
             patch("ghostfolio_sync.GHOSTFOLIO_TOKEN", "tok"), \
             patch("ghostfolio_sync.GHOSTFOLIO_ACCOUNTS", {"active": ["acc-x"]}):
            engine = GhostfolioSyncEngine()
        assert hasattr(engine, "active_account_ids"), "active_account_ids must exist after __init__"
        assert engine.active_account_ids == ["acc-x"]

    def test_discovered_accounts_initialized(self, monkeypatch):
        """
        discovered_accounts must default to [] so sync_portfolio() name lookup
        does not raise AttributeError when discover_accounts() was never called
        or failed at the outer exception level.
        """
        with patch("ghostfolio_sync.GHOSTFOLIO_URL", "http://ghost.local"), \
             patch("ghostfolio_sync.GHOSTFOLIO_TOKEN", "tok"), \
             patch("ghostfolio_sync.GHOSTFOLIO_ACCOUNTS", {"active": []}):
            engine = GhostfolioSyncEngine()
        assert hasattr(engine, "discovered_accounts"), "discovered_accounts must exist after __init__"
        assert engine.discovered_accounts == []


# ──────────────────────────────────────────────────────────────────────────────
# 1. authenticate()
# ──────────────────────────────────────────────────────────────────────────────

class TestAuthenticate:

    def test_success_sets_bearer_header(self, monkeypatch):
        """authenticate() must store the Bearer token in self.headers."""
        engine = _engine_with_bearer(monkeypatch)
        with patch("requests.post", return_value=_mock_resp(200, {"authToken": "tok-abc"})) as mock_post:
            engine.headers = {}
            result = engine.authenticate()
        assert result is True
        assert engine.headers == {"Authorization": "Bearer tok-abc"}
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert "/api/v1/auth/anonymous" in call_kwargs[0][0]
        assert call_kwargs[1]["json"]["accessToken"] == "test-token"

    def test_missing_auth_token_returns_false(self, monkeypatch):
        """authenticate() returns False when the server omits authToken."""
        engine = _engine_with_bearer(monkeypatch)
        with patch("requests.post", return_value=_mock_resp(200, {})):
            result = engine.authenticate()
        assert result is False

    def test_http_error_returns_false(self, monkeypatch):
        """authenticate() returns False on any HTTP/network error."""
        engine = _engine_with_bearer(monkeypatch)
        with patch("requests.post", side_effect=Exception("connection refused")):
            result = engine.authenticate()
        assert result is False

    def test_http_401_returns_false(self, monkeypatch):
        """authenticate() returns False on 401 Unauthorized."""
        engine = _engine_with_bearer(monkeypatch)
        with patch("requests.post", return_value=_mock_resp(401, {"error": "Unauthorized"})):
            result = engine.authenticate()
        assert result is False

    def test_uses_correct_endpoint(self, monkeypatch):
        """
        CONTRACT: auth endpoint must be /api/v1/auth/anonymous with POST.
        If Ghostfolio renames this endpoint this test will fail loudly.
        """
        engine = _engine_with_bearer(monkeypatch)
        calls = []
        with patch("requests.post", side_effect=lambda url, **kw: calls.append(url) or _mock_resp(200, {"authToken": "x"})):
            engine.authenticate()
        assert any("/api/v1/auth/anonymous" in url for url in calls), (
            "CONTRACT VIOLATION: Ghostfolio auth endpoint changed. "
            "Expected POST /api/v1/auth/anonymous"
        )


# ──────────────────────────────────────────────────────────────────────────────
# 2. discover_accounts()
# ──────────────────────────────────────────────────────────────────────────────

ACCOUNTS_RESP = {
    "accounts": [
        {"id": "acc-1", "name": "ISA",       "currency": "GBP", "isExcluded": False},
        {"id": "acc-2", "name": "FreeTrade",  "currency": "GBP", "isExcluded": False},
        {"id": "acc-3", "name": "Excluded",   "currency": "GBP", "isExcluded": True},
    ]
}


class TestDiscoverAccounts:

    def test_returns_non_excluded_accounts(self, monkeypatch, tmp_path):
        """discover_accounts() must skip accounts with isExcluded=True."""
        engine = _engine_with_bearer(monkeypatch)
        with patch("requests.get", return_value=_mock_resp(200, ACCOUNTS_RESP)), \
             patch("ghostfolio_sync.load_config", return_value={"GHOSTFOLIO_ACCOUNTS": {"active": []}}), \
             patch("ghostfolio_sync.update_config_atomic"):
            accounts = engine.discover_accounts()
        ids = [a["id"] for a in accounts]
        assert "acc-1" in ids
        assert "acc-2" in ids
        assert "acc-3" not in ids, "Excluded account must not appear in discovered list"

    def test_account_shape(self, monkeypatch):
        """
        CONTRACT: Each account object must have id, name, and currency.
        discover_accounts() must persist exactly these fields.
        """
        engine = _engine_with_bearer(monkeypatch)
        with patch("requests.get", return_value=_mock_resp(200, ACCOUNTS_RESP)), \
             patch("ghostfolio_sync.load_config", return_value={"GHOSTFOLIO_ACCOUNTS": {"active": []}}), \
             patch("ghostfolio_sync.update_config_atomic"):
            accounts = engine.discover_accounts()
        assert len(accounts) == 2
        for acc in accounts:
            assert "id"       in acc, f"Account missing 'id' field: {acc}"
            assert "name"     in acc, f"Account missing 'name' field: {acc}"
            assert "currency" in acc, f"Account missing 'currency' field: {acc}"

    def test_first_time_auto_activates_all(self, monkeypatch):
        """
        With no pre-existing active list, all discovered accounts are auto-activated.
        """
        engine = _engine_with_bearer(monkeypatch)
        captured = {}
        def capture(updates):
            captured.update(updates)

        with patch("requests.get", return_value=_mock_resp(200, ACCOUNTS_RESP)), \
             patch("ghostfolio_sync.load_config", return_value={"GHOSTFOLIO_ACCOUNTS": {"active": []}}), \
             patch("ghostfolio_sync.update_config_atomic", side_effect=capture):
            engine.discover_accounts()

        active = captured["GHOSTFOLIO_ACCOUNTS"]["active"]
        assert "acc-1" in active
        assert "acc-2" in active
        assert "acc-3" not in active

    def test_existing_active_list_preserved(self, monkeypatch):
        """Pre-existing active account selection must not be overwritten."""
        engine = _engine_with_bearer(monkeypatch)
        captured = {}
        with patch("requests.get", return_value=_mock_resp(200, ACCOUNTS_RESP)), \
             patch("ghostfolio_sync.load_config", return_value={"GHOSTFOLIO_ACCOUNTS": {"active": ["acc-1"]}}), \
             patch("ghostfolio_sync.update_config_atomic", side_effect=lambda u: captured.update(u)):
            engine.discover_accounts()
        assert captured["GHOSTFOLIO_ACCOUNTS"]["active"] == ["acc-1"]

    def test_http_error_returns_empty_list(self, monkeypatch):
        """discover_accounts() returns [] on network failure without raising."""
        engine = _engine_with_bearer(monkeypatch)
        with patch("requests.get", side_effect=Exception("timeout")):
            result = engine.discover_accounts()
        assert result == []

    def test_uses_correct_endpoint(self, monkeypatch):
        """CONTRACT: account list endpoint must be GET /api/v1/account."""
        engine = _engine_with_bearer(monkeypatch)
        calls = []
        with patch("requests.get", side_effect=lambda url, **kw: calls.append(url) or _mock_resp(200, {"accounts": []})), \
             patch("ghostfolio_sync.load_config", return_value={"GHOSTFOLIO_ACCOUNTS": {"active": []}}), \
             patch("ghostfolio_sync.update_config_atomic"):
            engine.discover_accounts()
        assert any("/api/v1/account" in url for url in calls), (
            "CONTRACT VIOLATION: Ghostfolio account list endpoint changed. "
            "Expected GET /api/v1/account"
        )


# ──────────────────────────────────────────────────────────────────────────────
# 3. sync_portfolio()  — current API format (assetProfile-nested)
# ──────────────────────────────────────────────────────────────────────────────

class TestSyncPortfolio:

    def _run_sync(self, monkeypatch, holdings_by_account: dict, tmp_path: Path):
        """
        Helper: run sync_portfolio() with mocked HTTP and capture written file.
        holdings_by_account: {acc_id: [holding, ...]}
        """
        engine = _engine_with_bearer(monkeypatch)
        portfolio_path = tmp_path / "portfolio.json"

        def fake_get(url, **kw):
            for acc_id, holdings in holdings_by_account.items():
                if acc_id in url:
                    return _mock_resp(200, {"holdings": holdings})
            return _mock_resp(200, {"holdings": []})

        with patch("requests.get", side_effect=fake_get), \
             patch("ghostfolio_sync.PORTFOLIO_PATH", portfolio_path):
            result = engine.sync_portfolio()

        data = json.loads(portfolio_path.read_text())
        return result, data

    # ── Happy path ─────────────────────────────────────────────────────────

    def test_success_returns_true(self, monkeypatch, tmp_path):
        result, _ = self._run_sync(monkeypatch, {"acc-1": [HOLDING_AAPL]}, tmp_path)
        assert result is True

    def test_current_api_format_assetprofile_nested(self, monkeypatch, tmp_path):
        """
        REGRESSION BUG-2024-06-04:
        Ghostfolio moved symbol/name/currency inside assetProfile.
        Old code used asset.get('symbol') → '' for all holdings.
        Result was one entry with key='' and ticker=''.
        """
        _, data = self._run_sync(monkeypatch, {"acc-1": [HOLDING_AAPL]}, tmp_path)

        # Must NOT collapse everything under empty key
        assert "" not in data, (
            "BUG-2024-06-04 REGRESSION: All holdings collapsed under empty key. "
            "Check that assetProfile.symbol/name/currency are being read correctly."
        )
        # Must produce exactly one entry with correct ticker
        assert len(data) == 1
        entry = next(iter(data.values()))
        assert entry["ticker"] == "AAPL", f"Expected ticker=AAPL, got {entry['ticker']!r}"

    def test_ticker_populated_from_assetprofile(self, monkeypatch, tmp_path):
        """Each entry's ticker must come from assetProfile.symbol."""
        _, data = self._run_sync(
            monkeypatch,
            {"acc-1": [HOLDING_AAPL, HOLDING_SMT]},
            tmp_path,
        )
        tickers = {v["ticker"] for v in data.values()}
        assert "AAPL"  in tickers
        assert "SMT.L" in tickers

    def test_legacy_flat_format_still_works(self, monkeypatch, tmp_path):
        """
        BACKWARD COMPAT: If Ghostfolio ever reverts to flat symbol/name/currency,
        the fallback `or asset.get(...)` must still produce a valid output.
        """
        _, data = self._run_sync(monkeypatch, {"acc-1": [HOLDING_AAPL_FLAT]}, tmp_path)
        assert "" not in data, "Flat-format holding collapsed to empty key"
        entry = next(iter(data.values()))
        assert entry["ticker"] == "AAPL"

    # ── GBp pence detection ───────────────────────────────────────────────

    def test_gbp_pence_flag_set_correctly(self, monkeypatch, tmp_path):
        """Holdings with currency=GBp must set price_in_pence=True."""
        _, data = self._run_sync(
            monkeypatch,
            {"acc-1": [HOLDING_AAPL, HOLDING_SMT]},
            tmp_path,
        )
        aapl = next(v for v in data.values() if v["ticker"] == "AAPL")
        smt  = next(v for v in data.values() if v["ticker"] == "SMT.L")
        assert aapl["price_in_pence"] is False
        assert smt["price_in_pence"]  is True

    # ── VWAP aggregation across accounts ─────────────────────────────────

    def test_cross_account_vwap(self, monkeypatch, tmp_path):
        """
        Same ticker in two accounts → global_shares is sum, global_buy_price
        is the VWAP (not a simple average).
        acc-1: 2 shares @ 250  (investment=500)
        acc-2: 3 shares @ 300  (investment=900)
        VWAP = (500+900)/(2+3) = 280.0
        """
        holding_acc2 = {
            **HOLDING_AAPL,
            "quantity": 3.0,
            "investment": 900.0,
            "assetProfile": {**HOLDING_AAPL["assetProfile"]},
        }
        _, data = self._run_sync(
            monkeypatch,
            {"acc-1": [HOLDING_AAPL], "acc-2": [holding_acc2]},
            tmp_path,
        )
        assert len(data) == 1
        entry = next(iter(data.values()))
        assert entry["global_shares"] == pytest.approx(5.0)
        assert entry["global_buy_price"] == pytest.approx(280.0, rel=1e-3)

    def test_cross_account_ledger_entries(self, monkeypatch, tmp_path):
        """Each per-account holding must appear as a separate entry in accounts[]."""
        holding_acc2 = {
            **HOLDING_AAPL,
            "quantity": 3.0,
            "investment": 900.0,
            "assetProfile": {**HOLDING_AAPL["assetProfile"]},
        }
        _, data = self._run_sync(
            monkeypatch,
            {"acc-1": [HOLDING_AAPL], "acc-2": [holding_acc2]},
            tmp_path,
        )
        entry = next(iter(data.values()))
        assert len(entry["accounts"]) == 2
        acc_ids = {a["id"] for a in entry["accounts"]}
        assert "acc-1" in acc_ids
        assert "acc-2" in acc_ids

    def test_account_buy_price_is_per_account_avg(self, monkeypatch, tmp_path):
        """Per-account buy_price must be investment/shares for that account only."""
        _, data = self._run_sync(monkeypatch, {"acc-1": [HOLDING_AAPL]}, tmp_path)
        entry = next(iter(data.values()))
        acc = entry["accounts"][0]
        expected_buy_price = 500.0 / 2.0  # = 250.0
        assert acc["buy_price"] == pytest.approx(expected_buy_price, rel=1e-3)

    # ── Output file structure ─────────────────────────────────────────────

    def test_global_total_investment_not_in_output(self, monkeypatch, tmp_path):
        """global_total_investment is a temp accumulator and must be stripped from the JSON file."""
        _, data = self._run_sync(monkeypatch, {"acc-1": [HOLDING_AAPL]}, tmp_path)
        for entry in data.values():
            assert "global_total_investment" not in entry, (
                "global_total_investment must be removed before writing to disk"
            )

    def test_output_file_written(self, monkeypatch, tmp_path):
        """sync_portfolio() must create/overwrite portfolio.json on disk."""
        portfolio_path = tmp_path / "portfolio.json"
        engine = _engine_with_bearer(monkeypatch)
        with patch("requests.get", return_value=_mock_resp(200, {"holdings": [HOLDING_AAPL]})), \
             patch("ghostfolio_sync.PORTFOLIO_PATH", portfolio_path):
            engine.sync_portfolio()
        assert portfolio_path.exists(), "portfolio.json was not created"
        content = json.loads(portfolio_path.read_text())
        assert isinstance(content, dict)
        assert len(content) > 0

    # ── Edge cases ────────────────────────────────────────────────────────

    def test_zero_quantity_holding_skipped(self, monkeypatch, tmp_path):
        """Holdings with quantity=0 must be silently ignored."""
        zero_holding = {**HOLDING_AAPL, "quantity": 0.0}
        result, data = self._run_sync(monkeypatch, {"acc-1": [zero_holding]}, tmp_path)
        assert result is True
        assert len(data) == 0, "Zero-quantity holding must not appear in output"

    def test_negative_quantity_holding_skipped(self, monkeypatch, tmp_path):
        """Holdings with quantity<0 must be silently ignored."""
        neg_holding = {**HOLDING_AAPL, "quantity": -1.0}
        result, data = self._run_sync(monkeypatch, {"acc-1": [neg_holding]}, tmp_path)
        assert len(data) == 0

    def test_empty_holdings_writes_empty_dict(self, monkeypatch, tmp_path):
        """When all accounts return empty holdings, output file is an empty dict."""
        result, data = self._run_sync(monkeypatch, {"acc-1": []}, tmp_path)
        assert result is True
        assert data == {}

    def test_no_active_accounts_returns_false(self, monkeypatch, tmp_path):
        """sync_portfolio() returns False immediately when active_account_ids is empty."""
        engine = _engine_with_bearer(monkeypatch)
        engine.active_account_ids = []
        with patch("ghostfolio_sync.PORTFOLIO_PATH", tmp_path / "portfolio.json"):
            result = engine.sync_portfolio()
        assert result is False

    def test_null_quantity_holding_skipped(self, monkeypatch, tmp_path):
        """
        API returns {"quantity": null} — float(None) must not crash the sync.
        The holding must be silently skipped (treated as zero quantity).
        """
        null_qty_holding = {**HOLDING_AAPL, "quantity": None}
        result, data = self._run_sync(monkeypatch, {"acc-1": [null_qty_holding]}, tmp_path)
        assert result is True
        assert len(data) == 0, "Null-quantity holding must be skipped, not crash"

    def test_null_investment_treated_as_zero(self, monkeypatch, tmp_path):
        """
        API returns {"investment": null} — float(None) must not crash the sync.
        The holding is kept (quantity is valid) with investment treated as 0.
        """
        null_inv_holding = {**HOLDING_AAPL, "investment": None}
        result, data = self._run_sync(monkeypatch, {"acc-1": [null_inv_holding]}, tmp_path)
        assert result is True
        assert len(data) == 1
        entry = next(iter(data.values()))
        assert entry["global_buy_price"] == pytest.approx(0.0)

    def test_http_error_on_one_account_continues(self, monkeypatch, tmp_path):
        """
        A 500 from one account must not abort the whole sync.
        Other accounts must still be processed.
        """
        engine = _engine_with_bearer(monkeypatch)
        portfolio_path = tmp_path / "portfolio.json"

        def fake_get(url, **kw):
            if "acc-1" in url:
                return _mock_resp(500, {})
            if "acc-2" in url:
                return _mock_resp(200, {"holdings": [HOLDING_AAPL]})
            return _mock_resp(200, {"holdings": []})

        with patch("requests.get", side_effect=fake_get), \
             patch("ghostfolio_sync.PORTFOLIO_PATH", portfolio_path):
            result = engine.sync_portfolio()

        data = json.loads(portfolio_path.read_text())
        assert result is True
        assert len(data) == 1, "acc-2 holdings must be in output even if acc-1 failed"

    def test_network_exception_returns_false(self, monkeypatch, tmp_path):
        """sync_portfolio() returns False when requests raises an exception."""
        engine = _engine_with_bearer(monkeypatch)
        with patch("requests.get", side_effect=Exception("connection refused")), \
             patch("ghostfolio_sync.PORTFOLIO_PATH", tmp_path / "portfolio.json"):
            result = engine.sync_portfolio()
        assert result is False

    def test_holdings_endpoint_url_format(self, monkeypatch, tmp_path):
        """
        CONTRACT: Holdings endpoint must be GET /api/v1/portfolio/holdings?accounts={id}.
        """
        engine = _engine_with_bearer(monkeypatch)
        called_urls = []

        def capture(url, **kw):
            called_urls.append(url)
            return _mock_resp(200, {"holdings": []})

        with patch("requests.get", side_effect=capture), \
             patch("ghostfolio_sync.PORTFOLIO_PATH", tmp_path / "portfolio.json"):
            engine.sync_portfolio()

        holding_calls = [u for u in called_urls if "portfolio/holdings" in u]
        assert len(holding_calls) == len(engine.active_account_ids), (
            "CONTRACT VIOLATION: Expected one holdings request per active account. "
            "Expected endpoint: /api/v1/portfolio/holdings?accounts={id}"
        )
        for url in holding_calls:
            assert "/api/v1/portfolio/holdings" in url
            assert "accounts=" in url

    def test_multiple_different_tickers(self, monkeypatch, tmp_path):
        """Multiple distinct tickers in one account produce separate output entries."""
        _, data = self._run_sync(
            monkeypatch,
            {"acc-1": [HOLDING_AAPL, HOLDING_SMT]},
            tmp_path,
        )
        assert len(data) == 2
        tickers = {v["ticker"] for v in data.values()}
        assert tickers == {"AAPL", "SMT.L"}


# ──────────────────────────────────────────────────────────────────────────────
# 4. sync_watchlist()
# ──────────────────────────────────────────────────────────────────────────────

class TestSyncWatchlist:

    def _run_sync(self, monkeypatch, api_payload, tmp_path):
        engine = _engine_with_bearer(monkeypatch)
        watchlist_path = tmp_path / "watchlist.json"
        with patch("requests.get", return_value=_mock_resp(200, api_payload)), \
             patch("ghostfolio_sync.WATCHLIST_PATH", watchlist_path):
            result = engine.sync_watchlist()
        data = json.loads(watchlist_path.read_text())
        return result, data

    def test_dict_format_with_watchlist_key(self, monkeypatch, tmp_path):
        """
        CONTRACT: Ghostfolio returns {"watchlist": [{"symbol": "TSLA"}, ...]}.
        Tickers must be extracted and saved.
        """
        payload = {"watchlist": [{"symbol": "TSLA"}, {"symbol": "NVDA"}]}
        result, data = self._run_sync(monkeypatch, payload, tmp_path)
        assert result is True
        assert set(data["watchlist"]) == {"TSLA", "NVDA"}

    def test_direct_list_format(self, monkeypatch, tmp_path):
        """
        BACKWARD COMPAT: Ghostfolio may return a bare list instead of a dict.
        sync_watchlist() must handle both.
        """
        payload = [{"symbol": "TSLA"}, {"symbol": "NVDA"}]
        result, data = self._run_sync(monkeypatch, payload, tmp_path)
        assert result is True
        assert set(data["watchlist"]) == {"TSLA", "NVDA"}

    def test_items_without_symbol_excluded(self, monkeypatch, tmp_path):
        """Items missing a symbol field must be silently dropped."""
        payload = {"watchlist": [{"symbol": "TSLA"}, {"name": "No ticker here"}]}
        _, data = self._run_sync(monkeypatch, payload, tmp_path)
        assert data["watchlist"] == ["TSLA"]

    def test_empty_watchlist_writes_empty_list(self, monkeypatch, tmp_path):
        payload = {"watchlist": []}
        result, data = self._run_sync(monkeypatch, payload, tmp_path)
        assert result is True
        assert data["watchlist"] == []

    def test_http_error_returns_false(self, monkeypatch, tmp_path):
        engine = _engine_with_bearer(monkeypatch)
        with patch("requests.get", side_effect=Exception("timeout")), \
             patch("ghostfolio_sync.WATCHLIST_PATH", tmp_path / "watchlist.json"):
            result = engine.sync_watchlist()
        assert result is False

    def test_file_written_with_watchlist_key(self, monkeypatch, tmp_path):
        """Output file must be {"watchlist": [...]} regardless of API format."""
        payload = {"watchlist": [{"symbol": "VOO"}]}
        _, data = self._run_sync(monkeypatch, payload, tmp_path)
        assert "watchlist" in data
        assert isinstance(data["watchlist"], list)

    def test_watchlist_endpoint_url(self, monkeypatch, tmp_path):
        """CONTRACT: watchlist endpoint must be GET /api/v1/watchlist."""
        engine = _engine_with_bearer(monkeypatch)
        called_urls = []
        with patch("requests.get", side_effect=lambda url, **kw: called_urls.append(url) or _mock_resp(200, {"watchlist": []})), \
             patch("ghostfolio_sync.WATCHLIST_PATH", tmp_path / "watchlist.json"):
            engine.sync_watchlist()
        assert any("/api/v1/watchlist" in u for u in called_urls), (
            "CONTRACT VIOLATION: Ghostfolio watchlist GET endpoint changed. "
            "Expected GET /api/v1/watchlist"
        )


# ──────────────────────────────────────────────────────────────────────────────
# 5. add_to_watchlist()
# ──────────────────────────────────────────────────────────────────────────────

class TestAddToWatchlist:

    def test_success_on_201(self, monkeypatch):
        engine = _engine_with_bearer(monkeypatch)
        with patch("requests.post", return_value=_mock_resp(201, {})) as mock_post, \
             patch.object(engine, "authenticate", return_value=True):
            result = engine.add_to_watchlist("TSLA")
        assert result is True

    def test_success_on_200(self, monkeypatch):
        engine = _engine_with_bearer(monkeypatch)
        with patch("requests.post", return_value=_mock_resp(200, {})), \
             patch.object(engine, "authenticate", return_value=True):
            result = engine.add_to_watchlist("TSLA")
        assert result is True

    def test_failure_on_400(self, monkeypatch):
        engine = _engine_with_bearer(monkeypatch)
        with patch("requests.post", return_value=_mock_resp(400, {"error": "bad request"})), \
             patch.object(engine, "authenticate", return_value=True):
            result = engine.add_to_watchlist("TSLA")
        assert result is False

    def test_auth_failure_returns_false(self, monkeypatch):
        engine = _engine_with_bearer(monkeypatch)
        with patch.object(engine, "authenticate", return_value=False):
            result = engine.add_to_watchlist("TSLA")
        assert result is False

    def test_network_exception_returns_false(self, monkeypatch):
        engine = _engine_with_bearer(monkeypatch)
        with patch("requests.post", side_effect=Exception("timeout")), \
             patch.object(engine, "authenticate", return_value=True):
            result = engine.add_to_watchlist("TSLA")
        assert result is False

    def test_payload_shape(self, monkeypatch):
        """
        CONTRACT: POST /api/v1/watchlist must receive {"symbol": ..., "dataSource": "YAHOO"}.
        If Ghostfolio changes the required payload fields this test will fail.
        """
        engine = _engine_with_bearer(monkeypatch)
        captured_payloads = []

        def capture(url, json=None, **kw):
            captured_payloads.append(json)
            return _mock_resp(201, {})

        with patch("requests.post", side_effect=capture), \
             patch.object(engine, "authenticate", return_value=True):
            engine.add_to_watchlist("NVDA")

        assert len(captured_payloads) == 1
        payload = captured_payloads[0]
        assert payload.get("symbol") == "NVDA", "Payload must include symbol"
        assert payload.get("dataSource") == "YAHOO", (
            "CONTRACT VIOLATION: add_to_watchlist payload must include dataSource=YAHOO"
        )

    def test_endpoint_url(self, monkeypatch):
        """CONTRACT: add_to_watchlist must POST to /api/v1/watchlist."""
        engine = _engine_with_bearer(monkeypatch)
        called_urls = []
        with patch("requests.post", side_effect=lambda url, **kw: called_urls.append(url) or _mock_resp(201, {})), \
             patch.object(engine, "authenticate", return_value=True):
            engine.add_to_watchlist("AMD")
        assert any("/api/v1/watchlist" in u for u in called_urls), (
            "CONTRACT VIOLATION: add_to_watchlist endpoint changed. "
            "Expected POST /api/v1/watchlist"
        )


# ──────────────────────────────────────────────────────────────────────────────
# 6. remove_from_watchlist()
# ──────────────────────────────────────────────────────────────────────────────

class TestRemoveFromWatchlist:

    def test_success_on_200(self, monkeypatch):
        engine = _engine_with_bearer(monkeypatch)
        with patch("requests.delete", return_value=_mock_resp(200, {})), \
             patch.object(engine, "authenticate", return_value=True):
            result = engine.remove_from_watchlist("TSLA")
        assert result is True

    def test_success_on_204(self, monkeypatch):
        engine = _engine_with_bearer(monkeypatch)
        with patch("requests.delete", return_value=_mock_resp(204, {})), \
             patch.object(engine, "authenticate", return_value=True):
            result = engine.remove_from_watchlist("TSLA")
        assert result is True

    def test_failure_on_404(self, monkeypatch):
        engine = _engine_with_bearer(monkeypatch)
        with patch("requests.delete", return_value=_mock_resp(404, {})), \
             patch.object(engine, "authenticate", return_value=True):
            result = engine.remove_from_watchlist("TSLA")
        assert result is False

    def test_auth_failure_returns_false(self, monkeypatch):
        engine = _engine_with_bearer(monkeypatch)
        with patch.object(engine, "authenticate", return_value=False):
            result = engine.remove_from_watchlist("TSLA")
        assert result is False

    def test_network_exception_returns_false(self, monkeypatch):
        engine = _engine_with_bearer(monkeypatch)
        with patch("requests.delete", side_effect=Exception("timeout")), \
             patch.object(engine, "authenticate", return_value=True):
            result = engine.remove_from_watchlist("TSLA")
        assert result is False

    def test_endpoint_url_includes_datasource_and_symbol(self, monkeypatch):
        """
        CONTRACT: remove_from_watchlist must DELETE /api/v1/watchlist/YAHOO/{symbol}.
        If Ghostfolio changes the DELETE route structure this test will fail loudly.
        """
        engine = _engine_with_bearer(monkeypatch)
        called_urls = []
        with patch("requests.delete", side_effect=lambda url, **kw: called_urls.append(url) or _mock_resp(204, {})), \
             patch.object(engine, "authenticate", return_value=True):
            engine.remove_from_watchlist("NVDA")
        assert len(called_urls) == 1
        url = called_urls[0]
        assert "/api/v1/watchlist/YAHOO/NVDA" in url, (
            f"CONTRACT VIOLATION: Expected DELETE /api/v1/watchlist/YAHOO/NVDA, got {url}"
        )


# ──────────────────────────────────────────────────────────────────────────────
# 7. run_full_sync()
# ──────────────────────────────────────────────────────────────────────────────

class TestRunFullSync:

    def test_not_configured_returns_false(self, monkeypatch):
        """run_full_sync() must abort early when credentials are missing."""
        with patch("ghostfolio_sync.GHOSTFOLIO_URL", ""), \
             patch("ghostfolio_sync.GHOSTFOLIO_TOKEN", ""):
            engine = GhostfolioSyncEngine()
        result = engine.run_full_sync()
        assert result is False

    def test_auth_failure_returns_false(self, monkeypatch):
        """run_full_sync() returns False when authentication fails."""
        engine = _engine_with_bearer(monkeypatch)
        with patch.object(engine, "authenticate", return_value=False):
            result = engine.run_full_sync()
        assert result is False

    def test_success_calls_all_steps_in_order(self, monkeypatch, tmp_path):
        """run_full_sync() must call authenticate → discover_accounts → sync_portfolio → sync_watchlist."""
        engine = _engine_with_bearer(monkeypatch)
        call_order = []
        with patch.object(engine, "authenticate",    side_effect=lambda: call_order.append("auth")     or True), \
             patch.object(engine, "discover_accounts", side_effect=lambda: call_order.append("discover") or []), \
             patch.object(engine, "sync_portfolio",  side_effect=lambda: call_order.append("portfolio") or True), \
             patch.object(engine, "sync_watchlist",  side_effect=lambda: call_order.append("watchlist") or True):
            engine.run_full_sync()
        assert call_order == ["auth", "discover", "portfolio", "watchlist"]

    def test_portfolio_failure_returns_false(self, monkeypatch):
        """run_full_sync() returns False when sync_portfolio fails."""
        engine = _engine_with_bearer(monkeypatch)
        with patch.object(engine, "authenticate",     return_value=True), \
             patch.object(engine, "discover_accounts", return_value=[]), \
             patch.object(engine, "sync_portfolio",   return_value=False), \
             patch.object(engine, "sync_watchlist",   return_value=True):
            result = engine.run_full_sync()
        assert result is False

    def test_watchlist_failure_returns_false(self, monkeypatch):
        """run_full_sync() returns False when sync_watchlist fails."""
        engine = _engine_with_bearer(monkeypatch)
        with patch.object(engine, "authenticate",     return_value=True), \
             patch.object(engine, "discover_accounts", return_value=[]), \
             patch.object(engine, "sync_portfolio",   return_value=True), \
             patch.object(engine, "sync_watchlist",   return_value=False):
            result = engine.run_full_sync()
        assert result is False


# ──────────────────────────────────────────────────────────────────────────────
# 8. FastAPI routes that wrap GhostfolioSyncEngine
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.api
class TestGhostfolioApiRoutes:

    def test_sync_ghostfolio_route_returns_success(self, client):
        """POST /api/sync-ghostfolio must accept the request and return success."""
        with patch("api_routes.run_ghostfolio_sync"):
            resp = client.post("/api/sync-ghostfolio")
        assert resp.status_code == 200
        assert resp.json().get("status") == "success"

    def test_ghostfolio_discover_success(self, client):
        """POST /api/ghostfolio/discover returns success when accounts are found."""
        mock_engine = MagicMock()
        mock_engine.authenticate.return_value = True
        mock_engine.discover_accounts.return_value = [
            {"id": "acc-1", "name": "ISA"},
            {"id": "acc-2", "name": "FreeTrade"},
        ]
        with patch("api_routes.GhostfolioSyncEngine", return_value=mock_engine), \
             patch("api_routes.reload_scheduler"):
            resp = client.post("/api/ghostfolio/discover")
        assert resp.status_code == 200
        assert resp.json().get("status") == "success"

    def test_ghostfolio_discover_auth_failure(self, client):
        """POST /api/ghostfolio/discover returns 500 when auth fails."""
        mock_engine = MagicMock()
        mock_engine.authenticate.return_value = False
        with patch("api_routes.GhostfolioSyncEngine", return_value=mock_engine):
            resp = client.post("/api/ghostfolio/discover")
        assert resp.status_code == 500
        assert resp.json().get("status") == "error"

    def test_ghostfolio_discover_no_accounts(self, client):
        """POST /api/ghostfolio/discover returns 500 when no accounts are found."""
        mock_engine = MagicMock()
        mock_engine.authenticate.return_value = True
        mock_engine.discover_accounts.return_value = []
        with patch("api_routes.GhostfolioSyncEngine", return_value=mock_engine):
            resp = client.post("/api/ghostfolio/discover")
        assert resp.status_code == 500

    def test_watchlist_add_success(self, client):
        """POST /api/watchlist/add returns success when Ghostfolio add succeeds."""
        mock_engine = MagicMock()
        mock_engine.add_to_watchlist.return_value = True
        mock_engine.sync_watchlist.return_value = True
        with patch("api_routes.GhostfolioSyncEngine", return_value=mock_engine):
            resp = client.post("/api/watchlist/add", json={"ticker": "NVDA"})
        assert resp.status_code == 200
        assert resp.json().get("status") == "success"

    def test_watchlist_add_triggers_sync(self, client):
        """POST /api/watchlist/add must re-sync the watchlist file after a successful add."""
        mock_engine = MagicMock()
        mock_engine.add_to_watchlist.return_value = True
        mock_engine.sync_watchlist.return_value = True
        with patch("api_routes.GhostfolioSyncEngine", return_value=mock_engine):
            client.post("/api/watchlist/add", json={"ticker": "NVDA"})
        mock_engine.sync_watchlist.assert_called_once()

    def test_watchlist_add_failure_returns_500(self, client):
        """POST /api/watchlist/add returns 500 when Ghostfolio rejects the add."""
        mock_engine = MagicMock()
        mock_engine.add_to_watchlist.return_value = False
        with patch("api_routes.GhostfolioSyncEngine", return_value=mock_engine):
            resp = client.post("/api/watchlist/add", json={"ticker": "NVDA"})
        assert resp.status_code == 500

    def test_watchlist_add_no_sync_on_failure(self, client):
        """POST /api/watchlist/add must NOT call sync_watchlist when add fails."""
        mock_engine = MagicMock()
        mock_engine.add_to_watchlist.return_value = False
        with patch("api_routes.GhostfolioSyncEngine", return_value=mock_engine):
            client.post("/api/watchlist/add", json={"ticker": "NVDA"})
        mock_engine.sync_watchlist.assert_not_called()

    def test_watchlist_remove_success(self, client):
        """POST /api/watchlist/remove returns success when Ghostfolio remove succeeds."""
        mock_engine = MagicMock()
        mock_engine.remove_from_watchlist.return_value = True
        mock_engine.sync_watchlist.return_value = True
        with patch("api_routes.GhostfolioSyncEngine", return_value=mock_engine):
            resp = client.post("/api/watchlist/remove", json={"ticker": "NVDA"})
        assert resp.status_code == 200
        assert resp.json().get("status") == "success"

    def test_watchlist_remove_triggers_sync(self, client):
        """POST /api/watchlist/remove must re-sync the watchlist file after a successful remove."""
        mock_engine = MagicMock()
        mock_engine.remove_from_watchlist.return_value = True
        mock_engine.sync_watchlist.return_value = True
        with patch("api_routes.GhostfolioSyncEngine", return_value=mock_engine):
            client.post("/api/watchlist/remove", json={"ticker": "NVDA"})
        mock_engine.sync_watchlist.assert_called_once()

    def test_watchlist_remove_failure_returns_500(self, client):
        """POST /api/watchlist/remove returns 500 when Ghostfolio rejects the remove."""
        mock_engine = MagicMock()
        mock_engine.remove_from_watchlist.return_value = False
        with patch("api_routes.GhostfolioSyncEngine", return_value=mock_engine):
            resp = client.post("/api/watchlist/remove", json={"ticker": "NVDA"})
        assert resp.status_code == 500

    def test_watchlist_remove_no_sync_on_failure(self, client):
        """POST /api/watchlist/remove must NOT call sync_watchlist when remove fails."""
        mock_engine = MagicMock()
        mock_engine.remove_from_watchlist.return_value = False
        with patch("api_routes.GhostfolioSyncEngine", return_value=mock_engine):
            client.post("/api/watchlist/remove", json={"ticker": "NVDA"})
        mock_engine.sync_watchlist.assert_not_called()


# ──────────────────────────────────────────────────────────────────────────────
# 9. API contract shape — explicit field-level checks
#    These are the canary tests: they document the exact API response shape
#    that the sync code depends on.  If Ghostfolio renames or moves a field,
#    one of these will be the first to fail.
# ──────────────────────────────────────────────────────────────────────────────

class TestApiContractShape:
    """
    Document the precise shape of each Ghostfolio API response that this app
    consumes.  These tests act as a living specification: if Ghostfolio changes
    a field name, the relevant test below will fail with a clear message before
    any data is corrupted.
    """

    def test_auth_response_contains_auth_token_key(self):
        """
        POST /api/v1/auth/anonymous → {"authToken": "..."}
        The key must be 'authToken' (not 'token', 'bearerToken', etc.).
        """
        response_payload = {"authToken": "some-jwt-here"}
        assert "authToken" in response_payload, (
            "CONTRACT: Ghostfolio auth response must contain 'authToken' key"
        )

    def test_account_list_response_wraps_in_accounts_key(self):
        """
        GET /api/v1/account → {"accounts": [...]}
        Accounts must be under the 'accounts' key.
        """
        response_payload = {"accounts": [{"id": "x", "name": "y", "currency": "GBP", "isExcluded": False}]}
        assert "accounts" in response_payload
        acc = response_payload["accounts"][0]
        for field in ("id", "name", "currency", "isExcluded"):
            assert field in acc, f"Account object missing required field: {field!r}"

    def test_holdings_response_uses_holdings_key(self):
        """
        GET /api/v1/portfolio/holdings → {"holdings": [...]}
        Holdings must be a list under the 'holdings' key (not 'data', not 'positions').
        """
        response_payload = {"holdings": [HOLDING_AAPL]}
        assert "holdings" in response_payload, (
            "CONTRACT: holdings response must use key 'holdings'"
        )
        assert isinstance(response_payload["holdings"], list)

    def test_holding_object_has_assetprofile_nested(self):
        """
        CONTRACT (current format, as of June 2026):
        symbol / name / currency live inside assetProfile, not at the top level.
        If this assertion fails, the API has reverted or changed again.
        """
        holding = HOLDING_AAPL
        assert "assetProfile" in holding, (
            "CONTRACT VIOLATION: holding object no longer has assetProfile sub-object"
        )
        profile = holding["assetProfile"]
        for field in ("symbol", "name", "currency"):
            assert field in profile, (
                f"CONTRACT VIOLATION: assetProfile missing field {field!r}. "
                f"Check if Ghostfolio moved it back to the top level."
            )

    def test_holding_object_has_quantity_and_investment_at_top_level(self):
        """
        CONTRACT: quantity and investment remain at the top level of each holding.
        """
        holding = HOLDING_AAPL
        assert "quantity"   in holding, "CONTRACT: 'quantity' must be at top level of holding"
        assert "investment" in holding, "CONTRACT: 'investment' must be at top level of holding"

    def test_watchlist_response_uses_watchlist_key(self):
        """
        GET /api/v1/watchlist → {"watchlist": [{"symbol": "..."}, ...]}
        Watchlist items must be under 'watchlist' key and each item must have 'symbol'.
        """
        response_payload = {"watchlist": [{"symbol": "TSLA"}, {"symbol": "NVDA"}]}
        assert "watchlist" in response_payload
        for item in response_payload["watchlist"]:
            assert "symbol" in item, f"Watchlist item missing 'symbol' field: {item}"

    def test_pence_currency_identifier(self):
        """
        CONTRACT: GBP pence is identified by currency=='GBp' (capital G, lowercase p).
        If Ghostfolio changes this to 'GBX' or 'GBp ' the price_in_pence flag breaks.
        """
        assert HOLDING_SMT["assetProfile"]["currency"] == "GBp", (
            "CONTRACT: pence currency must be 'GBp' exactly. "
            "Update is_pence detection if Ghostfolio changes this identifier."
        )
