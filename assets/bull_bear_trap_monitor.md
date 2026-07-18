# Market Trap & Recovery Monitor

The Market Trap & Recovery Monitor detects four sequential post-crash lifecycle phases from daily OHLCV data. Where the AI Contagion Monitor detects *that* a crash is spreading, this tool identifies *what comes next* — whether a bounce is a genuine recovery or a trap, when a true bottom is forming, and when smart money is quietly accumulating.

Page route: `GET /trap-monitor`
Engine: `bull_bear_trap_engine.py`
Scheduler job: `trap_monitor_job`
DB table: `trap_monitor_results`

---

## 1. The Four Lifecycle Phases

The four detectors map to a sequential post-crash arc:

```
CRASH → ACTIVE SELLOFF
           ↓
      BULL TRAP RISK?  ←  Dead Cat Bounce: price bounces but volume doesn't confirm
           ↓
     CAPITULATION?     ←  Volume climax + extreme RSI: institutions absorb panic
           ↓
      BEAR TRAP RISK?  ←  False breakdown: support breaks on low volume then recovers
           ↓
      ACCUMULATION     ←  BB squeeze + ATR contraction: smart money base-building
```

Each detector runs independently; `_derive_phase()` applies a priority hierarchy to select a single lifecycle label per ticker.

---

## 2. Detection Logic

All detectors read from `data/historical/{ticker}.parquet` — no HTTP calls are made at scan time. The last 60 trading days are used as the working window.

### 2.1 Bull Trap (Dead Cat Bounce)

**Principle:** Price is recovering on anemic volume while still trading below the 20-EMA. Volume on up-days that is substantially lower than volume on down-days is the classic signature of short-covering rather than institutional buying.

**Gate conditions (both required):**
- `close < EMA_20` — price is still in the downtrend
- Last bar is an up-day (`pct_change > 0`) — there is a bounce to evaluate

**Severity from `up_vol / down_vol` ratio over the trailing 15 bars:**

| Vol ratio | Level |
|-----------|-------|
| ≥ 0.90 | `SAFE` |
| 0.75 – 0.90 | `ELEVATED_RISK` |
| < 0.75 | `SEVERE_TRAP_RISK` |

Additional context appended to notes: RSI(14) < 50 (momentum not recovered); price within ±2% of EMA (approaching resistance).

If the price is still declining below EMA (last bar is a down-day), the level is `ACTIVE_SELLOFF` rather than a trap.

Both thresholds are configurable: `BULL_TRAP_VOLUME_RATIO` sets the severe threshold (default 0.75).

### 2.2 Bear Trap (False Breakdown)

**Principle:** Price intraday breaches the lower Bollinger Band or 20-day low but closes back above it on low volume. Short sellers who entered on the breakdown are trapped as the price reverses.

**Gate conditions (all required):**
- Intraday low pierced `min(BB_lower_band, 20-day low)`
- Close recovered above that support level
- Volume on the breakdown bar is below `BEAR_TRAP_VOLUME_RATIO × 20d_avg_vol` (default 1.20×)

**Levels:**

| Condition | Level |
|-----------|-------|
| Vol < 1.20× avg | `CONFIRMED_BEAR_TRAP` |
| Vol ≥ 1.20× avg (recovered but with higher volume) | `POSSIBLE_BEAR_TRAP` |

Additional context: RSI bullish divergence — if price is at a new low but RSI is higher than the prior 10-bar trough (and trough < 40), a divergence note is added.

### 2.3 Capitulation (Final Flush)

**Principle:** A volume climax spike combined with extreme oversold readings. Institutions absorb panic selling: the close recovers into the upper half of the day's range, leaving a long lower wick.

**Gate conditions (all required):**
- `volume > mean_20d + 3σ` (z-score ≥ `CAPITULATION_VOL_ZSCORE`, default 3.0)
- `RSI(14) < 30` — extreme oversold
- `close < EMA_20` — still in downtrend

**Level from close position within the day's range:**

| Close position | Level |
|----------------|-------|
| Upper 50% of range | `CAPITULATION_FORMING` — absorption wick |
| Lower 50% of range | `WATCH` — pressure may continue |

Additional context: if close is more than 7% below EMA, the deep extension is noted. The vol z-score is always persisted (`cap_vol_zscore`) regardless of gate pass/fail.

