# ai_engine.py
import json
import re
import pandas as pd
import ta
import yfinance as yf
from datetime import datetime
from config import PORTFOLIO_PATH, HISTORICAL_DIR, BASE_CURRENCY
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
                    macd_indicator = ta.trend.MACD(close=df['Close'])
                    metrics["macd_line"] = round(macd_indicator.macd().iloc[-1], 3)
                    metrics["macd_signal"] = round(macd_indicator.macd_signal().iloc[-1], 3)
                    
                    obv = ta.volume.OnBalanceVolumeIndicator(close=df['Close'], volume=df['Volume']).on_balance_volume()
                    obv_ma = obv.rolling(window=21).mean()
                    metrics["obv_trend"] = "Accumulation (Bullish)" if obv.iloc[-1] > obv_ma.iloc[-1] else "Distribution (Bearish)"
                    
                    metrics["recent_volume"] = f"{df['Volume'].iloc[-1]:,.0f}"
                    metrics["average_volume"] = f"{df['Volume'].rolling(21).mean().iloc[-1]:,.0f}"
            except Exception as e:
                print(f"[AI ENGINE] Warning: Failed to parse technicals for {ticker}: {e}")
                
        return metrics

    def generate_prompt(self, ticker: str, mode: str) -> str:
        """
        Compiles the master prompt string based on the requested analysis mode.
        """
        # 1. Fetch Core Database Record & Advanced Metrics
        conn = get_connection()
        cursor = conn.cursor()
        
        # ADDED: LEFT JOIN to ensure the LLM receives the ML, Risk, and Sentiment context.
        cursor.execute("""
            SELECT s.*, 
                   q.ml_confidence_score, 
                   q.var_95, 
                   q.cvar_95, 
                   q.sentiment_score
            FROM stock_signals s
            LEFT JOIN quant_signals q ON s.ticker = q.ticker 
                AND q.date = (SELECT MAX(date) FROM quant_signals WHERE ticker = s.ticker)
            WHERE s.ticker = ?
        """, (ticker,))
        stock_data = cursor.fetchone()
        conn.close()

        if not stock_data:
            return None
            
        stock_data = dict(stock_data)

        # 2. Fetch Auxiliary Context
        portfolio_data = self._get_portfolio_context(ticker)
        technicals = self._get_technical_indicators(ticker)
        clean_notes = self._clean_html(stock_data.get('educational_notes', ''))

        # 3. Safe Formatting Block
        def fmt_pct(val):
            return f"{(val * 100):.1f}%" if val is not None else "N/A"

        def fmt_float(val):
            return f"{val:.2f}" if val is not None else "N/A"

        rev_growth_str = fmt_pct(stock_data.get('revenue_growth'))
        trailing_pe_str = fmt_float(stock_data.get('trailing_pe'))
        pl_peg_str = fmt_float(stock_data.get('peter_lynch_peg'))
        debt_str = fmt_float(stock_data.get('debt_to_equity'))
        rsi_str = f"{stock_data.get('rsi_14'):.1f}" if stock_data.get('rsi_14') is not None else "N/A"
        beta_str = fmt_float(stock_data.get('beta'))
        
        low_52 = fmt_float(stock_data.get('fifty_two_week_low'))
        high_52 = fmt_float(stock_data.get('fifty_two_week_high'))

        # Advanced Engine Metrics Formatting
        ml_conf_str = f"{stock_data.get('ml_confidence_score'):.1f}%" if stock_data.get('ml_confidence_score') is not None else "N/A"
        var_str = f"{(stock_data.get('var_95') * 100):.2f}%" if stock_data.get('var_95') is not None else "N/A"
        cvar_str = f"{(stock_data.get('cvar_95') * 100):.2f}%" if stock_data.get('cvar_95') is not None else "N/A"
        sentiment_str = f"{stock_data.get('sentiment_score'):.3f}" if stock_data.get('sentiment_score') is not None else "N/A"

        # --- LIVE EXCHANGE RATE LOGIC (Matches main.py) ---
        stock_currency = stock_data['currency']
        exchange_rate = 1.0

        if stock_currency and stock_currency not in [BASE_CURRENCY, 'GBp', 'GBP']:
            try:
                # E.g., GBPUSD=X (Converts Base to Native)
                fx_ticker = f"{BASE_CURRENCY}{stock_currency}=X"
                fx_data = yf.Ticker(fx_ticker).history(period="1d")
                if not fx_data.empty:
                    exchange_rate = fx_data['Close'].iloc[-1]
            except Exception as e:
                print(f"[AI ENGINE] Warning: Could not fetch FX for {fx_ticker}: {e}")

        # 4. Format Portfolio String 
        portfolio_str = "No active holdings in the current portfolio."
        if portfolio_data and portfolio_data.get('global_shares', 0) > 0:
            global_shares = portfolio_data.get('global_shares', 0)
            
            # Apply FX Conversion to VWAP (Ghostfolio Base -> Stock Native)
            global_vwap_base = portfolio_data.get('global_buy_price', 0)
            vwap_native = global_vwap_base * exchange_rate
            
            # Re-scale if native is LSE pence (GBp)
            if portfolio_data.get('price_in_pence', False):
                vwap_native *= 100
                
            curr_price = stock_data['current_price']
            
            # Math
            cost_basis = global_shares * vwap_native
            current_value = global_shares * curr_price
            pnl = current_value - cost_basis
            pnl_pct = (pnl / cost_basis) * 100 if cost_basis > 0 else 0
            
            portfolio_str = (
                f"User currently holds {global_shares} shares.\n"
                f"Global VWAP (Cost Basis): {vwap_native:,.2f} {stock_data['currency']}.\n"
                f"Current Value: {current_value:,.2f} {stock_data['currency']}.\n"
                f"Unrealized P&L: {pnl:,.2f} {stock_data['currency']} ({pnl_pct:.2f}%).\n"
                f"CRITICAL INSTRUCTION: Do NOT recalculate these P&L numbers. Use them exactly as stated.\n"
            )
            
            accounts = portfolio_data.get('accounts', [])
            if len(accounts) > 1:
                portfolio_str += "\nThis holding is split across the following micro-ledgers:\n"
                for acc in accounts:
                    acc_buy_base = acc.get('buy_price', 0)
                    acc_buy_native = acc_buy_base * exchange_rate
                    if portfolio_data.get('price_in_pence', False):
                        acc_buy_native *= 100
                    portfolio_str += f"  - {acc.get('name', 'Unknown')}: {acc.get('shares', 0)} shares at {acc_buy_native:,.2f} {stock_data['currency']}\n"

        # --- GET CURRENT SYSTEM DATE ---
        current_date_str = datetime.now().strftime("%Y-%m-%d")

        # 5. Build The Master Context Payload
        context_payload = f"""
=========================================================
SYSTEM METADATA & SCORING LOGIC
=========================================================
Current System Date: {current_date_str}
The Quantamental System scores assets from -100 to 100.
- Scores >= 40: STRONG BUY
- Scores >= 20: BULLISH / HOLD
- Scores >= 0:  NEUTRAL
- Scores >= -30: BEARISH / CAUTION
- Scores >= -60: STRONG SELL
- Scores < -60: TOXIC / AVOID
The score is a weighted aggregation of Moving Average alignment (5D/10D/21D/50D/200D), RSI momentum, On-Balance Volume (OBV), and MACD Reversals. It overlays Mark Minervini's Volatility Contraction Pattern (VCP) and hierarchical candlestick recognition.

=========================================================
USER PORTFOLIO CONTEXT
=========================================================
{portfolio_str}

=========================================================
ASSET DATA: {stock_data['company_name']} ({stock_data['ticker']})
=========================================================
Sector: {stock_data.get('sector', 'Unknown')}
Current Price: {stock_data['current_price']:,.2f} {stock_data['currency']}
52-Week Range: {low_52} - {high_52}
System Verdict: {stock_data['overall_signal']} (Score: {stock_data['composite_score']}/100)
ATR Stop-Loss: {stock_data['atr_stop_loss']:,.2f} {stock_data['currency']}

--- AI, RISK & SENTIMENT ---
ML Confidence Score (>3% return in 5d): {ml_conf_str}
Parametric Log-Return VaR (95%): {var_str}
Conditional Log-Return CVaR (95% Tail Risk): {cvar_str}
VADER Media Sentiment: {sentiment_str}

--- FUNDAMENTALS & RISK ---
Wall Street Analyst Rating: {stock_data.get('analyst_rating', 'Unknown')}
Beta (Volatility vs Market): {beta_str}
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

        # 6. Wrap in the Specific Prompt Mode
        prompt_wrapper = ""

        if mode == "The Devil's Advocate analysis":
            prompt_wrapper = f"""
