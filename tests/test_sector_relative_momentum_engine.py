"""
tests/test_sector_relative_momentum_engine.py -- Sector-Relative Momentum

  score_cohort()                 -- leave-one-out peer return, ranks, percentile, ties, minimum peers
  run_sector_relative_momentum() -- cohorts (sector x currency), unavailable reasons, idempotency, retention
  read helpers                   -- latest-row selection, column values, report rows, cohort standing
  run_sector_relative_momentum_job() -- the scheduler runner itself
"""

import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import database as db
import sector_relative_momentum_engine as srm

SECTOR = "SRM_TEST_SECTOR"
OTHER_SECTOR = "SRM_OTHER_SECTOR"
USD = [f"SRM_U{i}" for i in range(6)]
GBP = [f"SRM_G{i}.L" for i in range(6)]
TINY = [f"SRM_T{i}" for i in range(3)]
ETF = "SRM_ETF"
NOSECTOR = "SRM_NOSEC"
NOHIST = "SRM_NOHIST"
ALL_TICKERS = USD + GBP + TINY + [ETF, NOSECTOR, NOHIST]


def _dates(n: int = 130):
    return pd.date_range("2026-01-05", periods=n, freq="B")


def _series(final_return: float, n: int = 130) -> pd.Series:
    """Flat at 100, then a straight line over the last 64 closes, so the 63- and 126-session returns both equal final_return."""
    close = np.concatenate([np.full(n - 64, 100.0), np.linspace(100.0, 100.0 * (1 + final_return), 64)])
    return pd.Series(close, index=_dates(n))


def _seed_signal(ticker, sector, currency, quote_type="EQUITY"):
    conn = db.get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO stock_signals (ticker, company_name, sector, currency, quote_type, current_price) "
            "VALUES (?, ?, ?, ?, ?, 1.0)",
            (ticker, f"{ticker} Co", sector, currency, quote_type),
        )
        conn.commit()
    finally:
        conn.close()


def _clear():
    conn = db.get_connection()
    try:
        marks = ",".join("?" * len(ALL_TICKERS))
        conn.execute(f"DELETE FROM sector_relative_momentum_results WHERE ticker IN ({marks})", ALL_TICKERS)
        conn.execute(f"DELETE FROM stock_signals WHERE ticker IN ({marks})", ALL_TICKERS)
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def seeded():
    _clear()
    returns = {}
    for i, t in enumerate(USD):
        _seed_signal(t, SECTOR, "USD")
        returns[t] = 0.05 * (i + 1)
    for i, t in enumerate(GBP):
        _seed_signal(t, SECTOR, "GBp" if i % 2 else "GBP")
        returns[t] = -0.02 * (i + 1)
    for i, t in enumerate(TINY):
        _seed_signal(t, OTHER_SECTOR, "USD")
        returns[t] = 0.01 * (i + 1)
    _seed_signal(ETF, "Fund", "USD", quote_type="ETF")
    _seed_signal(NOSECTOR, "Unclassified", "USD")
    _seed_signal(NOHIST, SECTOR, "USD")
    yield returns
    _clear()


def _run(returns, display):
    def fake_close(ticker):
        return _series(returns[ticker]) if ticker in returns else None

    with patch.object(srm, "_load_close", side_effect=fake_close), \
         patch.object(srm, "get_portfolio_watchlist_tickers", return_value=display):
        return srm.run_sector_relative_momentum()


def _closes_with_returns(returns: dict) -> pd.DataFrame:
    return pd.DataFrame({t: _series(r) for t, r in returns.items()})