### 2.4 Wyckoff Accumulation (Base Building)

**Principle:** Bollinger Bands contract to historically narrow width after a downtrend while ATR and volume dry up — classic signs of institutional accumulation before a breakout.

**Gate condition:**
- `BB_width < WYCKOFF_BB_SQUEEZE_PCT` (default 2%) AND `BB_width < 70%` of the 20-day maximum width

**Severity score (0–3), each contributing signal adds 1:**
- ATR(14) < 70% of its 20-day mean (volatility compression)
- 5-day avg volume < 70% of 20-day avg volume (supply exhaustion)
- 20-day price change < −5% (confirms post-downtrend context)

| Score | Level |
|-------|-------|
| ≥ 2 | `ACCUMULATION_PHASE` |
| < 2 | `SQUEEZE_FORMING` |

---

## 3. Phase Derivation

`_derive_phase()` applies signals in priority order to produce one lifecycle label per ticker:

| Priority | Phase returned | Trigger |
|----------|---------------|---------|
| 1 (highest) | `ACTIVE_SELLOFF` | bull_trap_level == `ACTIVE_SELLOFF` |
| 2 | `CAPITULATION_FORMING` | cap_level == `CAPITULATION_FORMING` |
| 3 | `BULL_TRAP_RISK` | bull_trap_level in (`SEVERE_TRAP_RISK`, `ELEVATED_RISK`) |
| 4 | `BEAR_TRAP_RISK` | bear_trap_level in (`CONFIRMED_BEAR_TRAP`, `POSSIBLE_BEAR_TRAP`) |
| 5 | `ACCUMULATION` | wyckoff_level == `ACCUMULATION_PHASE` |
| 6 | `CAUTION` | cap_level == `WATCH` OR wyckoff_level == `SQUEEZE_FORMING` |
| 7 (default) | `NEUTRAL` | no significant signal |

---

## 4. Ticker Universe

`_get_ticker_list()` unions up to three sources:

- **Proxy basket** — `NOTIFICATIONS.TRAP_MONITOR_ALERTS.PROXY_TICKERS` (default: `["QQQ", "SMH", "NVDA", "MSFT", "AAPL"]`). Configurable via Settings.
- **Portfolio** — `accounts_engine.get_combined_holdings()` (built-in Trading accounts + Ghostfolio when enabled), gated by `SCHEDULING.TRAP_MONITORS.MONITOR_PORTFOLIO` (default: `True`). Toggle via "Monitor Portfolio Tickers" checkbox in Settings.
- **Watchlist** — `database.get_watchlist_tickers()`, gated by `SCHEDULING.TRAP_MONITORS.MONITOR_WATCHLIST` (default: `False`). Toggle via "Monitor Watchlist Tickers" checkbox in Settings.

Each source is filtered through `utils.is_excluded_from_yahoo_fetch()` (synthetic tickers and the Settings-page Ignored Tickers list). Tickers are skipped if no Parquet file exists at `data/historical/{ticker}.parquet` and cannot be fetched.

---

## 5. Scheduler

Job ID: `trap_monitor_job`

Scheduled via `CronTrigger` in `scheduler_engine.reload_scheduler()`, using the `USER_TIMEZONE` from config. The trigger runs on `day_of_week=FREQUENCY` (default `"mon-fri"`) at `minute=*/INTERVAL_MINUTES` within `START_TIME`–`END_TIME` (default 08:00–21:00 every 30 minutes).

The job only appears in the scheduler when `SCHEDULING.TRAP_MONITORS.ENABLED` is `True`.

Last-run time is written to `scheduler_run_log` via `record_job_run('trap_monitor_job')` and is visible in the Settings → Diagnostics scheduler matrix (the `TRAP_MONITORS` config key maps to `trap_monitor_job` in `CONFIG_KEY_TO_JOB` in `scheduler_manifest.py`).

---

## 6. Alerting

### In-app notifications
The scheduler job fires an in-app notification for any ticker whose phase is `ACTIVE_SELLOFF`, `BULL_TRAP_RISK`, `CAPITULATION_FORMING`, or `BEAR_TRAP_RISK`. Tickers at `ACCUMULATION`, `CAUTION`, or `NEUTRAL` are scanned but do not generate alerts.

