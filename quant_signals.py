# quant_signals.py
import pandas as pd
import ta
import os
import json
from datetime import datetime
from config import HISTORICAL_DIR, FUNDAMENTALS_DIR
from database import get_connection

class QuantEngine:
    def __init__(self):
        self.conn = get_connection()

    def load_parquet(self, ticker):
        """Loads the historical daily data."""
        filepath = HISTORICAL_DIR / f"{ticker}.parquet"
        if not filepath.exists():
            return None
        return pd.read_parquet(filepath)

    def load_fundamentals(self, ticker):
        """Loads the raw Yahoo Finance .info dictionary."""
        filepath = FUNDAMENTALS_DIR / f"{ticker}.json"
        if not filepath.exists():
            return {}
        with open(filepath, 'r') as f:
            return json.load(f)

    def calculate_3_weeks_tight(self, df):
        """Classic CAN SLIM volatility contraction pattern."""
        weekly_data = df['Close'].resample('W-FRI').last()
        if len(weekly_data) < 4:
            return False
        last_3_weeks = weekly_data.iloc[-4:-1]
        variance_pct = (last_3_weeks.max() - last_3_weeks.min()) / last_3_weeks.min()
        return variance_pct <= 0.025

    def analyze_ticker(self, ticker):
        """Runs the combined Technical and Fundamental analysis."""
        df = self.load_parquet(ticker)
        info = self.load_fundamentals(ticker)
        
        if df is None or len(df) < 200:
            print(f"[SKIP] Not enough historical data to analyze {ticker} (requires 200 days).")
            return

        # ==========================================
        # PART 1: TECHNICAL ANALYSIS & MATH
        # ==========================================
        current_price = df['Close'].iloc[-1]
        
        # Moving Averages
        df['MA_5'] = df['Close'].rolling(window=5).mean()
        df['MA_10'] = df['Close'].rolling(window=10).mean()
        df['MA_21'] = df['Close'].rolling(window=21).mean()
        df['MA_50'] = df['Close'].rolling(window=50).mean()
        df['MA_200'] = df['Close'].rolling(window=200).mean()
        
        ma5 = df['MA_5'].iloc[-1]
        ma10 = df['MA_10'].iloc[-1]
        ma21 = df['MA_21'].iloc[-1]
        ma50 = df['MA_50'].iloc[-1]
        ma200 = df['MA_200'].iloc[-1]
        
        # Determine Long/Medium Trends
        trend_50d = "UP" if ma50 > df['MA_50'].iloc[-10] else "DOWN"
        trend_200d = "UP" if ma200 > df['MA_200'].iloc[-20] else "DOWN"

        # RSI & Volatility Stop Loss (ATR)
        df['RSI'] = ta.momentum.RSIIndicator(close=df['Close'], window=14).rsi()
        rsi_val = df['RSI'].iloc[-1]
        
        df['ATR'] = ta.volatility.AverageTrueRange(high=df['High'], low=df['Low'], close=df['Close'], window=14).average_true_range()
        atr_val = df['ATR'].iloc[-1]
        stop_loss = current_price - (2 * atr_val)

        # Volume Profile (OBV)
        df['OBV'] = ta.volume.OnBalanceVolumeIndicator(close=df['Close'], volume=df['Volume']).on_balance_volume()
        df['OBV_MA'] = df['OBV'].rolling(window=21).mean()
        obv_bullish = df['OBV'].iloc[-1] > df['OBV_MA'].iloc[-1]

        is_tight = self.calculate_3_weeks_tight(df)

        # ==========================================
        # PART 2: FUNDAMENTAL EXTRACTION
        # ==========================================
        company_name = info.get('shortName', ticker)
        sector = info.get('sector', 'Unknown')
        currency = info.get('currency', 'USD')
        
        trailing_pe = info.get('trailingPE', None)
        forward_pe = info.get('forwardPE', None)
        peg_ratio = info.get('pegRatio', None)
        price_to_book = info.get('priceToBook', None)
        
        profit_margin = info.get('profitMargins', None)
        roe = info.get('returnOnEquity', None)
        revenue_growth = info.get('revenueGrowth', None)
        earnings_growth = info.get('earningsGrowth', None)
        
        debt_to_equity = info.get('debtToEquity', None)
        current_ratio = info.get('currentRatio', None)
        operating_cash_flow = info.get('operatingCashflow', None)
        
        dividend_yield = info.get('dividendYield', None)
        ex_dividend_date = info.get('exDividendDate', None)
        target_price = info.get('targetMeanPrice', None)
        analyst_rating = info.get('recommendationKey', 'None').upper()
        
        earnings_ts = info.get('earningsTimestamp', None)
        if earnings_ts:
            next_earnings_date = datetime.fromtimestamp(earnings_ts).strftime('%Y-%m-%d')
        else:
            next_earnings_date = "Unknown"

        short_interest = info.get('shortPercentOfFloat', None)
        institutional_ownership = info.get('heldPercentInstitutions', None)
        beta = info.get('beta', None)

        peter_lynch_peg = None
        if trailing_pe and earnings_growth and earnings_growth > 0:
            peter_lynch_peg = trailing_pe / (earnings_growth * 100)

        # ==========================================
        # PART 3: SCORING & TRANSPARENCY (HTML FORMATTED)
        # ==========================================
        score = 0
        breakdown = []

        if current_price > ma5: 
            score += 15
            breakdown.append("+15: <abbr title='The current price is trading above the 5-Day Moving Average, signaling very short-term momentum.'>Price > 5D MA</abbr>")
        else:
            breakdown.append("+0: <abbr title='The current price is trading below the 5-Day Moving Average, signaling short-term weakness.'>Price < 5D MA</abbr>")

        if ma5 > ma10 and ma10 > ma21: 
            score += 15
            breakdown.append("+15: <abbr title='The 5, 10, and 21-day averages are perfectly stacked. This indicates strong, unified short-term trend alignment.'>Short-term MAs aligned</abbr>")
        else:
            breakdown.append("+0: <abbr title='The moving averages are crisscrossing, indicating choppy or weak price action.'>Short-term MAs unaligned</abbr>")

        if trend_200d == "UP":
            score += 15
            breakdown.append("+15: <abbr title='The 200-Day Moving Average is rising. This proves the long-term institutional trend is bullish.'>200D Institutional Trend is UP</abbr>")
        else:
            breakdown.append("+0: <abbr title='The 200-Day Moving Average is falling. Institutions are actively selling this stock over the long term.'>200D Trend is DOWN</abbr>")

        if 40 <= rsi_val <= 65: 
            score += 15
            breakdown.append("+15: <abbr title='RSI is between 40 and 65, meaning the stock has room to grow without being dangerously overextended.'>RSI in healthy momentum zone</abbr>")
        else:
            breakdown.append(f"+0: <abbr title='RSI is either above 70 (overbought risk) or below 30 (oversold/crashing).'>RSI is {rsi_val:.1f} (Overbought/Oversold risk)</abbr>")

        if is_tight: 
            score += 20
            breakdown.append("+20: <abbr title='Weekly closes have barely moved for 3 weeks. This indicates institutions are quietly accumulating shares without pushing the price up.'>'3-Weeks-Tight' volatility contraction detected</abbr>")
        else:
            breakdown.append("+0: <abbr title='Normal volatility. No tight weekly compression pattern detected.'>No tight weekly compression</abbr>")

        if obv_bullish: 
            score += 20
            breakdown.append("+20: <abbr title='On-Balance Volume is rising faster than its moving average, confirming that up-days have higher volume than down-days.'>OBV indicates Institutional Accumulation</abbr>")
        else:
            breakdown.append("+0: <abbr title='On-Balance Volume is falling, indicating that selling volume is outpacing buying volume.'>OBV indicates Distribution/Selling</abbr>")

        score = min(score, 100)

        if score >= 80: signal = "STRONG BUY"
        elif score >= 60: signal = "BULLISH / HOLD"
        elif score >= 40: signal = "NEUTRAL"
        else: signal = "BEARISH / CAUTION"

        # Construct highly formatted HTML for the Frontend
        notes_html = "<strong>Score Breakdown:</strong><br><ul style='margin-top: 5px; margin-bottom: 15px; font-size: 15px; color: #ccc;'>"
        for item in breakdown:
            notes_html += f"<li style='margin-bottom: 5px;'>{item}</li>"
        notes_html += "</ul>"
        
        notes_html += f"<strong>Risk Management:</strong> Mathematical <abbr title='Based on Average True Range. If the stock drops below this line, its normal mathematical volatility is broken.'>ATR Stop-Loss</abbr> is {currency} {stop_loss:.2f}.<br><br>"
        
        if rsi_val > 70:
            notes_html += "<strong><span style='color: #ff4d4d;'>Warning:</span></strong> <abbr title='When RSI passes 70, the asset has gone up too quickly and is highly susceptible to a sudden pullback.'>Stock is technically overbought.</abbr> Initiating new positions is high risk. Look to take profits.<br>"
        if short_interest and short_interest > 0.10:
            notes_html += f"<strong><span style='color: #ffaa00;'>Warning:</span></strong> High <abbr title='Percentage of shares being shorted by pessimistic investors. High short interest can trigger violent upwards squeezes.'>Short Interest</abbr> ({short_interest*100:.1f}%). Expect extreme volatility.<br>"

        self.save_to_db(
            ticker, company_name, sector, currency,
            current_price, ma5, ma10, ma21, trend_50d, trend_200d, rsi_val, stop_loss,
            trailing_pe, forward_pe, peg_ratio, peter_lynch_peg, price_to_book,
            profit_margin, roe, revenue_growth, debt_to_equity, current_ratio, operating_cash_flow,
            dividend_yield, ex_dividend_date, target_price, analyst_rating, next_earnings_date,
            short_interest, institutional_ownership, beta,
            score, signal, notes_html
        )

    def save_to_db(self, ticker, company_name, sector, currency,
                   price, ma5, ma10, ma21, trend_50d, trend_200d, rsi, stop_loss,
                   trailing_pe, forward_pe, peg_ratio, peter_lynch_peg, price_to_book,
                   profit_margin, roe, revenue_growth, debt_to_equity, current_ratio, operating_cash_flow,
                   dividend_yield, ex_dividend_date, target_price, analyst_rating, next_earnings_date,
                   short_interest, institutional_ownership, beta,
                   score, signal, notes):
        
        cursor = self.conn.cursor()
        
        query = '''
            INSERT OR REPLACE INTO stock_signals (
                ticker, last_updated, company_name, sector, currency,
                current_price, ma_5_day, ma_10_day, ma_21_day, trend_50d, trend_200d, rsi_14, atr_stop_loss,
                trailing_pe, forward_pe, peg_ratio, peter_lynch_peg, price_to_book,
                profit_margin, roe, revenue_growth, debt_to_equity, current_ratio, operating_cash_flow,
                dividend_yield, ex_dividend_date, target_price, analyst_rating, next_earnings_date,
                short_interest, institutional_ownership, beta,
                composite_score, overall_signal, educational_notes
            ) VALUES (
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?
            )
        '''
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        values = (
            ticker, timestamp, company_name, sector, currency,
            price, ma5, ma10, ma21, trend_50d, trend_200d, rsi, stop_loss,
            trailing_pe, forward_pe, peg_ratio, peter_lynch_peg, price_to_book,
            profit_margin, roe, revenue_growth, debt_to_equity, current_ratio, operating_cash_flow,
            dividend_yield, ex_dividend_date, target_price, analyst_rating, next_earnings_date,
            short_interest, institutional_ownership, beta,
            score, signal, notes
        )
        
        cursor.execute(query, values)
        self.conn.commit()
        print(f"[SUCCESS] Analyzed {ticker} | Signal: {signal} | Score: {score}/100")

    def run_all(self):
        print("Starting Institutional Quantamental Engine...")
        for filename in os.listdir(HISTORICAL_DIR):
            if filename.endswith(".parquet") and "SP500" not in filename:
                ticker = filename.replace(".parquet", "")
                self.analyze_ticker(ticker)
        print("Analysis Complete. Master Database is fully updated.")

if __name__ == "__main__":
    engine = QuantEngine()
    engine.run_all()