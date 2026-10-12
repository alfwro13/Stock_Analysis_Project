import os
import time
import random
import logging
import threading
from datetime import datetime, timezone
from typing import List, Any, Optional

from transformers import pipeline

from yahoo_engine import yahoo_engine, news_item_published_at, news_item_text
from notification_engine import notify
from database import get_connection
from db_helpers import sentiment_max_age_days
from config import load_config
from constants import (
    NLP_FINBERT_MAX_TOKENS, NLP_TEXT_TRUNCATE_CHARS,
    NLP_NEWS_FETCH_LIMIT, NLP_CB_NEWS_FETCH_LIMIT, NLP_CB_TONE_THRESHOLD,
    NLP_SENTIMENT_MIN_COVERAGE,
)

logger = logging.getLogger(__name__)

# GUI name: "NLP Market Sentiment Engine / Central Bank NLP Alert". Canonical scheduled-job names live in scheduler_engine.JOB_GRAPH.

_FINBERT_ANALYZER = None
_MODEL_LOCK = threading.Lock()

_scan_degraded_alerted = False


class SentimentUnavailableError(Exception):
    """The headlines could not be fetched or scored; unlike "no headlines", the stored score must be left alone."""


def _get_finbert_analyzer() -> Optional[Any]:
    """Thread-safe singleton; prevents multi-worker memory explosion on first load."""
    global _FINBERT_ANALYZER
    if _FINBERT_ANALYZER is None:
        with _MODEL_LOCK:
            # Double-checked locking to prevent race conditions during initialization
            if _FINBERT_ANALYZER is None:
                logger.info("Loading FinBERT model into memory (Lazy Initialization)...")

                # HuggingFace reads HF_TOKEN from the environment directly; no re-assignment needed.
                hf_token = os.environ.get("HF_TOKEN", "")
                if hf_token:
                    logger.debug("HF_TOKEN detected — authenticated Hub requests enabled.")

                try:
                    # Prefer local cache; falls back to Hub download only on first run.
                    try:
                        _FINBERT_ANALYZER = pipeline(
                            "sentiment-analysis",
                            model="ProsusAI/finbert",
                            local_files_only=True,
                        )
                    except OSError:
                        logger.info("FinBERT not in local cache; downloading from HuggingFace Hub...")
                        _FINBERT_ANALYZER = pipeline("sentiment-analysis", model="ProsusAI/finbert")
                except Exception as e:
                    logger.error("Failed to allocate memory or initialize FinBERT pipeline: %s", e)
                    return None
    return _FINBERT_ANALYZER


def _score_text(analyzer, text: str) -> float:
    """Run FinBERT on a single text and return a signed compound score [-1, 1]."""
    result = analyzer(text[:NLP_TEXT_TRUNCATE_CHARS], truncation=True, max_length=NLP_FINBERT_MAX_TOKENS)[0]
    label = result['label'].lower()
    prob = result['score']
    if label == 'positive':
        return prob
    if label == 'negative':
        return -prob
    return 0.0


def fetch_and_score_news(ticker: str, analyzer: Any, max_age_days: int) -> Optional[float]:
    """Mean FinBERT compound score [-1, 1] of the ticker's headlines no older than max_age_days; None when it has none."""
    news = yahoo_engine.get_news(ticker)
    if news is None:
        raise SentimentUnavailableError(ticker)

    cutoff = time.time() - max_age_days * 86400
    scores = []
    fresh = 0
    for item in news:
        if len(scores) >= NLP_NEWS_FETCH_LIMIT:
            break
        if news_item_published_at(item) < cutoff:
            continue
        title, summary, publisher = news_item_text(item)
        text_to_analyze = f"{title}. {summary}. {publisher}"
        if not text_to_analyze.strip(". "):
            continue

        fresh += 1
        try:
            scores.append(_score_text(analyzer, text_to_analyze))
        except Exception as e:
            logger.debug("FinBERT failed to score string for %s: %s", ticker, e)

    if fresh and not scores:
        raise SentimentUnavailableError(ticker)
    return sum(scores) / len(scores) if scores else None


def _alert_if_scan_degraded(scored: int, total: int, no_headlines: int, unavailable: int) -> None:
    global _scan_degraded_alerted
    if total and scored / total < NLP_SENTIMENT_MIN_COVERAGE:
        if not _scan_degraded_alerted:
            _scan_degraded_alerted = True
            notify(
                "sentiment_scan_degraded", "Warning",
                f"NLP Sentiment Scan scored only {scored} of {total} tickers "
                f"({no_headlines} had no headlines within the age limit, {unavailable} could not be fetched). "
                "Yahoo's news feed may have changed.",
                level="warning",
            )
    else:
        _scan_degraded_alerted = False


