import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import Dict, Optional

from config import load_config
from yahoo_engine import yahoo_engine
from database import get_connection, get_watchlist_tickers

logger = logging.getLogger(__name__)


def _build_ticker_source_map() -> Dict[str, str]:
    from accounts_engine import get_combined_holdings
    config = load_config()
    ignored = {t.upper() for t in config.get("IGNORED_TICKERS", [])}

    portfolio_tickers = {t.upper() for t in get_combined_holdings().keys() if t}
    watchlist_tickers = {t.upper() for t in get_watchlist_tickers() if t}

    ticker_map: Dict[str, str] = {}
    for t in portfolio_tickers - ignored:
        ticker_map[t] = "both" if t in watchlist_tickers else "portfolio"
    for t in watchlist_tickers - ignored - portfolio_tickers:
        ticker_map[t] = "watchlist"

    return ticker_map


def _make_article_id(item: dict, ticker: str, published_at: float) -> str:
    uid = item.get("uuid") or item.get("id") or ""
    if uid:
        return uid
    headline = (item.get("content") or {}).get("title") or item.get("title", "")
    raw = f"{ticker}_{headline}_{int(published_at)}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _extract_published_at(item: dict) -> float:
    """Returns Unix timestamp (seconds). Returns 0 if unparseable."""
    ts = item.get("providerPublishTime", 0)
    if ts and ts > 1_000_000_000_000:
        return ts / 1000
    if ts and ts > 1_000_000_000:
        return float(ts)

    content = item.get("content") or {}
    pub_str = content.get("pubDate") or item.get("pubDate") or ""
    if pub_str:
        try:
            dt = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
            return dt.timestamp()
        except ValueError:
            pass
    return 0.0


def _fetch_full_text(url: str) -> str | None:
    if not url or url == "N/A":
        return None
    try:
        import trafilatura
        downloaded = trafilatura.fetch_url(url)
        if downloaded is None:
            return None
        text = trafilatura.extract(downloaded, include_comments=False, include_tables=False)
        return text
    except Exception as e:
        logger.debug(f"trafilatura extraction failed for {url}: {e}")
        return None


def _get_company_name(ticker: str) -> str | None:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT company_name FROM stock_signals WHERE ticker = ? LIMIT 1", (ticker,)
        )
        row = cursor.fetchone()
        return row["company_name"] if row else None
    except Exception:
        return None
    finally:
        if conn:
            conn.close()


def _label_from_score(score: float) -> str:
    if score > 0.15:
        return "positive"
    if score < -0.15:
        return "negative"
    return "neutral"


def _score_unscoredrows(conn) -> None:
    try:
        from huggingface_engine import _get_finbert_analyzer, _score_text
    except ImportError:
        logger.warning("News Feed: sentiment_engine not available, skipping sentiment scoring.")
        return

    analyzer = _get_finbert_analyzer()
    if analyzer is None:
        logger.warning("News Feed: FinBERT unavailable, skipping sentiment scoring.")
        return

    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, headline, summary FROM news_articles WHERE sentiment_score IS NULL"
    )
    rows = cursor.fetchall()
    if not rows:
        return

    logger.info(f"News Feed: scoring sentiment for {len(rows)} articles...")
    scored = 0
    for row in rows:
        text = " ".join(filter(None, [row["headline"], row["summary"]])).strip()
        if not text:
            continue
        try:
            score = _score_text(analyzer, text)
            label = _label_from_score(score)
            cursor.execute(
                "UPDATE news_articles SET sentiment_score = ?, sentiment_label = ? WHERE id = ?",
                (round(score, 4), label, row["id"]),
            )
            scored += 1
        except Exception as e:
            logger.debug(f"News Feed: sentiment scoring failed for article {row['id']}: {e}")

    conn.commit()
    logger.info(f"News Feed: sentiment scored {scored}/{len(rows)} articles.")


def run_news_feed_job() -> int:
    """Fetch news for all portfolio+watchlist tickers, full-text extract via trafilatura, store in news_articles; returns new row count."""
    cfg = load_config().get("SCHEDULING", {}).get("NEWS_FEED", {})
    max_per_ticker = int(cfg.get("MAX_PER_TICKER", 5))
    max_age_days = int(cfg.get("MAX_AGE_DAYS", 7))
    cutoff_ts = time.time() - max_age_days * 86400

    ticker_map = _build_ticker_source_map()
    if not ticker_map:
        logger.warning("News Feed: no tickers found in portfolio/watchlist.")
        return 0

    logger.info(f"News Feed: fetching news for {len(ticker_map)} tickers (cutoff={max_age_days}d, max={max_per_ticker}/ticker)")

    conn = get_connection()
    cursor = conn.cursor()
    total_inserted = 0

    try:
        for ticker, source_list in sorted(ticker_map.items()):
            news_items = yahoo_engine.get_news(ticker) or []

            company_name = _get_company_name(ticker)
            inserted_this_ticker = 0

            for item in news_items:
                if inserted_this_ticker >= max_per_ticker:
                    break

                content = item.get("content") or {}

                finance_data = content.get("finance") or {}
                premium_data = finance_data.get("premiumFinance") or {}
                if premium_data.get("isPremiumNews", False):
                    continue

                published_at = _extract_published_at(item)
                if published_at == 0.0 or published_at < cutoff_ts:
                    continue

                headline = content.get("title") or item.get("title", "N/A")
                summary = content.get("summary") or item.get("summary") or ""

                url_info = content.get("canonicalUrl") or content.get("clickThroughUrl")
                url = url_info.get("url", item.get("link", "")) if url_info else item.get("link", "")

                publisher = (content.get("provider") or {}).get("displayName") or item.get("publisher") or ""

                article_id = _make_article_id(item, ticker, published_at)
                fetched_at = int(time.time())

                cursor.execute(
                    """INSERT OR IGNORE INTO news_articles
                       (article_id, ticker, company_name, source_list, headline, summary,
                        url, publisher, published_at, is_premium, fetched_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)""",
                    (article_id, ticker, company_name, source_list,
                     headline, summary, url, publisher, int(published_at), fetched_at),
                )
                new_row = cursor.rowcount == 1
                conn.commit()

                if new_row:
                    total_inserted += 1
                    inserted_this_ticker += 1

                    full_text = _fetch_full_text(url)
                    if full_text:
                        cursor.execute(
                            """UPDATE news_articles
                               SET full_text = ?, body_fetched = 1
                               WHERE article_id = ?""",
                            (full_text, article_id),
                        )
                        conn.commit()
                    logger.info(f"News Feed: new article for {ticker} (body={'yes' if full_text else 'no'}): {headline[:60]}")

        cursor.execute(
            "DELETE FROM news_articles WHERE published_at < ?", (int(cutoff_ts),)
        )
        conn.commit()
        pruned = cursor.rowcount
        if pruned > 0:
            logger.info(f"News Feed: pruned {pruned} expired articles.")

        _score_unscoredrows(conn)

    except Exception as e:
        logger.error(f"News Feed job failed: {e}")
    finally:
        conn.close()

    log_conn = None
    try:
        log_conn = get_connection()
        log_conn.execute(
            "INSERT OR REPLACE INTO scheduler_run_log (job_id, last_run) VALUES (?, ?)",
            ("news_feed_job", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")),
        )
        log_conn.commit()
    except Exception:
        pass
    finally:
        if log_conn:
            log_conn.close()

    logger.info(f"News Feed job complete. {total_inserted} new articles inserted.")
    return total_inserted
