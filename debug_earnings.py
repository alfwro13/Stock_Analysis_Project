import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

def debug_nvda_earnings():
    ticker = "NVDA"
    print(f"--- 🔍 DIAGNOSTIC: EARNINGS VOLATILITY FOR {ticker} ---")
    
    tk = yf.Ticker(ticker)
    
    # 1. Test Info Extraction (Where quant_signals gets the date)
    info = tk.info
    earnings_ts = info.get('earningsTimestamp')
    if earnings_ts:
        next_earnings_date = datetime.fromtimestamp(earnings_ts)
        print(f"✅ 1. YF .info payload returned Next Earnings Date: {next_earnings_date.strftime('%Y-%m-%d')}")
    else:
        print("❌ 1. YF .info payload failed to return 'earningsTimestamp'. (Check yfinance version)")
        # Hardcode for testing purposes if it fails
        next_earnings_date = datetime.now() + timedelta(days=2) 
        print(f"   -> Hardcoding date to {next_earnings_date.strftime('%Y-%m-%d')} to continue test.")

    # 2. Test Underlying Price
    hist = tk.history(period="5d")
    if hist.empty:
        print("❌ 2. Failed to fetch underlying price.")
        return
    underlying_price = hist['Close'].iloc[-1]
    print(f"✅ 2. Current Underlying Price: ${underlying_price:.2f}")

    # 3. Test Historical Earnings Dates (Where the engine gets past moves)
    print("\n--- Testing Historical Moves ---")
    try:
        earnings_dates = tk.get_earnings_dates(limit=10)
        if earnings_dates is None or earnings_dates.empty:
            print("❌ 3. tk.get_earnings_dates() returned empty. Yahoo Finance API may be blocking/deprecated.")
        else:
            past_dates = earnings_dates[earnings_dates.index < pd.Timestamp.now(tz='UTC')].index
            print(f"✅ 3. Found {len(past_dates)} past earnings dates. Calculating historical gaps...")
            
            moves = []
            for e_date in past_dates[:4]:
                start_date = (e_date - timedelta(days=3)).strftime('%Y-%m-%d')
                end_date = (e_date + timedelta(days=4)).strftime('%Y-%m-%d')
                e_hist = tk.history(start=start_date, end=end_date)
                
                if len(e_hist) >= 2:
                    tz_naive_date = e_date.tz_localize(None)
                    # Nearest index
                    closest_idx = e_hist.index.get_indexer([tz_naive_date], method='nearest')[0]
                    if 0 < closest_idx < len(e_hist):
                        pre_close = e_hist['Close'].iloc[closest_idx - 1]
                        post_close = e_hist['Close'].iloc[closest_idx]
                        move = abs((post_close - pre_close) / pre_close) * 100.0
                        moves.append(move)
                        print(f"   -> Move on {e_date.strftime('%Y-%m-%d')}: {move:.2f}%")
            
            if moves:
                hist_avg = np.mean(moves)
                print(f"✅ Historical Average Move: {hist_avg:.2f}%")
            else:
                print("❌ Failed to calculate historical average.")
    except Exception as e:
        print(f"❌ 3. Exception fetching historical dates: {e}")

    # 4. Test Options Chain (Where the engine gets Implied Volatility)
    print("\n--- Testing Implied Options Move ---")
    try:
        options = tk.options
        if not options:
            print("❌ 4. tk.options returned empty. Options API failed.")
        else:
            # Find the nearest expiration after our earnings date
            valid_expiries = [opt for opt in options if datetime.strptime(opt, '%Y-%m-%d') >= next_earnings_date]
            if not valid_expiries:
                print(f"❌ 4. No options expirations found after {next_earnings_date.strftime('%Y-%m-%d')}.")
            else:
                target_expiry = valid_expiries[0]
                print(f"✅ 4. Found valid options expiration mapping to earnings: {target_expiry}")
                
                chain = tk.option_chain(target_expiry)
                calls, puts = chain.calls, chain.puts
                
                # Find ATM Strike
                atm_strike = calls.iloc[(calls['strike'] - underlying_price).abs().argsort()[:1]]['strike'].values[0]
                atm_call = calls[calls['strike'] == atm_strike].iloc[0]
                atm_put = puts[puts['strike'] == atm_strike].iloc[0]
                
                def get_price(opt_row):
                    if opt_row['bid'] > 0 and opt_row['ask'] > 0:
                        return (opt_row['bid'] + opt_row['ask']) / 2.0
                    return opt_row['lastPrice']
                
                straddle_cost = get_price(atm_call) + get_price(atm_put)
                implied_move = (straddle_cost / underlying_price) * 100.0
                
                print(f"   -> ATM Strike Chosen: ${atm_strike}")
                print(f"   -> Straddle Cost (Call+Put): ${straddle_cost:.2f}")
                print(f"✅ Implied Move (Market Expectation): {implied_move:.2f}%")
                
    except Exception as e:
        print(f"❌ 4. Exception fetching options chain: {e}")

if __name__ == "__main__":
    debug_nvda_earnings()