import time

import pytest

import accounts_engine
from database import add_transaction, create_account, get_connection

from .helpers import ACCOUNT_TABLES, MCP_KEY


@pytest.fixture(autouse=True)
def _mcp_key(monkeypatch):
    monkeypatch.setenv("MCP_API_KEY", MCP_KEY)


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
