"""publishers: paywalled domains are dropped, bot-blocking ones kept headline-only, learned blocks retried after 14 days."""
from datetime import timedelta

import pytest

from db.database import get_connection
from news.gfetcher import client, publishers
from news.gfetcher.publishers import DROP, FETCH, HEADLINE_ONLY, PublisherPolicy

from .helpers import TABLES

pytestmark = pytest.mark.restore_tables(*TABLES)


@pytest.fixture(autouse=True)
def _clean():
    conn = get_connection()
    try:
        conn.execute("DELETE FROM gfetcher_publishers")
        conn.commit()
    finally:
        conn.close()


NOW = client._now()


@pytest.mark.parametrize("url,decision", [
    ("https://www.barrons.com/a", DROP),
    ("https://www.benzinga.com/a", HEADLINE_ONLY),
    ("https://uk.investing.com/a", HEADLINE_ONLY),
    ("https://www.zacks.com/a", FETCH),
    ("https://never-seen.example/a", FETCH),
])
def test_seeded_rules(url, decision):
    assert PublisherPolicy(NOW).decide(url) == decision


def test_the_seed_is_written_once_and_manual_rules_are_not_relearned():
    policy = PublisherPolicy(NOW)
    policy.record("https://www.benzinga.com/a", "full")
    policy.save()
    again = PublisherPolicy(NOW)
    assert again.rows["benzinga.com"]["full_count"] == 1
    assert again.decide("https://www.benzinga.com/b") == HEADLINE_ONLY


@pytest.mark.parametrize("statuses,decision", [
    (["paywall", "paywall"], DROP),
    (["blocked", "unreachable"], HEADLINE_ONLY),
    (["blocked"], FETCH),
    (["blocked", "blocked", "full"], FETCH),
])
def test_a_domain_is_learned_after_two_refusals_without_a_success(statuses, decision):
    policy = PublisherPolicy(NOW)
    for status in statuses:
        policy.record("https://new.example/a", status)
    assert policy.decide("https://new.example/b") == decision


def test_a_learned_block_is_retried_after_fourteen_days():
    policy = PublisherPolicy(NOW)
    for _ in range(2):
        policy.record("https://new.example/a", "blocked")
    policy.save()
    assert PublisherPolicy(NOW + timedelta(days=13)).decide("https://new.example/b") == HEADLINE_ONLY
    later = PublisherPolicy(NOW + timedelta(days=15))
    assert later.decide("https://new.example/b") == FETCH
    later.record("https://new.example/b", "blocked")
    assert later.decide("https://new.example/c") == HEADLINE_ONLY


def test_statuses_that_say_nothing_about_the_publisher_are_not_counted():
    policy = PublisherPolicy(NOW)
    policy.record("https://new.example/a", "non_english")
    assert "new.example" not in policy.rows


def test_an_operator_rule_is_manual_keeps_its_counts_and_is_never_relearned():
    policy = PublisherPolicy(NOW)
    for _ in range(3):
        policy.record("https://new.example/a", "blocked")
    policy.save()
    row = publishers.set_rule("new.example", "allow", NOW)
    assert (row["policy"], row["source"], row["blocked_count"]) == ("allow", "manual", 3)
    again = PublisherPolicy(NOW)
    again.record("https://new.example/b", "blocked")
    assert again.decide("https://new.example/c") == FETCH


def test_clearing_a_rule_goes_back_to_what_the_counts_teach():
    policy = PublisherPolicy(NOW)
    for _ in range(2):
        policy.record("https://new.example/a", "paywall")
    policy.save()
    publishers.set_rule("new.example", "allow", NOW)
    row = publishers.clear_rule("new.example", NOW)
    assert (row["policy"], row["source"]) == ("block_paywall", "auto")
    assert publishers.clear_rule("never-seen.example", NOW) is None


def test_a_rule_for_a_new_domain_is_created():
    row = publishers.set_rule("fresh.example", "block_bots", NOW)
    assert (row["policy"], row["source"], row["full_count"]) == ("block_bots", "manual", 0)
    assert PublisherPolicy(NOW).decide("https://fresh.example/x") == HEADLINE_ONLY


def test_the_list_includes_the_seed_without_writing_it():
    domains = [r["domain"] for r in publishers.list_rules(NOW)]
    assert "barrons.com" in domains and domains == sorted(domains)
    conn = get_connection()
    try:
        assert conn.execute("SELECT COUNT(*) FROM gfetcher_publishers").fetchone()[0] == 0
    finally:
        conn.close()
