# quant_signals.py
import pandas as pd
import numpy as np
import ta
import os
import json
import logging
from datetime import datetime
from config import HISTORICAL_DIR, FUNDAMENTALS_DIR
from database import get_connection

# Configure robust module-level logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - QUANT_SIGNALS - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def get_candlestick_patterns(prev2, prev1, curr):
    """
    Algorithmic Candlestick Pattern Recognition (Hierarchical Engine).
    Evaluates in order of mathematical priority to prevent overlapping bugs.
    """
    patterns = []
    
    # --- Anatomy of the Current Candle ---
    curr_body = abs(curr['Open'] - curr['Close'])
    curr_body_safe = max(curr_body, 0.001) 
    curr_range = max(curr['High'] - curr['Low'], 0.001) 
    curr_upper_wick = curr['High'] - max(curr['Open'], curr['Close'])
    curr_lower_wick = min(curr['Open'], curr['Close']) - curr['Low']
    curr_is_bullish = curr['Close'] > curr['Open']
    curr_is_bearish = curr['Close'] < curr['Open']

    # --- Anatomy of the Previous Candles ---
    prev1_body = abs(prev1['Open'] - prev1['Close'])
    prev1_range = max(prev1['High'] - prev1['Low'], 0.001)
    prev1_is_bearish = prev1['Close'] < prev1['Open']
    prev1_is_bullish = prev1['Close'] > prev1['Open']
    
    prev2_body = abs(prev2['Open'] - prev2['Close'])
    prev2_is_bearish = prev2['Close'] < prev2['Open']
    
    # ==========================================
    # TIER 1: 3-CANDLE PATTERNS (Highest Priority)
    # ==========================================
    
    # 1. Morning Star (Bullish Reversal)
    # Day 1: Strong Bearish. Day 2: Indecision/Gap down. Day 3: Strong Bullish pushing > 50% into Day 1.
    prev2_midpoint = (prev2['Open'] + prev2['Close']) / 2
    if prev2_is_bearish and prev2_body > (prev2['High'] - prev2['Low']) * 0.5:  # Day 1 is solid red
        if prev1_body <= (prev1_range * 0.3):                                   # Day 2 is a doji/spinning top
            if curr_is_bullish and curr['Close'] > prev2_midpoint:              # Day 3 is green and breaches Day 1 midpoint
                patterns.append({
                    "name": "🌅 Morning Star",
                    "tooltip": "A highly reliable 3-day bottoming pattern. Panic selling (Day 1) was met with indecision (Day 2), followed by strong institutional buying (Day 3) that recovered the majority of the original dump.",
                    "breakdown": "+20: <abbr title='3-Day Pattern: Heavy dump, followed by indecision, followed by violent recovery buying.'>Morning Star Reversal</abbr>",
                    "score": 20
                })
                return patterns # Return immediately to prevent lower-tier overlaps

    # ==========================================
    # TIER 2: 2-CANDLE PATTERNS
    # ==========================================

    # 2. Bullish Engulfing
    if prev1_is_bearish and curr_is_bullish and (curr['Open'] <= prev1['Close']) and (curr['Close'] >= prev1['Open']):
        patterns.append({
            "name": "🐂 Bullish Engulfing",
            "tooltip": "Buyers completely overwhelmed sellers. The current green body fully engulfed the previous red body. Signals a potential reversal to the upside.",
            "breakdown": "+10: <abbr title='Buyers completely overwhelmed sellers. The current green body fully engulfed the previous red body.'>Bullish Engulfing Pattern</abbr>",
            "score": 10
        })
        return patterns
        
    # 3. Bearish Engulfing
    if prev1_is_bullish and curr_is_bearish and (curr['Open'] >= prev1['Close']) and (curr['Close'] <= prev1['Open']):
        patterns.append({
            "name": "🐻 Bearish Engulfing",
            "tooltip": "Sellers took total control. The current red body fully engulfed the previous green body. A stark warning signal of impending downside.",
            "breakdown": "-15: <abbr title='Sellers took total control. The current red body fully engulfed the previous green body. Warning signal.'>Bearish Engulfing Pattern</abbr>",
            "score": -15
        })
        return patterns

    # ==========================================
    # TIER 3: 1-CANDLE PATTERNS (Lowest Priority)
    # ==========================================

    # 4. Hammer / Dragonfly Doji (Bullish Rejection)
    if curr_lower_wick >= (2 * curr_body_safe) and curr_upper_wick <= (0.2 * curr_range):
        patterns.append({
            "name": "🔨 Hammer Rejection",
            "tooltip": "Sellers tried to crash the price intraday, but institutional buyers violently rejected it and bought the dip. Indicates strong underlying support.",
            "breakdown": "+5: <abbr title='Sellers tried to crash the price intraday, but institutional buyers violently rejected it. Indicates strong support.'>Bullish Hammer Candlestick</abbr>",
            "score": 5
        })
        
    # 5. Shooting Star / Gravestone Doji (Bearish Rejection)
    elif curr_upper_wick >= (2 * curr_body_safe) and curr_lower_wick <= (0.2 * curr_range):
        patterns.append({
            "name": "🌠 Shooting Star",
            "tooltip": "Retail buyers tried to push the price up, but institutional sellers aggressively dumped shares into the rally. Momentum is fading.",
            "breakdown": "-10: <abbr title='Retail buyers tried to push the price up, but institutional sellers aggressively dumped shares.'>Bearish Shooting Star</abbr>",
            "score": -10
        })
        
    # 6. Standard Doji (Indecision)
    elif curr_body <= (curr_range * 0.1):
        patterns.append({
            "name": "⚖️ Doji",
            "tooltip": "The opening and closing prices are mathematically almost identical. This represents total equilibrium and indecision between buyers and sellers.",
            "breakdown": "+0: <abbr title='Opening and closing prices are mathematically almost identical. Total equilibrium/indecision between buyers and sellers.'>Doji Candlestick</abbr>",
            "score": 0
        })
        
    return patterns

