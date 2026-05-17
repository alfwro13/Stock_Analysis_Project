# debug_sentiment.py
import json
import logging
import sqlite3
from typing import List, Dict, Any, Optional

import yfinance as yf
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from database import DB_PATH, get_connection

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - DEBUG_SENTIMENT - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def test_yfinance_news_extraction(ticker: str) -> List[Dict[str, Any]]:
    """
    Tests if Yahoo Finance is successfully returning news payloads.
    """
    logger.info(f"--- [TEST 1] Testing Yahoo Finance News API for {ticker} ---")
    try:
        stock = yf.Ticker(ticker)
        news = stock.news
        
        if not news:
            logger.error(f"❌ yfinance returned empty news for {ticker}. API might be broken or blocked.")
            return []
            
        if not isinstance(news, list):
            logger.error(f"❌ yfinance returned malformed data type: {type(news)}")
            return []
            
        logger.info(f"✅ yfinance successfully returned {len(news)} news articles.")
        
        # Display the first article to verify structure
        first_article = news[0]
        logger.info(f"Sample Article Title: {first_article.get('title', 'NO TITLE')}")
        logger.info(f"Sample Article Publisher: {first_article.get('publisher', 'NO PUBLISHER')}")
        
        return news
        
    except Exception as e:
        logger.error(f"❌ Exception during yfinance news fetch: {e}")
        return []

def test_vader_nlp_scoring(news_data: List[Dict[str, Any]]) -> float:
    """
    Tests the VADER SentimentIntensityAnalyzer on the extracted news payload.
    """
    logger.info("--- [TEST 2] Testing VADER NLP Scoring Logic ---")
    if not news_data:
        logger.warning("No news data provided to VADER. Returning 0.0")
        return 0.0
        
    try:
        analyzer = SentimentIntensityAnalyzer()
        scores: List[float] = []
        
        for i, item in enumerate(news_data[:5]): # Test first 5 for brevity
            title = item.get('title', '')
            summary = item.get('summary', '')
            publisher = item.get('publisher', '')
            
            text_to_analyze = f"{title}. {summary}. {publisher}"
            
            if not text_to_analyze.strip(". "):
                logger.warning(f"Article {i+1} resulted in empty text string.")
                continue
                
            score_dict = analyzer.polarity_scores(text_to_analyze)
            compound_score = score_dict['compound']
            scores.append(compound_score)
            
            logger.info(f"Article {i+1} Score: {compound_score:+.3f} | Text: {text_to_analyze[:60]}...")
            
        if not scores:
            logger.error("❌ VADER failed to score any articles.")
            return 0.0
            
        avg_score = sum(scores) / len(scores)
        logger.info(f"✅ VADER NLP successful. Average Compound Score: {avg_score:+.3f}")
        return avg_score
        
    except Exception as e:
        logger.error(f"❌ Exception during VADER scoring: {e}")
        return 0.0

def test_database_state(ticker: str) -> None:
    """
    Checks the local SQLite database to see what the quant_signals table currently holds.
    """
    logger.info(f"--- [TEST 3] Testing Database State for {ticker} ---")
    if not DB_PATH.exists():
        logger.error(f"❌ Database not found at {DB_PATH}")
        return
        
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        # Check if the column actually exists in the schema
        cursor.execute("PRAGMA table_info(quant_signals)")
        columns = [info['name'] for info in cursor.fetchall()]
        
        if 'sentiment_score' not in columns:
            logger.error("❌ Schema Error: 'sentiment_score' column is missing from quant_signals table!")
            return
        else:
            logger.info("✅ 'sentiment_score' column exists in schema.")
            
        # Check the latest row
        cursor.execute("""
            SELECT date, close_price, sentiment_score 
            FROM quant_signals 
            WHERE ticker = ? 
            ORDER BY date DESC LIMIT 1
        """, (ticker,))
        
        row = cursor.fetchone()
        if not row:
            logger.warning(f"❌ No historical quant_signals rows found for {ticker}.")
            return
            
        logger.info(f"✅ Latest DB Row for {ticker} (Date: {row['date']}):")
        logger.info(f"   -> Close Price:     {row['close_price']}")
        logger.info(f"   -> Sentiment Score: {row['sentiment_score']} (If None, it shows as N/A in UI)")
        
    except Exception as e:
        logger.error(f"❌ Exception querying database: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

def run_diagnostics() -> None:
    print("=========================================================")
    print(" 🧠 SENTIMENT ENGINE DIAGNOSTICS")
    print("=========================================================")
    
    # Test with a highly liquid asset that is guaranteed to have news
    target_ticker = "AAPL"
    
    news_data = test_yfinance_news_extraction(target_ticker)
    print("")
    test_vader_nlp_scoring(news_data)
    print("")
    test_database_state(target_ticker)
    
    print("=========================================================")
    print(" DIAGNOSTICS COMPLETE")
    print("=========================================================")

if __name__ == "__main__":
    run_diagnostics()