"""Schema: the gfetcher tables exist and news_articles carries feed_source (yahoo by default) and published_precision."""
import pytest

from db.database import get_connection

from .helpers import TABLES

pytestmark = pytest.mark.restore_tables(*TABLES)


def _columns(table):
    conn = get_connection()
    try:
        return {r["name"]: r for r in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


@pytest.mark.parametrize("table", [t for t in TABLES if t.startswith("gfetcher_")])
def test_gfetcher_tables_exist(table):
    assert _columns(table)


def test_news_articles_has_the_feed_columns_and_old_rows_read_as_yahoo():
    cols = _columns("news_articles")
    assert "published_precision" in cols
    assert cols["feed_source"]["dflt_value"] == "'yahoo'"
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO news_articles (article_id, ticker, source_list, headline, published_at, fetched_at) "
            "VALUES ('zzgf-schema', 'ZZGF', 'portfolio', 'H', 1, 1)"
        )
        row = conn.execute("SELECT feed_source, published_precision FROM news_articles WHERE article_id = 'zzgf-schema'").fetchone()
        assert (row["feed_source"], row["published_precision"]) == ("yahoo", None)
    finally:
        conn.rollback()
        conn.close()