class TestScoreCohort:
    def test_leave_one_out_peer_return_and_relative_pp(self):
        returns = {"A": 0.10, "B": 0.20, "C": 0.30, "D": 0.40, "E": 0.50, "F": 0.60}
        rows, failed = srm.score_cohort(_closes_with_returns(returns), 63, sector="S", currency="USD", computed_at="t")
        by = {r["ticker"]: r for r in rows}
        assert not failed
        assert by["A"]["own_return"] == pytest.approx(0.10)
        assert by["A"]["peer_return"] == pytest.approx(0.40)
        assert by["A"]["relative_return_pp"] == pytest.approx((0.10 - 0.40) * 100)
        assert by["F"]["peer_return"] == pytest.approx(0.30)
        assert by["C"]["peer_return"] == pytest.approx(0.40)
        assert by["D"]["peer_return"] == pytest.approx(0.30)

    def test_peer_return_is_median_so_an_outlier_does_not_skew_it(self):
        returns = {"A": 0.10, "B": 0.12, "C": 0.14, "D": 0.16, "E": 0.18, "F": 30.0}
        rows, _ = srm.score_cohort(_closes_with_returns(returns), 63, sector="S", currency="USD", computed_at="t")
        by = {r["ticker"]: r for r in rows}
        assert by["A"]["peer_return"] == pytest.approx(0.16)
        assert by["F"]["peer_return"] == pytest.approx(0.14)
        assert by["A"]["relative_return_pp"] == pytest.approx((0.10 - 0.16) * 100)

    def test_rank_one_is_strongest_and_percentile_spans_0_to_100(self):
        returns = {"A": 0.10, "B": 0.20, "C": 0.30, "D": 0.40, "E": 0.50, "F": 0.60}
        rows, _ = srm.score_cohort(_closes_with_returns(returns), 63, sector="S", currency="USD", computed_at="t")
        by = {r["ticker"]: r for r in rows}
        assert by["F"]["rank"] == 1 and by["A"]["rank"] == 6
        assert by["F"]["percentile"] == pytest.approx(100.0)
        assert by["A"]["percentile"] == pytest.approx(0.0)
        assert by["C"]["percentile"] == pytest.approx(40.0)

    def test_ties_share_average_percentile_and_rank_breaks_on_ticker(self):
        returns = {"A": 0.10, "B": 0.30, "C": 0.30, "D": 0.40, "E": 0.50, "F": 0.60}
        rows, _ = srm.score_cohort(_closes_with_returns(returns), 63, sector="S", currency="USD", computed_at="t")
        by = {r["ticker"]: r for r in rows}
        assert by["B"]["percentile"] == pytest.approx(by["C"]["percentile"])
        assert by["B"]["percentile"] == pytest.approx((2.5 - 1) / 5 * 100)
        assert (by["B"]["rank"], by["C"]["rank"]) == (4, 5)

    def test_fewer_than_five_peers_is_small_cohort_without_metrics(self):
        returns = {"A": 0.1, "B": 0.2, "C": 0.3, "D": 0.4, "E": 0.5}
        rows, _ = srm.score_cohort(_closes_with_returns(returns), 63, sector="S", currency="USD", computed_at="t")
        assert {r["status"] for r in rows} == {"small_cohort"}
        assert all(r["peer_count"] == 4 and r.get("relative_return_pp") is None for r in rows)

    def test_exactly_five_peers_is_scored(self):
        returns = {t: 0.1 * i for i, t in enumerate("ABCDEF")}
        rows, _ = srm.score_cohort(_closes_with_returns(returns), 63, sector="S", currency="USD", computed_at="t")
        assert {r["status"] for r in rows} == {"ok"}
        assert rows[0]["peer_count"] == 5 and rows[0]["cohort_size"] == 6

    def test_member_without_window_endpoints_is_reported_not_scored(self):
        closes = _closes_with_returns({t: 0.1 * i for i, t in enumerate("ABCDEF")})
        closes["G"] = pd.Series([100.0] * 10, index=_dates(130)[:10])
        rows, failed = srm.score_cohort(closes, 63, sector="S", currency="USD", computed_at="t")
        assert failed == {"G": "insufficient_history"}
        assert "G" not in {r["ticker"] for r in rows}
        assert rows[0]["cohort_size"] == 6

    def test_short_calendar_marks_everyone_insufficient(self):
        closes = _closes_with_returns({t: 0.1 for t in "ABCDEF"}).iloc[:20]
        rows, failed = srm.score_cohort(closes, 63, sector="S", currency="USD", computed_at="t")
        assert rows == [] and set(failed) == set("ABCDEF")

    def test_window_dates_follow_cohort_calendar(self):
        returns = {t: 0.1 * i for i, t in enumerate("ABCDEF")}
        closes = _closes_with_returns(returns)
        rows, _ = srm.score_cohort(closes, 63, sector="S", currency="USD", computed_at="t")
        assert rows[0]["as_of_date"] == closes.index[-1].strftime("%Y-%m-%d")
        assert rows[0]["window_start_date"] == closes.index[-64].strftime("%Y-%m-%d")


