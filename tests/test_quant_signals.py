"""
tests/test_quant_signals.py

Unit tests for quant_signals.QuantEngine.run_all() business logic.
"""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import database as _db
from quant_signals import QuantEngine


def test_run_all_skips_tbill_synthetic_tickers(tmp_path):
    """TBILL-{txn_id} has no Yahoo Finance listing (see AGENTS.md's Treasury Bill section) —
    a stray parquet file for one must never reach analyze_ticker()."""
    (tmp_path / "TBILL-606.parquet").touch()
    (tmp_path / "AAPL.parquet").touch()
    (tmp_path / "SP500_BASELINE.parquet").touch()

    engine = QuantEngine()
    with patch("quant_signals.HISTORICAL_DIR", tmp_path), \
         patch.object(engine, "analyze_ticker") as mock_analyze:
        engine.run_all()

    analyzed = {c.args[0] for c in mock_analyze.call_args_list}
    assert analyzed == {"AAPL"}


_SAVE_TO_DB_DEFAULTS = dict(
    company_name="Test Co", sector="Technology", country="US", currency="USD", quote_type="EQUITY",
    price=100.0, ma5=None, ma10=None, ma21=None, ma50=None, ma200=None,
    trend_50d="Neutral", trend_200d="Neutral", rsi=None, stop_loss=None,
    fifty_two_week_low=None, fifty_two_week_high=None,
    trailing_pe=None, forward_pe=None, peg_ratio=None, peter_lynch_peg=None, price_to_book=None,
    price_to_sales=None, free_cash_flow=None,
    profit_margin=None, roe=None, revenue_growth=None, debt_to_equity=None, current_ratio=None,
    operating_cash_flow=None,
    ytd_return=None, total_assets=None, nav_price=None, expense_ratio=None,
    dividend_yield=None, ex_dividend_date=None, target_price=None, analyst_rating="none",
    next_earnings_date="Unknown",
    short_interest=None, institutional_ownership=None, beta=None, yield_correlation=None,
    score=50, signal="NEUTRAL", notes="", tags_json="[]",
)


class TestSaveToDbDoesNotWipeOtherEnginesColumns:
    """INSERT OR REPLACE used to reset every column not in this statement to its schema default,
    silently wiping top_holdings/sector_weightings/holdings_updated_at (universe_fundamentals_engine's
    sync_etf_holdings_cache) and piotroski_f_score/altman_z_score/beneish_m_score/forensic_last_updated
    (scheduler_jobs.py's monthly Forensic job) on every Mon-Fri quant scan run. Fixed via ON CONFLICT
    DO UPDATE with an explicit column list (found/fixed 2026-07-13)."""

    TICKER = "TST_QS_UPSERT_WIPE"

    def teardown_method(self):
        conn = _db.get_connection()
        try:
            conn.execute("DELETE FROM stock_signals WHERE ticker=?", (self.TICKER,))
            conn.commit()
        finally:
            conn.close()

    def test_second_write_preserves_other_engines_columns(self):
        conn = _db.get_connection()
        conn.execute(
            "INSERT INTO stock_signals (ticker, top_holdings, sector_weightings, holdings_updated_at, "
            "piotroski_f_score, altman_z_score, beneish_m_score, forensic_last_updated) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (self.TICKER, '[{"symbol": "NVDA", "weight": 0.1}]', '[{"name": "Technology", "weight": 1.0}]',
             "2026-07-01 00:00:00", 7.0, 3.2, -2.1, "2026-07-01 00:00:00"),
        )
        conn.commit()
        conn.close()

        QuantEngine().save_to_db(self.TICKER, **_SAVE_TO_DB_DEFAULTS)

        conn = _db.get_connection()
        row = conn.execute(
            "SELECT company_name, top_holdings, sector_weightings, holdings_updated_at, "
            "piotroski_f_score, altman_z_score, beneish_m_score, forensic_last_updated "
            "FROM stock_signals WHERE ticker=?",
            (self.TICKER,),
        ).fetchone()
        conn.close()

        assert row["company_name"] == "Test Co"  # confirms the upsert actually ran, not a no-op
        assert row["top_holdings"] == '[{"symbol": "NVDA", "weight": 0.1}]'
        assert row["sector_weightings"] == '[{"name": "Technology", "weight": 1.0}]'
        assert row["holdings_updated_at"] == "2026-07-01 00:00:00"
        assert row["piotroski_f_score"] == 7.0
        assert row["altman_z_score"] == 3.2
        assert row["beneish_m_score"] == -2.1
        assert row["forensic_last_updated"] == "2026-07-01 00:00:00"

    def test_first_insert_still_creates_a_row(self):
        QuantEngine().save_to_db(self.TICKER, **_SAVE_TO_DB_DEFAULTS)
        conn = _db.get_connection()
        row = conn.execute("SELECT company_name FROM stock_signals WHERE ticker=?", (self.TICKER,)).fetchone()
        conn.close()
        assert row["company_name"] == "Test Co"


class TestSaveToDbWritesAtrStopHistory:
    """save_to_db() must also snapshot atr_stop_loss into atr_stop_history so the Risk
    Orchestrator Digest (Pillar C1) can detect stops that moved up day over day."""

    TICKER = "TST_QS_ATR_HISTORY"

    def teardown_method(self):
        conn = _db.get_connection()
        try:
            conn.execute("DELETE FROM stock_signals WHERE ticker=?", (self.TICKER,))
            conn.execute("DELETE FROM atr_stop_history WHERE ticker=?", (self.TICKER,))
            conn.commit()
        finally:
            conn.close()

    def test_writes_a_row_for_todays_date(self):
        args = dict(_SAVE_TO_DB_DEFAULTS, stop_loss=95.5)
        QuantEngine().save_to_db(self.TICKER, **args)

        conn = _db.get_connection()
        rows = conn.execute(
            "SELECT date, atr_stop_loss FROM atr_stop_history WHERE ticker=?", (self.TICKER,)
        ).fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0]["atr_stop_loss"] == 95.5

    def test_second_write_same_day_updates_not_duplicates(self):
        QuantEngine().save_to_db(self.TICKER, **dict(_SAVE_TO_DB_DEFAULTS, stop_loss=90.0))
        QuantEngine().save_to_db(self.TICKER, **dict(_SAVE_TO_DB_DEFAULTS, stop_loss=92.0))

        conn = _db.get_connection()
        rows = conn.execute(
            "SELECT atr_stop_loss FROM atr_stop_history WHERE ticker=?", (self.TICKER,)
        ).fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0]["atr_stop_loss"] == 92.0

    def test_none_stop_loss_does_not_write_a_row(self):
        QuantEngine().save_to_db(self.TICKER, **dict(_SAVE_TO_DB_DEFAULTS, stop_loss=None))

        conn = _db.get_connection()
        rows = conn.execute(
            "SELECT * FROM atr_stop_history WHERE ticker=?", (self.TICKER,)
        ).fetchall()
        conn.close()
        assert rows == []