### Market-hours gating
The scan runs on a fixed daily window in `USER_TIMEZONE` (Section 7) regardless of which exchange each scanned ticker belongs to, but each ticker's own alert is only fired while *that ticker's* exchange is open. Each candidate ticker's exchange is resolved via `time_engine.ticker_exchange(ticker, currency)` (currency looked up from `stock_signals`), then gated with `time_engine.is_market_open(exchange, include_premarket=(exchange == "NYSE"))`. A phase computed from a stale daily close (e.g. a US proxy ticker or portfolio holding scanned before the US session opens) is still saved to `trap_monitor_results` for display, but does not fire a notification until its own exchange opens — this prevents a flood of alerts about the prior US session the moment the scan's daily window starts in a UK morning.

### Nextcloud Talk
Channel delivery is controlled by the **Notification Settings** panel in Settings (source key `trap_monitor_alert`); Nextcloud Talk is off by default for this source. The dispatch is centralised through `notification_engine.notify()`. Message format:

```
🎭 TRAP MONITOR: {TICKER} — {PHASE}

{signal notes}

RSI: {rsi} | EMA Distance: {ema_distance}%
Bull Trap: {level} | Bear Trap: {level}
Capitulation: {level} | Wyckoff: {level}
```

### Deduplication
Uses the existing `alert_state` table with `engine = "TrapMonitor"`. Alert gating is handled by `IntradayOrchestrator._evaluate_alert_gate()` — the same dedup logic used by crash/moonshot alerts, resolved via its own `NOTIFICATIONS.TRAP_MONITOR_ALERTS` config block (see Section 7). A UTC calendar-day rollover no longer re-fires an unchanged alert on its own — an alert only fires again once cooldown has elapsed and the condition has genuinely worsened, or the phase itself changes (a new fingerprint). Since Trap Monitor has no price to compare (its scoring is daily-bar-based, not a live quote), the magnitude check instead uses `ema_distance` — the same signed percentage shown in the alert text — and treats the move as a raw point delta (e.g. -4.5% → -8.0% is a 3.5-point deterioration) rather than a relative percentage change.

---

## 7. Configuration

All keys live under `config.json` / `config.py:DEFAULT_CONFIG`.

### Scheduling (`SCHEDULING.TRAP_MONITORS`)

| Key | Default | Description |
|-----|---------|-------------|
| `ENABLED` | `false` | Master on/off switch for the scheduler job |
| `BULL_TRAP` | `true` | Enable Bull Trap detector |
| `BEAR_TRAP` | `true` | Enable Bear Trap detector |
| `CAPITULATION` | `true` | Enable Capitulation detector |
| `WYCKOFF` | `true` | Enable Wyckoff Accumulation detector |
| `MONITOR_PORTFOLIO` | `true` | Include portfolio tickers in the scan universe |
| `MONITOR_WATCHLIST` | `false` | Include watchlist tickers in the scan universe |
| `FREQUENCY` | `"mon-fri"` | APScheduler day-of-week expression |
| `START_TIME` | `"08:00"` | Earliest hour to run (local timezone) |
| `END_TIME` | `"21:00"` | Latest hour to run (local timezone) |
| `INTERVAL_MINUTES` | `30` | How often to scan within the window |

### Notifications (`NOTIFICATIONS.TRAP_MONITOR_ALERTS`)

| Key | Default | Description |
|-----|---------|-------------|
| `COOLDOWN_MINUTES` | `120` | Minimum gap between repeated alerts for the same ticker |
| `RETRIGGER_PERCENT` | `3.0` | `ema_distance` point-move that re-fires the alert once cooldown has elapsed |
| `REARM_PERCENT` | `5.0` | `ema_distance` point-move (recovery) that fully resets the alert state |
| `BULL_TRAP_VOLUME_RATIO` | `0.75` | Vol ratio below which the Bull Trap is SEVERE |
| `BEAR_TRAP_VOLUME_RATIO` | `1.20` | Vol multiple above which a breakdown is higher-conviction |
| `CAPITULATION_VOL_ZSCORE` | `3.0` | Minimum z-score to gate the capitulation detector |
| `WYCKOFF_BB_SQUEEZE_PCT` | `2.0` | Maximum Bollinger width (%) to gate the Wyckoff detector |
| `PROXY_TICKERS` | `["QQQ","SMH","NVDA","MSFT","AAPL"]` | Tickers always scanned regardless of portfolio |