class TestRun:
    def test_scores_sector_currency_cohorts_separately(self, seeded):
        _run(seeded, display=USD)
        usd = srm.get_latest_results(USD, 63)
        gbp = srm.get_latest_results(GBP, 63)
        assert all(usd[t]["status"] == "ok" and usd[t]["cohort_size"] == 6 for t in USD)
        assert all(r["currency"] == "USD" for r in usd.values())
        assert {r["currency"] for r in gbp.values()} == {"GBP"}
        assert usd[USD[-1]]["rank"] == 1 and usd[USD[0]]["rank"] == 6

    def test_pence_and_pounds_quoted_tickers_share_one_cohort(self, seeded):
        _run(seeded, display=[])
        gbp = srm.get_latest_results(GBP, 63)
        assert len(gbp) == 6 and all(r["cohort_size"] == 6 for r in gbp.values())

    def test_both_windows_are_stored(self, seeded):
        _run(seeded, display=[])
        for window in srm.WINDOWS:
            assert srm.get_latest_results(USD, window)[USD[0]]["window_sessions"] == window

    def test_unavailable_reasons_only_for_display_tickers(self, seeded):
        _run(seeded, display=TINY[:1] + [ETF, NOSECTOR, NOHIST, "SRM_NOT_IN_SIGNALS"])
        rows = srm.get_latest_results(TINY[:1] + [ETF, NOSECTOR, NOHIST, "SRM_NOT_IN_SIGNALS"], 63)
        assert rows[TINY[0]]["status"] == "small_cohort" and rows[TINY[0]]["peer_count"] == 2
        assert rows[ETF]["status"] == "not_equity"
        assert rows[NOSECTOR]["status"] == "no_sector"
        assert rows[NOHIST]["status"] == "no_history"
        assert rows["SRM_NOT_IN_SIGNALS"]["status"] == "no_metadata"
        assert srm.get_latest_results(TINY[1:], 63) == {}

    def test_display_scope_does_not_change_scores(self, seeded):
        _run(seeded, display=[])
        before = srm.get_latest_results(USD, 63)
        _run(seeded, display=USD[:1])
        assert srm.get_latest_results(USD, 63) == {t: {**r} for t, r in before.items()}

    def test_rerun_is_idempotent(self, seeded):
        _run(seeded, display=USD)
        count_sql = "SELECT COUNT(*) FROM sector_relative_momentum_results WHERE ticker LIKE 'SRM_%'"
        conn = db.get_connection()
        first = conn.execute(count_sql).fetchone()[0]
        conn.close()
        _run(seeded, display=USD)
        conn = db.get_connection()
        second = conn.execute(count_sql).fetchone()[0]
        conn.close()
        assert first == second > 0

    def test_input_revision_is_stable_and_changes_with_prices(self, seeded):
        _run(seeded, display=[])
        first = srm.get_latest_results(USD[:1], 63)[USD[0]]["input_revision"]
        _run(seeded, display=[])
        assert srm.get_latest_results(USD[:1], 63)[USD[0]]["input_revision"] == first
        _run({**seeded, USD[0]: 0.5}, display=[])
        assert srm.get_latest_results(USD[:1], 63)[USD[0]]["input_revision"] != first

    def test_ignored_ticker_is_left_out_of_the_pool(self, seeded):
        cfg = {"IGNORED_TICKERS": [USD[0]]}
        with patch.object(srm, "load_config", return_value=cfg):
            _run(seeded, display=[USD[1]])
        assert USD[0] not in srm.get_latest_results(USD, 63)
        left = srm.get_latest_results([USD[1]], 63)[USD[1]]
        assert left["status"] == "small_cohort" and left["peer_count"] == 4

    def test_rows_older_than_retention_are_pruned(self, seeded):
        conn = db.get_connection()
        conn.execute(
            "INSERT INTO sector_relative_momentum_results (ticker, window_sessions, as_of_date, status, computed_at) "
            "VALUES (?, 63, '2000-01-03', 'ok', '2000-01-03 00:00:00')", (USD[0],),
        )
        conn.commit()
        conn.close()
        _run(seeded, display=[])
        conn = db.get_connection()
        old = conn.execute("SELECT COUNT(*) FROM sector_relative_momentum_results WHERE as_of_date = '2000-01-03'").fetchone()[0]
        conn.close()
        assert old == 0

    def test_stale_ticker_falls_out_of_cohort_as_insufficient_history(self, seeded):
        def fake_close(ticker):
            if ticker == USD[0]:
                return pd.Series(np.linspace(100.0, 110.0, 20), index=_dates(20))
            return _series(seeded[ticker]) if ticker in seeded else None

        with patch.object(srm, "_load_close", side_effect=fake_close), \
             patch.object(srm, "get_portfolio_watchlist_tickers", return_value=[USD[0], USD[1]]):
            srm.run_sector_relative_momentum()
        rows = srm.get_latest_results([USD[0], USD[1]], 63)
        assert rows[USD[0]]["status"] == "insufficient_history"
        assert rows[USD[1]]["cohort_size"] == 5 and rows[USD[1]]["peer_count"] == 4


