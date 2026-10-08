"""
tests/test_stable_shortlist_engine.py — Stable Shortlist Tests

Covers:
  • select_members()      — turnover control invariants (list size, n_drop swaps, minimum hold,
                            sector cap, forced exits, deterministic ties), hand-computed examples
  • take_snapshot()       — signed ML upside arithmetic, eligibility (positive only / stale /
                            min score), provenance, one snapshot per ISO week, no fresh signals
  • resolve_outcomes()    — forward return, direction/band checks, catch-up after a missed run
  • stable_shortlist_reads: get_evaluation() / get_shortlist() / get_column_values()
  • run_stable_shortlist() and scheduler_jobs.run_stable_shortlist_job() wiring
"""

import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import database as db
import scheduler_jobs
import stable_shortlist_engine as sse
from stable_shortlist_engine import (
    SIGNAL_ML,
    SIGNAL_QUANT,
    resolve_outcomes,
    run_stable_shortlist,
    select_members,
    take_snapshot,
)
from stable_shortlist_reads import get_column_values, get_evaluation, get_shortlist

PARAMS = {"TOPK": 3, "N_DROP": 1, "HOLD_THRESH": 1, "SECTOR_CAP": 2, "MAX_SIGNAL_AGE_DAYS": 4, "MIN_QUANT_SCORE": 50}
NOW = datetime(2026, 10, 9, 19, 30, tzinfo=timezone.utc)  # Friday, ISO week 2026-W41
NEXT_WEEK = datetime(2026, 10, 16, 19, 30, tzinfo=timezone.utc)
SIGNAL_DATE = "2026-10-08"


def cand(ticker, value, sector="Tech", eligible=True):
    return {"ticker": ticker, "sector": sector, "signal_value": value, "eligible": eligible}


def pick(candidates, prior=None, **kw):
    args = {"topk": 3, "n_drop": 1, "hold_thresh": 1, "sector_cap": 2}
    args.update(kw)
    return select_members(candidates, prior or {}, **args)


def selected(decisions):
    return sorted((t for t, d in decisions.items() if d["selected"]), key=lambda t: decisions[t]["rank"])


class TestSelectMembersInitialFill:
    def test_takes_top_k_by_signal(self):
        d = pick([cand("A", 5, "S1"), cand("B", 9, "S2"), cand("C", 7, "S3"), cand("D", 1, "S4")])
        assert selected(d) == ["B", "C", "A"]
        assert d["B"]["reason"] == "entered" and d["B"]["cycles_held"] == 1
        assert d["D"] == {"selected": False, "reason": "not_selected", "rank": 4, "cycles_held": 0}

    def test_ties_break_on_ticker(self):
        d = pick([cand("Z", 5, "S1"), cand("M", 5, "S2"), cand("A", 5, "S3"), cand("B", 5, "S4")])
        assert selected(d) == ["A", "B", "M"]

    def test_input_order_does_not_matter(self):
        cands = [cand(t, v, s) for t, v, s in
                 [("A", 5, "S1"), ("B", 9, "S1"), ("C", 7, "S2"), ("D", 7, "S2"), ("E", 3, "S3"), ("F", 8, "S1")]]
        prior = {"E": {"cycles_held": 2}, "A": {"cycles_held": 1}}
        baseline = pick(cands, prior)
        for seed in range(5):
            shuffled = cands[:]
            random.Random(seed).shuffle(shuffled)
            assert pick(shuffled, prior) == baseline

    def test_short_list_when_few_qualify_and_ineligible_never_selected(self):
        d = pick([cand("A", 5, "S1"), cand("B", 9, "S2", eligible=False), cand("C", 7, "S3", eligible=False)])
        assert selected(d) == ["A"]
        assert d["B"]["reason"] == "ineligible" and d["B"]["rank"] is None

    def test_never_exceeds_topk(self):
        d = pick([cand(f"T{i}", i, f"S{i}") for i in range(20)], topk=5, sector_cap=1)
        assert len(selected(d)) == 5


