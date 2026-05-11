# ai_engine.py
import json
import re
import pandas as pd
import ta
from config import PORTFOLIO_PATH, HISTORICAL_DIR
from database import get_connection

class AIPromptEngine:
    """
    Dedicated engine for compiling Quantamental data into structured LLM prompts.
    Isolates prompt engineering and context aggregation from the web server.
    """

    def __init__(self):
        pass

    def _clean_html(self, raw_html: str) -> str:
        """Strips HTML tags from the database's educational notes for plain-text AI consumption."""
        if not raw_html:
            return "No notes available."
        
        # Format lists cleanly before stripping tags
        text = raw_html.replace('<li>', '\n- ').replace('</li>', '').replace('<br>', '\n')
        cleanr = re.compile('<.*?>')
        cleantext = re.sub(cleanr, '', text)
        return cleantext.replace('&nbsp;', ' ').strip()

    def _get_portfolio_context(self, ticker: str) -> dict:
        """Extracts holdings, VWAP, and account splits for the specific ticker."""
        try:
            with open(PORTFOLIO_PATH, 'r') as f:
                portfolio = json.load(f)
                
            for _, data in portfolio.items():
                if data.get("ticker") == ticker:
                    return data
            return {}
        except Exception:
            return {}

    def _get_technical_indicators(self, ticker: str) -> dict:
        """Reads the raw Parquet file to extract exact MACD, Volume, and OBV metrics."""
        df_path = HISTORICAL_DIR / f"{ticker}.parquet"
        metrics = {
            "macd_line": "N/A",
            "macd_signal": "N/A",
            "obv_trend": "N/A",
            "recent_volume": "N/A",
            "average_volume": "N/A"
        }
        
        if df_path.exists():
            try:
                df = pd.read_parquet(df_path)
                if not df.empty and len(df) > 30:
                    # Calculate MACD
                    macd_indicator = ta.trend.MACD(close=df['Close'])
                    metrics["macd_line"] = round(macd_indicator.macd().iloc[-1], 3)
                    metrics["macd_signal"] = round(macd_indicator.macd_signal().iloc[-1], 3)
                    
                    # Calculate OBV Trend
                    obv = ta.volume.OnBalanceVolumeIndicator(close=df['Close'], volume=df['Volume']).on_balance_volume()
                    obv_ma = obv.rolling(window=21).mean()
                    metrics["obv_trend"] = "Accumulation (Bullish)" if obv.iloc[-1] > obv_ma.iloc[-1] else "Distribution (Bearish)"
                    
                    # Calculate Volume
                    metrics["recent_volume"] = f"{df['Volume'].iloc[-1]:,.0f}"
                    metrics["average_volume"] = f"{df['Volume'].rolling(21).mean().iloc[-1]:,.0f}"
            except Exception as e:
                print(f"[AI ENGINE] Warning: Failed to parse technicals for {ticker}: {e}")
                
        return metrics

    def generate_prompt(self, ticker: str, mode: str) -> str:
        """
        Compiles the master prompt string based on the requested analysis mode.
        """
        # 1. Fetch Core Database Record
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM stock_signals WHERE ticker = ?", (ticker,))
        stock_data = cursor.fetchone()
        conn.close()

        if not stock_data:
            return None
            
        stock_data = dict(stock_data)

        # 2. Fetch Auxiliary Context
        portfolio_data = self._get_portfolio_context(ticker)
        technicals = self._get_technical_indicators(ticker)
        clean_notes = self._clean_html(stock_data.get('educational_notes', ''))

        # --- NEW: Safe Formatting Block to match the Website UI ---
        def fmt_pct(val):
            return f"{(val * 100):.1f}%" if val is not None else "N/A"

        def fmt_float(val):
            return f"{val:.2f}" if val is not None else "N/A"

        rev_growth_str = fmt_pct(stock_data.get('revenue_growth'))
        trailing_pe_str = fmt_float(stock_data.get('trailing_pe'))
        pl_peg_str = fmt_float(stock_data.get('peter_lynch_peg'))
        debt_str = fmt_float(stock_data.get('debt_to_equity'))
        rsi_str = f"{stock_data.get('rsi_14'):.1f}" if stock_data.get('rsi_14') is not None else "N/A"
        # -----------------------------------------------------------

        # 3. Format Portfolio String
        portfolio_str = "No active holdings in the current portfolio."
        if portfolio_data and portfolio_data.get('global_shares', 0) > 0:
            global_shares = portfolio_data.get('global_shares', 0)
            global_vwap = portfolio_data.get('global_buy_price', 0)
            
            portfolio_str = f"User currently holds {global_shares} shares at a Global VWAP (Cost Basis) of {global_vwap:,.2f} {stock_data['currency']}.\n"
            
            accounts = portfolio_data.get('accounts', [])
            if len(accounts) > 1:
                portfolio_str += "This holding is split across the following micro-ledgers:\n"
                for acc in accounts:
                    portfolio_str += f"  - {acc.get('name', 'Unknown')}: {acc.get('shares', 0)} shares at {acc.get('buy_price', 0):,.2f} {stock_data['currency']}\n"

        # 4. Build The Master Context Payload (Updated with formatted strings)
        context_payload = f"""
=========================================================
SYSTEM METADATA & SCORING LOGIC
=========================================================
The Quantamental System scores assets from 0 to 100.
- Scores >= 80 dictate a STRONG BUY.
- Scores < 40 dictate BEARISH / CAUTION.
The score is a weighted aggregation of Moving Average alignment (5D/10D/21D/50D/200D), RSI momentum, On-Balance Volume (OBV), and MACD Reversals. It overlays Mark Minervini's Volatility Contraction Pattern (VCP) and hierarchical candlestick recognition.

=========================================================
USER PORTFOLIO CONTEXT
=========================================================
{portfolio_str}

=========================================================
ASSET DATA: {stock_data['company_name']} ({stock_data['ticker']})
=========================================================
Current Price: {stock_data['current_price']:,.2f} {stock_data['currency']}
System Verdict: {stock_data['overall_signal']} (Score: {stock_data['composite_score']}/100)
ATR Stop-Loss: {stock_data['atr_stop_loss']:,.2f} {stock_data['currency']}

--- FUNDAMENTALS ---
Trailing P/E: {trailing_pe_str}
Peter Lynch Fair Value PEG: {pl_peg_str}
Debt-to-Equity: {debt_str}
Revenue Growth (YoY): {rev_growth_str}
Next Earnings Date: {stock_data['next_earnings_date']}

--- MACRO TECHNICALS ---
50-Day Trend: {stock_data['trend_50d']}
200-Day Trend: {stock_data['trend_200d']}
RSI (14-Day): {rsi_str}
MACD Line: {technicals['macd_line']} | Signal Line: {technicals['macd_signal']}
OBV Trend: {technicals['obv_trend']}
Recent Volume: {technicals['recent_volume']} (21D Avg: {technicals['average_volume']})

--- ALGORITHMIC BREAKDOWN ---
{clean_notes}
=========================================================
"""

        # 5. Wrap in the Specific Prompt Mode
        prompt_wrapper = ""

        if mode == "The Devil's Advocate analysis":
            prompt_wrapper = f"""
You are an elite-level, highly skeptical Wall Street Analyst.
Review the Quantamental context provided below. Your job is to aggressively challenge the system's "{stock_data['overall_signal']}" verdict.
Actively hunt for bearish divergences, macro weaknesses, valuation traps, or mean-reversion risks that the algorithmic scoring may have overlooked. Be highly critical, concise, and professional.

{context_payload}
"""
        elif mode == "Risk/Reward Audit":
            prompt_wrapper = f"""
You are an elite-level Financial Risk Manager.
Review the Quantamental context provided below. Focus heavily on the ATR Stop-Loss, the user's specific cost basis (VWAP), and the account splits.
Calculate the mathematical risk buffer between the current price, the user's entry, and the ATR floor. Suggest position sizing, profit-taking, or tightening stops based on the current volatility and RSI.

{context_payload}
"""
        elif mode == "Quantamental Deep-Dive":
            prompt_wrapper = f"""
You are an elite-level Hedge Fund Strategist.
Review the Quantamental context provided below. Synthesize the fundamental metrics (e.g., Peter Lynch PEG, Debt, Growth) with the technical setup (e.g., VCP Breakouts, MACD, MAs).
Determine if the fundamental "story" of the business validates the current mathematical price action on the chart. Provide a comprehensive 12-month conviction rating.

{context_payload}
"""
        elif mode == "Earnings Strategy":
            prompt_wrapper = f"""
You are a Senior Options & Volatility Analyst.
Review the Quantamental context provided below, paying special attention to the approaching earnings date.
Based on the current technical extensions (RSI, MAs) and fundamental valuation, outline a strategic playbook. Should the user hold through earnings, trim the position to reduce exposure, or hedge? Explain your logic clearly.

{context_payload}
"""
        else:
            # Fallback generic prompt
            prompt_wrapper = f"""
You are an elite-level Stock Market Analyst. Review the following data and provide an institutional-grade assessment.

{context_payload}
"""

        return prompt_wrapper.strip()