class TestReaders:
    def test_latest_row_wins_when_dates_differ(self, seeded):
        conn = db.get_connection()
        for as_of, rank in (("2026-06-01", 9), ("2026-06-02", 2)):
            conn.execute(
                "INSERT INTO sector_relative_momentum_results (ticker, window_sessions, as_of_date, status, rank, computed_at) "
                "VALUES (?, 63, ?, 'ok', ?, 't')", (USD[0], as_of, rank),
            )
        conn.commit()
        conn.close()
        assert srm.get_latest_results([USD[0]], 63)[USD[0]]["rank"] == 2

    def test_column_values_only_for_scored_rows(self, seeded):
        _run(seeded, display=USD[:1] + [ETF])
        values = srm.get_column_values(USD[:1] + [ETF])
        assert values[USD[0]]["sector_rel_rank_63"] == 6
        assert values[USD[0]]["sector_rel_mom_126"] == srm.get_latest_results([USD[0]], 126)[USD[0]]["relative_return_pp"]
        assert ETF not in values

    def test_report_rows_scored_first_then_unavailable(self, seeded):
        _run(seeded, display=[ETF, USD[0], USD[5]])
        with patch.object(srm, "get_portfolio_watchlist_tickers", return_value=[ETF, USD[0], USD[5], "SRM_FRESH"]):
            rows = srm.get_report_rows(srm.SCOPE_PORTFOLIO_WATCHLIST, 63)
        assert [r["ticker"] for r in rows] == [USD[5], USD[0], ETF, "SRM_FRESH"]
        assert rows[2]["status_label"] == srm.STATUS_LABELS["not_equity"]
        assert rows[3]["status"] == "not_computed"
        assert rows[0]["company_name"] == f"{USD[5]} Co"

    def test_universe_scope_lists_every_stored_row(self, seeded):
        _run(seeded, display=[])
        tickers = {r["ticker"] for r in srm.get_report_rows(srm.SCOPE_UNIVERSE, 63)}
        assert set(USD + GBP) <= tickers

    def test_report_matches_columns(self, seeded):
        _run(seeded, display=USD)
        with patch.object(srm, "get_portfolio_watchlist_tickers", return_value=USD):
            report = {r["ticker"]: r for r in srm.get_report_rows(srm.SCOPE_PORTFOLIO_WATCHLIST, 63)}
        columns = srm.get_column_values(USD)
        for t in USD:
            assert columns[t]["sector_rel_mom_63"] == report[t]["relative_return_pp"]
            assert columns[t]["sector_rel_rank_63"] == report[t]["rank"]

    def test_cohort_standing_lists_never_overlap(self, seeded):
        _run(seeded, display=[])
        standing = srm.get_cohort_standing(USD[0], 63)
        top = {m["ticker"] for m in standing["top"]}
        bottom = {m["ticker"] for m in standing["bottom"]}
        assert top and bottom and not top & bottom
        assert standing["top"][0]["ticker"] == USD[-1]
        assert standing["bottom"][-1]["ticker"] == USD[0]

    def test_cohort_standing_for_unscored_ticker_has_no_peers(self, seeded):
        _run(seeded, display=[ETF])
        standing = srm.get_cohort_standing(ETF, 63)
        assert standing["row"]["status"] == "not_equity" and standing["top"] == []
        assert srm.get_cohort_standing("SRM_NEVER_SEEN", 63)["row"] is None


class TestRunner:
    def test_runner_invokes_engine_and_records_the_run(self):
        import scheduler_jobs
        with patch("sector_relative_momentum_engine.run_sector_relative_momentum",
                   return_value={"scored": 3, "cohorts": 1, "stored": 6}) as run, \
             patch("scheduler_jobs.record_job_run") as record, \
             patch("scheduler_jobs.log_sched_notification") as log:
            scheduler_jobs.run_sector_relative_momentum_job()
        run.assert_called_once_with()
        record.assert_called_once_with("sector_relative_momentum_job")
        assert log.call_args.args[0] == "Success"

    def test_runner_logs_error_and_still_records_the_run(self):
        import scheduler_jobs
        with patch("sector_relative_momentum_engine.run_sector_relative_momentum", side_effect=RuntimeError("boom")), \
             patch("scheduler_jobs.record_job_run") as record, \
             patch("scheduler_jobs.log_sched_notification") as log:
            scheduler_jobs.run_sector_relative_momentum_job()
        record.assert_called_once_with("sector_relative_momentum_job")
        assert log.call_args.args[0] == "Error"