class TestSelectMembersSectorCap:
    def test_sector_cap_blocks_extra_names_in_fill(self):
        d = pick([cand("A", 9, "Tech"), cand("B", 8, "Tech"), cand("C", 7, "Tech"), cand("D", 6, "Health")])
        assert selected(d) == ["A", "B", "D"]
        assert d["C"]["reason"] == "blocked_sector_cap"

    def test_unknown_sector_names_share_one_bucket(self):
        d = pick([cand("A", 9, "Unknown"), cand("B", 8, "Unknown"), cand("C", 7, "Unknown"), cand("D", 6, "Health")])
        assert selected(d) == ["A", "B", "D"]

    def test_over_cap_carried_members_are_trimmed_worst_first(self):
        prior = {t: {"cycles_held": 3} for t in ("A", "B", "C")}
        d = pick([cand("A", 9, "Tech"), cand("B", 8, "Tech"), cand("C", 7, "Tech")], prior, sector_cap=2)
        assert selected(d) == ["A", "B"]
        assert d["C"]["reason"] == "dropped_sector_cap"

    def test_shrunk_topk_drops_worst_carried_members(self):
        prior = {t: {"cycles_held": 3} for t in ("A", "B", "C")}
        d = pick([cand("A", 9, "S1"), cand("B", 8, "S2"), cand("C", 7, "S3")], prior, topk=2)
        assert selected(d) == ["A", "B"]
        assert d["C"]["reason"] == "dropped_rank"

    def test_swap_into_a_full_sector_needs_a_same_sector_partner(self):
        prior = {"A": {"cycles_held": 2}, "B": {"cycles_held": 2}, "C": {"cycles_held": 2}}
        cands = [cand("A", 1, "Tech"), cand("B", 8, "Tech"), cand("C", 7, "Health"), cand("X", 9, "Tech")]
        d = pick(cands, prior, n_drop=1)
        assert selected(d) == ["X", "B", "C"]
        assert d["A"]["reason"] == "dropped_rank"

    def test_swap_blocked_when_only_partner_is_in_another_full_sector(self):
        prior = {"A": {"cycles_held": 2}, "B": {"cycles_held": 2}, "C": {"cycles_held": 2}}
        cands = [cand("A", 10, "Tech"), cand("B", 9, "Tech"), cand("C", 1, "Health"), cand("X", 8, "Tech")]
        d = pick(cands, prior, n_drop=1)
        assert sorted(selected(d)) == ["A", "B", "C"]
        assert d["X"]["reason"] == "blocked_sector_cap"
        assert d["C"]["reason"] == "retained"


