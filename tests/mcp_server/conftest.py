import time

import pytest

import accounts_engine
import db_mcp_keys
from database import add_transaction, create_account, get_connection

from .helpers import ACCOUNT_TABLES, MCP_KEY, emptied_tables


class _InlineExecutor:
    def submit(self, fn, *args):
        fn(*args)


@pytest.fixture(autouse=True)
def _mcp_key(monkeypatch):
    from mcp_server import audit, keys, limits, oauth
    monkeypatch.setattr("mcp_server.auth.load_config", lambda: {"MCP_SERVER_ENABLED": True})
    monkeypatch.setattr(keys, "_executor", _InlineExecutor())
    monkeypatch.setattr(audit, "_executor", _InlineExecutor())
    monkeypatch.setattr(oauth, "_executor", _InlineExecutor())
    key_id = db_mcp_keys.add_key("Test key", keys.hash_key(MCP_KEY), "2026-10-01 09:00:00")
    keys.invalidate()
    limits.reset()
    try:
        yield
    finally:
        db_mcp_keys.delete_key(key_id)
        keys.invalidate()
        limits.reset()
        oauth.invalidate()
        conn = get_connection()
        try:
            for table in ("mcp_audit_log", "mcp_oauth_client", "mcp_oauth_requests", "mcp_oauth_grants", "mcp_oauth_tokens"):
                conn.execute(f"DELETE FROM {table}")
            conn.commit()
        finally:
            conn.close()


@pytest.fixture(autouse=True)
def remote_notifications(monkeypatch):
    """The MCP Remote Access events that notify() was asked to send, run inline and never delivered, so no test writes system_notifications or reaches Nextcloud."""
    from mcp_server import events
    sent = []
    monkeypatch.setattr(events, "_executor", _InlineExecutor())
    monkeypatch.setattr(events, "notify", lambda source, message_type, text, **kwargs: sent.append({"source": source, "type": message_type, "text": text, **kwargs}))
    return sent


@pytest.fixture(autouse=True)
def _no_macro_refresh_worker(monkeypatch):
    """get_market_regime reads Fear & Greed, which spawns a Yahoo-fetching thread when the macro cache is stale; its late yahoo_api_* writes race every table-count assertion."""
    monkeypatch.setattr("sentiment_engine._check_and_trigger_async_refresh", lambda: None)


@pytest.fixture
def no_mcp_keys():
    from mcp_server import keys
    with emptied_tables("mcp_api_keys"):
        keys.invalidate()
        yield
    keys.invalidate()


@pytest.fixture
def mcp_client(raw_client):
    return raw_client


@pytest.fixture
def seeded_accounts():
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO stock_signals (ticker, current_price, currency) VALUES ('ZZMCP', 120.0, 'GBP')"
        )
        conn.commit()
    finally:
        conn.close()
    with_snapshot = create_account("McpSnapshotAcc", "GBP", initial_cash=1000.0)
    add_transaction(with_snapshot, "Buy", "2026-01-05", ticker="ZZMCP", currency="GBP", quantity=10, unit_price=100, exchange_rate=1.0)
    accounts_engine.refresh_performance_cache(with_snapshot)
    without_snapshot = create_account("McpNoSnapshotAcc", "GBP", initial_cash=50.0)
    pension_unpriced = create_account("McpPensionAcc", "GBP", account_type="Pension")
    ids = {"snapshot": with_snapshot, "no_snapshot": without_snapshot, "pension": pension_unpriced}
    try:
        yield ids
    finally:
        conn = get_connection()
        try:
            for account_id in ids.values():
                for table in ACCOUNT_TABLES:
                    conn.execute(f"DELETE FROM {table} WHERE account_id = ?", (account_id,))
                conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
            conn.execute("DELETE FROM stock_signals WHERE ticker = 'ZZMCP'")
            conn.commit()
        finally:
            conn.close()


def _fake_rate(currency, **kwargs):
    return {"USD": None, "GBp": 0.01}.get(currency, 1.0)


_BOOK_TICKERS = ("ZZMCPA", "ZZMCPB", "ZZMCPU", "ZZMCPP")
_PRICE_EPOCH_B = "2026-10-01 09:00:00"


@pytest.fixture
def holdings_book(monkeypatch):
    monkeypatch.setattr(accounts_engine, "get_rate_to_base", _fake_rate)
    monkeypatch.setattr("portfolio_metrics_engine.get_rate_to_base", _fake_rate)
    live_epoch = time.time() - 120
    conn = get_connection()
    try:
        for ticker, price, currency, updated in (
            ("ZZMCPA", 120.0, "GBP", None), ("ZZMCPB", 50.0, "GBP", _PRICE_EPOCH_B),
            ("ZZMCPU", 10.0, "USD", _PRICE_EPOCH_B), ("ZZMCPP", 5000.0, "GBp", _PRICE_EPOCH_B),
        ):
            conn.execute(
                "INSERT OR REPLACE INTO stock_signals (ticker, current_price, currency, quote_type, last_updated) VALUES (?, ?, ?, 'EQUITY', ?)",
                (ticker, price, currency, updated),
            )
        conn.execute(
            "INSERT OR REPLACE INTO market_pulse_cache (ticker, name, price, change_pts, change_pct, is_positive, last_updated, "
            "extended_price, extended_change_pct, extended_session) VALUES ('ZZMCPA', 'A', 120.0, 1.5, 1.27, 1, ?, 125.0, 4.2, 'pre')",
            (live_epoch,),
        )
        conn.commit()
    finally:
        conn.close()
    first = create_account("McpBookA", "GBP", initial_cash=0.0)
    second = create_account("McpBookB", "GBP", initial_cash=0.0)
    empty = create_account("McpBookEmpty", "GBP")
    pension = create_account("McpBookPension", "GBP", account_type="Pension")
    add_transaction(first, "Buy", "2026-01-05", ticker="ZZMCPA", currency="GBP", quantity=10, unit_price=100, exchange_rate=1.0)
    add_transaction(first, "Buy", "2026-01-05", ticker="ZZMCPB", currency="GBP", quantity=5, unit_price=40, exchange_rate=1.0)
    add_transaction(second, "Buy", "2026-01-06", ticker="ZZMCPA", currency="GBP", quantity=2, unit_price=110, exchange_rate=1.0)
    add_transaction(second, "Buy", "2026-01-06", ticker="ZZMCPU", currency="USD", quantity=3, unit_price=8, exchange_rate=0.8)
    add_transaction(second, "Buy", "2026-01-06", ticker="ZZMCPP", currency="GBP", quantity=2, unit_price=40, exchange_rate=1.0)
    ids = {"first": first, "second": second, "empty": empty, "pension": pension, "live_epoch": live_epoch}
    try:
        yield ids
    finally:
        conn = get_connection()
        try:
            for account_id in (first, second, empty, pension):
                for table in ACCOUNT_TABLES:
                    conn.execute(f"DELETE FROM {table} WHERE account_id = ?", (account_id,))
                conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
            for ticker in _BOOK_TICKERS:
                conn.execute("DELETE FROM stock_signals WHERE ticker = ?", (ticker,))
                conn.execute("DELETE FROM market_pulse_cache WHERE ticker = ?", (ticker,))
                conn.execute("DELETE FROM xray_risk_cache WHERE ticker = ?", (ticker,))
            conn.commit()
        finally:
            conn.close()
