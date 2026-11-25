"""The Google News Fetcher job: the runner in scheduler_jobs, its registration from SCHEDULING.GFETCHER, and the two API routes."""
from unittest.mock import patch

import pytest
from starlette.background import BackgroundTasks

import scheduler_engine
import scheduler_jobs
from news.gfetcher.pipeline import FetchSettings, RunResult
from scheduler_manifest import JOB_GRAPH, display_name_for_config_key

from .helpers import TABLES

pytestmark = pytest.mark.restore_tables(*TABLES, "scheduler_run_log", "system_notifications")

ENABLED = {"SCHEDULING": {"GFETCHER": {"ENABLED": True, "FREQUENCY": "mon-fri", "START_TIME": "07:00", "END_TIME": "22:00",
                                       "INTERVAL_MINUTES": 20, "SCOPES": ["indexes"], "MAX_TICKERS_PER_RUN": 4}}}


def test_the_runner_runs_with_the_configured_settings_and_notifies_problems():
    sent = []
    result = RunResult("ok", refused_routes=[{"route": "ipv4", "reason": "HTTP 429", "until": None}])
    with patch("scheduler_jobs.load_config", return_value=ENABLED), \
         patch("news.gfetcher.run_fetch", return_value=result) as run, \
         patch("scheduler_jobs.log_sched_notification", side_effect=lambda t, m: sent.append((t, m))), \
         patch("scheduler_jobs.record_job_run") as recorded:
        scheduler_jobs.run_gfetcher_job()
    settings = run.call_args.args[0]
    assert isinstance(settings, FetchSettings) and settings.scopes == ("indexes",) and settings.max_tickers_per_run == 4
    assert [t for t, _ in sent] == ["Warning"] and "refused ipv4" in sent[0][1]
    recorded.assert_called_once_with("gfetcher_job")


def test_a_quiet_run_sends_no_notification():
    with patch("scheduler_jobs.load_config", return_value=ENABLED), \
         patch("news.gfetcher.run_fetch", return_value=RunResult("ok", articles_new=2)), \
         patch("scheduler_jobs.log_sched_notification") as notify, patch("scheduler_jobs.record_job_run"):
        scheduler_jobs.run_gfetcher_job()
    notify.assert_not_called()


def test_a_crash_is_an_error_status_naming_the_fetcher():
    with patch("scheduler_jobs.load_config", return_value=ENABLED), \
         patch("news.gfetcher.run_fetch", side_effect=RuntimeError("boom")), \
         patch("scheduler_jobs.log_sched_notification") as notify, patch("scheduler_jobs.record_job_run"):
        scheduler_jobs.run_gfetcher_job()
    notify.assert_called_once_with("Error", "Google News Fetcher failed: boom")


def test_the_job_is_registered_only_when_enabled():
    try:
        with patch("scheduler_engine.load_config", return_value=ENABLED):
            scheduler_engine.reload_scheduler()
            job = scheduler_engine.scheduler.get_job("gfetcher_job")
        fields = {f.name: str(f) for f in job.trigger.fields}
        assert (fields["day_of_week"], fields["hour"], fields["minute"]) == ("mon-fri", "7-22", "11-59/20")
        assert job.trigger.jitter == 120 and str(job.trigger.timezone)
        with patch("scheduler_engine.load_config", return_value={"SCHEDULING": {"GFETCHER": {"ENABLED": False}}}):
            scheduler_engine.reload_scheduler()
            assert scheduler_engine.scheduler.get_job("gfetcher_job") is None
    finally:
        scheduler_engine.reload_scheduler()


@pytest.mark.parametrize("interval,minute,runs_per_hour", [(15, "11-59/15", 4), (20, "11-59/20", 3), (30, "11-59/30", 2), (60, "11", 1)])
def test_every_interval_settings_offers_builds_a_valid_trigger(interval, minute, runs_per_hour):
    cfg = {"SCHEDULING": {"GFETCHER": {**ENABLED["SCHEDULING"]["GFETCHER"], "INTERVAL_MINUTES": interval}}}
    try:
        with patch("scheduler_engine.load_config", return_value=cfg), patch("scheduler_engine.logger") as log:
            scheduler_engine.reload_scheduler()
            job = scheduler_engine.scheduler.get_job("gfetcher_job")
        assert not any("Google News Fetcher" in str(c) for c in log.error.call_args_list)
        assert job is not None
        assert {f.name: str(f) for f in job.trigger.fields}["minute"] == minute
        from datetime import datetime, timedelta
        start = datetime(2026, 12, 1, 9, 0, tzinfo=job.trigger.timezone)
        fires, t = [], start
        while True:
            t = job.trigger.get_next_fire_time(None, t + timedelta(seconds=1)) if fires else job.trigger.get_next_fire_time(None, t)
            if t >= start + timedelta(hours=1):
                break
            fires.append(t.minute)
        assert len(fires) == runs_per_hour and 11 <= fires[0] <= 13
    finally:
        scheduler_engine.reload_scheduler()


def test_the_manifest_names_the_job_once():
    assert JOB_GRAPH["gfetcher_job"]["label"] == "Google News Fetcher"
    assert display_name_for_config_key("GFETCHER") == "Google News Fetcher"
    assert "google_finance_news" in JOB_GRAPH["google_finance_source"]["produces"]


def test_run_now_queues_the_job_when_enabled(client):
    with patch("api.routes_gfetcher._enabled", return_value=True), \
         patch.object(BackgroundTasks, "add_task") as add_task:
        resp = client.post("/api/gfetcher/run-now")
    assert resp.status_code == 200 and resp.json()["status"] == "success"
    assert add_task.call_args.args[1:] == ("gfetcher_job", scheduler_jobs.run_gfetcher_job)