class TestSelectMembersTurnoverControl:
    def test_swaps_at_most_n_drop_worst_for_best(self):
        prior = {t: {"cycles_held": 2} for t in ("A", "B", "C")}
        cands = [cand("A", 3, "S1"), cand("B", 2, "S2"), cand("C", 1, "S3"),
                 cand("X", 9, "S4"), cand("Y", 8, "S5"), cand("Z", 7, "S6")]
        d = pick(cands, prior, n_drop=2)
        assert sorted(selected(d)) == ["A", "X", "Y"]
        assert d["C"]["reason"] == "dropped_rank" and d["B"]["reason"] == "dropped_rank"
        assert d["X"]["reason"] == "entered" and d["A"]["reason"] == "retained"
        assert d["A"]["cycles_held"] == 3
        assert d["Z"]["reason"] == "not_selected"

    def test_zero_n_drop_freezes_the_list(self):
        prior = {t: {"cycles_held": 5} for t in ("A", "B", "C")}
        cands = [cand("A", 1, "S1"), cand("B", 2, "S2"), cand("C", 3, "S3"), cand("X", 99, "S4")]
        d = pick(cands, prior, n_drop=0)
        assert sorted(selected(d)) == ["A", "B", "C"]

    def test_no_swap_when_every_outsider_ranks_below_every_member(self):
        prior = {t: {"cycles_held": 5} for t in ("A", "B", "C")}
        cands = [cand("A", 9, "S1"), cand("B", 8, "S2"), cand("C", 7, "S3"), cand("X", 1, "S4")]
        d = pick(cands, prior, n_drop=3)
        assert sorted(selected(d)) == ["A", "B", "C"]
        assert all(d[t]["reason"] == "retained" for t in "ABC")

    def test_minimum_hold_blocks_an_early_drop(self):
        prior = {"A": {"cycles_held": 1}, "B": {"cycles_held": 3}, "C": {"cycles_held": 3}}
        cands = [cand("A", 1, "S1"), cand("B", 10, "S2"), cand("C", 9, "S3"), cand("X", 5, "S4")]
        held = pick(cands, prior, hold_thresh=2)
        assert sorted(selected(held)) == ["A", "B", "C"]
        assert held["A"]["reason"] == "blocked_hold_thresh"
        released = pick(cands, {**prior, "A": {"cycles_held": 2}}, hold_thresh=2)
        assert sorted(selected(released)) == ["B", "C", "X"]
        assert released["A"]["reason"] == "dropped_rank"

    def test_swap_prefers_a_same_sector_partner_when_the_sector_is_full(self):
        prior = {"A": {"cycles_held": 3}, "B": {"cycles_held": 3}, "C": {"cycles_held": 3}}
        cands = [cand("A", 1, "Tech"), cand("B", 2, "Health"), cand("C", 3, "Tech"), cand("X", 9, "Tech")]
        d = pick(cands, prior, n_drop=3, sector_cap=2)
        assert {t for t in "ABCX" if d[t]["selected"]} == {"B", "C", "X"}
        assert d["A"]["reason"] == "dropped_rank"

    def test_forced_exits_do_not_use_up_n_drop(self):
        prior = {t: {"cycles_held": 3} for t in ("A", "B", "C")}
        cands = [cand("A", 5, "S1", eligible=False), cand("B", 8, "S2"), cand("C", 1, "S3"),
                 cand("X", 9, "S4"), cand("Y", 7, "S5")]
        d = pick(cands, prior, n_drop=1)
        assert d["A"]["reason"] == "dropped_ineligible"
        assert d["C"]["reason"] == "dropped_rank"
        assert sorted(selected(d)) == ["B", "X", "Y"]

    def test_member_missing_from_candidates_is_dropped_ineligible(self):
        d = pick([cand("B", 8, "S2")], {"GONE": {"cycles_held": 4}})
        assert d["GONE"]["reason"] == "dropped_ineligible" and not d["GONE"]["selected"]
        assert selected(d) == ["B"]

    def test_retained_member_keeps_counting_cycles(self):
        prior = {"A": {"cycles_held": 4}}
        d = pick([cand("A", 5, "S1")], prior)
        assert d["A"] == {"selected": True, "reason": "retained", "rank": 1, "cycles_held": 5}


def _reset_tables():
    conn = db.get_connection()
    try:
        for table in ("stable_shortlist_members", "stable_shortlist_snapshots"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()


def _seed_quant(ticker, date, close, q10=None, q90=None, score=None):
    conn = db.get_connection()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO quant_signals (ticker, date, close_price, price_q10, price_q90, composite_score)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (ticker, date, close, q10, q90, score),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_sector(ticker, sector, name=None):
    conn = db.get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO stock_signals (ticker, company_name, sector) VALUES (?, ?, ?)",
            (ticker, name or f"{ticker} Inc", sector),
        )
        conn.commit()
    finally:
        conn.close()


def _members(snapshot_id):
    conn = db.get_connection()
    try:
        return {r["ticker"]: dict(r) for r in conn.execute(
            "SELECT * FROM stable_shortlist_members WHERE snapshot_id=?", (snapshot_id,)).fetchall()}
    finally:
        conn.close()


@pytest.fixture
def clean_tables():
    _reset_tables()
    yield
    _reset_tables()


