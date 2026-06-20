import os
import time
import random
import logging
import threading
from datetime import datetime, timezone
from typing import List, Any, Optional

from transformers import pipeline

from yahoo_engine import yahoo_engine
from notification_engine import notify
from database import get_connection
from config import load_config
from constants import (
    NLP_FINBERT_MAX_TOKENS, NLP_TEXT_TRUNCATE_CHARS,
    NLP_NEWS_FETCH_LIMIT, NLP_CB_NEWS_FETCH_LIMIT, NLP_CB_TONE_THRESHOLD,
)

logger = logging.getLogger(__name__)

# GUI name: "NLP Market Sentiment Engine / Central Bank NLP Alert". Canonical scheduled-job names live in scheduler_engine.JOB_GRAPH.

_FINBERT_ANALYZER = None
_MODEL_LOCK = threading.Lock()


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
                    logger.error(f"Failed to allocate memory or initialize FinBERT pipeline: {e}")
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


def fetch_and_score_news(ticker: str, analyzer: Any) -> float:
    """Fetch latest headlines via Yahoo Finance and return mean FinBERT compound score [-1, 1]."""
    try:
        news = yahoo_engine.get_news(ticker)

        if not news or not isinstance(news, list):
            return 0.0

        scores = []
        for item in news[:NLP_NEWS_FETCH_LIMIT]:
            # Defensively un-nest the Yahoo Finance payload
            content = item.get('content', item)

            title = content.get('title', '')
            summary = content.get('summary', '')

            # Publisher could be under 'publisher', 'provider', or nested inside provider
            publisher = content.get('publisher', '')
            if not publisher and isinstance(content.get('provider'), dict):
                publisher = content['provider'].get('displayName', '')

            text_to_analyze = f"{title}. {summary}. {publisher}"

            if not text_to_analyze.strip(". "):
                continue

            try:
                scores.append(_score_text(analyzer, text_to_analyze))
            except Exception as e:
                logger.debug(f"FinBERT failed to score string for {ticker}: {e}")
                continue

        if not scores:
            return 0.0

        return sum(scores) / len(scores)

    except Exception as e:
        logger.warning(f"Failed to fetch/score news for {ticker}: {e}")
        return 0.0


def update_all_sentiment(tickers: List[str]) -> None:
    """Score FinBERT sentiment for all tickers and upsert into quant_signals."""
    macro_tickers = ["^GSPC", "^NDX", "^FTSE", "^FTMC", "GBPUSD=X", "DX-Y.NYB"]
    combined_tickers = list(set((tickers if tickers else []) + macro_tickers))

    if not combined_tickers:
        logger.warning("Ticker list is empty. Aborting FinBERT sentiment scan.")
        return

    logger.info(f"Initiating FinBERT NLP Sentiment Scan for {len(combined_tickers)} assets.")

    analyzer = _get_finbert_analyzer()
    if not analyzer:
        logger.error("FinBERT pipeline unavailable. Aborting scan.")
        return

    conn = get_connection()
    try:
        cursor = conn.cursor()

        for i, ticker in enumerate(combined_tickers):
            try:
                score = fetch_and_score_news(ticker, analyzer)

                cursor.execute("""
                    UPDATE quant_signals
                    SET sentiment_score = ?
                    WHERE ticker = ? AND date = (SELECT MAX(date) FROM quant_signals WHERE ticker = ?)
                """, (score, ticker, ticker))

                # Upsert macro tickers / new assets if the UPDATE matched no existing rows
                if cursor.rowcount == 0:
                    today_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
                    cursor.execute("""
                        INSERT INTO quant_signals (ticker, date, sentiment_score)
                        VALUES (?, ?, ?)
                        ON CONFLICT(ticker, date) DO UPDATE SET
                            sentiment_score = excluded.sentiment_score
                    """, (ticker, today_str, score))

                conn.commit()
                logger.info(f"[{ticker}] Processed Sentiment: {score:+.3f}")

            except Exception as e:
                logger.error(f"Failed to process sentiment for {ticker}: {e}")
                conn.rollback()
            finally:
                time.sleep(random.uniform(0.5, 1.5))
    finally:
        conn.close()

    logger.info("FinBERT NLP Analysis completed successfully.")


# Triggered by run_central_bank_nlp_check() in scheduler_engine.py, polling macro_calendar every 30 min (mon–fri 12:00–21:00 UTC) for same-day CB events.
def run_central_bank_nlp_alert(event_name: str, currency: str) -> bool:
    """Classify FOMC/BoE tone as Hawkish/Dovish/Neutral and dispatch to Nextcloud Talk."""
    logger.info(f"Intercepting Central Bank Event for NLP Analysis: {event_name}")
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
        logger.warning(f"Unsupported currency {currency} for Central Bank NLP.")
        return False

    try:
        news = yahoo_engine.get_news(ticker_proxy)
        if not news:
            return False

        scores = []
        parsed_headlines = []

        for item in news[:NLP_CB_NEWS_FETCH_LIMIT]:
            content = item.get('content', item)
            title = content.get('title', '')
            summary = content.get('summary', '')

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

        logger.info(f"Central Bank NLP successfully dispatched: {tone}")
        return True

    except Exception as e:
        logger.error(f"Central Bank NLP analysis failed: {e}")
        return False
