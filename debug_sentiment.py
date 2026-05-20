# debug_sentiment.py
import json
import logging
import sqlite3
from typing import List, Dict, Any

import yfinance as yf
from transformers import pipeline

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
            logger.error(f"❌ yfinance returned empty news for {ticker}. API might be blocked.")
            return []
            
        if not isinstance(news, list):
            logger.error(f"❌ yfinance returned malformed data type: {type(news)}")
            return []
            
        logger.info(f"✅ yfinance successfully returned {len(news)} news articles.")
        return news
        
    except Exception as e:
        logger.error(f"❌ Exception during yfinance news fetch: {e}")
        return []

def test_finbert_nlp_scoring(news_data: List[Dict[str, Any]]) -> float:
    """
    Tests the FinBERT Sentiment Analyzer on the un-nested news payload.
    """
    logger.info("--- [TEST 2] Testing FinBERT NLP Scoring & Un-nesting Logic ---")
    if not news_data:
        logger.warning("No news data provided to FinBERT. Returning 0.0")
        return 0.0
        
    try:
        logger.info("Loading FinBERT model into memory...")
        analyzer = pipeline("sentiment-analysis", model="ProsusAI/finbert")
        scores: List[float] = []
        
        for i, item in enumerate(news_data[:5]):  # Test first 5 for brevity
            # 1. Defensive Extraction (Un-nesting Yahoo's payload)
            content = item.get('content', item)
            
            title = content.get('title', '')
            summary = content.get('summary', '')
            
            # Publisher could be under 'publisher', 'provider', or nested inside provider
            publisher = content.get('publisher', '')
            if not publisher and isinstance(content.get('provider'), dict):
                publisher = content['provider'].get('displayName', '')
                
            # 2. Construct the analysis string
            text_to_analyze = f"{title}. {summary}. {publisher}"
            
            if not text_to_analyze.strip(". "):
                logger.warning(f"Article {i+1} resulted in empty text string.")
                continue
                
            # 3. Execute FinBERT Scoring (Proper Token Truncation)
            # Allow up to 2000 chars to reach the tokenizer, then let the tokenizer strictly enforce the 512 token limit
            result = analyzer(text_to_analyze[:2000], truncation=True, max_length=512)[0]
            label = result['label'].lower()
            prob = result['score']
            
            # Map FinBERT's output to your database's expected -1.0 to 1.0 compound float
            if label == 'positive':
                compound = prob
            elif label == 'negative':
                compound = -prob
            else:
                compound = 0.0
                
            scores.append(compound)
            
            logger.info(f"Article {i+1} [{label.upper()}] Score: {compound:+.3f} | Text: {text_to_analyze[:60]}...")
            
        if not scores:
            logger.error("❌ FinBERT failed to score any articles.")
            return 0.0
            
        avg_score = sum(scores) / len(scores)
        logger.info(f"✅ FinBERT NLP successful. Average Compound Score: {avg_score:+.3f}")
        return avg_score
        
    except Exception as e:
        logger.error(f"❌ Exception during FinBERT scoring: {e}")
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
    print(" 🧠 FINBERT SENTIMENT ENGINE DIAGNOSTICS")
    print("=========================================================")
    
    # Test with a highly liquid asset that is guaranteed to have news
    target_ticker = "AAPL"
    
    news_data = test_yfinance_news_extraction(target_ticker)
    print("")
    test_finbert_nlp_scoring(news_data)
    print("")
    test_database_state(target_ticker)
    
    print("=========================================================")
    print(" DIAGNOSTICS COMPLETE")
    print("=========================================================")

if __name__ == "__main__":
    run_diagnostics()