@pytest.fixture
def scope_tickers():
    """Patches the engine's Portfolio list; Watchlist stays empty."""
    holder = {"portfolio": [], "watchlist": []}
    with patch("stable_shortlist_engine.get_portfolio_tickers", side_effect=lambda: list(holder["portfolio"])), \
         patch("stable_shortlist_engine.get_watchlist_only_tickers", side_effect=lambda: list(holder["watchlist"])):
        yield holder


class TestTakeSnapshotMl:
    def test_signed_upside_uses_reference_close_and_band_midpoint(self, clean_tables, scope_tickers):
        scope_tickers["portfolio"] = ["SL_UP", "SL_DOWN", "SL_PENCE"]
        _seed_quant("SL_UP", SIGNAL_DATE, 100.0, 105.0, 125.0)        # mid 115 -> +15%
        _seed_quant("SL_DOWN", SIGNAL_DATE, 100.0, 80.0, 100.0)       # mid 90 -> -10%
        _seed_quant("SL_PENCE", SIGNAL_DATE, 5000.0, 4900.0, 5300.0)  # mid 5100 -> +2% (pence-quoted, same units)
        for t in scope_tickers["portfolio"]:
            _seed_sector(t, "Tech")
        result = take_snapshot(SIGNAL_ML, "portfolio", PARAMS, now=NOW)
        assert result["status"] == "created"
        rows = _members(result["snapshot_id"])
        assert rows["SL_UP"]["signal_value"] == pytest.approx(15.0)
        assert rows["SL_PENCE"]["signal_value"] == pytest.approx(2.0)
        assert rows["SL_DOWN"]["signal_value"] == pytest.approx(-10.0)
        assert rows["SL_DOWN"]["eligible"] == 0 and rows["SL_DOWN"]["ineligible_reason"] == "not_positive"
        assert rows["SL_DOWN"]["selected"] == 0
        assert rows["SL_UP"]["selected"] == 1 and rows["SL_UP"]["rank"] == 1
        assert rows["SL_UP"]["reference_close"] == 100.0
        assert rows["SL_UP"]["target_date"] == "2026-10-22"

    def test_snapshot_stores_provenance(self, clean_tables, scope_tickers):
        scope_tickers["portfolio"] = ["SL_P1"]
        _seed_quant("SL_P1", SIGNAL_DATE, 100.0, 105.0, 125.0)
        result = take_snapshot(SIGNAL_ML, "portfolio", PARAMS, now=NOW)
        conn = db.get_connection()
        try:
            snap = dict(conn.execute("SELECT * FROM stable_shortlist_snapshots WHERE id=?", (result["snapshot_id"],)).fetchone())
        finally:
            conn.close()
        assert snap["cycle_key"] == "2026-W41"
        assert snap["decision_ts"] == "2026-10-09 19:30:00"
        assert json.loads(snap["config_json"]) == PARAMS
        assert snap["signal_as_of"] == SIGNAL_DATE
        assert snap["signal_version"].startswith("quantile models")
        assert snap["candidate_count"] == 1 and snap["member_count"] == 1

    def test_stale_signal_is_ineligible_and_untracked(self, clean_tables, scope_tickers):
        scope_tickers["portfolio"] = ["SL_FRESH", "SL_STALE"]
        _seed_quant("SL_FRESH", SIGNAL_DATE, 100.0, 105.0, 125.0)
        _seed_quant("SL_STALE", "2026-10-01", 100.0, 105.0, 125.0)  # 8 days old > 4
        result = take_snapshot(SIGNAL_ML, "portfolio", PARAMS, now=NOW)
        rows = _members(result["snapshot_id"])
        assert rows["SL_STALE"]["ineligible_reason"] == "stale_signal"
        assert rows["SL_STALE"]["selected"] == 0
        assert rows["SL_STALE"]["target_date"] is None
        assert rows["SL_FRESH"]["target_date"] is not None

    def test_nothing_written_when_no_signal_is_fresh(self, clean_tables, scope_tickers):
        scope_tickers["portfolio"] = ["SL_OLD"]
        _seed_quant("SL_OLD", "2026-09-01", 100.0, 105.0, 125.0)
        assert take_snapshot(SIGNAL_ML, "portfolio", PARAMS, now=NOW)["status"] == "no_fresh_signals"
        conn = db.get_connection()
        try:
            assert conn.execute("SELECT COUNT(*) AS c FROM stable_shortlist_snapshots").fetchone()["c"] == 0
        finally:
            conn.close()

    def test_empty_scope_writes_nothing(self, clean_tables, scope_tickers):
        assert take_snapshot(SIGNAL_ML, "watchlist", PARAMS, now=NOW)["status"] == "empty_scope"

    def test_same_week_is_idempotent_and_next_week_adds_a_snapshot(self, clean_tables, scope_tickers):
        scope_tickers["portfolio"] = ["SL_ID1"]
        _seed_quant("SL_ID1", SIGNAL_DATE, 100.0, 105.0, 125.0)
        assert take_snapshot(SIGNAL_ML, "portfolio", PARAMS, now=NOW)["status"] == "created"
        assert take_snapshot(SIGNAL_ML, "portfolio", PARAMS, now=NOW)["status"] == "exists"
        _seed_quant("SL_ID1", "2026-10-15", 100.0, 105.0, 125.0)
        assert take_snapshot(SIGNAL_ML, "portfolio", PARAMS, now=NEXT_WEEK)["status"] == "created"

    def test_second_snapshot_carries_membership_and_rank_movement(self, clean_tables, scope_tickers):
        scope_tickers["portfolio"] = ["SL_M1", "SL_M2", "SL_M3"]
        for i, t in enumerate(scope_tickers["portfolio"]):
            _seed_sector(t, f"Sector{i}")
        _seed_quant("SL_M1", SIGNAL_DATE, 100.0, 100.0, 130.0)  # +15%
        _seed_quant("SL_M2", SIGNAL_DATE, 100.0, 100.0, 120.0)  # +10%
        _seed_quant("SL_M3", SIGNAL_DATE, 100.0, 100.0, 110.0)  # +5%
        params = {**PARAMS, "TOPK": 2}
        first = take_snapshot(SIGNAL_ML, "portfolio", params, now=NOW)
        assert sorted(t for t, r in _members(first["snapshot_id"]).items() if r["selected"]) == ["SL_M1", "SL_M2"]
        _seed_quant("SL_M1", "2026-10-15", 100.0, 100.0, 110.0)  # falls to +5%
        _seed_quant("SL_M2", "2026-10-15", 100.0, 100.0, 120.0)  # +10%
        _seed_quant("SL_M3", "2026-10-15", 100.0, 100.0, 140.0)  # rises to +20%
        second = take_snapshot(SIGNAL_ML, "portfolio", {**params, "HOLD_THRESH": 1}, now=NEXT_WEEK)
        rows = _members(second["snapshot_id"])
        assert rows["SL_M3"]["reason"] == "entered" and rows["SL_M3"]["prev_rank"] == 3
        assert rows["SL_M1"]["reason"] == "dropped_rank" and rows["SL_M1"]["prev_rank"] == 1
        assert rows["SL_M2"]["reason"] == "retained" and rows["SL_M2"]["cycles_held"] == 2

    def test_member_that_leaves_the_scope_is_dropped_with_a_reason(self, clean_tables, scope_tickers):
        scope_tickers["portfolio"] = ["SL_LV1", "SL_LV2"]
        for t in scope_tickers["portfolio"]:
            _seed_quant(t, SIGNAL_DATE, 100.0, 100.0, 120.0)
        take_snapshot(SIGNAL_ML, "portfolio", PARAMS, now=NOW)
        scope_tickers["portfolio"] = ["SL_LV1"]
        _seed_quant("SL_LV1", "2026-10-15", 100.0, 100.0, 120.0)
        rows = _members(take_snapshot(SIGNAL_ML, "portfolio", PARAMS, now=NEXT_WEEK)["snapshot_id"])
        assert rows["SL_LV2"]["reason"] == "dropped_ineligible"
        assert rows["SL_LV2"]["ineligible_reason"] == "left_scope"
        assert rows["SL_LV2"]["selected"] == 0


