"""MCP write access (MCP-20): propose, confirm and undo for the Watchlist, the credential gates, the caps and what a change leaves behind."""
import contextvars
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from constants import MCP_WRITE_LIMIT_DEFAULTS
from db import mcp_keys as db_mcp_keys, mcp_write_log
from db.account_store import get_holding_price_limits_for_ticker, get_watchlist_account, get_watchlist_tickers, upsert_holding_price_limit
from db.database import get_connection
from mcp_server import caller, keys, writes

from .helpers import MCP_KEY, call, error_text, table_counts, tool_data
from .test_oauth_server import remote, sql, tokens  # noqa: F401  remote is a fixture

pytestmark = pytest.mark.restore_tables("watchlist_items", "holding_price_limits", "mcp_api_keys")

PROPOSAL_SECONDS = MCP_WRITE_LIMIT_DEFAULTS["proposal_seconds"]

TICKER = "ZZWRITE"
OTHER = "ZZWRITEB"
META = {"company_name": "Zz Write Inc.", "currency": "USD", "quote_type": "EQUITY", "exchange": "NYSE"}


class Clock:
    def __init__(self):
        self.moment = datetime(2026, 11, 22, 12, 0, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.moment

    def advance(self, **delta):
        self.moment += timedelta(**delta)


@pytest.fixture
def clock(monkeypatch):
    fake = Clock()
    monkeypatch.setattr(writes, "_now", fake)
    return fake


@pytest.fixture
def fetches(monkeypatch):
    """The data fetches a Watchlist add queues, run inline against mocks, and the Yahoo metadata lookup answered offline."""
    ran = []
    monkeypatch.setattr("accounts.accounts_engine.resolve_watchlist_metadata", lambda ticker: dict(META))
    monkeypatch.setattr("mcp_server.writes.request_cache_refresh", lambda key, refresh: ran.append(key) or refresh())
    with patch("profile_engine.update_single_profile"), patch("data_engine.fetch_and_save_single_ticker"), patch("quant_signals.QuantEngine"):
        yield ran


@pytest.fixture
def limits(monkeypatch):
    """The live MCP_WRITE_LIMITS dict the patched config serves; a test edits it between calls like the Settings route edits config.json."""
    values = dict(MCP_WRITE_LIMIT_DEFAULTS)
    monkeypatch.setattr("mcp_server.writes.load_config", lambda: {"MCP_WRITES_ENABLED": True, "MCP_WRITE_LIMITS": values})
    return values


@pytest.fixture
def writes_on(monkeypatch, fetches, limits):
    db_mcp_keys.set_key_write_allowed(keys.match(MCP_KEY), True)


@pytest.fixture(autouse=True)
def _clean_watchlist():
    yield
    watchlist = get_watchlist_account()
    for ticker in (TICKER, OTHER):
        from db.account_store import remove_watchlist_ticker
        remove_watchlist_ticker(watchlist["id"], ticker)


def propose(client, action, ticker=TICKER):
    return tool_data(call(client, "propose_watchlist_change", {"action": action, "ticker": ticker}))["data"]


def confirm(client, proposal_id):
    return tool_data(call(client, "confirm_change", {"proposal_id": proposal_id}))["data"]


def refusal(client, name, arguments):
    return error_text(call(client, name, arguments))


def log_rows(*where):
    return sql_rows("SELECT * FROM mcp_write_log " + (f"WHERE {where[0]} " if where else "") + "ORDER BY created_at, id", *where[1:])


def sql_rows(query, *args):
    conn = get_connection()
    try:
        return [dict(r) for r in conn.execute(query, args)]
    finally:
        conn.close()


class TestGates:
    def test_changes_are_off_by_default_everywhere(self, mcp_client, fetches):
        for name, arguments in (("propose_watchlist_change", {"action": "add", "ticker": TICKER}), ("propose_undo", {"change_id": "ABCDEFGHJK"}), ("confirm_change", {"proposal_id": "ABCDEFGHJK"})):
            assert refusal(mcp_client, name, arguments) == writes.NOT_ENABLED
        assert log_rows() == []

    def test_the_global_switch_alone_is_not_enough(self, mcp_client, monkeypatch, fetches):
        monkeypatch.setattr("mcp_server.writes.load_config", lambda: {"MCP_WRITES_ENABLED": True, "MCP_WRITE_LIMITS": dict(MCP_WRITE_LIMIT_DEFAULTS)})
        assert refusal(mcp_client, "propose_watchlist_change", {"action": "add", "ticker": TICKER}) == writes.NOT_ENABLED

    def test_the_key_flag_alone_is_not_enough(self, mcp_client, monkeypatch, fetches):
        monkeypatch.setattr("mcp_server.writes.load_config", lambda: {"MCP_WRITES_ENABLED": False, "MCP_WRITE_LIMITS": dict(MCP_WRITE_LIMIT_DEFAULTS)})
        db_mcp_keys.set_key_write_allowed(keys.match(MCP_KEY), True)
        assert refusal(mcp_client, "propose_watchlist_change", {"action": "add", "ticker": TICKER}) == writes.NOT_ENABLED

    def test_both_on_lets_a_proposal_through(self, mcp_client, writes_on):
        assert propose(mcp_client, "add")["kind"] == "watchlist_add"

    def test_turning_the_key_flag_off_kills_a_pending_proposal(self, mcp_client, writes_on):
        proposal = propose(mcp_client, "add")
        db_mcp_keys.set_key_write_allowed(keys.match(MCP_KEY), False)
        assert refusal(mcp_client, "confirm_change", {"proposal_id": proposal["proposal_id"]}) == writes.NOT_ENABLED
        assert TICKER not in get_watchlist_tickers()

    def test_a_call_with_no_credential_in_context_cannot_write(self, writes_on):
        assert caller.current.get() is None
        with pytest.raises(Exception, match=writes.NOT_ENABLED):
            writes.propose_watchlist("add", TICKER)


class TestProposal:
    def test_a_proposal_writes_only_its_own_row(self, mcp_client, writes_on):
        before = table_counts()
        proposal = propose(mcp_client, "add")
        assert table_counts() == before
        assert TICKER not in get_watchlist_tickers()
        [row] = log_rows()
        assert row["status"] == "pending" and row["id"] == proposal["proposal_id"] and row["credential_kind"] == "key"
        assert json.loads(row["proposed"]) == {"watched": True}

    def test_the_answer_carries_everything_the_user_needs_to_decide(self, mcp_client, writes_on, clock):
        proposal = propose(mcp_client, "add")
        assert proposal["summary"] == f"Add {TICKER} to the Watchlist."
        assert proposal["current"] == {"watched": False} and proposal["proposed"] == {"watched": True}
        assert proposal["expires_at"] == "2026-11-22T12:10:00Z"
        assert "never because of news" in proposal["instruction"]
        assert len(proposal["proposal_id"]) == 10 and proposal["proposal_id"].isalnum()

    def test_the_ticker_is_upper_cased_and_the_summary_names_only_validated_text(self, mcp_client, writes_on):
        assert propose(mcp_client, "add", TICKER.lower())["summary"] == f"Add {TICKER} to the Watchlist."

    @pytest.mark.parametrize("ticker", ["", "../etc", "a b", "PENSION-1", "TBILL-4", "0a1b2c3d-1111-2222-3333-444455556666", "X" * 80])
    def test_a_ticker_that_cannot_be_stored_is_refused_without_echo(self, mcp_client, writes_on, ticker):
        text = refusal(mcp_client, "propose_watchlist_change", {"action": "add", "ticker": ticker})
        assert text.startswith("Invalid ticker") or "at most" in text or "too long" in text.lower()
        assert ticker.strip() == "" or ticker not in text
        assert log_rows() == []

    def test_adding_a_watched_ticker_and_removing_an_unwatched_one_are_refused(self, mcp_client, writes_on):
        confirm(mcp_client, propose(mcp_client, "add")["proposal_id"])
        assert refusal(mcp_client, "propose_watchlist_change", {"action": "add", "ticker": TICKER}) == writes.ALREADY_WATCHED
        assert refusal(mcp_client, "propose_watchlist_change", {"action": "remove", "ticker": OTHER}) == writes.NOT_WATCHED

    def test_a_remove_names_the_position_target_that_goes_with_it(self, mcp_client, writes_on):
        wl = get_watchlist_account()["id"]
        confirm(mcp_client, propose(mcp_client, "add")["proposal_id"])
        upsert_holding_price_limit(wl, TICKER, low_limit=9.5, high_limit=12.0)
        proposal = propose(mcp_client, "remove")
        assert proposal["summary"] == f"Remove {TICKER} from the Watchlist. Its Position Target (low 9.5, high 12) is deleted with it."
        assert proposal["current"]["targets"] == {"low_limit": 9.5, "high_limit": 12.0}

    def test_the_audit_log_names_the_tool_and_never_the_ticker(self, mcp_client, writes_on):
        propose(mcp_client, "add")
        rows = sql_rows("SELECT * FROM mcp_audit_log WHERE mcp_name = 'propose_watchlist_change'")
        assert len(rows) == 1
        assert TICKER not in json.dumps(rows)
        assert rows[0]["mcp_method"] == "tools/call" and rows[0]["status"] == 200


class TestConfirm:
    def test_a_confirm_executes_exactly_what_was_stored(self, mcp_client, writes_on, fetches, remote_notifications):
        proposal = propose(mcp_client, "add")
        result = confirm(mcp_client, proposal["proposal_id"])
        assert result["status"] == "executed" and result["change_id"] == proposal["proposal_id"]
        assert result["previous"] == {"watched": False} and result["new"] == {"watched": True, "targets": None}
        assert TICKER in get_watchlist_tickers()
        [row] = log_rows()
        assert row["status"] == "executed" and row["executed_at"] and json.loads(row["previous_state"]) == {"watched": False}
        assert len(fetches) == 3 and all(key[0] == "mcp-watchlist-add" and key[-1] == TICKER for key in fetches)

    def test_one_notification_per_executed_change(self, mcp_client, writes_on, remote_notifications):
        confirm(mcp_client, propose(mcp_client, "add")["proposal_id"])
        [sent] = remote_notifications
        assert sent["source"] == "mcp_write_alert" and sent["type"] == "MCP Write Access"
        assert sent["text"].startswith("Claude (key #") and f"added {TICKER} to the Watchlist." in sent["text"]
        assert sent["nextcloud_text"] == "MCP Write Access: " + sent["text"]

    def test_a_replayed_confirm_changes_nothing_and_answers_the_first_result(self, mcp_client, writes_on, remote_notifications):
        proposal_id = propose(mcp_client, "add")["proposal_id"]
        first = confirm(mcp_client, proposal_id)
        replay = confirm(mcp_client, proposal_id)
        assert replay["status"] == "already_executed" and replay["change_id"] == first["change_id"]
        assert len(remote_notifications) == 1
        assert get_watchlist_tickers().count(TICKER) == 1
        assert len(log_rows()) == 1

    def test_two_confirms_at_once_execute_once(self, writes_on, fetches, remote_notifications):
        who = caller.Caller("key", keys.match(MCP_KEY), "127.0.0.1")
        token = caller.current.set(who)
        try:
            proposal_id = writes.propose_watchlist("add", TICKER)["proposal_id"]
        finally:
            caller.current.reset(token)

        def run():
            caller.current.set(who)
            try:
                return writes.confirm(proposal_id)["status"]
            except Exception as e:
                return str(e)

        with ThreadPoolExecutor(max_workers=6) as pool:
            outcomes = list(pool.map(lambda _: contextvars.copy_context().run(run), range(6)))
        assert outcomes.count("executed") == 1
        assert all(o in ("executed", "already_executed", writes.NO_PROPOSAL) for o in outcomes)
        assert get_watchlist_tickers().count(TICKER) == 1 and len(remote_notifications) == 1

    def test_an_expired_proposal_is_refused_and_marked(self, mcp_client, writes_on, clock):
        proposal_id = propose(mcp_client, "add")["proposal_id"]
        clock.advance(seconds=PROPOSAL_SECONDS + 1)
        assert refusal(mcp_client, "confirm_change", {"proposal_id": proposal_id}) == writes.EXPIRED
        assert TICKER not in get_watchlist_tickers() and log_rows()[0]["status"] == "expired"

    def test_the_last_second_is_still_inside_the_life(self, mcp_client, writes_on, clock):
        proposal_id = propose(mcp_client, "add")["proposal_id"]
        clock.advance(seconds=PROPOSAL_SECONDS - 1)
        assert confirm(mcp_client, proposal_id)["status"] == "executed"

    def test_another_credentials_proposal_looks_like_an_unknown_one(self, mcp_client, writes_on):
        proposal_id = propose(mcp_client, "add")["proposal_id"]
        other = keys.create_key("Second key")
        db_mcp_keys.set_key_write_allowed(other["id"], True)
        unknown = refusal(mcp_client, "confirm_change", {"proposal_id": "ABCDEFGHJK"})
        assert unknown == writes.NO_PROPOSAL
        assert error_text(call(mcp_client, "confirm_change", {"proposal_id": proposal_id}, key=other["key"])) == writes.NO_PROPOSAL
        assert TICKER not in get_watchlist_tickers()

    def test_a_ticker_added_meanwhile_makes_the_confirm_refuse(self, mcp_client, writes_on):
        proposal_id = propose(mcp_client, "add")["proposal_id"]
        from db.account_store import add_watchlist_item
        add_watchlist_item(get_watchlist_account()["id"], TICKER)
        assert refusal(mcp_client, "confirm_change", {"proposal_id": proposal_id}) == writes.CHANGED
        assert log_rows()[0]["status"] == "refused" and log_rows()[0]["failure_reason"] == "state_changed"

    def test_a_target_changed_meanwhile_makes_a_remove_refuse(self, mcp_client, writes_on):
        wl = get_watchlist_account()["id"]
        confirm(mcp_client, propose(mcp_client, "add")["proposal_id"])
        upsert_holding_price_limit(wl, TICKER, low_limit=9.5)
        proposal_id = propose(mcp_client, "remove")["proposal_id"]
        upsert_holding_price_limit(wl, TICKER, low_limit=8.0)
        assert refusal(mcp_client, "confirm_change", {"proposal_id": proposal_id}) == writes.CHANGED
        assert TICKER in get_watchlist_tickers()

    def test_a_restore_in_progress_leaves_the_proposal_pending(self, mcp_client, writes_on):
        proposal_id = propose(mcp_client, "add")["proposal_id"]
        with patch("scheduler_engine.restore_in_progress", return_value=True):
            assert refusal(mcp_client, "confirm_change", {"proposal_id": proposal_id}) == writes.RESTORING
        assert log_rows()[0]["status"] == "pending"
        assert confirm(mcp_client, proposal_id)["status"] == "executed"

    def test_a_failed_write_is_logged_notified_never_and_leaves_no_partial_state(self, mcp_client, writes_on, remote_notifications):
        proposal_id = propose(mcp_client, "add")["proposal_id"]
        with patch("mcp_server.writes.add_watchlist_ticker", return_value=None):
            assert refusal(mcp_client, "confirm_change", {"proposal_id": proposal_id}) == writes.FAILED
        assert log_rows()[0]["status"] == "failed" and log_rows()[0]["failure_reason"] == "write_failed"
        assert TICKER not in get_watchlist_tickers() and remote_notifications == []
        assert refusal(mcp_client, "confirm_change", {"proposal_id": proposal_id}) == writes.NO_PROPOSAL

    def test_an_exception_text_never_reaches_the_client(self, mcp_client, writes_on):
        proposal_id = propose(mcp_client, "add")["proposal_id"]
        with patch("mcp_server.writes.add_watchlist_ticker", side_effect=RuntimeError("secret-path /etc/shadow")):
            text = refusal(mcp_client, "confirm_change", {"proposal_id": proposal_id})
        assert text == writes.FAILED and "shadow" not in text

    def test_the_proposal_id_is_not_case_sensitive(self, mcp_client, writes_on):
        proposal_id = propose(mcp_client, "add")["proposal_id"]
        assert confirm(mcp_client, proposal_id.lower())["status"] == "executed"


class TestRemoveAndUndo:
    def _add(self, client):
        return confirm(client, propose(client, "add")["proposal_id"])

    def test_a_remove_deletes_the_ticker_and_its_target_and_records_both(self, mcp_client, writes_on):
        wl = get_watchlist_account()["id"]
        self._add(mcp_client)
        upsert_holding_price_limit(wl, TICKER, low_limit=9.5, high_limit=12.0)
        result = confirm(mcp_client, propose(mcp_client, "remove")["proposal_id"])
        assert result["previous"]["targets"] == {"low_limit": 9.5, "high_limit": 12.0}
        assert TICKER not in get_watchlist_tickers() and get_holding_price_limits_for_ticker(TICKER) == {}

    def test_undoing_an_add_removes_the_ticker_and_marks_the_change(self, mcp_client, writes_on):
        change = self._add(mcp_client)
        proposal = tool_data(call(mcp_client, "propose_undo", {"change_id": change["change_id"]}))["data"]
        assert proposal["kind"] == "undo_watchlist_add" and "remove" in proposal["summary"]
        assert TICKER in get_watchlist_tickers()
        assert confirm(mcp_client, proposal["proposal_id"])["status"] == "executed"
        assert TICKER not in get_watchlist_tickers()
        assert log_rows("id = ?", change["change_id"])[0]["status"] == "undone"

    def test_undoing_a_remove_adds_the_ticker_back_with_its_target(self, mcp_client, writes_on):
        wl = get_watchlist_account()["id"]
        self._add(mcp_client)
        upsert_holding_price_limit(wl, TICKER, low_limit=9.5, high_limit=12.0)
        removed = confirm(mcp_client, propose(mcp_client, "remove")["proposal_id"])
        proposal = tool_data(call(mcp_client, "propose_undo", {"change_id": removed["change_id"]}))["data"]
        assert "Position Target is restored" in proposal["summary"]
        confirm(mcp_client, proposal["proposal_id"])
        assert TICKER in get_watchlist_tickers()
        assert get_holding_price_limits_for_ticker(TICKER)[wl] == {"low_limit": 9.5, "high_limit": 12.0}

    def test_a_change_can_be_undone_once(self, mcp_client, writes_on):
        change = self._add(mcp_client)
        confirm(mcp_client, tool_data(call(mcp_client, "propose_undo", {"change_id": change["change_id"]}))["data"]["proposal_id"])
        assert refusal(mcp_client, "propose_undo", {"change_id": change["change_id"]}) == writes.NOT_UNDOABLE

    def test_an_undo_is_refused_when_the_data_changed_since(self, mcp_client, writes_on):
        wl = get_watchlist_account()["id"]
        change = self._add(mcp_client)
        upsert_holding_price_limit(wl, TICKER, low_limit=9.5)
        assert refusal(mcp_client, "propose_undo", {"change_id": change["change_id"]}) == writes.CHANGED

    def test_an_undo_confirm_is_refused_when_the_data_changed_after_the_proposal(self, mcp_client, writes_on):
        change = self._add(mcp_client)
        proposal_id = tool_data(call(mcp_client, "propose_undo", {"change_id": change["change_id"]}))["data"]["proposal_id"]
        from db.account_store import remove_watchlist_ticker
        remove_watchlist_ticker(get_watchlist_account()["id"], TICKER)
        assert refusal(mcp_client, "confirm_change", {"proposal_id": proposal_id}) == writes.CHANGED
        assert log_rows("id = ?", change["change_id"])[0]["status"] == "executed"

    def test_only_an_executed_change_of_the_same_credential_can_be_undone(self, mcp_client, writes_on):
        pending = propose(mcp_client, "add")["proposal_id"]
        assert refusal(mcp_client, "propose_undo", {"change_id": pending}) == writes.NOT_UNDOABLE
        assert refusal(mcp_client, "propose_undo", {"change_id": "ABCDEFGHJK"}) == writes.NOT_UNDOABLE

    def test_an_undo_is_itself_a_proposal_and_changes_nothing_until_confirmed(self, mcp_client, writes_on):
        change = self._add(mcp_client)
        before = table_counts()
        tool_data(call(mcp_client, "propose_undo", {"change_id": change["change_id"]}))
        assert table_counts() == before and TICKER in get_watchlist_tickers()


class TestCaps:
    def test_proposals_per_hour(self, mcp_client, writes_on, limits, remote_notifications):
        limits["proposals_per_hour"] = 2
        limits["pending_max"] = 50
        propose(mcp_client, "add", "ZZCAPA")
        propose(mcp_client, "add", "ZZCAPB")
        assert refusal(mcp_client, "propose_watchlist_change", {"action": "add", "ticker": "ZZCAPC"}) == writes._CAP_TEXT["proposals_per_hour"]
        assert [r["status"] for r in log_rows()].count("refused") == 1
        assert any("Changes from key #" in n["text"] for n in remote_notifications)

    def test_a_refusal_row_is_written_once_per_window_so_a_loop_cannot_flood_the_table(self, mcp_client, writes_on, limits):
        limits["proposals_per_hour"] = 1
        propose(mcp_client, "add", "ZZCAPA")
        for _ in range(5):
            refusal(mcp_client, "propose_watchlist_change", {"action": "add", "ticker": "ZZCAPB"})
        assert [r["status"] for r in log_rows()].count("refused") == 1

    def test_the_proposals_an_hour_ago_do_not_count(self, mcp_client, writes_on, limits, clock):
        limits["proposals_per_hour"] = 1
        limits["pending_max"] = 50
        propose(mcp_client, "add", "ZZCAPA")
        clock.advance(hours=1, seconds=1)
        assert propose(mcp_client, "add", "ZZCAPB")["summary"].startswith("Add ZZCAPB")

    def test_pending_proposals(self, mcp_client, writes_on, limits):
        limits["pending_max"] = 2
        propose(mcp_client, "add", "ZZCAPA")
        propose(mcp_client, "add", "ZZCAPB")
        assert refusal(mcp_client, "propose_watchlist_change", {"action": "add", "ticker": "ZZCAPC"}) == writes._CAP_TEXT["pending_max"]

    def test_an_expired_proposal_no_longer_counts_as_pending(self, mcp_client, writes_on, limits, clock):
        limits["pending_max"] = 1
        propose(mcp_client, "add", "ZZCAPA")
        clock.advance(seconds=PROPOSAL_SECONDS + 1)
        assert propose(mcp_client, "add", "ZZCAPB")

    def test_changes_per_day_and_the_next_day_starts_fresh(self, mcp_client, writes_on, limits, clock):
        limits["changes_per_day"] = 1
        limits["pending_max"] = 50
        confirm(mcp_client, propose(mcp_client, "add")["proposal_id"])
        assert refusal(mcp_client, "propose_watchlist_change", {"action": "add", "ticker": OTHER}) == writes._CAP_TEXT["changes_per_day"]
        clock.advance(days=1)
        assert propose(mcp_client, "add", OTHER)

    def test_a_confirm_is_also_held_to_the_daily_cap(self, mcp_client, writes_on, limits):
        limits["pending_max"] = 50
        first = propose(mcp_client, "add")["proposal_id"]
        second = propose(mcp_client, "add", OTHER)["proposal_id"]
        limits["changes_per_day"] = 1
        confirm(mcp_client, first)
        assert refusal(mcp_client, "confirm_change", {"proposal_id": second}) == writes._CAP_TEXT["changes_per_day"]
        assert OTHER not in get_watchlist_tickers()

    def test_adds_per_day_do_not_limit_removes(self, mcp_client, writes_on, limits):
        limits["adds_per_day"] = 1
        limits["pending_max"] = 50
        confirm(mcp_client, propose(mcp_client, "add")["proposal_id"])
        assert refusal(mcp_client, "propose_watchlist_change", {"action": "add", "ticker": OTHER}) == writes._CAP_TEXT["adds_per_day"]
        assert confirm(mcp_client, propose(mcp_client, "remove")["proposal_id"])["status"] == "executed"

    def test_an_undo_that_re_adds_counts_as_an_add(self, mcp_client, writes_on, limits):
        limits["pending_max"] = 50
        added = confirm(mcp_client, propose(mcp_client, "add")["proposal_id"])
        removed = confirm(mcp_client, propose(mcp_client, "remove")["proposal_id"])
        limits["adds_per_day"] = 1
        assert refusal(mcp_client, "propose_undo", {"change_id": removed["change_id"]}) == writes._CAP_TEXT["adds_per_day"]
        assert added["status"] == "executed"


class TestLimitsAreLive:
    def test_proposals_per_hour_follows_the_config_without_a_restart(self, mcp_client, writes_on, limits):
        limits["pending_max"] = 50
        limits["proposals_per_hour"] = 1
        propose(mcp_client, "add", "ZZCAPA")
        assert refusal(mcp_client, "propose_watchlist_change", {"action": "add", "ticker": "ZZCAPB"}) == writes._CAP_TEXT["proposals_per_hour"]
        limits["proposals_per_hour"] = 2
        assert propose(mcp_client, "add", "ZZCAPB")
        limits["proposals_per_hour"] = 1
        assert refusal(mcp_client, "propose_watchlist_change", {"action": "add", "ticker": "ZZCAPC"}) == writes._CAP_TEXT["proposals_per_hour"]

    def test_pending_max_follows_the_config_without_a_restart(self, mcp_client, writes_on, limits):
        limits["pending_max"] = 1
        propose(mcp_client, "add", "ZZCAPA")
        assert refusal(mcp_client, "propose_watchlist_change", {"action": "add", "ticker": "ZZCAPB"}) == writes._CAP_TEXT["pending_max"]
        limits["pending_max"] = 2
        assert propose(mcp_client, "add", "ZZCAPB")
        limits["pending_max"] = 1
        assert refusal(mcp_client, "propose_watchlist_change", {"action": "add", "ticker": "ZZCAPC"}) == writes._CAP_TEXT["pending_max"]

    def test_changes_per_day_follows_the_config_without_a_restart(self, mcp_client, writes_on, limits):
        limits["pending_max"] = 50
        limits["changes_per_day"] = 1
        confirm(mcp_client, propose(mcp_client, "add")["proposal_id"])
        assert refusal(mcp_client, "propose_watchlist_change", {"action": "add", "ticker": OTHER}) == writes._CAP_TEXT["changes_per_day"]
        limits["changes_per_day"] = 2
        assert propose(mcp_client, "add", OTHER)
        limits["changes_per_day"] = 1
        assert refusal(mcp_client, "propose_watchlist_change", {"action": "remove", "ticker": TICKER}) == writes._CAP_TEXT["changes_per_day"]

    def test_adds_per_day_follows_the_config_without_a_restart(self, mcp_client, writes_on, limits):
        limits["pending_max"] = 50
        limits["adds_per_day"] = 1
        confirm(mcp_client, propose(mcp_client, "add")["proposal_id"])
        assert refusal(mcp_client, "propose_watchlist_change", {"action": "add", "ticker": OTHER}) == writes._CAP_TEXT["adds_per_day"]
        limits["adds_per_day"] = 2
        assert propose(mcp_client, "add", OTHER)
        limits["adds_per_day"] = 1
        assert refusal(mcp_client, "propose_watchlist_change", {"action": "add", "ticker": "ZZCAPC"}) == writes._CAP_TEXT["adds_per_day"]

    def test_proposal_seconds_sets_the_life_of_each_new_proposal(self, mcp_client, writes_on, limits, clock):
        limits["pending_max"] = 50
        limits["proposal_seconds"] = 60
        short = propose(mcp_client, "add", "ZZCAPA")["proposal_id"]
        limits["proposal_seconds"] = 3600
        long = propose(mcp_client, "add", "ZZCAPB")["proposal_id"]
        clock.advance(seconds=61)
        assert refusal(mcp_client, "confirm_change", {"proposal_id": short}) == writes.EXPIRED
        assert confirm(mcp_client, long)["status"] == "executed"

    def test_zero_adds_per_day_refuses_adds_but_allows_removes(self, mcp_client, writes_on, limits):
        limits["pending_max"] = 50
        confirm(mcp_client, propose(mcp_client, "add")["proposal_id"])
        limits["adds_per_day"] = 0
        assert refusal(mcp_client, "propose_watchlist_change", {"action": "add", "ticker": OTHER}) == writes._CAP_TEXT["adds_per_day"]
        assert confirm(mcp_client, propose(mcp_client, "remove")["proposal_id"])["status"] == "executed"

    @pytest.mark.parametrize("key,cap", [("proposals_per_hour", "proposals_per_hour"), ("pending_max", "pending_max"), ("changes_per_day", "changes_per_day")])
    def test_zero_blocks_that_action(self, mcp_client, writes_on, limits, key, cap):
        limits[key] = 0
        assert refusal(mcp_client, "propose_watchlist_change", {"action": "add", "ticker": TICKER}) == writes._CAP_TEXT[cap]
        assert TICKER not in get_watchlist_tickers()



class TestRemoteGrant:
    def test_a_grant_without_the_flag_is_refused_and_with_it_may_write(self, remote, monkeypatch, fetches):
        monkeypatch.setattr("mcp_server.writes.load_config", lambda: {"MCP_WRITES_ENABLED": True, "MCP_WRITE_LIMITS": dict(MCP_WRITE_LIMIT_DEFAULTS)})
        access = tokens(remote)["access_token"]
        arguments = {"action": "add", "ticker": TICKER}
        assert error_text(call(remote.http, "propose_watchlist_change", arguments, key=access)) == writes.NOT_ENABLED
        grant_id = sql("SELECT id FROM mcp_oauth_grants")[0]["id"]
        assert sql("SELECT write_allowed FROM mcp_oauth_grants")[0]["write_allowed"] == 0
        conn = get_connection()
        try:
            conn.execute("UPDATE mcp_oauth_grants SET write_allowed = 1 WHERE id = ?", (grant_id,))
            conn.commit()
        finally:
            conn.close()
        proposal = tool_data(call(remote.http, "propose_watchlist_change", arguments, key=access))["data"]
        assert sql("SELECT credential_kind, credential_id FROM mcp_write_log") == [{"credential_kind": "oauth", "credential_id": grant_id}]
        assert tool_data(call(remote.http, "confirm_change", {"proposal_id": proposal["proposal_id"]}, key=access))["data"]["status"] == "executed"
        assert TICKER in get_watchlist_tickers()

    def test_a_named_key_cannot_confirm_a_grants_proposal(self, remote, mcp_client, monkeypatch, fetches):
        monkeypatch.setattr("mcp_server.writes.load_config", lambda: {"MCP_WRITES_ENABLED": True, "MCP_WRITE_LIMITS": dict(MCP_WRITE_LIMIT_DEFAULTS)})
        db_mcp_keys.set_key_write_allowed(keys.match(MCP_KEY), True)
        access = tokens(remote)["access_token"]
        conn = get_connection()
        try:
            conn.execute("UPDATE mcp_oauth_grants SET write_allowed = 1")
            conn.commit()
        finally:
            conn.close()
        proposal = tool_data(call(remote.http, "propose_watchlist_change", {"action": "add", "ticker": TICKER}, key=access))["data"]
        lan = {"Host": "127.0.0.1"}
        assert error_text(call(mcp_client, "confirm_change", {"proposal_id": proposal["proposal_id"]}, headers=lan)) == writes.NO_PROPOSAL

    def test_a_revoked_grant_can_no_longer_confirm(self, remote, monkeypatch, fetches):
        from mcp_server import oauth
        monkeypatch.setattr("mcp_server.writes.load_config", lambda: {"MCP_WRITES_ENABLED": True, "MCP_WRITE_LIMITS": dict(MCP_WRITE_LIMIT_DEFAULTS)})
        access = tokens(remote)["access_token"]
        grant_id = sql("SELECT id FROM mcp_oauth_grants")[0]["id"]
        conn = get_connection()
        try:
            conn.execute("UPDATE mcp_oauth_grants SET write_allowed = 1")
            conn.commit()
        finally:
            conn.close()
        proposal = tool_data(call(remote.http, "propose_watchlist_change", {"action": "add", "ticker": TICKER}, key=access))["data"]
        assert oauth.revoke_grant(grant_id)
        response = call(remote.http, "confirm_change", {"proposal_id": proposal["proposal_id"]}, key=access)
        assert response.status_code == 401
        assert TICKER not in get_watchlist_tickers()


class TestRegistryAndRetention:
    def test_the_notification_source_has_its_canonical_label_and_is_served_to_clients(self):
        import notification_engine as ne
        meta = ne.NOTIFICATION_SOURCES["mcp_write_alert"]
        assert meta["label"] == "MCP Write Access" and meta["job_id"] == "claude_mcp_server_source"
        assert meta["alert_type"] == "MCP Write Access" and "MCP Write Access" in ne.ALERT_MESSAGE_TYPES

    def test_the_global_switch_is_off_by_default_and_not_in_the_settings_schema(self):
        import config
        from api.routes_system import SettingsConfig
        assert config.DEFAULT_CONFIG["MCP_WRITES_ENABLED"] is False
        assert "MCP_WRITES_ENABLED" not in SettingsConfig.model_fields
        assert "MCP_WRITE_LIMITS" not in SettingsConfig.model_fields

    def test_old_rows_are_pruned_and_new_ones_kept(self, mcp_client, writes_on):
        propose(mcp_client, "add")
        mcp_write_log.insert({
            "id": "OLDROW0001", "kind": "watchlist_add", "credential_kind": "key", "credential_id": 1, "ticker": TICKER,
            "expected": "{}", "proposed": "{}", "summary": "old", "status": "executed", "created_at": "2020-01-01 00:00:00", "expires_at": "2020-01-01 00:10:00",
        })
        assert mcp_write_log.prune_older_than("2026-01-01 00:00:00") == 1
        assert len(log_rows()) == 1