You are an elite-level, highly analytical Wall Street Risk Manager.
Review the Quantamental context provided below. Your job is to act as the "Devil's Advocate" against the system's "{stock_data['overall_signal']}" verdict.

IMPORTANT: You must acknowledge the stock's Sector, its Beta (stability/risk), and any obvious macroeconomic tailwinds (e.g., AI/Semiconductor supercycles, Blue-Chip stability) that explain its current valuation or momentum. Do not blindly dismiss a strong trend.
However, once you have acknowledged the narrative, carefully point out the mathematical exhaustion risks, valuation traps, or mean-reversion dangers. Be critical, balanced, and professional.

{context_payload}
"""
        elif mode == "Risk/Reward Audit":
            prompt_wrapper = f"""
You are an elite-level Financial Risk Manager.
Review the Quantamental context provided below. Focus heavily on the ATR Stop-Loss, the user's specific Unrealized P&L, the Parametric Value at Risk (VaR), and the 52-Week Range.
Calculate the mathematical risk buffer between the current price, the user's entry, and the ATR floor. Suggest position sizing, profit-taking, or tightening stops based on the current volatility (Beta), VaR, and RSI.

{context_payload}
"""
        elif mode == "Quantamental Deep-Dive":
            prompt_wrapper = f"""
You are an elite-level Hedge Fund Strategist.
Review the Quantamental context provided below. Synthesize the fundamental metrics (e.g., Peter Lynch PEG, Sector, Growth) with the technical setup (e.g., 52-Week Range, VCP Breakouts, MAs), and the Machine Learning/Sentiment vectors.
Determine if the fundamental "story" of the business validates the current mathematical price action on the chart. Provide a comprehensive 12-month conviction rating.

{context_payload}
"""
        elif mode == "Earnings Strategy":
            prompt_wrapper = f"""
You are a Senior Options & Volatility Analyst.
Review the Quantamental context provided below, paying special attention to the approaching earnings date, the stock's Beta, its Expected Shortfall (CVaR), and its Unrealized P&L.
Based on the current technical extensions (RSI, MAs) and fundamental valuation, outline a strategic playbook. Should the user hold through earnings, trim the position to lock in gains, or hedge? Explain your logic clearly.

{context_payload}
"""
        else:
            prompt_wrapper = f"""
You are an elite-level Stock Market Analyst. Review the following data and provide an institutional-grade assessment.

{context_payload}
"""

        return prompt_wrapper.strip()