class TestTakeSnapshotQuantScore:
    def test_minimum_score_and_deterministic_ties(self, clean_tables, scope_tickers):
        scope_tickers["watchlist"] = ["SL_Q_B", "SL_Q_A", "SL_Q_LOW", "SL_Q_C"]
        for t, score, sector in (("SL_Q_B", 70, "S1"), ("SL_Q_A", 70, "S2"), ("SL_Q_LOW", 49, "S3"), ("SL_Q_C", 65, "S4")):
            _seed_quant(t, SIGNAL_DATE, 20.0, score=score)
            _seed_sector(t, sector)
        result = take_snapshot(SIGNAL_QUANT, "watchlist", PARAMS, now=NOW)
        rows = _members(result["snapshot_id"])
        assert rows["SL_Q_A"]["rank"] == 1 and rows["SL_Q_B"]["rank"] == 2 and rows["SL_Q_C"]["rank"] == 3
        assert rows["SL_Q_LOW"]["ineligible_reason"] == "below_min_score" and rows["SL_Q_LOW"]["selected"] == 0
        assert rows["SL_Q_A"]["signal_value"] == 70.0
        assert rows["SL_Q_A"]["price_q10"] is None
        assert result["members"] == 3

    def test_missing_sector_is_bucketed_as_unknown(self, clean_tables, scope_tickers):
        scope_tickers["watchlist"] = ["SL_QU1"]
        _seed_quant("SL_QU1", SIGNAL_DATE, 20.0, score=80)
        conn = db.get_connection()
        try:
            conn.execute("DELETE FROM stock_signals WHERE ticker='SL_QU1'")
            conn.commit()
        finally:
            conn.close()
        rows = _members(take_snapshot(SIGNAL_QUANT, "watchlist", PARAMS, now=NOW)["snapshot_id"])
        assert rows["SL_QU1"]["sector"] == "Unknown"


