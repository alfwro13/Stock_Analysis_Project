# quant_signals.py
import os
import json
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple

import numpy as np
import pandas as pd
import ta

from config import HISTORICAL_DIR, FUNDAMENTALS_DIR
from database import get_connection

logger = logging.getLogger(__name__)


def get_candlestick_patterns(prev2: pd.Series, prev1: pd.Series, curr: pd.Series) -> List[Dict[str, Any]]:
    """
    Algorithmic Candlestick Pattern Recognition (Hierarchical Engine).
    Evaluates in order of mathematical priority to prevent overlapping bugs.
    """
    patterns: List[Dict[str, Any]] = []
    
    # --- Anatomy of the Current Candle ---
    curr_body = abs(curr['Open'] - curr['Close'])
    curr_body_safe = max(curr_body, 0.001) 
    curr_range = max(curr['High'] - curr['Low'], 0.001) 
    curr_upper_wick = curr['High'] - max(curr['Open'], curr['Close'])
    curr_lower_wick = min(curr['Open'], curr['Close']) - curr['Low']
    curr_is_bullish = bool(curr['Close'] > curr['Open'])
    curr_is_bearish = bool(curr['Close'] < curr['Open'])

    # --- Anatomy of the Previous Candles ---
    prev1_body = abs(prev1['Open'] - prev1['Close'])
    prev1_range = max(prev1['High'] - prev1['Low'], 0.001)
    prev1_is_bearish = bool(prev1['Close'] < prev1['Open'])
    prev1_is_bullish = bool(prev1['Close'] > prev1['Open'])
    
    prev2_body = abs(prev2['Open'] - prev2['Close'])
    prev2_is_bearish = bool(prev2['Close'] < prev2['Open'])
    
    # ==========================================
    # TIER 1: 3-CANDLE PATTERNS (Highest Priority)
    # ==========================================
    
    # 1. Morning Star (Bullish Reversal)
    # Day 1: Strong Bearish. Day 2: Indecision/Gap down. Day 3: Strong Bullish pushing > 50% into Day 1.
    prev2_midpoint = (prev2['Open'] + prev2['Close']) / 2.0
    if prev2_is_bearish and prev2_body > (prev2['High'] - prev2['Low']) * 0.5:
        if prev1_body <= (prev1_range * 0.3):
            if curr_is_bullish and curr['Close'] > prev2_midpoint:
                patterns.append({
                    "name": "🌅 Morning Star",
                    "tooltip": "A highly reliable 3-day bottoming pattern. Panic selling (Day 1) was met with indecision (Day 2), followed by strong institutional buying (Day 3) that recovered the majority of the original dump.",
                    "breakdown": "+20: <abbr title='3-Day Pattern: Heavy dump, followed by indecision, followed by violent recovery buying.'>Morning Star Reversal</abbr>",
                    "score": 20
                })
                return patterns 

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
    if curr_lower_wick >= (2.0 * curr_body_safe) and curr_upper_wick <= (0.2 * curr_range):
        patterns.append({
            "name": "🔨 Hammer Rejection",
            "tooltip": "Sellers tried to crash the price intraday, but institutional buyers violently rejected it and bought the dip. Indicates strong underlying support.",
            "breakdown": "+5: <abbr title='Sellers tried to crash the price intraday, but institutional buyers violently rejected it. Indicates strong support.'>Bullish Hammer Candlestick</abbr>",
            "score": 5
        })
        
    # 5. Shooting Star / Gravestone Doji (Bearish Rejection)
    elif curr_upper_wick >= (2.0 * curr_body_safe) and curr_lower_wick <= (0.2 * curr_range):
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
    def __init__(self) -> None:
        self.conn = get_connection()

    def close(self):
        """Safely closes the database connection."""
        if hasattr(self, 'conn') and self.conn:
            self.conn.close()

    def load_parquet(self, ticker: str) -> Optional[pd.DataFrame]:
        """Loads the historical daily data from the local Parquet file."""
        filepath = HISTORICAL_DIR / f"{ticker}.parquet"
        if not filepath.exists():
            return None
        return pd.read_parquet(filepath)

    def load_fundamentals(self, ticker: str) -> dict:
        """Loads the raw Yahoo Finance .info dictionary."""
        filepath = FUNDAMENTALS_DIR / f"{ticker}.json"
        if not filepath.exists():
            return {}
        with open(filepath, 'r') as f:
            return json.load(f)

    def calculate_vcp_breakout(self, df: pd.DataFrame) -> Tuple[bool, bool]:
        """Advanced Minervini VCP: Price contraction AND Volume dry-up."""
        weekly_data = df.resample('W-FRI').agg({
            'Open': 'first',
            'High': 'max',
            'Low': 'min',
            'Close': 'last',
            'Volume': 'sum'
        })
        
        if len(weekly_data) < 4:
            return False, False
        
        last_3_weeks = weekly_data.iloc[-4:-1]
        
        # Calculate variance using the High-Low range across the 3 weeks
        max_high = last_3_weeks['High'].max()
        min_low = last_3_weeks['Low'].min()
        
        variance_pct = (max_high - min_low) / min_low if min_low > 0 else 1.0
        is_tight = bool(variance_pct <= 0.025)
        
        avg_vol_50d = df['Volume'].rolling(50).mean().iloc[-1]
        if pd.isna(avg_vol_50d):
            is_dry_volume = False
        else:
            avg_vol_3w = last_3_weeks['Volume'].mean() / 5.0 # Approx daily average over the 3 weeks
            is_dry_volume = bool(avg_vol_3w < (avg_vol_50d * 0.8)) # Volume must be 20% below average
        
        return is_tight, is_dry_volume

    def detect_bearish_divergence(self, df: pd.DataFrame) -> bool:
        """Checks if price is making structurally higher highs while RSI makes lower highs."""
        last_30 = df.tail(30).copy()
        if len(last_30) < 30: 
            return False
        
        # Split into two 15-day halves to ensure true structural peaks
        p1 = last_30.iloc[:15]
        p2 = last_30.iloc[15:]
        
        price_peak1 = p1['High'].max() if 'High' in p1.columns else p1['Close'].max()
        price_peak2 = p2['High'].max() if 'High' in p2.columns else p2['Close'].max()
        
        rsi_peak1 = p1['RSI'].max()
        rsi_peak2 = p2['RSI'].max()
        
        # Divergence confirmed: Price is higher, RSI is lower, and the first RSI peak was overbought
        if price_peak2 > price_peak1 and rsi_peak2 < rsi_peak1 and rsi_peak1 > 70.0:
            return True
        return False

    def analyze_ticker(self, ticker: str) -> None:
        """
        Runs the combined Technical and Fundamental analysis.
        Gracefully handles missing historical data and filters trailing NaNs for funds/ETFs.
        Dynamically adjusts stop-loss based on Macro AI Engine.
        """
        try:
            df = self.load_parquet(ticker)
            info = self.load_fundamentals(ticker)
            
            # --- DYNAMIC BENCHMARK SELECTION ---
            currency = info.get('currency', 'USD')
            is_uk_asset = bool(ticker.endswith('.L') or currency in ['GBp', 'GBP'])
            baseline_name = "FTSE_BASELINE" if is_uk_asset else "SP500_BASELINE"
            df_baseline = self.load_parquet(baseline_name)
            
            # --- INTERMARKET COST OF CAPITAL TRACKING ---
            yield_baseline = "UK_GILT_BASELINE" if is_uk_asset else "TYX_BASELINE"
            df_yield = self.load_parquet(yield_baseline)
            
            # Mathematical Vector Correlation Math (60-Day Lookback Window)
            yield_correlation = None
            if df_yield is not None and not df_yield.empty and df is not None and len(df) >= 60:
                try:
                    yield_aligned = df_yield['Close'].reindex(df.index, method='ffill')
                    asset_returns = df['Close'].pct_change()
                    yield_returns = yield_aligned.pct_change()
                    rolling_corr = asset_returns.rolling(window=60).corr(yield_returns)
                    if not pd.isna(rolling_corr.iloc[-1]):
                        yield_correlation = float(rolling_corr.iloc[-1])
                except Exception as ex:
                    logger.debug(f"Failed rolling yield correlation calculations for {ticker}: {ex}")

            # --- MACRO AI PREEMPTIVE DEFENSE ---
            has_volatility_warning = False
            try:
                cursor = self.conn.cursor()
                cursor.execute('''
                    SELECT COUNT(*) as cnt FROM macro_calendar 
                    WHERE currency = ? 
                    AND ai_volatility_warning > 2.0 
                    AND date(event_date) >= date('now') 
                    AND date(event_date) <= date('now', '+2 days')
                ''', (currency,))
                row = cursor.fetchone()
                if row and row['cnt'] > 0:
                    has_volatility_warning = True
            except Exception as e:
                logger.error(f"Failed AI Macro defense check: {e}")

            # ==========================================
            # PART 1: TECHNICAL ANALYSIS & MATH
            # ==========================================
            current_price = None
            ma5 = ma10 = ma21 = ma50 = ma200 = None
            trend_50d = trend_200d = "N/A"
            rsi_val = stop_loss = None
            obv_bullish = False
            is_tight = is_dry_volume = False
            is_bearish_divergence = False
            is_market_leader = False
            rs_slope = 0.0

            if df is not None and len(df) >= 21:
                current_price = df['Close'].iloc[-1]
                
                df['MA_5'] = df['Close'].rolling(window=5).mean()
                df['MA_10'] = df['Close'].rolling(window=10).mean()
                df['MA_21'] = df['Close'].rolling(window=21).mean()
                df['MA_50'] = df['Close'].rolling(window=50).mean()
                df['MA_200'] = df['Close'].rolling(window=200).mean()
                
                # Filter out any trailing NaNs caused by dividend distribution rows
                valid_ma5 = df['MA_5'].dropna()
                valid_ma10 = df['MA_10'].dropna()
                valid_ma21 = df['MA_21'].dropna()
                valid_ma50 = df['MA_50'].dropna()
                valid_ma200 = df['MA_200'].dropna()

                # Extract last valid technical metrics
                ma5 = valid_ma5.iloc[-1] if not valid_ma5.empty else None
                ma10 = valid_ma10.iloc[-1] if not valid_ma10.empty else None
                ma21 = valid_ma21.iloc[-1] if not valid_ma21.empty else None
                ma50 = valid_ma50.iloc[-1] if not valid_ma50.empty else None
                ma200 = valid_ma200.iloc[-1] if not valid_ma200.empty else None
                
                # Bulletproof Trend Direction Checks using sanitized series lengths
                if not valid_ma50.empty and len(valid_ma50) >= 10:
                    trend_50d = "UP" if valid_ma50.iloc[-1] > valid_ma50.iloc[-10] else "DOWN"
                else:
                    trend_50d = "DOWN"

                if not valid_ma200.empty and len(valid_ma200) >= 20:
                    trend_200d = "UP" if valid_ma200.iloc[-1] > valid_ma200.iloc[-20] else "DOWN"
                else:
                    trend_200d = "DOWN"

                df['RSI'] = ta.momentum.RSIIndicator(close=df['Close'], window=14).rsi()
                rsi_series_clean = df['RSI'].dropna()
                rsi_val = rsi_series_clean.iloc[-1] if not rsi_series_clean.empty else None
                
                if 'High' in df.columns and 'Low' in df.columns:
                    df['ATR'] = ta.volatility.AverageTrueRange(high=df['High'], low=df['Low'], close=df['Close'], window=14).average_true_range()
                    atr_series_clean = df['ATR'].dropna()
                    if not rsi_series_clean.empty and current_price is not None:
                        atr_val = atr_series_clean.iloc[-1]
                        if not pd.isna(atr_val):
                            if has_volatility_warning:
                                # Preemptive Defense Logic
                                stop_loss = current_price - (1.0 * atr_val)
                            else:
                                stop_loss = current_price - (2.0 * atr_val)

                if 'Volume' in df.columns and not df['Volume'].isna().all():
                    df['OBV'] = ta.volume.OnBalanceVolumeIndicator(close=df['Close'], volume=df['Volume']).on_balance_volume()
                    df['OBV_MA'] = df['OBV'].rolling(window=21).mean()
                    obv_bullish = bool(df['OBV'].iloc[-1] > df['OBV_MA'].iloc[-1]) if not pd.isna(df['OBV_MA'].iloc[-1]) else False

                macd = ta.trend.MACD(close=df['Close'])
                df['MACD_Line'] = macd.macd()
                df['MACD_Signal'] = macd.macd_signal()
                df['MACD_Hist'] = macd.macd_diff()

                is_tight, is_dry_volume = self.calculate_vcp_breakout(df)
                is_bearish_divergence = self.detect_bearish_divergence(df)

                if df_baseline is not None and not df_baseline.empty:
                    baseline_aligned = df_baseline['Close'].reindex(df.index, method='ffill')
                    df['RS_Line'] = df['Close'] / baseline_aligned
                    if len(df['RS_Line'].dropna()) >= 60:
                        y = df['RS_Line'].dropna().tail(60).values
                        x = np.arange(len(y))
                        slope, _ = np.polyfit(x, y, 1)
                        # Normalize slope by the initial value to prevent price-bias
                        rs_slope = slope / y[0] if y[0] != 0 else slope
                        
                        if rs_slope > 0 and df['RS_Line'].iloc[-1] >= (df['RS_Line'].tail(60).max() * 0.95):
                            is_market_leader = True
            else:
                logger.warning(f"[PARTIAL SKIP] Insufficient historical data for {ticker}. Proceeding with fundamental analysis only.")
                current_price = info.get('navPrice') or info.get('regularMarketPrice') or info.get('previousClose')

            # ==========================================
            # PART 2: FUNDAMENTAL EXTRACTION
            # ==========================================
            quote_type = info.get('quoteType', 'EQUITY')
            is_fund = bool(quote_type in ['ETF', 'MUTUALFUND'])
            
            company_name = info.get('shortName') or info.get('longName') or ticker
            sector = info.get('category', info.get('sector', 'Fund')) if is_fund else info.get('sector', 'Unknown')
            
            fifty_two_week_low = info.get('fiftyTwoWeekLow', None)
            fifty_two_week_high = info.get('fiftyTwoWeekHigh', None)
            
            country_raw = info.get('country', 'Unknown')
            country = "UK" if country_raw == "United Kingdom" else ("US" if country_raw == "United States" else country_raw)
            
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
            if dividend_yield is not None and dividend_yield > 0.50 and currency in ['GBp', 'GBP']:
                dividend_yield = dividend_yield / 100.0

            ex_dividend_date = info.get('exDividendDate', None)
            target_price = info.get('targetMeanPrice', None)
            analyst_rating = info.get('recommendationKey', 'None').upper()
            
            earnings_ts = info.get('earningsTimestamp', None)
            next_earnings_date = datetime.fromtimestamp(earnings_ts).strftime('%Y-%m-%d') if earnings_ts else "Unknown"

            short_interest = info.get('shortPercentOfFloat', None)
            institutional_ownership = info.get('heldPercentInstitutions', None)
            beta = info.get('beta', None)

            peter_lynch_peg = None
            if trailing_pe and trailing_pe > 0 and earnings_growth and earnings_growth > 0:
                peter_lynch_peg = trailing_pe / (earnings_growth * 100.0)

            # ==========================================
            # PART 3: SCORING & SETUP TAGS
            # ==========================================
            score = 0
            breakdown = []
            tags = []

            if df is not None and len(df) >= 21:
                if not is_fund and len(df) >= 3 and 'Open' in df.columns:
                    candlestick_patterns = get_candlestick_patterns(df.iloc[-3], df.iloc[-2], df.iloc[-1])
                    for pattern in candlestick_patterns:
                        tags.append({"name": pattern["name"], "tooltip": pattern["tooltip"]})
                        breakdown.append(pattern["breakdown"])
                        score += pattern["score"]

                if df['MACD_Line'].iloc[-1] > df['MACD_Signal'].iloc[-1] and df['MACD_Line'].iloc[-2] <= df['MACD_Signal'].iloc[-2]:
                    # MACD Bullish Crossover in any zone
                    tags.append({
                        "name": "⚡ MACD Bullish Cross", 
                        "tooltip": "The MACD momentum line just crossed positive over the signal line."
                    })
                    score += 10
                    breakdown.append("+10: <abbr title='MACD Bullish Crossover'>MACD Bullish Crossover</abbr>")

                if is_tight and is_dry_volume and not is_fund:
                    tags.append({"name": "🔥 VCP Breakout", "tooltip": "Volatility Contraction Pattern."})
                    score += 20
                    breakdown.append("+20: Minervini VCP")
                elif is_tight:
                    score += 10
                    breakdown.append("+10: 3-Weeks-Tight")

                if is_market_leader and rs_slope > 0:
                    tags.append({"name": "👑 Market Leader", "tooltip": "Relative Strength slope is sharply up."})
                    score += 15
                    breakdown.append("+15: Market Leader vs Benchmark")

                if ma5 is not None and current_price is not None and current_price > ma5: 
                    score += 15
                    breakdown.append("+15: Price > 5D MA (Short-term Momentum)")
                else:
                    breakdown.append("+0: Price <= 5D MA (Bearish short-term momentum)")
                
                if ma5 is not None and ma10 is not None and ma21 is not None and ma5 > ma10 and ma10 > ma21: 
                    score += 15
                    breakdown.append("+15: MAs Aligned (5 > 10 > 21)")

                if trend_200d == "UP":
                    score += 15
                    breakdown.append("+15: 200D Trend UP (Institutional Backing)")
                else:
                    breakdown.append("+0: 200D Trend DOWN (Lacking institutional backing)")

                if rsi_val is not None and 40.0 <= rsi_val <= 65.0: 
                    score += 10
                    breakdown.append("+10: RSI Healthy (Room to run)")

                # Exclude OBV from Mutual Funds logic to prevent artificial point boosts
                if is_fund: 
                    score += 0
                    breakdown.append("+0: OBV Ignored (Fund Exemption)")
                elif obv_bullish:
                    score += 20
                    breakdown.append("+20: OBV Bullish")
                else:
                    breakdown.append("+0: OBV Bearish")

                if is_bearish_divergence and not is_fund:
                    tags.append({"name": "🚨 Divergence Warning", "tooltip": "Price higher high, RSI lower high."})
                    score -= 30
                    breakdown.append("-30: Algorithmic Bearish Divergence")
                
                # Append Macro AI Preemptive Defense Logic to Breakdown
                if has_volatility_warning:
                    breakdown.append("-0: 🛡️ Preemptive Defense Active: Stop-Loss tightened to 1x ATR due to imminent high-volatility macro event.")

            else:
                breakdown.append("+0: Technical indicators skipped (Insufficient Historical Data)")

            score = max(0, min(score, 100))

            if score >= 80: signal = "STRONG BUY"
            elif score >= 60: signal = "BULLISH / HOLD"
            elif score >= 40: signal = "NEUTRAL"
            else: signal = "BEARISH / CAUTION"

            notes_html = "<strong>Algorithmic Breakdown:</strong><br><ul class='algo-breakdown-list'>"
            for item in breakdown:
                notes_html += f"<li>{item}</li>"
            notes_html += "</ul>"
            
            if stop_loss is not None:
                notes_html += f"<strong>Risk Management:</strong> Mathematical <abbr title='Based on Average True Range.'>ATR Stop-Loss</abbr> is {stop_loss:,.2f} {currency}.<br><br>"
            
            if not is_fund and rsi_val is not None and rsi_val > 70.0:
                notes_html += "<strong><span class='risk-warning'>Warning:</span></strong> Stock is technically overbought (RSI > 70).<br>"

            tags_json = json.dumps(tags)
            
            self.save_to_db(
                ticker, company_name, sector, country, currency, quote_type,
                current_price, ma5, ma10, ma21, trend_50d, trend_200d, rsi_val, stop_loss,
                fifty_two_week_low, fifty_two_week_high,
                trailing_pe, forward_pe, peg_ratio, peter_lynch_peg, price_to_book,
                profit_margin, roe, revenue_growth, debt_to_equity, current_ratio, operating_cash_flow,
                ytd_return, total_assets, nav_price, expense_ratio, top_holdings, sector_weightings,
                dividend_yield, ex_dividend_date, target_price, analyst_rating, next_earnings_date,
                short_interest, institutional_ownership, beta, yield_correlation,
                score, signal, notes_html, tags_json
            )
            
        except Exception as e:
            logger.error(f"Failed to analyze {ticker}: {e}")

    def save_to_db(self, ticker: str, company_name: str, sector: str, country: str, currency: str, quote_type: str,
                   price: Optional[float], ma5: Optional[float], ma10: Optional[float], ma21: Optional[float], 
                   trend_50d: str, trend_200d: str, rsi: Optional[float], stop_loss: Optional[float],
                   fifty_two_week_low: Optional[float], fifty_two_week_high: Optional[float],
                   trailing_pe: Optional[float], forward_pe: Optional[float], peg_ratio: Optional[float], 
                   peter_lynch_peg: Optional[float], price_to_book: Optional[float],
                   profit_margin: Optional[float], roe: Optional[float], revenue_growth: Optional[float], 
                   debt_to_equity: Optional[float], current_ratio: Optional[float], operating_cash_flow: Optional[float],
                   ytd_return: Optional[float], total_assets: Optional[float], nav_price: Optional[float], 
                   expense_ratio: Optional[float], top_holdings: str, sector_weightings: str,
                   dividend_yield: Optional[float], ex_dividend_date: Optional[str], target_price: Optional[float], 
                   analyst_rating: str, next_earnings_date: str, short_interest: Optional[float], 
                   institutional_ownership: Optional[float], beta: Optional[float], yield_correlation: Optional[float],
                   score: int, signal: str, notes: str, tags_json: str) -> None:
        
        # Internal cleaner to aggressively handle pandas NaNs, Inf, and String Variants
        def _clean(v: Any) -> Any:
            if v is None: 
                return None
            if isinstance(v, str):
                if v.lower() in ['nan', 'infinity', '-infinity', 'inf', '-inf']:
                    return None
                try:
                    val = float(v)
                    if pd.isna(val) or np.isinf(val): 
                        return None
                    return val
                except ValueError:
                    return v # Preserve genuine strings
            if isinstance(v, (float, int)) and (pd.isna(v) or np.isinf(v)): 
                return None
            return v

        cursor = self.conn.cursor()
        
        # FIX EMBEDDED: 47 total mapping column dimensions with exactly 47 corresponding question mark nodes
        query = '''
            INSERT OR REPLACE INTO stock_signals (
                ticker, last_updated, company_name, sector, country, currency, quote_type,
                current_price, ma_5_day, ma_10_day, ma_21_day, trend_50d, trend_200d, rsi_14, atr_stop_loss,
                fifty_two_week_low, fifty_two_week_high,
                trailing_pe, forward_pe, peg_ratio, peter_lynch_peg, price_to_book,
                profit_margin, roe, revenue_growth, debt_to_equity, current_ratio, operating_cash_flow,
                ytd_return, total_assets, nav_price, expense_ratio, top_holdings, sector_weightings,
                dividend_yield, ex_dividend_date, target_price, analyst_rating, next_earnings_date,
                short_interest, institutional_ownership, beta, yield_correlation,
                composite_score, overall_signal, educational_notes, setup_tags
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
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
            _clean(short_interest), _clean(institutional_ownership), _clean(beta), _clean(yield_correlation),
            _clean(score), signal, notes, tags_json
        )
        
        cursor.execute(query, values)
        self.conn.commit()
        logger.info(f"[SUCCESS] Analyzed {ticker} | Signal: {signal} | Score: {score}/100")

    def run_all(self) -> None:
        logger.info("Starting Institutional Quantamental Engine...")
        try:
            for filename in os.listdir(HISTORICAL_DIR):
                if filename.endswith(".parquet") and "BASELINE" not in filename:
                    ticker = filename.replace(".parquet", "")
                    self.analyze_ticker(ticker)
            logger.info("Analysis Complete. Master Database is fully updated.")
        finally:
            self.close()

if __name__ == "__main__":
    engine = QuantEngine()
    engine.run_all()