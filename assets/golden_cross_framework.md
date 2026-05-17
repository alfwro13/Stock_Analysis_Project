# The Structural Golden Cross Framework

While VCP looks for tight chart structures, the **Golden Cross** framework acts as your primary momentum and macro alignment system. It maps long-term trend changes driven by major institutional inflows.

Your codebase implements this momentum architecture across two distinct modules:

### A. The MACD Bullish Cross Indicator (`quant_engine.py`)

Your script utilizes the Moving Average Convergence Divergence (MACD) oscillator to track structural trend reversals. Rather than checking for a general positive value, the script uses a strict **2-day lookback validation trigger** to isolate the exact day a crossover occurs:

Python

```
# The exact check executed in your daily quant engine
bullish_cross = False
if len(macd_indicator.macd()) >= 2 and not pd.isna(macd_indicator.macd().iloc[-2]):
    prev_macd = macd_indicator.macd().iloc[-2]
    prev_sig = macd_indicator.macd_signal().iloc[-2]
    bullish_cross = bool((c_macd > c_signal) and (prev_macd <= prev_sig))
```

- **The Trigger:** The current day's MACD line must be strictly greater than the Signal line, while the previous day's MACD line was less than or equal to the Signal line.
    
- **System Action:** If this condition evaluates to true, the database sets the `bullish_cross` flag to `1`. This feeds directly into your **Overnight Quant Screener** markdown reports as a new **`📈 Golden MACD Cross`** signal entry.
    

### B. Moving Average Convergence Verification (`quant_signals.py`)

To ensure shorter-term crossovers aren't whipsawed by bad price action, your core scoring model applies a strict trend convergence verification filter. It demands clean structural separation across multiple trend indicators:

- **Short-Term Alignment:** The 5-day Simple Moving Average must sit cleanly above the 10-day SMA, and the 10-day SMA must sit above the 21-day SMA (\$\\text{SMA}\_5 > \\text{SMA}\_{10} > \\text{SMA}\_{21}\$). Clearing this hurdle secures an immediate **+15 points**.
    
- **Long-Term Macro Floor:** The system independently checks the direction of the 200-day SMA over an explicit 20-day window. The current 200-day average price must be strictly higher than it was 20 trading sessions ago, confirming that institutions are actively supporting the stock's long-term trend. This long-term trend health confirms institutional backing and adds another **+15 points**.
    

* * *

## Strategic Integration & The Macro Override Filter

Because your terminal is **regime-aware**, your code automatically filters these individual chart setups through macro risk overrides before displaying them:

- **Normal Regimes:** When macro turbulence is low (like right now, sitting comfortably at `14.72`), these indicators run in high-precision mode. A VCP tight-base breakout or Golden MACD Cross is considered highly valid and actionable.
    
- **Turbulent Overrides (`Volatile` / `Crash` / `RED Yield Threat`):** If your macro metrics jump, the terminal automatically alters its filtering logic. The `quant_screener.py` engine will immediately **veto and drop** any breakout setups if the asset's current price is stuck below its 200-day moving average. This ensures the terminal blocks speculative breakouts when a choppy macro environment makes them highly prone to failing.