class TestResolveOutcomes:
    def _snapshot_with(self, scope_tickers, target_close_date, actual_close):
        scope_tickers["portfolio"] = ["SL_O1", "SL_O2"]
        _seed_quant("SL_O1", SIGNAL_DATE, 100.0, 105.0, 125.0)   # mid 115, member
        _seed_quant("SL_O2", SIGNAL_DATE, 100.0, 80.0, 100.0)    # mid 90, not positive -> other, still tracked
        params = {**PARAMS, "TOPK": 1}
        result = take_snapshot(SIGNAL_ML, "portfolio", params, now=NOW)
        _seed_quant("SL_O1", target_close_date, actual_close)
        _seed_quant("SL_O2", target_close_date, 95.0)
        return result["snapshot_id"]

    def test_fills_forward_return_direction_and_band(self, clean_tables, scope_tickers):
        snapshot_id = self._snapshot_with(scope_tickers, "2026-10-22", 120.0)
        with patch("stable_shortlist_engine.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 10, 23, tzinfo=timezone.utc)
            mock_dt.strptime = datetime.strptime
            assert resolve_outcomes() == 2
        rows = _members(snapshot_id)
        assert rows["SL_O1"]["actual_price"] == 120.0
        assert rows["SL_O1"]["forward_return_pct"] == pytest.approx(20.0)
        assert rows["SL_O1"]["direction_correct"] == 1
        assert rows["SL_O1"]["within_band_correct"] == 1
        assert rows["SL_O2"]["forward_return_pct"] == pytest.approx(-5.0)
        assert rows["SL_O2"]["direction_correct"] == 1  # predicted down (mid 90 < 100), fell to 95

    def test_outside_band_and_wrong_direction(self, clean_tables, scope_tickers):
        snapshot_id = self._snapshot_with(scope_tickers, "2026-10-22", 90.0)
        with patch("stable_shortlist_engine.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 10, 23, tzinfo=timezone.utc)
            resolve_outcomes()
        row = _members(snapshot_id)["SL_O1"]
        assert row["direction_correct"] == 0 and row["within_band_correct"] == 0
        assert row["forward_return_pct"] == pytest.approx(-10.0)

    def test_pending_until_target_date_and_catches_up_later(self, clean_tables, scope_tickers):
        snapshot_id = self._snapshot_with(scope_tickers, "2026-10-22", 120.0)
        with patch("stable_shortlist_engine.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 10, 20, tzinfo=timezone.utc)
            assert resolve_outcomes() == 0
        assert _members(snapshot_id)["SL_O1"]["actual_price"] is None
        with patch("stable_shortlist_engine.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 11, 20, tzinfo=timezone.utc)
            assert resolve_outcomes() == 2
            assert resolve_outcomes() == 0
        assert _members(snapshot_id)["SL_O1"]["actual_date"] == "2026-10-22"

    def test_stale_rows_are_never_resolved(self, clean_tables, scope_tickers):
        scope_tickers["portfolio"] = ["SL_OF", "SL_OS"]
        _seed_quant("SL_OF", SIGNAL_DATE, 100.0, 105.0, 125.0)
        _seed_quant("SL_OS", "2026-10-01", 100.0, 105.0, 125.0)
        snapshot_id = take_snapshot(SIGNAL_ML, "portfolio", PARAMS, now=NOW)["snapshot_id"]
        _seed_quant("SL_OS", "2026-10-22", 130.0)
        _seed_quant("SL_OF", "2026-10-22", 130.0)
        with patch("stable_shortlist_engine.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 11, 1, tzinfo=timezone.utc)
            assert resolve_outcomes() == 1
        assert _members(snapshot_id)["SL_OS"]["actual_price"] is None


class TestEvaluationAndPayloads:
    def _evaluated_snapshot(self, scope_tickers):
        scope_tickers["portfolio"] = ["SL_E1", "SL_E2", "SL_E3", "SL_E4"]
        for t, score in (("SL_E1", 90), ("SL_E2", 80), ("SL_E3", 60), ("SL_E4", 55)):
            _seed_quant(t, SIGNAL_DATE, 100.0, score=score)
            _seed_sector(t, f"Sector{t}")
        result = take_snapshot(SIGNAL_QUANT, "portfolio", {**PARAMS, "TOPK": 2}, now=NOW)
        for t, close in (("SL_E1", 110.0), ("SL_E2", 104.0), ("SL_E3", 100.0), ("SL_E4", 98.0)):
            _seed_quant(t, "2026-10-22", close)
        with patch("stable_shortlist_engine.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 11, 1, tzinfo=timezone.utc)
            resolve_outcomes()
        return result["snapshot_id"]

    def test_member_average_versus_other_average(self, clean_tables, scope_tickers):
        self._evaluated_snapshot(scope_tickers)
        ev = get_evaluation(SIGNAL_QUANT, "portfolio")
        row = ev["snapshots"][0]
        assert row["member_count"] == 2 and row["other_count"] == 2
        assert row["member_avg_return_pct"] == pytest.approx(7.0)    # (10 + 4) / 2
        assert row["other_avg_return_pct"] == pytest.approx(-1.0)    # (0 - 2) / 2
        assert row["excess_pct"] == pytest.approx(8.0)
        assert ev["summary"]["snapshots_beating_others"] == 1
        assert ev["summary"]["direction_accuracy"] is None

    def test_pending_snapshots_are_counted(self, clean_tables, scope_tickers):
        scope_tickers["portfolio"] = ["SL_EP1"]
        _seed_quant("SL_EP1", SIGNAL_DATE, 100.0, score=90)
        take_snapshot(SIGNAL_QUANT, "portfolio", PARAMS, now=NOW)
        ev = get_evaluation(SIGNAL_QUANT, "portfolio")
        assert ev["snapshots"] == [] and ev["summary"]["snapshots_pending"] == 1

    def test_get_shortlist_payload(self, clean_tables, scope_tickers):
        self._evaluated_snapshot(scope_tickers)
        data = get_shortlist(SIGNAL_QUANT, "portfolio")
        assert [m["ticker"] for m in data["members"]] == ["SL_E1", "SL_E2"]
        assert data["members"][0]["company_name"] == "SL_E1 Inc"
        assert data["members"][0]["reason_label"] == "Entered the list"
        assert [c["ticker"] for c in data["changes"]] == ["SL_E1", "SL_E2"]
        assert [o["ticker"] for o in data["others"]] == ["SL_E3", "SL_E4"]
        assert data["snapshot"]["config"]["TOPK"] == 2
        assert data["snapshot"]["decision_local"].startswith("2026-10-09")
        assert data["label"] == "Quant Score Shortlist"
        assert data["evaluation"]["summary"]["snapshots_evaluated"] == 1

    def test_get_shortlist_without_a_snapshot(self, clean_tables):
        data = get_shortlist(SIGNAL_ML, "watchlist")
        assert data["snapshot"] is None and data["members"] == [] and data["changes"] == []

    def test_column_values_come_from_the_latest_snapshot_of_each_signal(self, clean_tables, scope_tickers):
        self._evaluated_snapshot(scope_tickers)
        values = get_column_values(["SL_E1", "SL_E3", "SL_NONE"])
        assert values["SL_E1"] == {"quant_shortlist_member": 1, "quant_shortlist_rank": 1}
        assert values["SL_E3"] == {"quant_shortlist_member": 0, "quant_shortlist_rank": 3}
        assert "SL_NONE" not in values
        assert get_column_values([]) == {}


class TestRunners:
    def test_run_resolves_then_snapshots_every_list(self):
        calls = []
        with patch("stable_shortlist_engine.resolve_outcomes", side_effect=lambda: calls.append("resolve") or 4), \
             patch("stable_shortlist_engine.take_snapshot",
                   side_effect=lambda sig, scope, params: calls.append((sig, scope)) or {"status": "created"}):
            summary = run_stable_shortlist()
        assert calls[0] == "resolve"
        assert calls[1:] == [(SIGNAL_ML, "portfolio"), (SIGNAL_ML, "watchlist"),
                             (SIGNAL_QUANT, "portfolio"), (SIGNAL_QUANT, "watchlist")]
        assert summary["resolved"] == 4 and len(summary["snapshots"]) == 4

    def test_job_runner_reports_success_and_skips(self):
        summary = {"resolved": 2, "snapshots": [
            {"signal_type": "ml_upside", "scope": "portfolio", "status": "created"},
            {"signal_type": "ml_upside", "scope": "watchlist", "status": "empty_scope"},
        ]}
        with patch("stable_shortlist_engine.run_stable_shortlist", return_value=summary), \
             patch("scheduler_jobs.log_sched_notification") as notify, \
             patch("scheduler_jobs.record_job_run") as record:
            scheduler_jobs.run_stable_shortlist_job()
        level, message = notify.call_args.args
        assert level == "Success"
        assert "1 snapshot(s) taken" in message and "2 outcome(s) resolved" in message
        assert "ml_upside/watchlist (empty_scope)" in message
        record.assert_called_once_with("stable_shortlist_job")

    def test_job_runner_reports_a_failed_list(self):
        summary = {"resolved": 0, "snapshots": [
            {"signal_type": "ml_upside", "scope": "portfolio", "status": "error", "message": "boom"},
        ]}
        with patch("stable_shortlist_engine.run_stable_shortlist", return_value=summary), \
             patch("scheduler_jobs.log_sched_notification") as notify, \
             patch("scheduler_jobs.record_job_run"):
            scheduler_jobs.run_stable_shortlist_job()
        assert notify.call_args.args[0] == "Error" and "boom" in notify.call_args.args[1]

    def test_job_runner_survives_an_engine_exception(self):
        with patch("stable_shortlist_engine.run_stable_shortlist", side_effect=RuntimeError("dead")), \
             patch("scheduler_jobs.log_sched_notification") as notify, \
             patch("scheduler_jobs.record_job_run") as record:
            scheduler_jobs.run_stable_shortlist_job()
        assert notify.call_args.args[0] == "Error"
        record.assert_called_once_with("stable_shortlist_job")
