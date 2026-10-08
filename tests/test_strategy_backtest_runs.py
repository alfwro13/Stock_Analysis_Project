"""
tests/test_strategy_backtest_runs.py — Strategy Backtester data layer, run lifecycle and API tests

Covers:
  • Basket rules: one currency per run, minimum tickers, benchmark currency match
  • Price-matrix assembly: known-closure carry-forward vs unexpected gaps (run marked Incomplete)
  • Extended-history cache: preferred when current, ignored when stale, written through the canonical cleaning path
  • Run lifecycle against the real SQLite fixture: queued → completed/failed, Parquet artifacts,
    immutable config, deterministic input digest, retention pruning, interrupted-run expiry
  • The /api/strategy-backtester endpoints end to end
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

import database as db
import strategy_backtest_data as sbd
import strategy_backtest_reads as sbq
import strategy_backtest_runs as sbr
from database import add_transaction, create_account
from strategy_backtest_engine import InsufficientHistory
from xray_engine import resolve_scope_holdings

SBT_A, SBT_B, SBT_C, SBT_USD, SBT_BENCH = "SBT_A", "SBT_B", "SBT_C", "SBT_USD", "SBT_BENCH"
DAYS = 400
START = "2025-01-02"


def _builtin_config(extra=None):
    cfg = {"GHOSTFOLIO_ACCOUNTS": {"active": []}, "BASE_CURRENCY": "GBP", "RISK_FREE_RATE": 0.045}
    cfg.update(extra or {})
    return cfg


def _closes(seed, n=DAYS, drift=0.0004, vol=0.01):
    rng = np.random.default_rng(seed)
    index = pd.bdate_range(START, periods=n)
    return pd.DataFrame({"Close": 100 * np.cumprod(1 + rng.normal(drift, vol, n))}, index=index)


def _seed_signal(ticker, currency):
    conn = db.get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO stock_signals (ticker, current_price, currency) VALUES (?, ?, ?)",
        (ticker, 100.0, currency),
    )
    conn.commit()
    conn.close()


@pytest.fixture(autouse=True)
def _isolated_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(sbr, "BACKTEST_RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(sbd, "BACKTEST_HISTORY_DIR", tmp_path / "history")
    (tmp_path / "runs").mkdir()
    (tmp_path / "history").mkdir()
    for ticker, currency in ((SBT_A, "GBP"), (SBT_B, "GBp"), (SBT_C, "GBP"), (SBT_USD, "USD"), (SBT_BENCH, "GBP")):
        _seed_signal(ticker, currency)
    conn = db.get_connection()
    conn.execute("DELETE FROM strategy_backtest_runs")
    conn.commit()
    conn.close()


@pytest.fixture
def histories():
    frames = {
        SBT_A: _closes(1), SBT_B: _closes(2, vol=0.015), SBT_C: _closes(3, vol=0.02),
        SBT_USD: _closes(4), SBT_BENCH: _closes(5, vol=0.008),
    }

    def fake_loader(ticker, **kwargs):
        return frames.get(ticker)

    with patch("strategy_backtest_data.data_engine.load_or_fetch_daily_history", side_effect=fake_loader):
        yield frames


@pytest.fixture
def account():
    aid = create_account(f"SbtAcc{datetime.now(timezone.utc).timestamp()}", "GBP")
    for ticker, qty, price in ((SBT_A, 10, 80), (SBT_B, 5, 50), (SBT_C, 20, 20), (SBT_USD, 3, 100)):
        add_transaction(aid, "Buy", "2026-01-05", ticker=ticker, currency="GBP", quantity=qty,
                        unit_price=price, exchange_rate=1.0)
    with patch("xray_engine.load_config", return_value=_builtin_config()):
        yield f"acct:{aid}"


def _request(account_id, tickers, **overrides):
    req = {
        "basket_type": "account", "account_id": account_id, "include_tickers": tickers,
        "shortlist_signal": None, "shortlist_scope": None, "currency": None,
        "strategies": ["buy_hold_ew", "rebalanced_ew"], "cadence": "quarterly", "lookback": 60,
        "initial_capital": 10000.0, "cost_preset": "low", "commission_bps": 0.0, "spread_bps": 0.0,
        "slippage_bps": 0.0, "cash_rate": 0.0, "band_pp": 5.0, "max_weight": 0.5, "cash_reserve": 0.0,
        "benchmark": SBT_BENCH, "history": "standard",
    }
    req.update(overrides)
    return req


def _run(account_id, tickers, **overrides):
    created = sbr.create_run(_request(account_id, tickers, **overrides))
    assert created["status"] == "success", created
    sbr.execute_run(created["run_id"])
    return created["run_id"]


class TestBasketRules:
    def test_mixed_currencies_are_rejected_with_the_currencies_named(self, account, histories):
        out = sbr.create_run(_request(account, [SBT_A, SBT_USD]))
        assert out["status"] == "error"
        assert "GBP" in out["message"] and "USD" in out["message"]

    def test_currency_choice_keeps_one_bucket_and_names_the_exclusions(self, account, histories):
        run_id = _run(account, [SBT_A, SBT_B, SBT_USD], currency="GBP")
        detail = sbq.get_run(run_id)
        assert sorted(detail["run"]["basket"]["tickers"]) == [SBT_A, SBT_B]
        assert any("SBT_USD" in w for w in detail["result"]["warnings"])

    def test_chosen_currency_ignores_unknown_currency_tickers(self, histories):
        _seed_signal("SBT_NOCUR", None)
        out = sbd._single_currency([SBT_A, SBT_B, "SBT_NOCUR", SBT_USD], "GBP")
        assert out["tickers"] == [SBT_A, SBT_B]
        assert out["excluded"] == ["SBT_NOCUR", SBT_USD]

    def test_unknown_currency_blocks_a_run_with_no_currency_chosen(self, histories):
        _seed_signal("SBT_NOCUR", None)
        with pytest.raises(sbd.BasketError, match="SBT_NOCUR"):
            sbd._single_currency([SBT_A, "SBT_NOCUR"], None)

    def test_pence_and_pounds_share_one_bucket(self, account, histories):
        assert sbd.currency_buckets([SBT_A, SBT_B]) == {SBT_A: "GBP", SBT_B: "GBP"}

    def test_fewer_than_two_tickers_is_rejected(self, account, histories):
        assert sbr.create_run(_request(account, [SBT_A]))["status"] == "error"

    def test_unknown_currency_is_rejected(self, account, histories):
        _seed_signal("SBT_NOCUR", None)
        out = sbr.create_run(_request(account, [SBT_A, "SBT_NOCUR"]))
        assert out["status"] == "error" and "SBT_NOCUR" in out["message"]

    def test_benchmark_must_match_the_basket_currency(self, account, histories):
        out = sbr.create_run(_request(account, [SBT_A, SBT_B], benchmark=SBT_USD))
        assert out["status"] == "error" and "currency" in out["message"]

    def test_auto_benchmark_defaults_by_currency(self):
        with patch.object(sbd, "_benchmark_bucket", return_value="GBP"):
            assert sbd.resolve_benchmark("auto", "GBP") == "SWDA.L"
        with patch.object(sbd, "_benchmark_bucket", return_value="USD"):
            assert sbd.resolve_benchmark("auto", "USD") == "SPY"
        assert sbd.resolve_benchmark("none", "GBP") is None
        assert sbd.resolve_benchmark("auto", "EUR") is None

    def test_duplicate_strategy_ids_run_once(self, account, histories):
        run_id = _run(account, [SBT_A, SBT_B], strategies=["buy_hold_ew", "buy_hold_ew"])
        assert [s["id"] for s in sbq.get_run(run_id)["result"]["strategies"]] == ["buy_hold_ew"]

    def test_unknown_strategy_is_rejected(self, account, histories):
        assert sbr.create_run(_request(account, [SBT_A, SBT_B], strategies=["nope"]))["status"] == "error"

    def test_infeasible_cap_skips_only_the_optimizer_strategies(self, account, histories):
        run_id = _run(account, [SBT_A, SBT_B, SBT_C], strategies=["buy_hold_ew", "min_variance"], max_weight=0.2)
        by_id = {s["id"]: s for s in sbq.get_run(run_id)["result"]["strategies"]}
        assert by_id["buy_hold_ew"]["available"] is True
        assert by_id["min_variance"]["available"] is False
        assert "Weight Cap" in by_id["min_variance"]["reason"]

    def test_run_is_rejected_when_every_strategy_is_skipped(self, account, histories):
        out = sbr.create_run(_request(account, [SBT_A, SBT_B, SBT_C], strategies=["min_variance"], max_weight=0.2))
        assert out["status"] == "error" and "Weight Cap" in out["message"]


class TestPriceMatrix:
    def _series(self, drop=()):
        index = pd.bdate_range("2025-03-03", "2025-05-30")
        s = pd.Series(100.0, index=index)
        return s.drop(pd.to_datetime(list(drop))) if drop else s

    def test_known_closure_is_carried_without_an_issue(self):
        full = self._series()
        gappy = self._series(drop=["2025-04-18"])
        out = sbd.build_price_matrix({"A": full, "B": gappy}, {"A": "LSE", "B": "LSE"})
        assert out["issues"] == []
        assert out["prices"].loc["2025-04-18", "B"] == 100.0
        assert not out["real"].loc["2025-04-18", "B"]

    def test_unexpected_gap_on_an_open_session_is_reported(self):
        full = self._series()
        gappy = self._series(drop=["2025-04-16"])
        out = sbd.build_price_matrix({"A": full, "B": gappy}, {"A": "LSE", "B": "LSE"})
        assert out["issues"] == [{"ticker": "B", "date": "2025-04-16"}]

    def test_window_is_the_overlap_of_all_series(self):
        long_series = pd.Series(1.0, index=pd.bdate_range("2025-01-02", "2025-06-30"))
        short_series = pd.Series(1.0, index=pd.bdate_range("2025-03-03", "2025-05-30"))
        out = sbd.build_price_matrix({"A": long_series, "B": short_series}, {"A": "LSE", "B": "LSE"})
        assert out["prices"].index[0] == pd.Timestamp("2025-03-03")
        assert out["prices"].index[-1] == pd.Timestamp("2025-05-30")

    def test_no_overlap_raises(self):
        a = pd.Series(1.0, index=pd.bdate_range("2025-01-02", "2025-02-28"))
        b = pd.Series(1.0, index=pd.bdate_range("2025-04-01", "2025-05-30"))
        with pytest.raises(InsufficientHistory):
            sbd.build_price_matrix({"A": a, "B": b}, {"A": "LSE", "B": "LSE"})

    def test_digest_changes_when_a_price_changes(self):
        a = self._series()
        out = sbd.build_price_matrix({"A": a, "B": a}, {"A": "LSE", "B": "LSE"})
        base = sbd.input_digest(out["prices"], None)
        changed = out["prices"].copy()
        changed.iloc[5, 0] += 0.01
        assert sbd.input_digest(changed, None) != base
        assert sbd.input_digest(out["prices"], None) == base


class TestExtendedHistory:
    def _long(self, n=1100):
        index = pd.bdate_range(end=pd.Timestamp(START) + pd.offsets.BDay(DAYS - 1), periods=n)
        return pd.DataFrame({"Close": np.linspace(50, 100, n)}, index=index)

    def test_prepared_history_is_preferred_when_current(self, histories):
        extended = self._long()
        extended.to_parquet(sbd._extended_path(SBT_A))
        loaded = sbd.load_close_series(SBT_A, "extended")
        assert loaded["source"] == "extended" and loaded["sessions"] == len(extended)

    def test_stale_prepared_history_is_ignored_with_a_note(self, histories):
        stale = self._long().iloc[:-30]
        stale.to_parquet(sbd._extended_path(SBT_A))
        loaded = sbd.load_close_series(SBT_A, "extended")
        assert loaded["source"] == "standard" and "ignored" in loaded["note"]

    def test_missing_prepared_history_falls_back_with_a_note(self, histories):
        loaded = sbd.load_close_series(SBT_B, "extended")
        assert loaded["source"] == "standard" and "not prepared" in loaded["note"]

    def test_standard_mode_never_reads_the_extended_cache(self, histories):
        self._long().to_parquet(sbd._extended_path(SBT_A))
        assert sbd.load_close_series(SBT_A, "standard")["source"] == "standard"

    def test_prepare_writes_through_the_canonical_cleaning_path(self):
        frame = self._long(300)
        with patch("strategy_backtest_data.yahoo_engine.get_price_history", return_value={SBT_A: frame}) as fetch, \
             patch("strategy_backtest_data.data_engine._prepare_daily_history", side_effect=lambda t, df, live: df) as clean:
            result = sbd.prepare_history_blocking([SBT_A, SBT_B])
        assert fetch.call_args.kwargs["period"] == "5y"
        assert clean.call_count == 1
        assert result == {"prepared": [SBT_A], "failed": [SBT_B]}
        assert len(pd.read_parquet(sbd._extended_path(SBT_A))) == 300
        assert sbd._prepare_state[SBT_A] == "ready" and sbd._prepare_state[SBT_B] == "failed"

    def test_prepare_with_nothing_fetched_reports_failure_to_the_coordinator(self):
        with patch("strategy_backtest_data.yahoo_engine.get_price_history", return_value={}):
            assert sbd.prepare_history_blocking([SBT_C]) is None
        assert sbd._prepare_state[SBT_C] == "failed"

    def test_prepare_failure_never_leaves_a_ticker_stuck_preparing(self):
        sbd._prepare_state[SBT_C] = "preparing"
        with patch("strategy_backtest_data.yahoo_engine.get_price_history", side_effect=RuntimeError("down")):
            with pytest.raises(RuntimeError):
                sbd.prepare_history_blocking([SBT_C])
        assert sbd._prepare_state[SBT_C] == "failed"

    def test_history_status_reports_both_caches(self, histories):
        self._long().to_parquet(sbd._extended_path(SBT_A))
        status = sbd.history_status([SBT_A, SBT_B])
        assert status[SBT_A]["extended_usable"] is True
        assert status[SBT_A]["standard"]["sessions"] == DAYS
        assert status[SBT_B]["extended"] is None


class TestRunLifecycle:
    def test_completed_run_stores_artifacts_and_a_consistent_result(self, account, histories):
        run_id = _run(account, [SBT_A, SBT_B, SBT_C], strategies=["buy_hold_ew", "inverse_vol", "tolerance_band"])
        directory = sbr.run_dir(run_id)
        assert {p.name for p in directory.iterdir()} == {"equity.parquet", "allocations.parquet", "transactions.parquet"}
        detail = sbq.get_run(run_id)
        assert detail["run"]["state"] == "completed"
        result = detail["result"]
        start_index = 60
        assert result["period"]["sessions"] == DAYS - start_index
        assert len(result["dates"]) == result["period"]["sessions"]
        for strategy in result["strategies"]:
            assert strategy["available"] is True
            assert len(strategy["equity"]) == len(result["dates"]) == len(strategy["drawdown"]) == len(strategy["cash_fraction"])
            assert strategy["summary"]["final_value"] == pytest.approx(strategy["equity"][-1], abs=0.01)
            assert strategy["summary"]["gross_final_value"] >= strategy["summary"]["final_value"] - 1e-6
            assert strategy["transactions_total"] == len(strategy["transactions"]) > 0
        assert result["benchmark"]["label"] == SBT_BENCH
        assert result["incomplete"] is False
        json.dumps(detail)

    def test_reproducibility_fields_are_saved(self, account, histories):
        run_id = _run(account, [SBT_A, SBT_B])
        run = sbq.get_run(run_id)["run"]
        assert len(run["inputs"]["digest"]) == 64
        assert set(run["inputs"]["sources"]) == {SBT_A, SBT_B}
        assert run["inputs"]["sources"][SBT_A]["source"] == "standard"
        assert run["config"]["version"] == sbr.CONFIG_VERSION
        assert run["config"]["risk_free_rate"] == pytest.approx(0.045)
        assert run["config"]["commission_bps"] == 5.0
        decision = sbq.get_run(run_id)["result"]["strategies"][0]["decisions"][0]
        assert decision["decided_at_utc"].endswith(":00") and decision["executed_at_utc"] > decision["decided_at_utc"]

    def test_same_inputs_give_the_same_digest_and_numbers(self, account, histories):
        first = sbq.get_run(_run(account, [SBT_A, SBT_B]))
        second = sbq.get_run(_run(account, [SBT_A, SBT_B]))
        assert first["run"]["inputs"]["digest"] == second["run"]["inputs"]["digest"]
        assert first["result"]["strategies"][0]["equity"] == second["result"]["strategies"][0]["equity"]

    def test_completed_runs_are_immutable_to_a_second_execution(self, account, histories):
        run_id = _run(account, [SBT_A, SBT_B])
        before = sbq.get_run(run_id)["result"]["strategies"][0]["equity"]
        sbr.execute_run(run_id)
        assert sbq.get_run(run_id)["result"]["strategies"][0]["equity"] == before

    def test_unexpected_missing_price_marks_the_run_incomplete(self, account, histories):
        gap = histories[SBT_B].index[200]
        histories[SBT_B] = histories[SBT_B].drop(gap)
        result = sbq.get_run(_run(account, [SBT_A, SBT_B]))["result"]
        assert result["incomplete"] is True
        assert result["issues"] == [{"ticker": SBT_B, "date": gap.strftime("%Y-%m-%d")}]
        assert result["issues_total"] == 1

    def test_trade_never_executes_on_a_carried_price(self, account, histories):
        gap = histories[SBT_B].index[1]
        start = pd.Timestamp(histories[SBT_A].index[0])
        histories[SBT_B] = histories[SBT_B].drop(gap)
        result = sbq.get_run(_run(account, [SBT_A, SBT_B], strategies=["buy_hold_ew"], lookback=20))["result"]
        first_trade = result["strategies"][0]["transactions"][0]["date"]
        assert first_trade != gap.strftime("%Y-%m-%d")
        assert first_trade > start.strftime("%Y-%m-%d")

    def test_insufficient_history_fails_the_run_with_an_explanation(self, account, histories):
        run_id = _run(account, [SBT_A, SBT_B], strategies=["inverse_vol"], lookback=390)
        run = sbq.get_run(run_id)
        assert run["run"]["state"] == "failed"
        assert "warm-up" in run["run"]["error"]
        assert not sbr.run_dir(run_id).exists()

    def test_unexpected_crash_is_logged_and_reported_generically(self, account, histories):
        created = sbr.create_run(_request(account, [SBT_A, SBT_B]))
        with patch("strategy_backtest_runs.run_backtest", side_effect=ZeroDivisionError("secret detail")):
            sbr.execute_run(created["run_id"])
        run = sbq.get_run(created["run_id"])["run"]
        assert run["state"] == "failed" and "secret detail" not in run["error"]

    def test_benchmark_without_a_stored_currency_uses_the_basket_currency_for_its_calendar(self, histories):
        histories["SBT_SPY"] = _closes(9)
        basket = {"tickers": [SBT_USD, SBT_A], "currency": "USD"}
        _, _, exchanges, bench_exchange = sbr._load_inputs({"history": "standard"}, basket, "SBT_SPY")
        assert exchanges[SBT_USD] == "NYSE"
        assert bench_exchange == "NYSE"

    def test_no_benchmark_runs_fine(self, account, histories):
        result = sbq.get_run(_run(account, [SBT_A, SBT_B], benchmark="none"))["result"]
        assert result["benchmark"] is None

    def test_all_strategies_unavailable_fails_the_run(self, account, histories):
        histories[SBT_A]["Close"] = np.linspace(100, 10, DAYS)
        histories[SBT_B]["Close"] = np.linspace(100, 20, DAYS)
        run_id = _run(account, [SBT_A, SBT_B], strategies=["max_sharpe"], max_weight=1.0, lookback=60)
        run = sbq.get_run(run_id)["run"]
        assert run["state"] == "failed" and "was able to run" in run["error"]

    def test_allocations_endpoint_data(self, account, histories):
        run_id = _run(account, [SBT_A, SBT_B], strategies=["rebalanced_ew"])
        data = sbq.get_allocations(run_id, "rebalanced_ew")
        assert set(data["weights"]) == {SBT_A, SBT_B}
        assert len(data["cash"]) == len(data["dates"])
        assert sbq.get_allocations(run_id, "nope")["status"] == "error"

    def test_current_weights_strategy_uses_the_held_weights(self, account, histories):
        run_id = _run(account, [SBT_A, SBT_B, SBT_C], strategies=["current_weights"])
        target = sbq.get_run(run_id)["result"]["strategies"][0]["decisions"][0]["target"]
        held = {h["symbol"]: h["weight"] for h in resolve_scope_holdings(account)[0] if h["symbol"] in target}
        assert sum(target.values()) == pytest.approx(1.0)
        for ticker, weight in held.items():
            assert target[ticker] == pytest.approx(weight / sum(held.values()), rel=1e-6)


class TestRetentionAndCleanup:
    def test_oldest_runs_beyond_the_limit_are_pruned_with_their_files(self, account, histories):
        ids = [_run(account, [SBT_A, SBT_B], strategies=["buy_hold_ew"]) for _ in range(3)]
        conn = db.get_connection()
        for i, run_id in enumerate(ids):
            conn.execute("UPDATE strategy_backtest_runs SET created_at = ? WHERE id = ?", (f"2020-01-0{i + 1} 00:00:00", run_id))
        conn.commit()
        conn.close()
        with patch.object(sbr, "MAX_SAVED_RUNS", 2):
            assert sbr.prune_runs() == 1
        assert not sbr.run_dir(ids[0]).exists()
        assert sbr.run_dir(ids[2]).exists()
        assert sbq.get_run(ids[0])["status"] == "error"

    def test_delete_removes_row_and_files(self, account, histories):
        run_id = _run(account, [SBT_A, SBT_B])
        assert sbr.delete_run(run_id)["status"] == "success"
        assert not sbr.run_dir(run_id).exists()
        assert sbr.delete_run(run_id)["status"] == "error"

    def test_a_running_run_cannot_be_deleted(self, account, histories):
        created = sbr.create_run(_request(account, [SBT_A, SBT_B]))
        assert sbr.delete_run(created["run_id"])["status"] == "error"

    def test_interrupted_runs_are_marked_failed(self, account, histories):
        created = sbr.create_run(_request(account, [SBT_A, SBT_B]))
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime(sbr.TS_FORMAT)
        conn = db.get_connection()
        conn.execute("UPDATE strategy_backtest_runs SET created_at = ? WHERE id = ?", (old, created["run_id"]))
        conn.commit()
        conn.close()
        run = sbq.get_run(created["run_id"])["run"]
        assert run["state"] == "failed" and "interrupted" in run["error"]

    def test_list_runs_summarises_each_run(self, account, histories):
        run_id = _run(account, [SBT_A, SBT_B])
        listed = {r["id"]: r for r in sbq.list_runs()}
        assert listed[run_id]["state"] == "completed"
        assert listed[run_id]["tickers"] == 2 and listed[run_id]["currency"] == "GBP"


class TestShortlistBasket:
    def test_shortlist_members_become_the_basket(self, histories):
        payload = {
            "snapshot": {"id": 7, "decision_ts": "2026-10-02 19:30:00"},
            "members": [{"ticker": SBT_A}, {"ticker": SBT_B}, {"ticker": SBT_C}],
        }
        with patch("strategy_backtest_data.get_shortlist", return_value=payload):
            basket = sbd.resolve_basket({
                "basket_type": "shortlist", "shortlist_signal": "ml_upside", "shortlist_scope": "portfolio",
                "currency": None, "account_id": "all",
            })
        assert basket["tickers"] == [SBT_A, SBT_B, SBT_C]
        assert basket["shortlist"]["snapshot_id"] == 7
        assert basket["current_weights"] is None

    def test_missing_snapshot_is_rejected(self):
        with patch("strategy_backtest_data.get_shortlist", return_value={"snapshot": None, "members": []}):
            with pytest.raises(sbd.BasketError, match="no snapshot"):
                sbd.resolve_basket({"basket_type": "shortlist", "shortlist_signal": "ml_upside", "shortlist_scope": "watchlist"})

    def test_listing_only_includes_lists_with_a_snapshot(self, histories):
        def fake(signal, scope):
            if (signal, scope) == ("ml_upside", "portfolio"):
                return {"label": "ML Upside Shortlist", "snapshot": {"id": 1, "decision_ts": "2026-10-02 19:30:00"},
                        "members": [{"ticker": SBT_A, "company_name": "A"}, {"ticker": SBT_USD, "company_name": "U"}]}
            return {"label": "x", "snapshot": None, "members": []}
        with patch("strategy_backtest_data.get_shortlist", side_effect=fake):
            baskets = sbd.list_shortlist_baskets()
        assert len(baskets) == 1
        assert baskets[0]["by_currency"] == {"GBP": [SBT_A], "USD": [SBT_USD]}


class TestApi:
    def test_meta_lists_strategies_defaults_and_presets(self, client):
        data = client.get("/api/strategy-backtester/meta").json()
        assert data["status"] == "success"
        assert {s["id"] for s in data["strategies"]} == {
            "buy_hold_ew", "rebalanced_ew", "current_weights", "inverse_vol", "tolerance_band", "min_variance", "max_sharpe",
        }
        assert data["defaults"]["cadence"] == "quarterly" and data["defaults"]["lookback"] == 252
        assert data["cost_presets"]["typical"]["commission_bps"] == 10.0

    @pytest.mark.parametrize("body", [
        {"strategies": []},
        {"strategies": ["buy_hold_ew"], "cadence": "daily"},
        {"strategies": ["buy_hold_ew"], "max_weight": 0},
        {"strategies": ["buy_hold_ew"], "cash_reserve": 1},
        {"strategies": ["buy_hold_ew"], "commission_bps": -1},
        {"strategies": ["buy_hold_ew"], "initial_capital": 0},
        {"strategies": ["buy_hold_ew"], "basket_type": "shortlist"},
    ])
    def test_run_rejects_invalid_requests(self, client, body):
        assert client.post("/api/strategy-backtester/run", json=body).status_code == 422

    def test_run_poll_and_delete_end_to_end(self, client, account, histories):
        resp = client.post("/api/strategy-backtester/run", json={
            "account_id": account, "include_tickers": [SBT_A, SBT_B], "strategies": ["buy_hold_ew", "rebalanced_ew"],
            "lookback": 60, "benchmark": SBT_BENCH, "cost_preset": "none",
        })
        body = resp.json()
        assert resp.status_code == 200 and body["status"] == "success"
        run_id = body["run_id"]
        detail = client.get(f"/api/strategy-backtester/runs/{run_id}").json()
        assert detail["run"]["state"] == "completed"
        assert len(detail["result"]["strategies"]) == 2
        assert any(r["id"] == run_id for r in client.get("/api/strategy-backtester/runs").json()["runs"])
        alloc = client.get(f"/api/strategy-backtester/runs/{run_id}/allocations?strategy=buy_hold_ew").json()
        assert alloc["status"] == "success"
        assert client.delete(f"/api/strategy-backtester/runs/{run_id}").json()["status"] == "success"
        assert client.get(f"/api/strategy-backtester/runs/{run_id}").json()["status"] == "error"

    def test_run_with_a_bad_basket_returns_a_message_not_a_500(self, client, account, histories):
        resp = client.post("/api/strategy-backtester/run", json={
            "account_id": account, "include_tickers": [SBT_A, SBT_USD], "strategies": ["buy_hold_ew"],
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "error" and "USD" in resp.json()["message"]

    def test_run_id_path_is_validated(self, client):
        assert client.get("/api/strategy-backtester/runs/not-a-run-id").status_code == 422
        assert client.delete("/api/strategy-backtester/runs/..%2F..%2Fetc").status_code in (404, 422)

    def test_candidates_carry_currency_and_history(self, client, account, histories):
        data = client.get(f"/api/strategy-backtester/candidates?account_id={account}").json()
        assert data["status"] == "success"
        by_symbol = {c["symbol"]: c for c in data["candidates"]}
        assert by_symbol[SBT_A]["currency"] == "GBP" and by_symbol[SBT_USD]["currency"] == "USD"
        assert by_symbol[SBT_A]["history"]["standard"]["sessions"] == DAYS
        assert {c["currency"] for c in data["currencies"]} >= {"GBP", "USD"}

    def test_history_status_endpoint(self, client, histories):
        data = client.get(f"/api/strategy-backtester/history-status?tickers={SBT_A},{SBT_B}").json()
        assert data["status"] == "success" and data["tickers"][SBT_A]["standard"]["sessions"] == DAYS

    def test_prepare_history_endpoint_starts_background_work(self, client):
        with patch("strategy_backtest_data.request_cache_refresh", return_value=object()) as submit:
            data = client.post("/api/strategy-backtester/prepare-history", json={"tickers": [SBT_A, SBT_B]}).json()
        assert data["status"] == "success" and submit.call_count == 1

    def test_prepare_history_reports_a_refused_submission(self, client):
        with patch("strategy_backtest_data.request_cache_refresh", return_value=None):
            data = client.post("/api/strategy-backtester/prepare-history", json={"tickers": [SBT_A]}).json()
        assert data["status"] == "error"
        assert sbd._prepare_state.get(SBT_A) is None

    def test_shortlists_endpoint(self, client):
        with patch("api_routes_backtester.list_shortlist_baskets", return_value=[]):
            assert client.get("/api/strategy-backtester/shortlists").json() == {"status": "success", "baskets": []}
