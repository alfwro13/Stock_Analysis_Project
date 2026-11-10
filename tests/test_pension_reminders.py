"""Tests for Pension Reminders: reminder CRUD, the per-reminder job, pending items and their resolution."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def _json(resp) -> dict:
    return resp.json()


def _live_job_ids():
    import scheduler_engine
    return {j.id for j in scheduler_engine.scheduler.get_jobs()}


@pytest.fixture
def pension(client):
    from db import database as _db
    from db.account_reminders import get_reminders
    from scheduler_jobs import unregister_account_reminder_job

    account_id = _json(client.post("/api/accounts", json={
        "name": "Reminder Pension", "currency": "GBP", "initial_cash": 0, "account_type": "Pension",
    }))["id"]
    client.post(f"/api/accounts/{account_id}/price-history/import-csv", json={
        "csv_text": "date;marketPrice\n2026-01-01;1.00\n2026-11-01;2.00\n",
    })
    yield account_id

    conn = None
    try:
        for reminder in get_reminders(account_id):
            unregister_account_reminder_job(reminder["id"])
        conn = _db.get_connection()
        conn.execute("DELETE FROM account_reminders WHERE account_id = ?", (account_id,))
        conn.execute("DELETE FROM account_reminder_pending WHERE account_id = ?", (account_id,))
        conn.commit()
    finally:
        if conn:
            conn.close()
    _db.soft_delete_account(account_id)


def _add_reminder(client, account_id, **overrides) -> int:
    body = {"reminder_type": "pay_in", "cadence": "monthly", "day_of_month": 1}
    body.update(overrides)
    resp = client.post(f"/api/accounts/{account_id}/reminders", json=body)
    assert resp.status_code == 200, resp.text
    return _json(resp)["id"]


def _stage_pending(account_id, reminder_id, reminder_type="pay_in", due_date="2026-11-01") -> int:
    from db.account_reminders import create_pending_reminder
    return create_pending_reminder({"id": reminder_id, "account_id": account_id, "reminder_type": reminder_type}, due_date)


@pytest.mark.api
def test_reminder_defaults_to_disabled_and_registers_no_job(client, pension):
    reminder_id = _add_reminder(client, pension)

    reminders = _json(client.get(f"/api/accounts/{pension}/reminders"))["reminders"]
    assert [(r["id"], r["enabled"], r["cadence"]) for r in reminders] == [(reminder_id, 0, "monthly")]
    assert f"account_reminder_{reminder_id}_job" not in _live_job_ids()


@pytest.mark.api
def test_enabling_registers_job_and_disabling_or_deleting_removes_it(client, pension):
    reminder_id = _add_reminder(client, pension, enabled=True)
    job_id = f"account_reminder_{reminder_id}_job"
    assert job_id in _live_job_ids()

    resp = client.put(f"/api/accounts/{pension}/reminders/{reminder_id}", json={
        "reminder_type": "admin_fee", "cadence": "monthly", "day_of_month": 15, "enabled": False,
    })
    assert resp.status_code == 200
    assert job_id not in _live_job_ids()
    saved = _json(client.get(f"/api/accounts/{pension}/reminders"))["reminders"][0]
    assert (saved["reminder_type"], saved["day_of_month"], saved["enabled"]) == ("admin_fee", 15, 0)

    client.put(f"/api/accounts/{pension}/reminders/{reminder_id}", json={
        "reminder_type": "admin_fee", "cadence": "monthly", "day_of_month": 15, "enabled": True,
    })
    assert job_id in _live_job_ids()

    assert client.delete(f"/api/accounts/{pension}/reminders/{reminder_id}").status_code == 200
    assert job_id not in _live_job_ids()
    assert _json(client.get(f"/api/accounts/{pension}/reminders"))["reminders"] == []


@pytest.mark.api
@pytest.mark.parametrize("body", [
    {"reminder_type": "top_up", "day_of_month": 1},
    {"reminder_type": "pay_in", "cadence": "weekly", "day_of_month": 1},
    {"reminder_type": "pay_in", "day_of_month": 0},
    {"reminder_type": "pay_in", "day_of_month": 32},
])
def test_reminder_validation_rejects_bad_input(client, pension, body):
    resp = client.post(f"/api/accounts/{pension}/reminders", json=body)
    assert resp.status_code == 422
    assert _json(resp)["status"] == "error"


@pytest.mark.api
def test_reminder_endpoints_reject_non_pension_account(client):
    from db import database as _db
    trading_id = _json(client.post("/api/accounts", json={"name": "Reminder Trading", "currency": "GBP", "initial_cash": 0}))["id"]
    try:
        assert client.get(f"/api/accounts/{trading_id}/reminders").status_code == 400
        resp = client.post(f"/api/accounts/{trading_id}/reminders", json={"reminder_type": "pay_in", "day_of_month": 1})
        assert resp.status_code == 400
    finally:
        _db.soft_delete_account(trading_id)


@pytest.mark.api
def test_reminder_of_another_account_is_not_found(client, pension):
    from db import database as _db
    other_id = _db.create_account("Other Reminder Pension", "GBP", account_type="Pension")
    try:
        reminder_id = _add_reminder(client, other_id)
        resp = client.put(f"/api/accounts/{pension}/reminders/{reminder_id}", json={
            "reminder_type": "pay_in", "cadence": "monthly", "day_of_month": 2, "enabled": False,
        })
        assert resp.status_code == 404
        assert client.delete(f"/api/accounts/{pension}/reminders/{reminder_id}").status_code == 404
    finally:
        conn = _db.get_connection()
        try:
            conn.execute("DELETE FROM account_reminders WHERE account_id = ?", (other_id,))
            conn.commit()
        finally:
            conn.close()
        _db.soft_delete_account(other_id)


@pytest.mark.db
def test_job_runner_stages_a_labelled_pending_item_and_notifies(client, pension):
    import scheduler_jobs
    from accounts.account_reminders_engine import labelled_pending_reminders

    reminder_id = _add_reminder(client, pension, enabled=True)
    with patch("accounts.account_reminders_engine.time_engine.now_local", lambda: datetime(2026, 11, 1, 8, 0, tzinfo=timezone.utc)), \
            patch("scheduler_jobs.notify") as notify_mock:
        scheduler_jobs._run_account_reminder_job(reminder_id)

    pending = labelled_pending_reminders(pension)
    assert [(p["title"], p["due_date"], p["status"]) for p in pending] == [("November 2026 Pay In", "2026-11-01", "pending")]
    notify_mock.assert_called_once()
    assert notify_mock.call_args.args[0] == "account_reminder_status"
    assert "November 2026 Pay In" in notify_mock.call_args.args[2]


@pytest.mark.db
def test_job_runner_stacks_missed_months_and_ignores_a_double_fire(client, pension):
    import scheduler_jobs
    from accounts.account_reminders_engine import labelled_pending_reminders

    reminder_id = _add_reminder(client, pension, reminder_type="admin_fee", enabled=True)
    clock = {"now": datetime(2026, 11, 1, 8, 0, tzinfo=timezone.utc)}
    with patch("accounts.account_reminders_engine.time_engine.now_local", lambda: clock["now"]), \
            patch("scheduler_jobs.notify") as notify_mock:
        scheduler_jobs._run_account_reminder_job(reminder_id)
        scheduler_jobs._run_account_reminder_job(reminder_id)
        assert notify_mock.call_count == 1
        clock["now"] = datetime(2026, 12, 1, 8, 0, tzinfo=timezone.utc)
        scheduler_jobs._run_account_reminder_job(reminder_id)
        assert notify_mock.call_count == 2

    assert [p["title"] for p in labelled_pending_reminders(pension)] == ["November 2026 Admin Fee", "December 2026 Admin Fee"]


@pytest.mark.db
def test_job_runner_does_nothing_for_a_disabled_reminder(client, pension):
    import scheduler_jobs
    from accounts.account_reminders_engine import labelled_pending_reminders

    reminder_id = _add_reminder(client, pension, enabled=False)
    with patch("scheduler_jobs.notify") as notify_mock:
        scheduler_jobs._run_account_reminder_job(reminder_id)

    assert labelled_pending_reminders(pension) == []
    notify_mock.assert_not_called()


@pytest.mark.api
def test_pending_reminders_appear_on_the_tile_list_and_the_pension_page(client, pension):
    reminder_id = _add_reminder(client, pension)
    _stage_pending(pension, reminder_id)

    accounts = _json(client.get("/api/accounts"))["accounts"]
    tile = next(a for a in accounts if a["id"] == pension)
    assert [p["title"] for p in tile["pending_actions"]] == ["November 2026 Pay In"]

    page = client.get(f"/accounts/{pension}/pension")
    assert page.status_code == 200
    assert "November 2026 Pay In" in page.text
    assert "recordPensionReminder" in page.text


@pytest.mark.api
def test_dismiss_resolves_once_and_only_for_its_own_account(client, pension):
    reminder_id = _add_reminder(client, pension)
    pending_id = _stage_pending(pension, reminder_id)

    from db import database as _db
    other_id = _db.create_account("Other Dismiss Pension", "GBP", account_type="Pension")
    try:
        resp = client.post(f"/api/accounts/{other_id}/reminders/pending/{pending_id}/dismiss")
        assert resp.status_code == 400
    finally:
        _db.soft_delete_account(other_id)

    assert client.post(f"/api/accounts/{pension}/reminders/pending/{pending_id}/dismiss").status_code == 200
    resp = client.post(f"/api/accounts/{pension}/reminders/pending/{pending_id}/dismiss")
    assert resp.status_code == 400
    assert "already been dismissed" in _json(resp)["message"]

    from accounts.account_reminders_engine import labelled_pending_reminders
    assert labelled_pending_reminders(pension) == []


@pytest.mark.api
def test_pay_in_with_pending_id_resolves_the_reminder_with_its_transaction(client, pension):
    reminder_id = _add_reminder(client, pension)
    pending_id = _stage_pending(pension, reminder_id)

    resp = client.post(f"/api/accounts/{pension}/pension/contribution", json={
        "txn_date": "2026-11-01", "amount": 200.0, "reminder_pending_id": pending_id,
    })
    assert resp.status_code == 200, resp.text
    txn_id = _json(resp)["txn_id"]

    from db.account_reminders import get_pending_reminder
    pending = get_pending_reminder(pending_id)
    assert (pending["status"], pending["txn_id"]) == ("done", txn_id)


@pytest.mark.api
def test_admin_fee_with_pending_id_resolves_the_reminder(client, pension):
    client.post(f"/api/accounts/{pension}/pension/contribution", json={"txn_date": "2026-01-01", "amount": 500.0})
    reminder_id = _add_reminder(client, pension, reminder_type="admin_fee")
    pending_id = _stage_pending(pension, reminder_id, "admin_fee")

    resp = client.post(f"/api/accounts/{pension}/pension/fee", json={
        "txn_date": "2026-11-01", "units_removed": 1.0, "reminder_pending_id": pending_id,
    })
    assert resp.status_code == 200, resp.text

    from db.account_reminders import get_pending_reminder
    assert get_pending_reminder(pending_id)["status"] == "done"


@pytest.mark.api
def test_recording_the_wrong_kind_of_task_is_refused_before_any_transaction(client, pension):
    from accounts.accounts_engine import pension_units_as_of
    reminder_id = _add_reminder(client, pension, reminder_type="admin_fee")
    pending_id = _stage_pending(pension, reminder_id, "admin_fee")

    resp = client.post(f"/api/accounts/{pension}/pension/contribution", json={
        "txn_date": "2026-11-01", "amount": 200.0, "reminder_pending_id": pending_id,
    })
    assert resp.status_code == 422
    assert pension_units_as_of(pension, "2026-11-30") == 0

    from db.account_reminders import get_pending_reminder
    assert get_pending_reminder(pending_id)["status"] == "pending"


@pytest.mark.parametrize("day, year, expected", [
    (15, 2026, ["01-15", "02-15", "03-15", "04-15", "05-15", "06-15", "07-15", "08-15", "09-15", "10-15", "11-15", "12-15"]),
    (31, 2026, ["01-31", "02-28", "03-31", "04-30", "05-31", "06-30", "07-31", "08-31", "09-30", "10-31", "11-30", "12-31"]),
    (30, 2026, ["01-30", "02-28", "03-30", "04-30", "05-30", "06-30", "07-30", "08-30", "09-30", "10-30", "11-30", "12-30"]),
    (29, 2028, ["01-29", "02-29", "03-29", "04-29", "05-29", "06-29", "07-29", "08-29", "09-29", "10-29", "11-29", "12-29"]),
    (29, 2026, ["01-29", "02-28", "03-29", "04-29", "05-29", "06-29", "07-29", "08-29", "09-29", "10-29", "11-29", "12-29"]),
])
def test_monthly_trigger_falls_back_to_the_last_day_of_a_short_month(day, year, expected):
    import time_engine
    from scheduler_jobs import _reminder_trigger

    trigger = _reminder_trigger(day)
    cursor = datetime(year, 1, 1, tzinfo=time_engine.get_user_tz())
    fired = []
    for _ in range(12):
        fire_time = trigger.get_next_fire_time(None, cursor)
        assert fire_time.hour == 8
        fired.append(fire_time.strftime("%m-%d"))
        cursor = fire_time + timedelta(days=1)
    assert fired == expected


@pytest.mark.db
def test_reminder_job_is_labelled_and_linked_to_pension_accounts_node(client, pension):
    from scheduler_monitor import build_workflow_graph

    reminder_id = _add_reminder(client, pension, enabled=True)
    graph = build_workflow_graph()
    job_id = f"account_reminder_{reminder_id}_job"
    node = next((n for n in graph["nodes"] if n["id"] == job_id), None)
    assert node is not None
    assert node["label"] == "Pension Reminders — Reminder Pension (Pay In)"
    assert node["produces"] == ["account_reminder_pending"]
    assert (job_id, "pension_accounts_node") in {(e["from"], e["to"]) for e in graph["edges"]}
