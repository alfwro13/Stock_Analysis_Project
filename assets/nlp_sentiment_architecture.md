# NLP Market Sentiment Engine Architecture

## 1. Overview
Historically, the terminal's news sentiment was processed as a batch operation during the overnight cron job. To capture intraday narrative shifts, macroeconomic data releases, and rapid market reactions, the system has been upgraded to a **dynamic 4-hour rolling interval**. Utilizing `APScheduler` and FastAPI `BackgroundTasks`, this engine continually ingests, processes, and scores the broader market narrative in near real-time, ensuring our dashboards reflect the actual intraday psychological regime.

## 2. The FinBERT Model
The core of the NLP engine relies on `ProsusAI/finbert` via the HuggingFace pipeline. Unlike generalized sentiment models (like VADER), FinBERT is a state-of-the-art transformer fine-tuned specifically on financial lexicons, allowing it to accurately differentiate between corporate actions, earnings phrasing, and macro headwinds.

**Data Pipeline:**
1. **Extraction:** We fetch news payloads via the `yfinance` API.
2. **Parsing:** The pipeline recursively handles nested provider dictionaries to isolate the raw `headline` and `summary` strings.
3. **Truncation:** To prevent pipeline crashes and comply with standard BERT tensor limits, all text is stringently truncated to a maximum of 512 tokens.
4. **Scoring:** The pipeline evaluates the parsed text and outputs a normalized, compound float score ranging from `-1.0` (Maximum Pessimism) to `1.0` (Maximum Optimism).

## 3. Macro Application & Forex
Sentiment analysis is not restricted solely to individual equities. The engine applies the FinBERT pipeline to core macro indicators to gauge the structural health of the market:
* **Broad Indices:** S&P 500 (`^GSPC`), Nasdaq 100 (`^NDX`), and the FTSE.
* **Forex:** GBP/USD (`GBPUSD=X`) and the Dollar Index (`DX-Y.NYB`).

Evaluating news attached to these macro-level tickers provides a leading indicator of broad market regimes, which is critical for adjusting the exposure of our quantitative trading strategies.

## 4. The Yield Exclusion Thesis (CRITICAL)
A deliberate and non-negotiable architectural safeguard has been implemented: **Sovereign bond yields (e.g., US 30Y, UK Gilts) are strictly excluded from NLP scoring.**

* **The Inverse-Logic Trap:** Financial NLP models inherently classify action-verbs like "surging," "soaring," or "breaking out" as positive sentiment. 
* **The Macro Reality:** In quantitative finance, a surging risk-free yield compresses equity valuation multiples, dramatically increases the cost of capital, and acts as a fundamentally bearish headwind for the stock market. 

If we allowed FinBERT to score news on the US 10-Year or 30-Year yields, a headline like *"Bond Yields Surge to 5% on Inflation Fears"* would be mapped as strongly positive, resulting in a "Euphoria" badge being displayed during a toxic equity sell-off. Therefore, yield metrics rely entirely on quantitative rate-of-change math and bypass the NLP pipeline completely.

## 5. UI Mapping Logic
The raw continuous compound score (-1.0 to 1.0) is mathematically mapped into 5 categorical badges for the frontend Jinja2 templates. This ensures rapid visual comprehension for the end-user:

* **Euphoria:** Score >= 0.60
* **Bullish:** 0.15 <= Score < 0.60
* **Neutral:** -0.15 < Score < 0.15
* **Bearish:** -0.60 < Score <= -0.15
* **Extreme Fear:** Score <= -0.60

*Note: Boundaries are strictly defined in the backend processing utility before being committed via raw SQL to the database.*