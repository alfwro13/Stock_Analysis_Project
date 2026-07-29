import os
import json
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple

import numpy as np
import pandas as pd
import ta

from config import HISTORICAL_DIR, FUNDAMENTALS_DIR
from database import get_connection, log_score_event
from fundamentals_helpers import calculate_peter_lynch_peg
from pattern_geometry_helpers import (
    candle_body_wick_metrics, is_bullish_engulfing, is_bearish_engulfing, is_hammer, is_shooting_star,
)
from utils import safe_ticker_filename
from constants import (
    RSI_HEALTHY_MIN, RSI_HEALTHY_MAX,
    SCORE_STRONG_BUY, SCORE_BULLISH, SCORE_NEUTRAL, SCORE_BEARISH, SCORE_STRONG_SELL,
)

logger = logging.getLogger(__name__)

# GUI name: "Quantamental Analysis Engine". Canonical scheduled-job names live in scheduler_engine.JOB_GRAPH.


def get_candlestick_patterns(prev2: pd.Series, prev1: pd.Series, curr: pd.Series) -> List[Dict[str, Any]]:
    """Pattern recognition engine; patterns are non-exclusive to allow signal confluence."""
    patterns: List[Dict[str, Any]] = []

    curr_metrics = candle_body_wick_metrics(curr['Open'], curr['High'], curr['Low'], curr['Close'])
    curr_body = curr_metrics["body"]
    curr_body_safe = curr_metrics["body_safe"]
    curr_range = curr_metrics["range"]
    curr_upper_wick = curr_metrics["upper_wick"]
    curr_lower_wick = curr_metrics["lower_wick"]
    curr_is_bullish = curr_metrics["is_bullish"]
    curr_is_bearish = curr_metrics["is_bearish"]

    prev1_body = abs(prev1['Open'] - prev1['Close'])
    prev1_range = max(prev1['High'] - prev1['Low'], 0.001)
    prev1_is_bearish = bool(prev1['Close'] < prev1['Open'])
    prev1_is_bullish = bool(prev1['Close'] > prev1['Open'])
    prev2_is_bullish = bool(prev2['Close'] > prev2['Open'])
    
    prev2_body = abs(prev2['Open'] - prev2['Close'])
    prev2_is_bearish = bool(prev2['Close'] < prev2['Open'])

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
    # Day 1: Strong Bullish. Day 2: Indecision/Gap up. Day 3: Strong Bearish pushing below 50% of Day 1.
    prev2_midpoint_bull = (prev2['Open'] + prev2['Close']) / 2.0
    if prev2_is_bullish and prev2_body > (prev2['High'] - prev2['Low']) * 0.5:
        if prev1_body <= (prev1_range * 0.3):
            if curr_is_bearish and curr['Close'] < prev2_midpoint_bull:
                patterns.append({
                    "name": "🌇 Evening Star",
                    "tooltip": "A highly reliable 3-day topping pattern. Strong buying (Day 1) was met with indecision (Day 2), followed by aggressive institutional selling (Day 3) that erased the majority of the prior rally.",
                    "breakdown": "-20: <abbr title='3-Day Pattern: Heavy rally, followed by indecision, followed by violent selling.'>Evening Star Reversal</abbr>",
                    "score": -20
                })

    # Three consecutive bullish candles, each opening inside prior body and closing higher.
    prev2_range = max(prev2['High'] - prev2['Low'], 0.001)
    if (prev2_is_bullish and prev1_is_bullish and curr_is_bullish
            and prev2_body > prev2_range * 0.40
            and prev1_body > prev1_range * 0.40
            and curr_body > curr_range * 0.40
            and prev1['Close'] > prev2['Close']
            and curr['Close'] > prev1['Close']
            and prev2['Open'] <= prev1['Open'] <= prev2['Close']
            and prev1['Open'] <= curr['Open'] <= prev1['Close']):
        patterns.append({
            "name": "🪖 Three White Soldiers",
            "tooltip": "Three consecutive bullish candles, each opening inside the prior body and closing higher. Signals sustained institutional buying pressure across multiple sessions — buyers are in full control.",
            "breakdown": "+18: <abbr title='Three consecutive bullish closes, each higher than the last, with opens inside prior body.'>Three White Soldiers</abbr>",
            "score": 18
        })

    harami_cross_detected = False


    if is_bullish_engulfing(prev1['Open'], prev1['Close'], curr['Open'], curr['Close']):
        patterns.append({
            "name": "🐂 Bullish Engulfing",
            "tooltip": "Buyers completely overwhelmed sellers. The current green body fully engulfed the previous red body. Signals a potential reversal to the upside.",
            "breakdown": "+15: <abbr title='Buyers completely overwhelmed sellers. The current green body fully engulfed the previous red body.'>Bullish Engulfing Pattern</abbr>",
            "score": 15
        })

    if is_bearish_engulfing(prev1['Open'], prev1['Close'], curr['Open'], curr['Close']):
        patterns.append({
            "name": "🐻 Bearish Engulfing",
            "tooltip": "Sellers took total control. The current red body fully engulfed the previous green body. A stark warning signal of impending downside.",
            "breakdown": "-15: <abbr title='Sellers took total control. The current red body fully engulfed the previous green body. Warning signal.'>Bearish Engulfing Pattern</abbr>",
            "score": -15
        })

    # Large bearish D1 followed by Doji inside its body; stronger signal than standalone Doji.
    if (prev1_is_bearish and prev1_body > prev1_range * 0.50
            and curr_body <= curr_range * 0.10
            and prev1['Close'] <= curr['Open'] <= prev1['Open']
            and prev1['Close'] <= curr['Close'] <= prev1['Open']):
        patterns.append({
            "name": "🌱 Bullish Harami Cross",
            "tooltip": "A Doji forms entirely inside a large bearish candle. Unlike a standalone Doji, the containment signals that sellers have exhausted themselves — buyers are stepping in cautiously.",
            "breakdown": "+8: <abbr title='Doji contained within a large bearish body: sellers exhausted, potential bullish reversal.'>Bullish Harami Cross</abbr>",
            "score": 8
        })
        harami_cross_detected = True

    elif (prev1_is_bullish and prev1_body > prev1_range * 0.50
            and curr_body <= curr_range * 0.10
            and prev1['Open'] <= curr['Open'] <= prev1['Close']
            and prev1['Open'] <= curr['Close'] <= prev1['Close']):
        patterns.append({
            "name": "🕸️ Bearish Harami Cross",
            "tooltip": "A Doji forms entirely inside a large bullish candle. Unlike a standalone Doji, the containment signals that buyers have run out of steam — sellers are beginning to assert control.",
            "breakdown": "-8: <abbr title='Doji contained within a large bullish body: buyers exhausted, potential bearish reversal.'>Bearish Harami Cross</abbr>",
            "score": -8
        })
        harami_cross_detected = True

    # Bearish D1 + bullish D2 that opens below D1 close and pierces above midpoint; weaker than engulfing.
    prev1_midpoint = (prev1['Open'] + prev1['Close']) / 2.0
    if (prev1_is_bearish and prev1_body > prev1_range * 0.50
            and curr_is_bullish
            and curr['Open'] <= prev1['Close']
            and curr['Close'] > prev1_midpoint
            and curr['Close'] < prev1['Open']):
        patterns.append({
            "name": "🗡️ Piercing Line",
            "tooltip": "A strong bearish day is followed by a bullish candle that opens lower but fights back above the midpoint of the prior red body. Buyers are defending the low — a moderate reversal signal.",
            "breakdown": "+10: <abbr title='Bullish candle opens below prior close and closes above D1 midpoint without fully engulfing.'>Piercing Line</abbr>",
            "score": 10
        })

    if is_hammer(curr_upper_wick, curr_lower_wick, curr_body_safe, curr_range):
        patterns.append({
            "name": "🔨 Hammer Rejection",
            "tooltip": "Sellers tried to crash the price intraday, but institutional buyers violently rejected it and bought the dip. Indicates strong underlying support.",
            "breakdown": "+10: <abbr title='Sellers tried to crash the price intraday, but institutional buyers violently rejected it. Indicates strong support.'>Bullish Hammer Candlestick</abbr>",
            "score": 10
        })

    elif is_shooting_star(curr_upper_wick, curr_lower_wick, curr_body_safe, curr_range):
        patterns.append({
            "name": "🌠 Shooting Star",
            "tooltip": "Retail buyers tried to push the price up, but institutional sellers aggressively dumped shares into the rally. Momentum is fading.",
            "breakdown": "-10: <abbr title='Retail buyers tried to push the price up, but institutional sellers aggressively dumped shares.'>Bearish Shooting Star</abbr>",
            "score": -10
        })

    # Doji suppressed when Harami Cross already fired on this candle.
    elif not harami_cross_detected and curr_body <= (curr_range * 0.1):
        patterns.append({
            "name": "⚖️ Doji",
            "tooltip": "The opening and closing prices are mathematically almost identical. This represents total equilibrium and indecision between buyers and sellers.",
            "breakdown": "+0: <abbr title='Opening and closing prices are mathematically almost identical. Total equilibrium/indecision between buyers and sellers.'>Doji Candlestick</abbr>",
            "score": 0
        })
        
    return patterns


