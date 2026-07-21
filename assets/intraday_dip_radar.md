# Intraday Dip Radar — Technical Documentation

**Project:** Stock Analysis Quantitative Trading Terminal  
**Engine:** `intraday_bottom_engine.py`  
**API Endpoints:** `POST /api/intraday-monitor/add`, `POST /api/intraday-monitor/remove`, `GET /api/intraday-monitor/list`, `GET /api/intraday-monitor/analysis/{ticker}`  
**Last Updated:** 2026-06-05  

---

## Table of Contents

1. [Overview](#1-overview)
2. [Quantitative Approach — Why These Four Signals](#2-quantitative-approach--why-these-four-signals)
3. [Scoring Algorithm](#3-scoring-algorithm)
4. [VWAP Calculation](#4-vwap-calculation)
5. [Bollinger Band Configuration (2.5σ)](#5-bollinger-band-configuration-25σ)
6. [Volume Capitulation Detection](#6-volume-capitulation-detection)
7. [Session Lifecycle](#7-session-lifecycle)
8. [Alert & Notification System](#8-alert--notification-system)
9. [Deduplication](#9-deduplication)
10. [Scheduler](#10-scheduler)
11. [API Endpoints](#11-api-endpoints)
12. [UI Integration](#12-ui-integration)
13. [Database Schema](#13-database-schema)
14. [Settings & Configuration](#14-settings--configuration)
15. [Known Limitations](#15-known-limitations)

---

## 1. Overview

Dip Radar is an **on-demand, session-scoped intraday bottom detection system**. When the market is pulling back on a stock, it answers a specific question in real time: *Is selling pressure mathematically exhausted, or is further downside likely?*

The feature is distinct from the always-on Crash Engine (`crash_engine.py`), which detects that a drop has already occurred. Dip Radar detects the *end* of that drop — the capitulation point where forced sellers have been washed out and a mean-reversion rally is likely.

| System | Question it answers | Scope |
|--------|---------------------|-------|
| `crash_engine.py` | *Has a stock dropped enough to alert me?* | Always-on, all portfolio tickers |
| `intraday_bottom_engine.py` | *Has the selling pressure exhausted itself — is the bottom in?* | On-demand, user-selected tickers, one trading day |

**Design philosophy — on-demand only:**  
The feature does not run continuously on all portfolio tickers. The user arms specific tickers from the stock detail page on the days they are actively watching a pullback. This is intentional:

- Prevents scanning noise on stocks not in a dip
- Eliminates alert fatigue from scores firing on minor intraday fluctuations
- Keeps the 2-minute scheduler job lightweight (fast-exits silently when no tickers are armed)
- Mirrors the institutional workflow: a trader identifies a dipping stock of interest and *then* monitors it for entry signals

---

## 2. Quantitative Approach — Why These Four Signals

Catching the exact bottom of a dip is statistically impossible. The correct framing is identifying **Exhaustion Zones** — price regions where the probability of further selling is low because the *mechanical* sources of downward pressure (margin calls, stop-loss cascades, forced redemptions) have been mathematically exhausted.

Four independent dimensions are measured:

### Dimension A — RSI Momentum Exhaustion
RSI (Relative Strength Index) at 14-period on 1-minute data. An RSI below 30 on daily data indicates oversold conditions. On 1-minute data, the same threshold filters noise — only extreme, fast selling moves trigger it. An RSI below 25 (Extreme Oversold) indicates that the velocity of selling is at a statistical extreme.

**Why RSI on 1-minute data specifically:**  
Daily RSI smooths out intraday extremes. Intraday RSI captures the *speed* of the current selling wave, which is the relevant measure for timing an entry point.

### Dimension B — Bollinger Band Piercing (Lower Band, 2.5σ)
Standard Bollinger Bands use 2σ. At that width, a closing price below the lower band is expected ~5% of the time under a normal distribution. Dip Radar uses 2.5σ, reducing expected frequency to ~1.2%. A price piercing the 2.5σ lower band on 1-minute data represents a statistically extreme dislocation — price has moved far enough that statistical reversion pressure is significant.

### Dimension C — VWAP Deviation (−2.5σ)
VWAP is the intraday fair value benchmark — the weighted average price at which all volume has traded since the open. Institutional execution desks use VWAP as their baseline. A price trading 2.5σ below VWAP means the stock has deviated so far from the consensus intraday fair value that it is a mathematical outlier. Institutions buying at VWAP or better will find the current price attractive, providing a demand floor.

### Dimension D — Volume Climax (Capitulation)
The most powerful single signal. Capitulation occurs when volume on a down-candle is 3+ standard deviations above its 20-candle rolling mean. This is the quantitative fingerprint of forced selling exhaustion: all participants who *had* to sell (margin calls, stop-losses, panic redemptions) have done so simultaneously. After a volume climax, supply is temporarily exhausted and the path of least resistance is upward.

**The directional requirement:** The volume climax is only counted when the candle closes lower than the prior candle. A high-volume up-candle (breakout) would otherwise score this condition, which would be incorrect for bottom detection.

---

## 3. Scoring Algorithm

All scoring uses the second-to-last available 1-minute candle (`df.iloc[-2]`). The most recent candle (`df.iloc[-1]`) is still forming and has incomplete volume and price data.

```python
score = 0
reasons = []

# A: RSI
if rsi < 25:
    score += 30  # extreme oversold
elif rsi < 30:
    score += 15  # oversold

# B: Bollinger Band (20-period, 2.5σ)
if close < bb_lower:
    score += 25

# C: VWAP Deviation (rolling 30-bar σ on Close)
if close < (vwap - 2.5 * vwap_std):
    score += 20

# D: Volume Climax on a down-candle
if volume > (vol_sma20 + 3 * vol_std20) and close < prior_close:
    score += 25
```

**Score interpretation:**

| Score | Interpretation |
|-------|---------------|
| 0–39 | No meaningful exhaustion signal — selling momentum intact |
| 40–64 | Partial signal — oversold in one or two dimensions, monitor but wait |
| 65–79 | **Alert threshold** — multiple exhaustion conditions active, high-probability reversal zone |
| 80–100 | **Capitulation zone** — all major conditions confirmed, volume washout likely complete |

**Maximum possible score: 100** (RSI < 25 = 30pts, BB pierce = 25pts, VWAP deviation = 20pts, Volume Climax = 25pts)

**Alert fires at: ≥ 65**

The threshold of 65 requires at least two to three conditions to be simultaneously active. A single condition alone (maximum 30 pts from RSI alone) is never sufficient to alert. This prevents single-indicator false positives, which are common in volatile markets.

---

## 4. VWAP Calculation

VWAP is recalculated from the start of each trading session using the cumulative formula:

```python
typical_price = (df['High'] + df['Low'] + df['Close']) / 3
vwap = (typical_price * df['Volume']).cumsum() / df['Volume'].cumsum()
```

**Anchoring:** `yfinance` with `period="1d"` returns data from the current session open only. The cumulative VWAP therefore automatically anchors to today's open without requiring explicit session start detection.

**Standard deviation band:** A 30-bar rolling standard deviation of `Close` is applied around VWAP to define the ±2.5σ bands. This is intentionally *not* a VWAP standard deviation (which would use `(typical_price - VWAP)²`) — the simpler Close-rolling-std is used because it is more responsive to rapid intraday price compression and expansion.

---

## 5. Bollinger Band Configuration (2.5σ)

```python
bb_mid   = df['Close'].rolling(20).mean()
bb_std   = df['Close'].rolling(20).std()
bb_lower = bb_mid - (2.5 * bb_std)
```

**Why 20 bars (not the standard 14 or 26):**  
20 bars ≈ 20 minutes of 1-minute data, which captures roughly one "micro session" of intraday trading. This provides enough history for the standard deviation to be statistically meaningful while remaining responsive to the current session's volatility regime.

**Why 2.5σ (not the standard 2.0σ):**  
Standard 2σ Bollinger Bands fire on ~5% of candles. On a 400-candle trading day, that would be ~20 signals. At 2.5σ the expected frequency drops to ~1.2%, yielding ~5 signals per day — low enough to be meaningful but high enough to capture real extremes.

---

## 6. Volume Capitulation Detection

```python
vol_sma20 = df['Volume'].rolling(20).mean()
vol_std20 = df['Volume'].rolling(20).std()
vol_climax = df['Volume'] > (vol_sma20 + 3 * vol_std20)
```

The condition fires only when BOTH are true:
1. `vol_climax` is `True` for the scored candle (volume > rolling mean + 3σ)
2. The scored candle closes *below* the prior candle's close (confirming the high volume is on a down-move, not an up-breakout)

**Statistical context:**  
Under a normal distribution, a value 3σ above the mean has a probability of ~0.13% — roughly 1 in 750 candles. On a 390-candle trading day, this is expected less than once per day under normal conditions. When it does occur, it is a high-conviction signal.

---

## 7. Session Lifecycle

```
User opens stock detail page
    → "Dip Radar" panel visible (collapsed)
    → User checks "Monitor [TICKER] for today's session"
        → POST /api/intraday-monitor/add
        → Row inserted: intraday_monitors(ticker, date_added=today, is_active=1)
        → alert_state(engine='dip_radar', ticker, armed=1)
        → Scheduler picks up ticker on next 2-minute tick

Every 2 minutes (09:00–15:59 ET, Mon–Fri):
    → run_intraday_dip_scan() checks for active monitors
    → If none: function returns immediately (no yfinance calls)
    → If active: analyzes each ticker, persists results to intraday_monitor_results
    → If score ≥ 65: fires alert (in-app + optionally Nextcloud Talk)

Stock detail page polls every 2 minutes (if monitoring active):
    → GET /api/intraday-monitor/analysis/{ticker}
    → Renders score, price, VWAP, reasons in the Dip Radar panel

At 16:05 ET (Mon–Fri):
    → run_intraday_dip_reset() fires
    → UPDATE intraday_monitors SET is_active=0 WHERE date_added=today
    → Logs "session ended" notification
    → Next day all monitors must be re-armed manually
```

**Day boundary behaviour:**  
The `date_added` column is set to today's ISO date at arm time. The `get_active_monitors()` query filters on `is_active=1 AND date_added=today`. A monitor armed on Monday is automatically invisible to Tuesday's scan without requiring any cleanup — the date filter silently excludes yesterday's rows.

---

## 8. Alert & Notification System

When score ≥ 65 and the alert is armed, two notifications are dispatched:

### In-app notification (always)
Written to `system_notifications` via `log_notification()` from `database.py`:

```
[DipRadar] 🎯 Dip Radar | AAPL @ 185.42 | Score: 75/100 — Extreme Oversold (RSI: 22.1) | Volume Capitulation detected
```

Appears in the notification bell (orange `DipRadar` badge) and is visible on all pages.

### Nextcloud Talk (optional)
Dispatched through `notification_engine.notify()` (source key `dip_radar_alert`) when the alert is still armed (not already disarmed by a prior firing this session) and the source's Nextcloud Talk channel is enabled. Nextcloud is off by default for this source.

```
🎯 Dip Radar | AAPL @ 185.42 | Score: 75/100
• Extreme Oversold (RSI: 22.1)
• Volume Capitulation — high-volume down-candle (weak hands washing out)
```

**Enabling Nextcloud Talk alerts:**  
Settings → 🔔 Notification Settings → enable the *Nextcloud Talk* column on the **Dip Radar — Bottom Detected** row.

---

## 9. Deduplication

Dip Radar uses the shared `alert_state` table with `engine='dip_radar'`.

```
arm_alert(ticker):
    INSERT OR REPLACE INTO alert_state
    (engine='dip_radar', ticker, armed=1, state_date=today)

_should_alert(ticker):
    SELECT armed FROM alert_state
    WHERE engine='dip_radar' AND ticker=?
    → Returns True if armed=1

_disarm_alert(ticker):
    UPDATE alert_state SET armed=0
    WHERE engine='dip_radar' AND ticker=?
```

**Behaviour:** Once the alert fires for a session, `armed` is set to 0. Subsequent scans that still find score ≥ 65 will persist the result to `intraday_monitor_results` (so the UI continues to update) but will not re-fire the in-app or Nextcloud notification. This prevents the user from being spammed during a period of sustained oversold conditions.

**Re-arming:** The alert re-arms the next time the user enables monitoring for that ticker (i.e., the next trading day they tick the checkbox on the stock detail page).

---

## 10. Scheduler

| Job | Function | Schedule | Config key |
|-----|----------|----------|-----------|
| Intraday bottom scan | `run_intraday_dip_scan()` | Mon–Fri 09:00–15:59 every 2 min | Always-on |
| Session reset | `run_intraday_dip_reset()` | Mon–Fri 16:05 | Always-on |

Both jobs are **always registered** (no config flag required to enable them). The scan job exits immediately if no tickers are armed, so the overhead when the feature is not in use is negligible (one SQLite read per 2-minute tick).

**Timing of the scan window:**  
`CronTrigger(day_of_week='mon-fri', hour='7-21', minute='1-59/2', timezone=timezone.utc)`

(Starts at minute 1, not 0 — staggered off the round-minute boundary alongside several other write-heavy scheduled jobs, to avoid the multi-job "database is locked" pileups found 2026-07-21.)

This covers 09:00–15:59 ET. The final scan of the session occurs at 15:58. The reset job fires at 16:05, giving a 7-minute window after close for any final analysis before monitors are cleared.

**`misfire_grace_time=60`** on the scan job: if a scan tick is missed (e.g., the server is under load), APScheduler will execute it up to 60 seconds late rather than skipping it. This prevents gaps during volatile market hours.

---

## 11. API Endpoints

### `POST /api/intraday-monitor/add`
Arms a ticker for monitoring.

**Request body:**
```json
{ "ticker": "AAPL" }
```

**Response:**
```json
{ "status": "ok", "ticker": "AAPL" }
```

**Side effects:**
- Upserts a row in `intraday_monitors` (`date_added=today, is_active=1, activated_by='user'`)
- Arms `alert_state` row (`engine='dip_radar', ticker, armed=1, state_date=today`)

---

### `POST /api/intraday-monitor/remove`
Disarms a ticker for the current session.

**Request body:**
```json
{ "ticker": "AAPL" }
```

**Response:**
```json
{ "status": "ok", "ticker": "AAPL" }
```

**Side effects:**
- Sets `intraday_monitors.is_active=0` for the ticker

---

### `GET /api/intraday-monitor/list`
Returns all monitors for today.

**Response:**
```json
{
  "monitors": [
    { "ticker": "AAPL", "date_added": "2026-06-05", "is_active": 1 },
    { "ticker": "NVDA", "date_added": "2026-06-05", "is_active": 0 }
  ]
}
```

---

### `GET /api/intraday-monitor/analysis/{ticker}`
Returns the latest scan result for a ticker. Returns `null` if no scan has completed yet.

**Response (hit):**
```json
{
  "ticker": "AAPL",
  "scan_ts": "2026-06-05 14:32",
  "current_price": 185.42,
  "reversal_score": 75,
  "is_bottoming": 1,
  "reasons": [
    "Extreme Oversold (RSI: 22.1)",
    "Volume Capitulation — high-volume down-candle (weak hands washing out)"
  ],
  "rsi": 22.1,
  "vwap": 188.75,
  "vwap_deviation": -3.33
}
```

**Response (no data):** `null` (HTTP 200)

---

## 12. UI Integration

### Stock Detail Page (`stock_detail.html`)

A collapsible "🎯 Dip Radar" panel is inserted between the **Intraday Pulse** chart section and the **Price Action & Pivot Levels** section.

**States:**
- **Collapsed, inactive** — default state for all stocks. Panel title visible, collapsed.
- **Expanded, inactive** — user has expanded the panel but not enabled monitoring. Shows the checkbox and description only.
- **Expanded, active** — monitoring is enabled. Shows checkbox (checked), and the live score display (`dip-score-display` div) which polls `/api/intraday-monitor/analysis/{ticker}` every 2 minutes.

**Score display colours:**
- Score ≥ 65 (alert zone): `#00ff00` (green)
- Score 40–64 (watch zone): `#ffaa00` (amber)
- Score 0–39 (no signal): `#ff4d4d` (red)

**Server-side state:** The `is_dip_monitored` template variable is set by `page_routes.py` at render time. When `True`, the panel is rendered open and the 2-minute polling `setInterval` is injected into the page immediately.

### Settings Page (`settings.html`)

A new **"🎯 Dip Radar — Intraday Bottom Finder"** card is inserted in the intraday monitoring section (between Crash & Moonshot Alerts and News & RSS).

The card contains **Active Session Monitors** — a dynamically loaded list of today's monitors with per-ticker Disable buttons, populated by polling `GET /api/intraday-monitor/list` on page load. Channel routing for the bottom-detected alert now lives in the dedicated **Notification Settings** card (source `dip_radar_alert`), not in this card.

---

## 13. Database Schema

### `intraday_monitors`

Tracks which tickers the user has armed for the current session.

```sql
CREATE TABLE IF NOT EXISTS intraday_monitors (
    ticker       TEXT PRIMARY KEY,
    date_added   DATE NOT NULL,
    is_active    INTEGER NOT NULL DEFAULT 1,
    activated_by TEXT
);
```

| Column | Type | Notes |
|--------|------|-------|
| `ticker` | TEXT PK | Uppercase ticker symbol |
| `date_added` | DATE | ISO date string (e.g. `2026-06-05`). Used to filter "today's" monitors. |
| `is_active` | INTEGER | 1 = armed, 0 = disabled/reset |
| `activated_by` | TEXT | Always `'user'` in current implementation |

**Upsert behaviour:** Adding a ticker that was already monitored today re-arms it without creating a duplicate row (`ON CONFLICT(ticker) DO UPDATE`).

---

### `intraday_monitor_results`

Stores the latest scan output per ticker. Overwritten on each scan.

```sql
CREATE TABLE IF NOT EXISTS intraday_monitor_results (
    ticker          TEXT PRIMARY KEY,
    scan_ts         DATETIME NOT NULL,
    current_price   REAL,
    reversal_score  INTEGER,
    is_bottoming    INTEGER,
    reasons_json    TEXT,
    rsi             REAL,
    vwap            REAL,
    vwap_deviation  REAL
);
```

| Column | Type | Notes |
|--------|------|-------|
| `ticker` | TEXT PK | One row per ticker, overwritten each scan |
| `scan_ts` | DATETIME | Timestamp of the scored candle (not the scan wall-clock time) |
| `current_price` | REAL | Close price of the scored candle |
| `reversal_score` | INTEGER | 0–100 composite score |
| `is_bottoming` | INTEGER | 1 if score ≥ 65, else 0 |
| `reasons_json` | TEXT | JSON array of reason strings (decoded by API into `reasons` key) |
| `rsi` | REAL | 14-period RSI value at the scored candle |
| `vwap` | REAL | VWAP at the scored candle |
| `vwap_deviation` | REAL | `close - vwap` (negative = price below VWAP) |

---

### `alert_state` (shared, `engine='dip_radar'`)

No schema changes. Existing `alert_state` table handles Dip Radar via the `engine` discriminator column.

```sql
SELECT * FROM alert_state WHERE engine = 'dip_radar';
```

---

## 14. Settings & Configuration

**Channel routing:** `NOTIFICATION_ROUTING.dip_radar_alert` (see the **Notification Settings** panel), e.g.

```json
{
  "NOTIFICATION_ROUTING": {
    "dip_radar_alert": { "log_file": true, "in_app": true, "nextcloud_talk": false }
  }
}
```

Routing is the only persistent config for Dip Radar. All other behaviour (scan interval, threshold, session window) is hardcoded in `intraday_bottom_engine.py` as named constants:

| Constant | Value | Description |
|----------|-------|-------------|
| `_BOTTOMING_THRESHOLD` | `65` | Minimum score to fire an alert |
| RSI extreme oversold | `25` | RSI below this → +30 pts |
| RSI oversold | `30` | RSI below this → +15 pts |
| BB window | `20` | Bollinger Band lookback period (candles) |
| BB σ multiplier | `2.5` | Standard deviations for the lower band |
| VWAP σ window | `30` | Rolling window for VWAP standard deviation |
| VWAP σ multiplier | `2.5` | Standard deviations below VWAP to score |
| Volume SMA window | `20` | Rolling mean window for volume climax |
| Volume climax σ | `3` | Standard deviations above volume mean to confirm climax |

---

## 15. Known Limitations

**1. 1-minute data availability**  
`yfinance` returns 1-minute data reliably for the current trading session, but free-tier access may occasionally return stale or incomplete candles during periods of heavy yfinance API load. A minimum of 30 bars is required before scoring begins; if the download returns fewer, the ticker is silently skipped for that scan cycle.

**2. Scoring uses the second-to-last candle**  
`df.iloc[-2]` is scored rather than `df.iloc[-1]` because the most recent candle is still forming and has artificially low volume. This means there is a 1-minute lag between a true bottom forming and the system detecting it. This is a deliberate trade-off for data integrity.

**3. No multi-day memory**  
Monitors reset at 16:05 each day. If the user wants to monitor a stock the following day, they must re-arm it from the stock detail page. This is intentional — a stock that was capitulating on Monday may have completely different intraday dynamics on Tuesday.

**4. No score history**  
Only the *latest* scan result is persisted to `intraday_monitor_results`. There is no historical time-series of intraday scores for charting. If this is desired in a future version, a separate `intraday_score_history` table would be required.

**5. US market hours only**  
The scan window is hardcoded to 09:00–15:59 ET. UK or European stocks traded on LSE (10:00–16:30 London time) will be underscanned in the morning and missed after the window closes. The reset job at 16:05 ET may also fire before the LSE close. For non-US tickers, the signal is still mathematically valid but may miss intraday extremes outside the scan window.

**6. Single-ticker, no confirmation basket**  
The score is entirely based on the individual ticker's own data. There is no cross-reference to whether the broader sector or index is also selling off (which would increase conviction) or is flat (which might suggest idiosyncratic rather than macro-driven selling). Combining Dip Radar with the existing `crash_engine` output and `market_regimes` table for macro context is recommended before acting on a signal.