class QuantEngine:
    def __init__(self):
        # Establish connection to the SQLite database
        self.conn = get_connection()

    def load_parquet(self, ticker):
        """Loads the historical daily data from the local Parquet file."""
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

    def calculate_vcp_breakout(self, df):
        """Advanced Minervini VCP: Price contraction AND Volume dry-up."""
        weekly_data = df.resample('W-FRI').agg({'Close': 'last', 'Volume': 'sum'})
        if len(weekly_data) < 4:
            return False, False
        
        last_3_weeks = weekly_data.iloc[-4:-1]
        variance_pct = (last_3_weeks['Close'].max() - last_3_weeks['Close'].min()) / last_3_weeks['Close'].min()
        is_tight = variance_pct <= 0.025
        
        # Volume Dry-up Check gracefully handles missing 50-day data
        avg_vol_50d = df['Volume'].rolling(50).mean().iloc[-1]
        if pd.isna(avg_vol_50d):
            is_dry_volume = False
        else:
            avg_vol_3w = last_3_weeks['Volume'].mean() / 5 # Approx daily average over the 3 weeks
            is_dry_volume = avg_vol_3w < (avg_vol_50d * 0.8) # Volume must be 20% below average
        
        return is_tight, is_dry_volume

    def detect_bearish_divergence(self, df):
        """Checks if price is making higher highs while RSI makes lower highs."""
        # Simple local peak detection over last 30 days
        last_30 = df.tail(30)
        if len(last_30) < 30: return False
        
        price_max_idx = last_30['Close'].idxmax()
        rsi_at_max_price = last_30.loc[price_max_idx, 'RSI']
        
        # If the absolute highest RSI in the period occurred BEFORE the highest price, momentum is fading
        rsi_max_idx = last_30['RSI'].idxmax()
        if price_max_idx > rsi_max_idx and rsi_at_max_price < 60 and last_30['RSI'].max() > 70:
            return True
        return False

    def analyze_ticker(self, ticker):
        """
        Runs the combined Technical and Fundamental analysis.
        NOTE: Any new mathematical scoring rules added below MUST be documented in the glossary.
        """
        try:
            df = self.load_parquet(ticker)
            info = self.load_fundamentals(ticker)
            
            # --- DYNAMIC BENCHMARK SELECTION ---
            currency = info.get('currency', 'USD')
            is_uk_asset = ticker.endswith('.L') or currency in ['GBp', 'GBP']
            baseline_name = "FTSE_BASELINE" if is_uk_asset else "SP500_BASELINE"
            df_baseline = self.load_parquet(baseline_name)
            
            # Decoupled 200-day limit to support recent IPOs or API data truncations
            if df is None or len(df) < 21:
                logger.warning(f"[SKIP] Not enough historical data to analyze {ticker} (requires at least 21 days).")
                return

            # ==========================================
            # PART 1: TECHNICAL ANALYSIS & MATH
            # ==========================================
            current_price = df['Close'].iloc[-1]
            
            df['MA_5'] = df['Close'].rolling(window=5).mean()
            df['MA_10'] = df['Close'].rolling(window=10).mean()
            df['MA_21'] = df['Close'].rolling(window=21).mean()
            df['MA_50'] = df['Close'].rolling(window=50).mean()
            df['MA_200'] = df['Close'].rolling(window=200).mean()
            
            # Safe extraction avoiding NaNs
            ma5 = df['MA_5'].iloc[-1] if not pd.isna(df['MA_5'].iloc[-1]) else None
            ma10 = df['MA_10'].iloc[-1] if not pd.isna(df['MA_10'].iloc[-1]) else None
            ma21 = df['MA_21'].iloc[-1] if not pd.isna(df['MA_21'].iloc[-1]) else None
            ma50 = df['MA_50'].iloc[-1] if not pd.isna(df['MA_50'].iloc[-1]) else None
            ma200 = df['MA_200'].iloc[-1] if not pd.isna(df['MA_200'].iloc[-1]) else None
            
            # Safe Trend Calculation protecting against bounds errors
            trend_50d = "UP" if ma50 and len(df) >= 60 and ma50 > df['MA_50'].iloc[-10] else "DOWN"
            trend_200d = "UP" if ma200 and len(df) >= 220 and ma200 > df['MA_200'].iloc[-20] else "DOWN"

            df['RSI'] = ta.momentum.RSIIndicator(close=df['Close'], window=14).rsi()
            rsi_val = df['RSI'].iloc[-1]
            
            df['ATR'] = ta.volatility.AverageTrueRange(high=df['High'], low=df['Low'], close=df['Close'], window=14).average_true_range()
            atr_val = df['ATR'].iloc[-1]
            stop_loss = current_price - (2 * atr_val) if not pd.isna(atr_val) else None

            df['OBV'] = ta.volume.OnBalanceVolumeIndicator(close=df['Close'], volume=df['Volume']).on_balance_volume()
            df['OBV_MA'] = df['OBV'].rolling(window=21).mean()
            obv_bullish = df['OBV'].iloc[-1] > df['OBV_MA'].iloc[-1] if not pd.isna(df['OBV_MA'].iloc[-1]) else False

            macd = ta.trend.MACD(close=df['Close'])
            df['MACD_Line'] = macd.macd()
            df['MACD_Signal'] = macd.macd_signal()
            df['MACD_Hist'] = macd.macd_diff()

            is_tight, is_dry_volume = self.calculate_vcp_breakout(df)
            is_bearish_divergence = self.detect_bearish_divergence(df)

            # Relative Strength Math
            rs_slope = 0
            is_market_leader = False
            if df_baseline is not None and not df_baseline.empty:
                baseline_aligned = df_baseline['Close'].reindex(df.index, method='ffill')
                df['RS_Line'] = df['Close'] / baseline_aligned
                if len(df['RS_Line'].dropna()) >= 60:
                    y = df['RS_Line'].dropna().tail(60).values
                    x = np.arange(len(y))
                    slope, _ = np.polyfit(x, y, 1)
                    rs_slope = slope
                    # If slope is sharply up and we are near the 60-day RS High
                    if rs_slope > 0 and df['RS_Line'].iloc[-1] >= (df['RS_Line'].tail(60).max() * 0.95):
                        is_market_leader = True

            # ==========================================
            # PART 2: FUNDAMENTAL EXTRACTION
            # ==========================================
            quote_type = info.get('quoteType', 'EQUITY')
            is_fund = quote_type in ['ETF', 'MUTUALFUND']
            company_name = info.get('shortName', ticker)
            sector = info.get('category', info.get('sector', 'Fund')) if is_fund else info.get('sector', 'Unknown')
            currency = info.get('currency', 'USD')
            fifty_two_week_low = info.get('fiftyTwoWeekLow', None)
            fifty_two_week_high = info.get('fiftyTwoWeekHigh', None)
            
            # --- PHASE 6: Country Extraction & Normalization ---
            country_raw = info.get('country', 'Unknown')
            if country_raw == "United Kingdom":
                country = "UK"
            elif country_raw == "United States":
                country = "US"
            else:
                country = country_raw
            
            ytd_return = info.get('ytdReturn', None)
            total_assets = info.get('totalAssets', None)
            nav_price = info.get('navPrice', None)
            expense_ratio = info.get('expenseRatio', info.get('annualReportExpenseRatio', None))
            top_holdings = json.dumps(info.get('holdings', []))
            sector_weightings = json.dumps(info.get('sectorWeightings', []))
            
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
            next_earnings_date = datetime.fromtimestamp(earnings_ts).strftime('%Y-%m-%d') if earnings_ts else "Unknown"

            short_interest = info.get('shortPercentOfFloat', None)
            institutional_ownership = info.get('heldPercentInstitutions', None)
            beta = info.get('beta', None)

            peter_lynch_peg = None
            if trailing_pe and earnings_growth and earnings_growth > 0:
                peter_lynch_peg = trailing_pe / (earnings_growth * 100)

            # ==========================================
            # PART 3: SCORING & SETUP TAGS
            # ==========================================
            score = 0
            breakdown = []
            tags = []

            # Tag: Algorithmic Candlestick Pattern Injection
            if not is_fund and len(df) >= 3:
                candlestick_patterns = get_candlestick_patterns(df.iloc[-3], df.iloc[-2], df.iloc[-1])
                for pattern in candlestick_patterns:
                    tags.append({"name": pattern["name"], "tooltip": pattern["tooltip"]})
                    breakdown.append(pattern["breakdown"])
                    score += pattern["score"]

            # Tag: MACD Reversal
            if df['MACD_Line'].iloc[-1] > df['MACD_Signal'].iloc[-1] and df['MACD_Line'].iloc[-2] <= df['MACD_Signal'].iloc[-2]:
                if df['MACD_Line'].iloc[-1] < 0 and rsi_val > 30:
                    tags.append({
                        "name": "⚡ MACD Reversal", 
                        "tooltip": "The MACD momentum line just crossed positive from below the zero line. This is an early indicator that a downtrend is mathematically exhausting itself."
                    })
                    score += 10
                    breakdown.append("+10: <abbr title='MACD just crossed positive from below the zero line. An early indicator that a downtrend is ending.'>MACD Golden Reversal</abbr>")

            # Tag: VCP Breakout
            if is_tight and is_dry_volume and not is_fund:
                tags.append({
                    "name": "🔥 VCP Breakout", 
                    "tooltip": "Volatility Contraction Pattern. Price has tightened over 3 weeks and volume has dried up. Institutions have stopped selling; asset is ready for a breakout."
                })
                score += 20
                breakdown.append("+20: <abbr title='Price tightened over 3 weeks AND volume dried up. Institutions have stopped selling; ready for breakout.'>Minervini VCP (Price + Vol Contraction)</abbr>")
            elif is_tight:
                score += 10
                breakdown.append("+10: <abbr title='Price is tight, but volume has not dried up yet. Potential base forming.'>3-Weeks-Tight (No Vol Contraction)</abbr>")

            # Tag: Market Leader
            if is_market_leader and rs_slope > 0:
                tags.append({
                    "name": "👑 Market Leader", 
                    "tooltip": "The Relative Strength line compared to the Benchmark is sloped sharply up. This asset is aggressively absorbing market liquidity."
                })
                score += 15
                breakdown.append("+15: <abbr title='Relative Strength line is sloped sharply up. This asset is absorbing market liquidity.'>Market Leader vs Benchmark</abbr>")
            elif rs_slope < -0.001:
                breakdown.append("+0: <abbr title='Relative Strength slope is negative. This asset is underperforming the broader market.'>Laggard vs Benchmark</abbr>")

            # Standard Core Score
            if ma5 and current_price > ma5: 
                score += 15
                breakdown.append("+15: Price > 5D MA (Short-term Momentum)")
            
            if ma5 and ma10 and ma21 and ma5 > ma10 and ma10 > ma21: 
                score += 15
                breakdown.append("+15: MAs Aligned (5 > 10 > 21)")

            if trend_200d == "UP":
                score += 15
                breakdown.append("+15: 200D Trend UP (Institutional Backing)")

            if not pd.isna(rsi_val) and 40 <= rsi_val <= 65: 
                score += 10
                breakdown.append("+10: RSI Healthy (Room to run)")

            if is_fund or obv_bullish: 
                score += 20
                breakdown.append("+20: OBV Bullish / Fund Exemption")

            # Tag: Divergence Circuit Breaker
            if is_bearish_divergence and not is_fund:
                tags.append({
                    "name": "🚨 Divergence Warning", 
                    "tooltip": "Price made a higher high, but the RSI oscillator made a lower high. Momentum is secretly dying. High risk of an imminent dump."
                })
                score -= 30
                breakdown.append("-30: <abbr title='Price made a higher high, but RSI made a lower high. Momentum is secretly dying. High risk of dump.'>Algorithmic Bearish Divergence</abbr>")

            score = max(0, min(score, 100))

            if score >= 80: signal = "STRONG BUY"
            elif score >= 60: signal = "BULLISH / HOLD"
            elif score >= 40: signal = "NEUTRAL"
            else: signal = "BEARISH / CAUTION"

            # Construct Educational Notes
            notes_html = "<strong>Algorithmic Breakdown:</strong><br><ul style='margin-top: 5px; margin-bottom: 15px; font-size: 15px; color: #ccc; padding-left: 20px;'>"
            for item in breakdown:
                notes_html += f"<li style='margin-bottom: 5px;'>{item}</li>"
            notes_html += "</ul>"
            
            if stop_loss:
                notes_html += f"<strong>Risk Management:</strong> Mathematical <abbr title='Based on Average True Range.'>ATR Stop-Loss</abbr> is {stop_loss:,.2f} {currency}.<br><br>"
            
            if not is_fund and not pd.isna(rsi_val) and rsi_val > 70:
                notes_html += "<strong><span style='color: #ff4d4d;'>Warning:</span></strong> Stock is technically overbought (RSI > 70).<br>"

            # Save to DB (JSON dumping the array of dictionaries)
            tags_json = json.dumps(tags)
            
            # --- PHASE 6: Pass country to save_to_db ---
            self.save_to_db(
                ticker, company_name, sector, country, currency, quote_type,
                current_price, ma5, ma10, ma21, trend_50d, trend_200d, rsi_val, stop_loss,
                fifty_two_week_low, fifty_two_week_high,
                trailing_pe, forward_pe, peg_ratio, peter_lynch_peg, price_to_book,
                profit_margin, roe, revenue_growth, debt_to_equity, current_ratio, operating_cash_flow,
                ytd_return, total_assets, nav_price, expense_ratio, top_holdings, sector_weightings,
                dividend_yield, ex_dividend_date, target_price, analyst_rating, next_earnings_date,
                short_interest, institutional_ownership, beta,
                score, signal, notes_html, tags_json
            )
            
        except Exception as e:
            logger.error(f"Failed to analyze {ticker}: {e}")

    def save_to_db(self, ticker, company_name, sector, country, currency, quote_type,
                   price, ma5, ma10, ma21, trend_50d, trend_200d, rsi, stop_loss,
                   fifty_two_week_low, fifty_two_week_high,
                   trailing_pe, forward_pe, peg_ratio, peter_lynch_peg, price_to_book,
                   profit_margin, roe, revenue_growth, debt_to_equity, current_ratio, operating_cash_flow,
                   ytd_return, total_assets, nav_price, expense_ratio, top_holdings, sector_weightings,
                   dividend_yield, ex_dividend_date, target_price, analyst_rating, next_earnings_date,
                   short_interest, institutional_ownership, beta,
                   score, signal, notes, tags_json):
        
        # Internal cleaner to aggressively handle pandas NaNs, Inf, and String Variants
        def _clean(v):
            if v is None: 
                return None
            if isinstance(v, str):
                if v.lower() in ['nan', 'infinity', '-infinity', 'inf', '-inf']:
                    return None
                try:
                    # Catch strings that are implicitly numeric
                    val = float(v)
                    if pd.isna(val) or np.isinf(val): return None
                    return val
                except ValueError:
                    return v # Preserve genuine strings
            if isinstance(v, (float, int)) and (pd.isna(v) or np.isinf(v)): 
                return None
            return v

        cursor = self.conn.cursor()
        
        # --- PHASE 6: Update SQL to include country column ---
        query = '''
            INSERT OR REPLACE INTO stock_signals (
                ticker, last_updated, company_name, sector, country, currency, quote_type,
                current_price, ma_5_day, ma_10_day, ma_21_day, trend_50d, trend_200d, rsi_14, atr_stop_loss,
                fifty_two_week_low, fifty_two_week_high,
                trailing_pe, forward_pe, peg_ratio, peter_lynch_peg, price_to_book,
                profit_margin, roe, revenue_growth, debt_to_equity, current_ratio, operating_cash_flow,
                ytd_return, total_assets, nav_price, expense_ratio, top_holdings, sector_weightings,
                dividend_yield, ex_dividend_date, target_price, analyst_rating, next_earnings_date,
                short_interest, institutional_ownership, beta,
                composite_score, overall_signal, educational_notes, setup_tags
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?
            )
        '''
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        values = (
            ticker, timestamp, company_name, sector, country, currency, quote_type,
            _clean(price), _clean(ma5), _clean(ma10), _clean(ma21), trend_50d, trend_200d, _clean(rsi), _clean(stop_loss),
            _clean(fifty_two_week_low), _clean(fifty_two_week_high),
            _clean(trailing_pe), _clean(forward_pe), _clean(peg_ratio), _clean(peter_lynch_peg), _clean(price_to_book),
            _clean(profit_margin), _clean(roe), _clean(revenue_growth), _clean(debt_to_equity), _clean(current_ratio), _clean(operating_cash_flow),
            _clean(ytd_return), _clean(total_assets), _clean(nav_price), _clean(expense_ratio), top_holdings, sector_weightings,
            _clean(dividend_yield), ex_dividend_date, _clean(target_price), analyst_rating, next_earnings_date,
            _clean(short_interest), _clean(institutional_ownership), _clean(beta),
            _clean(score), signal, notes, tags_json
        )
        
        cursor.execute(query, values)
        self.conn.commit()
        logger.info(f"[SUCCESS] Analyzed {ticker} | Signal: {signal} | Score: {score}/100")

    def run_all(self):
        logger.info("Starting Institutional Quantamental Engine...")
        for filename in os.listdir(HISTORICAL_DIR):
            if filename.endswith(".parquet") and "BASELINE" not in filename:
                ticker = filename.replace(".parquet", "")
                self.analyze_ticker(ticker)
        logger.info("Analysis Complete. Master Database is fully updated.")

if __name__ == "__main__":
    engine = QuantEngine()
    engine.run_all()