
change portfolio and watchlist to use the same table as the 4K report
fix the menus/liks
make sure notification audo-refreshes (any new notifications)


reporting: monthly_gains_3 etc

sector analysis
to do later?
Remove the Options sandbox - I do not see myself using it. I am not a day/swing trader. So I am not sure how much use this will be to me. 

Migrated
earnings_tracker.py  -> earnings_vol_engine


Mabey later:
portfolio-sizing.py - Trade execution math (like position sizing and risk management per ticket) falls slightly outside that scope. Furthermore, your Ghostfolio integration handles your actual portfolio tracking. If you want this back, we can easily add a small "Position Sizer" widget to the Settings or Watchlist page in the future!

reit-correlation.py - how can we use this comparison? I'm not interested in reti but is the comparison useful for something else?

rsi-estimate.py

rsi_dips.py

spy-vix-live-tracker.py  - what is the VIX index, and how can it be useful?

spy_overnight_double_diagonal.py

spy_performance.py

spy_weekly_gain_loss_charts.py

spy-vix-desktop-tracker.py

stock-price-prediction-model.py

stock-volatility.py

stock-weekday-volatility.py

stock_correlations.py

stockbee-market-monitor-plotter.py

stocks_data_enricher.py

streamgraph_chart.py

tqqq-for-the-long-run.py - can we use that to come up with a strategy?

trend-plotter.py

vix_basis_analysis.py

weekly-returns-analysis.py

stocks-scanner-reporting.gif - that gif shows html report generated with a log of 


Option 2: The Backtesting Sandbox (The Quant Route)
We intentionally left the backtrader scripts behind in the old repo because they belonged in their own dedicated module. We can build a /backtester web UI where you can test strategies (e.g., "Buy SPY when RSI < 30") over the last 10 years and visualize the equity curve and drawdown stats instantly.

Option 3: Machine Learning (The Deep Tech Route)
Since you have 4,000+ stocks constantly updating with technical indicators, we can introduce scikit-learn or XGBoost. We can train an ML model to look at the historical quant_signals and predict short-term directional probability, adding a new "AI Confidence Score" column to your Market Screener.