def update_all_sentiment(tickers: List[str]) -> None:
    """Score FinBERT sentiment for all tickers and upsert into quant_signals; NULL when a ticker has no fresh headlines."""
    macro_tickers = ["^GSPC", "^NDX", "^FTSE", "^FTMC", "GBPUSD=X", "DX-Y.NYB"]
    combined_tickers = list(set((tickers if tickers else []) + macro_tickers))

    if not combined_tickers:
        logger.warning("Ticker list is empty. Aborting FinBERT sentiment scan.")
        return

    logger.info("Initiating FinBERT NLP Sentiment Scan for %d assets.", len(combined_tickers))

    analyzer = _get_finbert_analyzer()
    if not analyzer:
        logger.error("FinBERT pipeline unavailable. Aborting scan.")
        return

    max_age_days = sentiment_max_age_days()
    scored = no_headlines = unavailable = 0

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        for i, ticker in enumerate(combined_tickers):
            try:
                try:
                    score = fetch_and_score_news(ticker, analyzer, max_age_days)
                except SentimentUnavailableError:
                    unavailable += 1
                    logger.warning("[%s] Sentiment unavailable; stored score left unchanged.", ticker)
                    continue

                scored_at = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S') if score is not None else None
                cursor.execute("""
                    UPDATE quant_signals
                    SET sentiment_score = ?, sentiment_scored_at = ?
                    WHERE ticker = ? AND date = (SELECT MAX(date) FROM quant_signals WHERE ticker = ?)
                """, (score, scored_at, ticker, ticker))

                # Upsert macro tickers / new assets if the UPDATE matched no existing rows
                if cursor.rowcount == 0 and score is not None:
                    today_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
                    cursor.execute("""
                        INSERT INTO quant_signals (ticker, date, sentiment_score, sentiment_scored_at)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(ticker, date) DO UPDATE SET
                            sentiment_score = excluded.sentiment_score,
                            sentiment_scored_at = excluded.sentiment_scored_at
                    """, (ticker, today_str, score, scored_at))

                conn.commit()
                if score is None:
                    no_headlines += 1
                    logger.info("[%s] No headlines within %d days; sentiment cleared.", ticker, max_age_days)
                else:
                    scored += 1
                    logger.info("[%s] Processed Sentiment: %+.3f", ticker, score)

            except Exception as e:
                logger.error("Failed to process sentiment for %s: %s", ticker, e)
                conn.rollback()
            finally:
                time.sleep(random.uniform(0.5, 1.5))
    finally:
        if conn:
            conn.close()

    logger.info(
        "FinBERT NLP Analysis completed: %d scored, %d without headlines, %d unavailable.",
        scored, no_headlines, unavailable,
    )
    _alert_if_scan_degraded(scored, len(combined_tickers), no_headlines, unavailable)


# Triggered by run_central_bank_nlp_check() in scheduler_engine.py, polling macro_calendar every 30 min (mon–fri 12:00–21:00 UTC) for same-day CB events.
def run_central_bank_nlp_alert(event_name: str, currency: str) -> bool:
    """Classify FOMC/BoE tone as Hawkish/Dovish/Neutral and dispatch to Nextcloud Talk."""
    logger.info("Intercepting Central Bank Event for NLP Analysis: %s", event_name)
    config = load_config()

    analyzer = _get_finbert_analyzer()
    if not analyzer:
        logger.error("FinBERT pipeline unavailable. Aborting Central Bank NLP.")
        return False

    if currency == "USD":
        target_entity = "Federal Reserve"
        ticker_proxy = "^TNX" # 10Y Treasury news usually captures Fed statements fastest
    elif currency == "GBP":
        target_entity = "Bank of England"
        ticker_proxy = "^FTSE"
    else:
        logger.warning("Unsupported currency %s for Central Bank NLP.", currency)
        return False

    try:
        news = yahoo_engine.get_news(ticker_proxy)
        if not news:
            return False

        scores = []
        parsed_headlines = []

        for item in news[:NLP_CB_NEWS_FETCH_LIMIT]:
            title, summary, _ = news_item_text(item)

            text_to_analyze = f"{title}. {summary}"
            if "rate" not in text_to_analyze.lower() and "inflation" not in text_to_analyze.lower() and target_entity.lower() not in text_to_analyze.lower():
                continue

            scores.append(_score_text(analyzer, text_to_analyze))
            parsed_headlines.append(title)

        if not scores:
            logger.info("No relevant Central Bank headlines found in the immediate fetch window.")
            return False

        avg_score = sum(scores) / len(scores)

        # Negative FinBERT score = bad for equities = Hawkish; Positive = good for equities = Dovish.
        if avg_score < -NLP_CB_TONE_THRESHOLD:
            tone = "🦅 HAWKISH (Restrictive)"
            equity_impact = "Bearish for Equities"
        elif avg_score > NLP_CB_TONE_THRESHOLD:
            tone = "🕊️ DOVISH (Accommodative)"
            equity_impact = "Bullish for Equities"
        else:
            tone = "⚖️ NEUTRAL"
            equity_impact = "Market pricing unchanged"

        msg = (
            f"🏛️ **CENTRAL BANK NLP ANALYSIS** 🏛️\n\n"
            f"**Event:** {event_name} ({currency})\n"
            f"**Calculated Tone:** {tone}\n"
            f"**Expected Equity Impact:** {equity_impact}\n\n"
            f"**Analyzed FinBERT Score:** {avg_score:+.3f}\n"
            f"*Top Headline Parsed:* {parsed_headlines[0]}"
        )

        notify("cb_nlp_alert", "Macro NLP", msg)

        logger.info("Central Bank NLP successfully dispatched: %s", tone)
        return True

    except Exception as e:
        logger.error("Central Bank NLP analysis failed: %s", e)
        return False
