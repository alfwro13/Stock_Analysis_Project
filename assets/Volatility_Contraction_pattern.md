## 1\. Minervini Volatility Contraction Pattern (VCP)

The Volatility Contraction Pattern (VCP) is a reliable price-action setup popularized by US Investing Champion Mark Minervini. It functions like a coiled spring: it identifies areas where institutional accumulation is quietly absorbing the market supply without pushing prices aggressively higher, setting up a high-probability breakout event.

### A. The Core Logic & Variables

A standard VCP setup forms within a primary structural uptrend and exhibits a series of price contractions (retrenchments) from left to right. Each wave's peak-to-trough drop becomes successively smaller (e.g., a \$25\\%\$ drop, followed by a \$10\\%\$ drop, followed by a \$3\\%\$ drop). This contraction sequence indicates that overhead selling pressure is drying up.

Your background engine handles this by scanning a multi-week rolling lookback window:

- **The Lookback Window:** The system evaluates weekly data slices across a 4-week boundary.
    
- **Price Variance Filter:** It isolates the immediate 3 weeks leading right up to the current session (`iloc[-4:-1]`). It solves for the extreme variance range over that period:
    
    Variance % = (Weekly Close MAX = Weekly Close MIN) / Weekly Close MIN
    
- **The Strict Threshold:** For a stock to trigger a true **`🔥 VCP Breakout`** tag, the price variance must be exceptionally tight—specifically **\$\\le 2.5\\%\$**. This indicates the asset has formed an incredibly quiet, narrow horizontal price base (known as "3-Weeks-Tight").
    

### B. The Volume Dry-Up Multiplier

Price containment alone isn't enough; true institutional absorption requires volume confirmation. Your `QuantEngine` solves for this using a 50-day rolling baseline average:

- It averages the transaction volume over the 3-week base period and annualizes it to a daily equivalent.
    
- **The Volume Constraints:** The system requires the base period's daily average volume to dry up significantly—dropping **below \$80\\%\$** of the 50-day average volume baseline (\$\\text{Volume}\_{\\text{BaseAvg}} < \\text{Volume}\_{\\text{SMA50}} \\times 0.8\$).
    
- **System Outputs:** If both parameters clear, the stock is awarded **+20 points** toward its composite score and flags a bright purple **`🔥 VCP Breakout`** setup badge on your dashboard tables. If the price is tight but volume hasn't dried up yet, it scales back to a moderate **+10 points** under a placeholder "3-Weeks-Tight" classification.
    

