
change portfolio and watchlist to use the same table as the 4K report
fix the menus/liks
make sure notification audo-refreshes (any new notifications)
add company profile to details page
for univers tickers -add dynamic data download from yahoo and caching so I ce see the details page

the trading-utils has graphs for each ticker after running the make weekend routing - why do I not have that data? what are we doing different

reporting: monthly_gains_3 etc

sector analysis
to do later?
Remove the Options sandbox - I do not see myself using it. I am not a day/swing trader. So I am not sure how much use this will be to me. 

Migrated
earnings_tracker.py  -> earnings_vol_engine


Mabey later:
portfolio-sizing.py - Trade execution math (like position sizing and risk management per ticket) falls slightly outside that scope. Furthermore, your Ghostfolio integration handles your actual portfolio tracking. If you want this back, we can easily add a small "Position Sizer" widget to the Settings or Watchlist page in the future!

reit-correlation.py
            What it did: Calculated a Pearson correlation matrix to see how closely the daily price movements of various Real Estate Investment Trusts (REITs) tracked each other.

            Migration Status: Not Migrated.

            Why the math is highly useful elsewhere:

            Pairs Trading (Statistical Arbitrage): You can use this logic to find two highly correlated stocks (like Visa and Mastercard). If their prices temporarily diverge, you short the winner and buy the loser, betting they will snap back together.

            Hedging: You can find assets that are negatively correlated to your current portfolio to protect yourself during market crashes.

            Diversification: If you own 5 tech stocks with a 0.95 correlation, you aren't actually diversified. This math proves true diversification.

            Future potential: We could easily repurpose this math to build a "Correlation Matrix" tool in your /market-reports dashboard, allowing you to test the correlation of your personal portfolio against SPY or QQQ!

spy-vix-live-tracker.py  -
            Future potential: We can easily pull the daily VIX close into a "Macro/Market Pulse" widget on your dashboard to help you gauge overall market fear before you place trades!


stock-price-prediction-model.py
            Future potential: This is exactly what I mentioned in the "Option 3: Deep Tech Route" earlier! We can eventually use your massive new SQLite database to train a robust ML model and display an "AI Prediction Score" right next to your Technical Indicators on the Market Screener.


stock-weekday-volatility.py
            Future potential: If you rely on day-of-the-week seasonality for trading edge, this math is a perfect candidate for the future /backtester UI we discussed, where you can test rules like "Only buy on Thursdays."

tqqq-for-the-long-run.py - can we use that to come up with a strategy?

trend-plotter.py

vix_basis_analysis.py

weekly-returns-analysis.py

stocks-scanner-reporting.gif - that gif shows html report generated with a log of 


Option 2: The Backtesting Sandbox (The Quant Route)
We intentionally left the backtrader scripts behind in the old repo because they belonged in their own dedicated module. We can build a /backtester web UI where you can test strategies (e.g., "Buy SPY when RSI < 30") over the last 10 years and visualize the equity curve and drawdown stats instantly.

Option 3: Machine Learning (The Deep Tech Route)
Since you have 4,000+ stocks constantly updating with technical indicators, we can introduce scikit-learn or XGBoost. We can train an ML model to look at the historical quant_signals and predict short-term directional probability, adding a new "AI Confidence Score" column to your Market Screener.