---

## 8. Tool Page (`/trap-monitor`)

The page renders a unified view of all four signals per ticker:

**Lifecycle arc diagram** — a horizontal CSS stepper highlighting the dominant phase across all scanned tickers. Phases are colour-coded: red = ACTIVE_SELLOFF / BULL_TRAP_RISK, orange = CAPITULATION_FORMING, cyan = BEAR_TRAP_RISK, blue = ACCUMULATION.

**Active alert strip** — colour-coded cards at the top of the page for any ticker whose phase is not NEUTRAL or CAUTION. Hidden when all tickers are clear.

**Ticker status table** — one row per ticker with columns: Phase (colour pill badge), Bull Trap level, Bear Trap level, Capitulation level, Wyckoff level, EMA Distance (%), RSI, and last scan timestamp. Sorted by phase severity (most severe first).

**Signal legend** — collapsible `<details>` panel explaining each phase and warning level.

**Auto-refresh** — `setInterval` every 60 seconds re-fetches `GET /api/trap-monitor/results` and re-renders the table without a full page reload.

**Run Scan Now** — a button that posts to `POST /api/trap-monitor/run`, disables with a spinner while running, and re-fetches results on completion.

---

## 9. Prediction Accuracy Tracking

Each time the scan runs, phase assignments are also appended to `trap_phase_history` (one row per ticker per day via `INSERT OR IGNORE`). A daily background job — `trap_accuracy_fill_job`, scheduled at 20:30 UTC whenever `TRAP_MONITORS.ENABLED` is true — resolves these rows by looking up the forward close price from each ticker's Parquet file.

**Resolution logic:** For each unresolved row whose `scan_date` is at least N calendar days ago, the job finds the first available trading-day close at or after `scan_date + N` and computes `direction_correct_Nd` (1 = correct, 0 = incorrect) based on the expected directional outcome for each phase:

| Phase | Expected outcome |
|---|---|
| `BULL_TRAP_RISK` | Price **lower** (false bounce plays out) |
| `CAPITULATION_FORMING` | Price **higher** (selling climax → bounce) |
| `BEAR_TRAP_RISK` | Price **higher** (false breakdown → recovery) |
| `ACCUMULATION` | Price **higher** (Wyckoff markup phase begins) |
| `ACTIVE_SELLOFF` | Price **lower** (trend continuation) |

Both a 14-day and a 30-day horizon are resolved independently per row. `NEUTRAL` rows are never resolved.

Aggregated accuracy stats are served by `GET /api/trap-monitor/accuracy` and displayed on the `/trap-monitor` page in a collapsible "Prediction Accuracy" section.

---

## 10. Key Files

| File | Role |
|------|------|
| `bull_bear_trap_engine.py` | `TrapEngine` class: all four detectors, phase derivation, DB persistence; `fill_trap_phase_actuals()` for accuracy resolution |
| `scheduler_jobs.py` | `run_trap_monitor_job()`, `run_trap_accuracy_fill_job()` runner functions |
| `scheduler_engine.py` | CronTrigger scheduling blocks in `reload_scheduler()` |
| `api_routes_analysis.py` | `GET /api/trap-monitor/results`, `POST /api/trap-monitor/run`, `GET /api/trap-monitor/accuracy` |
| `page_routes.py` | `GET /trap-monitor` route |
| `templates/trap_monitor.html` | Page template: lifecycle arc, alert strip, ticker table, accuracy panel, auto-refresh |
| `templates/tools.html` | Guide-card entry |
| `templates/settings.html` | Settings card: enable toggle, detector toggles, portfolio checkbox, proxy tickers, notification config |
| `templates/glossary.html` | Term-box entries: Bull Trap, Bear Trap, Capitulation, Wyckoff Accumulation, Trap Phase History |
| `db_schema.py` | `trap_monitor_results` and `trap_phase_history` table definitions in `init_db()` |
| `db_helpers.py` | `log_trap_phase()`, `get_unresolved_trap_phases()`, `update_trap_phase_actual()`, `batch_update_trap_phase_actuals()`, `get_trap_phase_accuracy()` (all re-exported from `database.py`) |
| `config.py` | `SCHEDULING.TRAP_MONITORS` and `NOTIFICATIONS.TRAP_MONITOR_ALERTS` in `DEFAULT_CONFIG` |