def test_run_now_refuses_while_disabled(client):
    with patch("api.routes_gfetcher._enabled", return_value=False), \
         patch.object(BackgroundTasks, "add_task") as add_task:
        resp = client.post("/api/gfetcher/run-now")
    assert resp.status_code == 409 and resp.json()["status"] == "disabled"
    add_task.assert_not_called()


def test_status_route_answers_the_health_read(client):
    resp = client.get("/api/gfetcher/status")
    data = resp.json()
    assert resp.status_code == 200 and data["status"] == "success"
    assert {"enabled", "last_run", "routes", "requests_24h", "refusals_24h", "articles", "publishers"} <= set(data)


VALID_BLOCK = {"ENABLED": False, "FREQUENCY": "*", "START_TIME": "06:30", "END_TIME": "23:00", "INTERVAL_MINUTES": 60,
               "SCOPES": ["portfolio", "indexes"], "MAX_TICKERS_PER_RUN": 8, "MAX_REQUESTS_PER_DAY": 200,
               "GOOGLE_DELAY_SECONDS": 4.5, "FETCH_FULL_TEXT": True, "MAX_PER_TICKER": 12, "MAX_AGE_DAYS": 5}


def test_settings_save_round_trips_the_block(client, step_up_headers):
    import config as _config
    resp = client.post("/api/settings", json={"SCHEDULING": {"GFETCHER": VALID_BLOCK}}, headers=step_up_headers)
    assert resp.status_code == 200, resp.text
    assert _config.load_config()["SCHEDULING"]["GFETCHER"] == VALID_BLOCK


@pytest.mark.parametrize("field,value", [
    ("INTERVAL_MINUTES", 45), ("GOOGLE_DELAY_SECONDS", 1), ("GOOGLE_DELAY_SECONDS", "Infinity"),
    ("SCOPES", ["portfolio", "universe"]), ("MAX_REQUESTS_PER_DAY", 5000), ("START_TIME", "7am"), ("FREQUENCY", "sat"),
])
def test_settings_save_refuses_a_bad_value(client, step_up_headers, field, value):
    resp = client.post("/api/settings", json={"SCHEDULING": {"GFETCHER": {**VALID_BLOCK, field: value}}}, headers=step_up_headers)
    assert resp.status_code == 422


def test_the_probe_runner_reports_its_summary():
    summary = {"status": "ok", "reason": None, "checked": 2, "found": ["^FTSE -> UKX:INDEXFTSE"], "not_found": ["^IBEX"]}
    with patch("scheduler_jobs.load_config", return_value=ENABLED), \
         patch("news.gfetcher.probe_symbols", return_value=summary), \
         patch("scheduler_jobs.log_sched_notification") as notify:
        scheduler_jobs.run_gfetcher_probe()
    kind, text = notify.call_args.args
    assert kind == "Success" and "2 checked, 1 found, 1 without a Google page (^IBEX)" in text


def test_the_probe_route_queues_refuses_while_disabled_and_while_the_fetcher_runs(client):
    with patch("api.routes_gfetcher._enabled", return_value=True), patch.object(BackgroundTasks, "add_task") as add_task:
        assert client.post("/api/gfetcher/probe").status_code == 200
    assert add_task.call_args.args[1:] == ("gfetcher_probe_source", scheduler_jobs.run_gfetcher_probe)
    with patch("api.routes_gfetcher._enabled", return_value=False), patch.object(BackgroundTasks, "add_task") as add_task:
        assert client.post("/api/gfetcher/probe").status_code == 409
    add_task.assert_not_called()
    with patch("scheduler_engine.is_job_running", side_effect=lambda job_id: job_id == "gfetcher_job"), \
         patch.object(BackgroundTasks, "add_task") as add_task:
        resp = client.post("/api/gfetcher/probe")
    assert resp.status_code == 409 and resp.json()["status"] == "busy"
    add_task.assert_not_called()


def test_the_symbols_route_lists_the_mappings(client):
    from datetime import datetime, timezone
    from news.gfetcher import store
    store.save_symbol("^FTSE", "UKX", "INDEXFTSE", "index", datetime.now(timezone.utc), "FTSE 100", "found")
    rows = client.get("/api/gfetcher/symbols").json()["symbols"]
    assert {"ticker": "^FTSE", "google_symbol": "UKX", "google_exchange": "INDEXFTSE"}.items() <= next(r for r in rows if r["ticker"] == "^FTSE").items()


def test_publisher_rules_can_be_set_listed_and_cleared(client):
    resp = client.post("/api/gfetcher/publishers", json={"domain": " WWW.Example-News.com ", "policy": "block_paywall"})
    assert resp.status_code == 200 and resp.json()["publisher"]["domain"] == "example-news.com"
    listed = {r["domain"]: r for r in client.get("/api/gfetcher/publishers").json()["publishers"]}
    assert (listed["example-news.com"]["policy"], listed["example-news.com"]["source"]) == ("block_paywall", "manual")
    cleared = client.delete("/api/gfetcher/publishers/example-news.com")
    assert cleared.status_code == 200 and cleared.json()["publisher"]["source"] == "auto"
    assert client.delete("/api/gfetcher/publishers/nothing-here.example").status_code == 404


@pytest.mark.parametrize("body", [
    {"domain": "not a domain", "policy": "allow"}, {"domain": "https://x.com/path", "policy": "allow"},
    {"domain": "example.com", "policy": "maybe"}, {"domain": "a" * 300 + ".com", "policy": "allow"},
])
def test_a_bad_publisher_rule_is_refused(client, body):
    assert client.post("/api/gfetcher/publishers", json=body).status_code == 422