class QuantEngine:
    def __init__(self) -> None:
        pass

    def close(self):
        if hasattr(self, 'conn') and self.conn:
            self.conn.close()

    def load_parquet(self, ticker: str) -> Optional[pd.DataFrame]:
        safe_ticker = safe_ticker_filename(ticker)
        if not safe_ticker:
            return None
        filepath = HISTORICAL_DIR / f"{safe_ticker}.parquet"
        if not filepath.exists():
            return None
        return pd.read_parquet(filepath)

    def load_fundamentals(self, ticker: str) -> dict:
        safe_ticker = safe_ticker_filename(ticker)
        if not safe_ticker:
            return {}
        filepath = FUNDAMENTALS_DIR / f"{safe_ticker}.json"
        if not filepath.exists():
            return self._fundamentals_from_profile(ticker)
        with open(filepath, 'r') as f:
            return json.load(f)

    @staticmethod
    def _fundamentals_from_profile(ticker: str) -> dict:
        """The `asset_profiles` row reshaped into the Yahoo `.info` keys analyze_ticker reads, for a
        ticker whose fundamentals dump was never fetched. Without it an empty dict falls through to
        the 'USD'/'EQUITY'/'Unknown' defaults, which then overwrite the profile's correct values in
        `stock_signals` — mistagging a GBP LSE ETF as USD and applying a phantom FX conversion to
        every price derived from it."""
        conn = None
        try:
            conn = get_connection()
            row = conn.execute(
                "SELECT company_name, sector, country, currency, quote_type FROM asset_profiles WHERE ticker = ?",
                (ticker,),
            ).fetchone()
            if row is None:
                return {}
            info = {
                'shortName': row['company_name'],
                'sector': row['sector'],
                'country': row['country'],
                'currency': row['currency'],
                'quoteType': row['quote_type'],
            }
            return {k: v for k, v in info.items() if v}
        except Exception as e:
            logger.error("Profile fundamentals fallback failed for %s: %s", ticker, e)
            return {}
        finally:
            if conn:
                conn.close()

    def calculate_vcp_breakout(self, df: pd.DataFrame) -> Tuple[bool, bool, bool]:
        """Returns (is_vcp_base, is_confirmed_breakout, has_prior_uptrend) for all 5 Minervini VCP criteria."""
        # Require minimum 1 year of data to compute a valid 52-week window
        if df is None or len(df) < 252:
            return False, False, False

        # Use calendar-exact 52W window via DateOffset to capture true intraday extremes.
        cutoff_52w = df.index[-1] - pd.DateOffset(weeks=52)
        df_52w     = df[df.index >= cutoff_52w]
        high_52w   = df_52w['High'].max()
        low_52w    = df_52w['Low'].min()

        if pd.isna(high_52w) or pd.isna(low_52w) or low_52w <= 0:
            return False, False, False

        prior_advance = (high_52w - low_52w) / low_52w
        has_prior_uptrend: bool = bool(prior_advance >= 0.30)

        # VCP bases form near the top of a prior advance; a stock 40% below its 52W high is not a launchpad.
        current_price = float(df['Close'].iloc[-1])
        if pd.isna(current_price) or current_price <= 0:
            return False, False, has_prior_uptrend

        dist_from_52w_high: float = (high_52w - current_price) / high_52w
        is_near_high: bool = bool(dist_from_52w_high <= 0.15)

        weekly_data = df.resample('W-FRI').agg({
            'Open': 'first',
            'High': 'max',
            'Low': 'min',
            'Close': 'last',
            'Volume': 'sum'
        })

        if len(weekly_data) < 4:
            return False, False, has_prior_uptrend

        # Drop partial week (non-Friday) to avoid corrupting the contraction measurement.
        if df.index[-1].weekday() < 4:
            completed_weeks = weekly_data.iloc[:-1]
        else:
            completed_weeks = weekly_data

        if len(completed_weeks) < 3:
            return False, False, has_prior_uptrend

        last_3_weeks = completed_weeks.iloc[-3:]

        w1_range = float(last_3_weeks.iloc[0]['High'] - last_3_weeks.iloc[0]['Low'])
        w2_range = float(last_3_weeks.iloc[1]['High'] - last_3_weeks.iloc[1]['Low'])
        w3_range = float(last_3_weeks.iloc[2]['High'] - last_3_weeks.iloc[2]['Low'])

        # Strict sequential: every week must be smaller than the prior
        is_contracting: bool = bool(w1_range > w2_range > w3_range > 0)

        max_high = float(last_3_weeks['High'].max())
        min_low = float(last_3_weeks['Low'].min())
        # Normalise against current price (Minervini's published definition, not against the low).
        total_range_pct = (max_high - min_low) / current_price if current_price > 0 else 1.0
        is_tight: bool = is_contracting and (total_range_pct <= 0.10)

        weekly_vol_avg_10w = completed_weeks['Volume'].rolling(window=10).mean().iloc[-1]

        is_dry_volume: bool = False
        if not pd.isna(weekly_vol_avg_10w) and weekly_vol_avg_10w > 0:
            avg_vol_3w = float(last_3_weeks['Volume'].mean())
            is_dry_volume = bool(avg_vol_3w < (weekly_vol_avg_10w * 0.80))

        is_vcp_base: bool = bool(
            has_prior_uptrend and
            is_near_high and
            is_tight and
            is_dry_volume
        )

        # Daily volume for criterion 5: weekly volume is too coarse to detect a single-session institutional surge.
        is_confirmed_breakout: bool = False
        if is_vcp_base:
            resistance_level = float(last_3_weeks['High'].max())
            daily_vol_sma_50 = df['Volume'].rolling(window=50).mean().iloc[-1]
            current_daily_vol = float(df['Volume'].iloc[-1])

            if not pd.isna(daily_vol_sma_50) and daily_vol_sma_50 > 0:
                # Require a 2% decisive close above the pivot to eliminate noise touches.
                BREAKOUT_BUFFER = 1.02
                price_broke_out = bool(current_price > resistance_level * BREAKOUT_BUFFER)
                volume_confirmed = bool(current_daily_vol >= (daily_vol_sma_50 * 1.50))
                is_confirmed_breakout = bool(price_broke_out and volume_confirmed)

        return is_vcp_base, is_confirmed_breakout, has_prior_uptrend

    def detect_bearish_divergence(self, df: pd.DataFrame) -> bool:
        """Checks if price is making structurally higher highs while RSI makes lower highs."""
        if 'RSI' not in df.columns:
            return False

        last_30 = df.tail(30).copy()
        if len(last_30) < 30: 
            return False
        
        # Split into two 15-day halves to ensure true structural peaks
        p1 = last_30.iloc[:15]
        p2 = last_30.iloc[15:]
        
        high_col = 'High' if 'High' in df.columns else 'Close'
        
        # Find integer indices to create flexible rolling windows
        peak1_iloc = p1[high_col].argmax()
        peak2_iloc = p2[high_col].argmax() + 15
        
        price_peak1 = last_30[high_col].iloc[peak1_iloc]
        price_peak2 = last_30[high_col].iloc[peak2_iloc]
        
        # Right boundary clamped to +1 (exclusive) to eliminate look-ahead bias.
        window1_start = max(0, peak1_iloc - 2)
        window1_end = min(len(last_30), peak1_iloc + 1)
        window2_start = max(0, peak2_iloc - 2)
        window2_end = min(len(last_30), peak2_iloc + 1)
        
        rsi_at_peak1 = last_30['RSI'].iloc[window1_start:window1_end].max()
        rsi_at_peak2 = last_30['RSI'].iloc[window2_start:window2_end].max()

        if price_peak2 > price_peak1 and rsi_at_peak2 < rsi_at_peak1 and rsi_at_peak1 > 55.0:
            return True
        return False

    def analyze_ticker(self, ticker: str) -> None:
        """Combined technical + fundamental analysis with dynamic ATR stop-loss per Macro AI regime."""
        try:
            df = self.load_parquet(ticker)
            info = self.load_fundamentals(ticker)

            currency = info.get('currency', 'USD')
            is_uk_asset = bool(ticker.endswith('.L') or currency in ['GBp', 'GBP'])
            baseline_name = "FTSE_BASELINE" if is_uk_asset else "SP500_BASELINE"
            df_baseline = self.load_parquet(baseline_name)

            yield_baseline = "UK_GILT_BASELINE" if is_uk_asset else "TYX_BASELINE"
            df_yield = self.load_parquet(yield_baseline)

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
                    logger.debug("Failed rolling yield correlation for %s: %s", ticker, ex)

            has_volatility_warning = False
            try:
                conn = get_connection()
                try:
                    cursor = conn.cursor()
                    cursor.execute('''
                        SELECT COUNT(*) as cnt FROM macro_calendar 
                        WHERE currency = ? 
                        AND ai_volatility_warning > 2.0 
                        AND date(event_date) >= date('now') 
                        AND date(event_date) <= date('now', '+1 day')
                    ''', (currency,))
                    row = cursor.fetchone()
                    if row and row['cnt'] > 0:
                        has_volatility_warning = True
                finally:
                    conn.close()
            except Exception as e:
                logger.error("Failed AI Macro defense check: %s", e)

            current_price = None
            ma5 = ma10 = ma21 = ma50 = ma200 = None
            trend_50d = trend_200d = "N/A"
            rsi_val = stop_loss = None
            obv_bullish = False
            is_vcp_base = is_confirmed_breakout = has_prior_uptrend = False
            is_bearish_divergence = False
            is_market_leader = False

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

                ma5 = valid_ma5.iloc[-1] if not valid_ma5.empty else None
                ma10 = valid_ma10.iloc[-1] if not valid_ma10.empty else None
                ma21 = valid_ma21.iloc[-1] if not valid_ma21.empty else None
                ma50 = valid_ma50.iloc[-1] if not valid_ma50.empty else None
                ma200 = valid_ma200.iloc[-1] if not valid_ma200.empty else None
                
                # Trend Direction Checks using 21-bar (1-month) institutional slope baseline
                if not valid_ma50.empty and len(valid_ma50) >= 21:
                    trend_50d = "UP" if valid_ma50.iloc[-1] > valid_ma50.iloc[-21] else "DOWN"
                else:
                    trend_50d = "DOWN"

                if not valid_ma200.empty and len(valid_ma200) >= 21:
                    trend_200d = "UP" if valid_ma200.iloc[-1] > valid_ma200.iloc[-21] else "DOWN"
                else:
                    trend_200d = "DOWN"

                df['RSI'] = ta.momentum.RSIIndicator(close=df['Close'], window=14).rsi()
                rsi_series_clean = df['RSI'].dropna()
                rsi_val = rsi_series_clean.iloc[-1] if not rsi_series_clean.empty else None
                
                if 'High' in df.columns and 'Low' in df.columns:
                    df['ATR'] = ta.volatility.AverageTrueRange(high=df['High'], low=df['Low'], close=df['Close'], window=14).average_true_range()
                    df['ATR_MA'] = df['ATR'].rolling(window=20).mean()
                    
                    atr_series_clean = df['ATR'].dropna()
                    atr_ma_clean = df['ATR_MA'].dropna()
                    
                    if not atr_series_clean.empty and not atr_ma_clean.empty and current_price is not None:
                        atr_val = atr_series_clean.iloc[-1]
                        atr_ma_val = atr_ma_clean.iloc[-1]
                        
                        if not pd.isna(atr_val) and atr_ma_val > 0:
                            # Volatility Stability Ratio: 1.0 is stable, >1.2 is expanding volatility
                            atr_stability = atr_val / atr_ma_val
                            
                            # Institutional Dynamic ATR Stop-Loss Multipier Logic
                            if has_volatility_warning:
                                multiplier = 3.0  # Widen stop defensively to survive imminent macro shock whipsaws
                            elif atr_stability > 1.2:
                                multiplier = 2.5  # Volatility expanding, widen stop to avoid noise whipsaws
                            elif atr_stability < 0.8:
                                multiplier = 1.8  # Volatility dying down, can afford slightly tighter stops
                            else:
                                multiplier = 2.0  # Standard conservative swing trading baseline
                                
                            stop_loss = max(current_price - (multiplier * atr_val), 0.01)

                if 'Volume' in df.columns and not df['Volume'].isna().all():
                    df['OBV'] = ta.volume.OnBalanceVolumeIndicator(close=df['Close'], volume=df['Volume']).on_balance_volume()
                    df['OBV_MA'] = df['OBV'].rolling(window=21).mean()

                    obv_bullish = (
                        bool(df['OBV'].iloc[-1] > df['OBV_MA'].iloc[-1])
                        if not pd.isna(df['OBV_MA'].iloc[-1]) and not pd.isna(df['OBV'].iloc[-1])
                        else False
                    )

                macd = ta.trend.MACD(close=df['Close'])
                df['MACD_Line'] = macd.macd()
                df['MACD_Signal'] = macd.macd_signal()
                df['MACD_Hist'] = macd.macd_diff()

                is_vcp_base, is_confirmed_breakout, has_prior_uptrend = self.calculate_vcp_breakout(df)
                is_bearish_divergence = self.detect_bearish_divergence(df)

                if df_baseline is not None and not df_baseline.empty:
                    baseline_aligned = df_baseline['Close'].reindex(df.index, method='ffill')
                    df['RS_Line'] = df['Close'] / baseline_aligned
                    
                    # William O'Neil Style 1-Year Relative Strength Check
                    valid_rs = df['RS_Line'].dropna()
                    if len(valid_rs) >= 252:
                        rs_1y_ago = valid_rs.iloc[-252]
                        rs_now = valid_rs.iloc[-1]
                        
                        if rs_1y_ago > 0:
                            rs_1y_return = (rs_now - rs_1y_ago) / rs_1y_ago
                            
                            # True market leaders mathematically outperform the broader market by >15% over a year
                            # AND must be currently supported by their 200D SMA
                            ma200_val = df['MA_200'].dropna().iloc[-1] if not df['MA_200'].dropna().empty else 0
                            
                            if rs_1y_return > 0.15 and current_price is not None and current_price > ma200_val:
                                is_market_leader = True
            else:
                logger.warning("[PARTIAL SKIP] Insufficient historical data for %s. Proceeding with fundamental analysis only.", ticker)
                current_price = info.get('navPrice') or info.get('regularMarketPrice') or info.get('previousClose')

            quote_type = info.get('quoteType', 'EQUITY')
            is_fund = bool(quote_type in ['ETF', 'MUTUALFUND'])

            company_name = info.get('shortName') or info.get('longName')
            if not company_name:
                try:
                    conn = get_connection()
                    try:
                        cursor = conn.cursor()
                        cursor.execute("SELECT company_name FROM asset_profiles WHERE ticker = ?", (ticker,))
                        p_row = cursor.fetchone()
                        if p_row and p_row['company_name'] and p_row['company_name'] != ticker:
                            company_name = p_row['company_name']
                        else:
                            cursor.execute("SELECT company_name FROM market_universe WHERE ticker = ?", (ticker,))
                            m_row = cursor.fetchone()
                            if m_row and m_row['company_name'] and m_row['company_name'] != ticker:
                                company_name = m_row['company_name']
                    finally:
                        conn.close()
                except Exception as ex:
                    logger.debug("Failed to fetch database fallback name for %s: %s", ticker, ex)
            company_name = company_name or ticker
            
            sector = info.get('category', info.get('sector', 'Fund')) if is_fund else info.get('sector', 'Unknown')
            
            fifty_two_week_low = info.get('fiftyTwoWeekLow', None)
            fifty_two_week_high = info.get('fiftyTwoWeekHigh', None)
            
            country_raw = info.get('country', 'Unknown')
            country = "UK" if country_raw == "United Kingdom" else ("US" if country_raw == "United States" else country_raw)
            
            ytd_return = info.get('ytdReturn', None)
            total_assets = info.get('totalAssets', None)
            nav_price = info.get('navPrice', None)
            expense_ratio = info.get('expenseRatio', info.get('annualReportExpenseRatio', None))

            trailing_pe = info.get('trailingPE', None)
            forward_pe = info.get('forwardPE', None)
            peg_ratio = info.get('pegRatio', None)
            price_to_book = info.get('priceToBook', None)
            price_to_sales = info.get('priceToSalesTrailing12Months', None)
            free_cash_flow = info.get('freeCashflow', None)
            profit_margin = info.get('profitMargins', None)
            roe = info.get('returnOnEquity', None)
            revenue_growth = info.get('revenueGrowth', None)
            earnings_growth = info.get('earningsGrowth', None)
            debt_to_equity = info.get('debtToEquity', None)
            current_ratio = info.get('currentRatio', None)
            operating_cash_flow = info.get('operatingCashflow', None)
            
            dividend_yield = info.get('dividendYield', None)
            
            # GBp yields can arrive as 100× the true decimal (pence_dividend/pence_price); >1.0 is impossible for a real yield.
            if dividend_yield is not None and currency in ['GBp', 'GBP']:
                if dividend_yield > 1.0:
                    dividend_yield /= 100.0

            ex_dividend_date = info.get('exDividendDate', None)
            target_price = info.get('targetMeanPrice', None)
            analyst_rating = (info.get('recommendationKey') or 'None').upper()
            
            earnings_ts = info.get('earningsTimestamp', None)
            next_earnings_date = datetime.fromtimestamp(earnings_ts, tz=timezone.utc).strftime('%Y-%m-%d') if earnings_ts else "Unknown"

            short_interest = info.get('shortPercentOfFloat', None)
            institutional_ownership = info.get('heldPercentInstitutions', None)
            beta = info.get('beta', None)

            # Compute canonical Peter Lynch PEG via shared helper to ensure
            # identical math across portfolio/watchlist + universe pipelines.
            peter_lynch_peg = calculate_peter_lynch_peg(
                forward_pe=forward_pe,
                trailing_pe=trailing_pe,
                earnings_growth=earnings_growth,
                dividend_yield=dividend_yield,
            )

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

                macd_line_clean = df['MACD_Line'].dropna()
                macd_signal_clean = df['MACD_Signal'].dropna()
                
                if len(macd_line_clean) >= 2 and len(macd_signal_clean) >= 2:
                    # Enforce zero-line filter for bottom-fishing & align UI string
                    if macd_line_clean.iloc[-1] > macd_signal_clean.iloc[-1] and macd_line_clean.iloc[-2] <= macd_signal_clean.iloc[-2]:
                        if macd_line_clean.iloc[-1] < 0:
                            tags.append({
                                "name": "⚡ MACD Reversal",
                                "tooltip": "The MACD momentum line just crossed positive over the signal line while below the zero line (Oversold Bottom-Fishing)."
                            })
                            breakdown.append("+0: <abbr title='MACD Reversal Below Zero (Captured by Engine DB Flag)'>MACD Reversal</abbr>")
                        else:
                            tags.append({
                                "name": "⚡ MACD Bullish Cross",
                                "tooltip": "The MACD momentum line just crossed positive over the signal line while above the zero line (Trend Continuation)."
                            })
                            breakdown.append("+0: <abbr title='MACD Bullish Cross Above Zero (Captured by Engine DB Flag)'>MACD Bullish Cross</abbr>")

                if not is_fund:
                    if is_confirmed_breakout:
                        tags.append({
                            "name": "🔥 VCP Confirmed Breakout",
                            "tooltip": (
                                "All 5 Minervini VCP criteria satisfied: the stock had a 30%+ prior "
                                "uptrend, is within 15% of its 52-week high, built a sequentially "
                                "contracting 3-week base on drying volume, and today broke above base "
                                "resistance on institutional volume (≥1.5× 50D average)."
                            )
                        })
                        score += 25
                        breakdown.append(
                            "+25: <abbr title='Prior uptrend + near 52W high + contraction + vol dry-up "
                            "+ confirmed breakout on elevated volume.'>Minervini VCP — Confirmed Breakout</abbr>"
                        )

                    elif is_vcp_base:
                        tags.append({
                            "name": "🌀 VCP Base Forming",
                            "tooltip": (
                                "Minervini VCP base structure confirmed: prior 30%+ uptrend, stock within "
                                "15% of 52-week high, sequential weekly range contraction, and volume "
                                "drying up. Awaiting a breakout above base resistance on elevated volume."
                            )
                        })
                        score += 15
                        breakdown.append(
                            "+15: <abbr title='Base structure confirmed: uptrend + near high + contraction "
                            "+ dry volume. Awaiting volume breakout.'>Minervini VCP — Base Setup (Pre-Breakout)</abbr>"
                        )

                    elif has_prior_uptrend:
                        score += 5
                        breakdown.append(
                            "+5: <abbr title='30%+ prior uptrend confirmed but base not yet formed.'>Prior "
                            "Uptrend Foundation</abbr>"
                        )

                if is_market_leader:
                    tags.append({"name": "👑 Market Leader", "tooltip": "Strong >15% Relative Strength outperformance vs S&P 500 Baseline over 1-year."})
                    score += 15
                    breakdown.append("+15: 1Y Market Leader vs Benchmark")

                if ma5 is not None and current_price is not None and current_price > ma5: 
                    score += 15
                    breakdown.append("+15: Price > 5D MA (Short-term Momentum)")
                else:
                    score -= 5
                    breakdown.append("-5: Price <= 5D MA (Bearish short-term momentum)")
                
                if ma5 is not None and ma10 is not None and ma21 is not None:
                    if ma5 > ma10 and ma10 > ma21:
                        score += 15
                        breakdown.append("+15: MAs Fully Aligned (5 > 10 > 21)")
                    elif ma5 > ma10:
                        score += 7
                        breakdown.append("+7: MAs Partially Aligned (5 > 10, short-term momentum only)")

                if trend_200d == "UP":
                    score += 20
                    breakdown.append("+20: 200D Trend UP (Institutional Backing)")
                else:
                    score -= 10
                    breakdown.append("-10: 200D Trend DOWN (Lacking institutional backing)")

                if rsi_val is not None and RSI_HEALTHY_MIN <= rsi_val <= RSI_HEALTHY_MAX:
                    score += 10
                    breakdown.append("+10: RSI Healthy (Room to run)")

                if is_fund:
                    score += 0
                    breakdown.append("+0: OBV Ignored (Fund Exemption)")
                elif obv_bullish:
                    score += 10
                    breakdown.append("+10: OBV Bullish")
                else:
                    score -= 5
                    breakdown.append("-5: OBV Bearish")

                if is_bearish_divergence and not is_fund:
                    tags.append({"name": "🚨 Divergence Warning", "tooltip": "Price higher high, RSI lower high."})
                    score -= 30
                    breakdown.append("-30: Algorithmic Bearish Divergence")
                
                if has_volatility_warning:
                    breakdown.append("-0: 🛡️ Preemptive Defense Active: Stop-Loss tightened defensively due to imminent high-volatility macro event.")

            else:
                breakdown.append("+0: Technical indicators skipped (Insufficient Historical Data)")

            score = max(-100, min(score, 100))
            _has_tech = df is not None and len(df) >= 21
            if not _has_tech:
                signal = "INSUFFICIENT DATA"
            elif score >= SCORE_STRONG_BUY:  signal = "STRONG BUY"
            elif score >= SCORE_BULLISH:     signal = "BULLISH / HOLD"
            elif score >= SCORE_NEUTRAL:     signal = "NEUTRAL"
            elif score >= SCORE_BEARISH:     signal = "BEARISH / CAUTION"
            elif score >= SCORE_STRONG_SELL: signal = "STRONG SELL"
            else:                            signal = "TOXIC / AVOID"

            notes_html = "<strong>Algorithmic Breakdown:</strong><br><ul class='algo-breakdown-list'>"
            for item in breakdown:
                notes_html += f"<li>{item}</li>"
            notes_html += "</ul>"
            
            if stop_loss is not None:
                # Normalise Pence to Pounds purely for the UI note
                disp_stop = stop_loss / 100.0 if currency == 'GBp' else stop_loss
                disp_curr = 'GBP' if currency == 'GBp' else currency
                notes_html += f"<strong>Risk Management:</strong> Mathematical <abbr title='Based on Average True Range and dynamically scaled by Historical Stability.'>Dynamic ATR Stop-Loss</abbr> is {disp_stop:,.2f} {disp_curr}.<br><br>"
            
            if not is_fund and rsi_val is not None and rsi_val > 70.0:
                notes_html += "<strong><span class='risk-warning'>Warning:</span></strong> Stock is technically overbought (RSI > 70).<br>"

            tags_json = json.dumps(tags)
            
            self.save_to_db(
                ticker, company_name, sector, country, currency, quote_type,
                current_price, ma5, ma10, ma21, ma50, ma200, trend_50d, trend_200d, rsi_val, stop_loss,
                fifty_two_week_low, fifty_two_week_high,
                trailing_pe, forward_pe, peg_ratio, peter_lynch_peg, price_to_book,
                price_to_sales, free_cash_flow,
                profit_margin, roe, revenue_growth, debt_to_equity, current_ratio, operating_cash_flow,
                ytd_return, total_assets, nav_price, expense_ratio,
                dividend_yield, ex_dividend_date, target_price, analyst_rating, next_earnings_date,
                short_interest, institutional_ownership, beta, yield_correlation,
                score, signal, notes_html, tags_json
            )

            if _has_tech and df is not None and not df.empty:
                event_date = df.index[-1].strftime('%Y-%m-%d') if hasattr(df.index[-1], 'strftime') else str(df.index[-1])[:10]
                log_score_event(ticker, event_date, score, signal, current_price)

        except Exception as e:
            logger.error("Failed to analyze %s: %s", ticker, e)

    def save_to_db(self, ticker: str, company_name: str, sector: str, country: str, currency: str, quote_type: str,
                   price: Optional[float], ma5: Optional[float], ma10: Optional[float], ma21: Optional[float],
                   ma50: Optional[float], ma200: Optional[float],
                   trend_50d: str, trend_200d: str, rsi: Optional[float], stop_loss: Optional[float],
                   fifty_two_week_low: Optional[float], fifty_two_week_high: Optional[float],
                   trailing_pe: Optional[float], forward_pe: Optional[float], peg_ratio: Optional[float],
                   peter_lynch_peg: Optional[float], price_to_book: Optional[float],
                   price_to_sales: Optional[float], free_cash_flow: Optional[float],
                   profit_margin: Optional[float], roe: Optional[float], revenue_growth: Optional[float],
                   debt_to_equity: Optional[float], current_ratio: Optional[float], operating_cash_flow: Optional[float],
                   ytd_return: Optional[float], total_assets: Optional[float], nav_price: Optional[float],
                   expense_ratio: Optional[float],
                   dividend_yield: Optional[float], ex_dividend_date: Optional[str], target_price: Optional[float],
                   analyst_rating: str, next_earnings_date: str, short_interest: Optional[float],
                   institutional_ownership: Optional[float], beta: Optional[float], yield_correlation: Optional[float],
                   score: int, signal: str, notes: str, tags_json: str) -> None:
        
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

        try:
            conn = get_connection()
            try:
                cursor = conn.cursor()
                
                # ON CONFLICT DO UPDATE (not INSERT OR REPLACE) — the latter resets every column
                # NOT in this statement to its schema default, silently wiping out top_holdings/
                # sector_weightings/holdings_updated_at (universe_fundamentals_engine.py) and
                # piotroski_f_score/altman_z_score/beneish_m_score/forensic_last_updated
                # (scheduler_jobs.py's monthly Forensic job) on every single quant scan run.
                query = '''
                    INSERT INTO stock_signals (
                        ticker, last_updated, company_name, sector, country, currency, quote_type,
                        current_price, ma_5_day, ma_10_day, ma_21_day, ma_50_day, ma_200_day, trend_50d, trend_200d, rsi_14, atr_stop_loss,
                        fifty_two_week_low, fifty_two_week_high,
                        trailing_pe, forward_pe, peg_ratio, peter_lynch_peg, price_to_book,
                        price_to_sales, free_cash_flow,
                        profit_margin, roe, revenue_growth, debt_to_equity, current_ratio, operating_cash_flow,
                        ytd_return, total_assets, nav_price, expense_ratio,
                        dividend_yield, ex_dividend_date, target_price, analyst_rating, next_earnings_date,
                        short_interest, institutional_ownership, beta, yield_correlation,
                        composite_score, overall_signal, educational_notes, setup_tags
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?,
                        ?, ?, ?, ?, ?,
                        ?, ?,
                        ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?,
                        ?, ?, ?, ?, ?,
                        ?, ?, ?, ?,
                        ?, ?, ?, ?
                    )
                    ON CONFLICT(ticker) DO UPDATE SET
                        last_updated=excluded.last_updated, company_name=excluded.company_name, sector=excluded.sector,
                        country=excluded.country, currency=excluded.currency, quote_type=excluded.quote_type,
                        current_price=excluded.current_price, ma_5_day=excluded.ma_5_day, ma_10_day=excluded.ma_10_day,
                        ma_21_day=excluded.ma_21_day, ma_50_day=excluded.ma_50_day, ma_200_day=excluded.ma_200_day,
                        trend_50d=excluded.trend_50d, trend_200d=excluded.trend_200d, rsi_14=excluded.rsi_14,
                        atr_stop_loss=excluded.atr_stop_loss,
                        fifty_two_week_low=excluded.fifty_two_week_low, fifty_two_week_high=excluded.fifty_two_week_high,
                        trailing_pe=excluded.trailing_pe, forward_pe=excluded.forward_pe, peg_ratio=excluded.peg_ratio,
                        peter_lynch_peg=excluded.peter_lynch_peg, price_to_book=excluded.price_to_book,
                        price_to_sales=excluded.price_to_sales, free_cash_flow=excluded.free_cash_flow,
                        profit_margin=excluded.profit_margin, roe=excluded.roe, revenue_growth=excluded.revenue_growth,
                        debt_to_equity=excluded.debt_to_equity, current_ratio=excluded.current_ratio,
                        operating_cash_flow=excluded.operating_cash_flow,
                        ytd_return=excluded.ytd_return, total_assets=excluded.total_assets, nav_price=excluded.nav_price,
                        expense_ratio=excluded.expense_ratio,
                        dividend_yield=excluded.dividend_yield, ex_dividend_date=excluded.ex_dividend_date,
                        target_price=excluded.target_price, analyst_rating=excluded.analyst_rating,
                        next_earnings_date=excluded.next_earnings_date,
                        short_interest=excluded.short_interest, institutional_ownership=excluded.institutional_ownership,
                        beta=excluded.beta, yield_correlation=excluded.yield_correlation,
                        composite_score=excluded.composite_score, overall_signal=excluded.overall_signal,
                        educational_notes=excluded.educational_notes, setup_tags=excluded.setup_tags
                '''

                timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                values = (
                    ticker, timestamp, company_name, sector, country, currency, quote_type,
                    _clean(price), _clean(ma5), _clean(ma10), _clean(ma21), _clean(ma50), _clean(ma200), trend_50d, trend_200d, _clean(rsi), _clean(stop_loss),
                    _clean(fifty_two_week_low), _clean(fifty_two_week_high),
                    _clean(trailing_pe), _clean(forward_pe), _clean(peg_ratio), _clean(peter_lynch_peg), _clean(price_to_book),
                    _clean(price_to_sales), _clean(free_cash_flow),
                    _clean(profit_margin), _clean(roe), _clean(revenue_growth), _clean(debt_to_equity), _clean(current_ratio), _clean(operating_cash_flow),
                    _clean(ytd_return), _clean(total_assets), _clean(nav_price), _clean(expense_ratio),
                    _clean(dividend_yield), ex_dividend_date, _clean(target_price), analyst_rating, next_earnings_date,
                    _clean(short_interest), _clean(institutional_ownership), _clean(beta), _clean(yield_correlation),
                    int(score), signal, notes, tags_json
                )
                
                cursor.execute(query, values)

                cleaned_stop_loss = _clean(stop_loss)
                if cleaned_stop_loss is not None:
                    cursor.execute('''
                        INSERT INTO atr_stop_history (ticker, date, atr_stop_loss)
                        VALUES (?, ?, ?)
                        ON CONFLICT(ticker, date) DO UPDATE SET atr_stop_loss=excluded.atr_stop_loss
                    ''', (ticker, timestamp.split(" ")[0], cleaned_stop_loss))

                # Stamp the composite score onto the latest quant_signals row so
                # it is part of the time series and available for future backtesting.
                cursor.execute('''
                    UPDATE quant_signals
                    SET composite_score = ?, overall_signal = ?
                    WHERE ticker = ?
                      AND date = (SELECT MAX(date) FROM quant_signals WHERE ticker = ?)
                ''', (int(score), signal, ticker, ticker))

                conn.commit()
                logger.info("[SUCCESS] Analyzed %s | Signal: %s | Score: %s/100", ticker, signal, score)
                
            except Exception as e:
                conn.rollback()
                logger.error("Failed to execute insertion for %s: %s", ticker, e)
            finally:
                conn.close()
        except Exception as conn_e:
            logger.error("Failed to secure database connection for %s: %s", ticker, conn_e)

    def run_all(self) -> None:
        from utils import is_synthetic_ticker
        logger.info("Starting Institutional Quantamental Engine...")
        for filename in os.listdir(HISTORICAL_DIR):
            if filename.endswith(".parquet") and "BASELINE" not in filename:
                ticker = filename.replace(".parquet", "")
                if is_synthetic_ticker(ticker):
                    continue
                self.analyze_ticker(ticker)
        logger.info("Analysis Complete. Master Database is fully updated.")

if __name__ == "__main__":
    engine = QuantEngine()
    engine.run_all()