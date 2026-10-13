# NLP Market Sentiment Engine Architecture

## 1. Overview

The NLP sentiment subsystem scores financial news headlines and summaries using **FinBERT** (`ProsusAI/finbert`), a transformer model fine-tuned on financial corpora. Unlike generalised models (VADER, TextBlob), FinBERT correctly handles domain-specific language such as "rate hike," "margin compression," and "dovish pivot."

The subsystem is split across two modules with distinct responsibilities:

| Module | Responsibility |
|---|---|
| `huggingface_engine.py` | Model lifecycle, text scoring, news fetching, DB writes, central bank NLP |
| `sentiment_engine.py` | Macro charts (Fear & Greed, VIX, yield spreads), Plotly HTML cache, Nextcloud alert dispatch |

This separation ensures the model singleton, HuggingFace credentials, and NLP I/O are isolated from the charting and visualisation layer.

Every Plotly chart on `/market-sentiment` (the five built in `sentiment_engine.py`, the seven macro charts and the two Yield vs Nominal GDP charts in `visuals.py`) takes its layout from `visuals.apply_sentiment_chart_layout()`: centred title, legend below the plot, `margin.b` 70, y-axis automargin and one desktop height (`SENTIMENT_CHART_HEIGHT`, 420). `static/js/market_sentiment.js` relayouts them to 400 px under 768 px wide (AGENTS.md rule 18).

---

## 2. Module: `huggingface_engine.py`

### 2.1 Public API

| Function | Signature | Description |
|---|---|---|
| `_get_finbert_analyzer()` | `() → Optional[pipeline]` | Thread-safe singleton loader |
| `_score_text()` | `(analyzer, text) → float` | Score one string, returns `[-1, 1]` |
| `fetch_and_score_news()` | `(ticker, analyzer, max_age_days) → Optional[float]` | Fetch + score the ticker's headlines no older than `max_age_days`; `None` when it has none, `SentimentUnavailableError` when the fetch or every scoring failed |
| `update_all_sentiment()` | `(tickers: List[str]) → None` | Batch score all portfolio/watchlist tickers + macro set |
| `run_central_bank_nlp_alert()` | `(event_name, currency) → bool` | Central bank tone detection + Nextcloud dispatch |

### 2.2 FinBERT Model Singleton

The model is loaded once per process via double-checked locking:

```python
_FINBERT_ANALYZER = None   # module-level singleton
_MODEL_LOCK = threading.Lock()
```

**Load sequence:**

1. Check `_FINBERT_ANALYZER is None` (fast path, no lock)
2. Acquire `_MODEL_LOCK`
3. Re-check (double-checked locking — guards against race on startup)
4. Inject `HF_TOKEN` from environment (see §4)
5. Attempt `pipeline(..., local_files_only=True)` — skips all HuggingFace Hub HTTP round-trips if the model is cached
6. On `OSError` (cache absent), fall back to standard online download — only happens on first run

This means **production restarts incur zero network calls** once the model has been downloaded once.

### 2.3 Scoring Logic

`_score_text(analyzer, text)` passes the truncated text (capped at `NLP_TEXT_TRUNCATE_CHARS` characters, `NLP_FINBERT_MAX_TOKENS` BERT tokens) to the pipeline and maps the output:

| FinBERT label | Returned score |
|---|---|
| `positive` | `+prob` (0 to 1) |
| `negative` | `-prob` (-1 to 0) |
| `neutral` | `0.0` |

The resulting continuous float is stored in `quant_signals.sentiment_score`, with the UTC time it was scored in `quant_signals.sentiment_scored_at`. A ticker with no headlines inside the age limit has `sentiment_score = NULL` (never `0.0`): a neutral 0.000 means FinBERT scored real headlines as neutral.

### 2.4 UI Badge Mapping

The raw score is mapped to categorical badges in the frontend Jinja2 templates:

| Badge | Score range |
|---|---|
| **Euphoria** | ≥ 0.60 |
| **Bullish** | 0.15 – 0.60 |
| **Neutral** | −0.15 – 0.15 |
| **Bearish** | −0.60 – −0.15 |
| **Extreme Fear** | ≤ −0.60 |

---

## 3. Data Pipeline

### 3.1 Scheduled Sentiment Scan (`update_all_sentiment`)

Triggered by the scheduler (configurable interval, default 4 hours, Mon–Fri).

```
Ticker list (portfolio + watchlist)
        +
Macro set: ^GSPC, ^NDX, ^FTSE, ^FTMC, GBPUSD=X, DX-Y.NYB
        ↓
yahoo_engine.get_news(ticker)          ← Yahoo RSS headlines (see below)
        ↓
drop headlines older than SENTIMENT_ENGINE.MAX_NEWS_AGE_DAYS (default 3);
at most NLP_NEWS_FETCH_LIMIT remaining headlines are scored
        ↓
_score_text() per headline+summary+publisher
        ↓
Compound average score  (None when no headline is left)
        ↓
UPDATE quant_signals SET sentiment_score = ?, sentiment_scored_at = ?
   (upsert via INSERT ... ON CONFLICT only when there is a score and the ticker has no row)
```

**Three outcomes per ticker.** A score is written with its `sentiment_scored_at`. No headline inside the age limit writes `NULL` to both columns. A failed fetch (`get_news()` returned `None`) or a scan where every fresh headline failed to score leaves the stored value untouched, so an outage never overwrites a good score.

**News source.** `yahoo_engine.get_news()` reads Yahoo's per-ticker RSS headline feed through the same impersonating session as every other Yahoo call (plain `requests` is answered with HTTP 429 whatever its User-Agent). yfinance's `Ticker.news` is no longer used: it POSTs to `finance.yahoo.com/xhr/ncp`, which Yahoo retired on 2026-10-02 (HTTP 404) and which yfinance reports as an empty list, so from that date every scan silently scored every ticker `0.0`. The headlines are returned in the nested `content` shape (`title`, `summary`, `pubDate`, `provider`, `canonicalUrl`) that `news_feed_engine`, `crash_engine` and this engine already read. Only when the RSS feed itself answers with a non-200 status or unparseable XML does `get_news()` fall back to `yf.Search(...).news`, keeping only items whose `relatedTickers` include the ticker; an empty Search answer after an RSS failure is treated as unavailable, not as "no headlines". A 200 answer with no items is authoritative: `get_news()` returns `[]` and caches it for 4 hours. Instruments Yahoo has no headlines for (several LSE ETFs) therefore have a `NULL` score.

When Yahoo news requests fail after HTTP 5xx retries, hit a rate limit, or encounter a transport error, `yahoo_engine.get_news()` returns `None` for that ticker (no Search fallback) and logs a one-line warning with the error. Unexpected news-fetch errors retain their traceback.

**Degraded-scan alert.** When fewer than `NLP_SENTIMENT_MIN_COVERAGE` (50%) of a scan's tickers get a score, the scan raises one `sentiment_scan_degraded` notification ("NLP Sentiment Scan Degraded", routed through `notification_engine.notify`, Settings → Notification Settings) giving the scored, no-headline and unavailable counts. It is sent once per degraded stretch and re-arms after the first healthy scan; the flag is in memory, so a restart during an outage may repeat it once.

**Readers bound the score's age.** Portfolio/Watchlist rows, Stock Detail, Market Pulse and the Screener show the most recent non-NULL score, carried forward past newer rows the next scan has not reached yet. They only accept a score whose `sentiment_scored_at` is within the same `MAX_NEWS_AGE_DAYS` (`db_helpers.sentiment_fresh_since()`), so a ticker whose headlines dry up shows no score instead of its last real one. The three readers of the latest row's own score (the AI prompt's "VADER Media Sentiment" line, the Crash/Moonshot alert metadata and the Index detail page) apply the same bound, so a score the scan stopped refreshing reads as `N/A` rather than as current. A weekend leaves Friday morning's headlines at the 3-day limit on Monday morning; raise **Max Headline Age (Days)** in Settings → NLP Market Sentiment Engine if that is too tight.

### 3.2 News Feed Article Scoring (`_score_unscoredrows` in `news_feed_engine.py`)

When `run_news_feed_job()` inserts new articles, a second scoring pass runs against the `news_articles` table for any rows where `sentiment_score IS NULL`:

```
news_articles (unscored rows)
        ↓
huggingface_engine._get_finbert_analyzer()   ← shared singleton
        ↓
_score_text(analyzer, headline + " " + summary)
        ↓
UPDATE news_articles SET sentiment_score, sentiment_label
```

Labels are coarser (`positive` / `negative` / `neutral`) using ±0.15 as the threshold.

### 3.3 Central Bank NLP Alert (`run_central_bank_nlp_alert`)

Triggered by `run_central_bank_nlp_check()` in the scheduler, which polls `macro_calendar` every 30 minutes on weekdays 12:00–21:00 UTC for same-day Tier-1 events (FOMC, BoE).

**Ticker proxy per currency:**

| Currency | Proxy ticker | Rationale |
|---|---|---|
| `USD` | `^TNX` (10Y Treasury) | Fed statements move Treasuries first |
| `GBP` | `^FTSE` | BoE decisions propagate through UK equities |

**Keyword filter:** Only headlines containing `"rate"`, `"inflation"`, or the central bank name are scored. Entertainment / off-topic news is discarded.

**Tone classification:**

| Condition | Tone | Equity signal |
|---|---|---|
| avg score < −`NLP_CB_TONE_THRESHOLD` | 🦅 HAWKISH (Restrictive) | Bearish |
| avg score > +`NLP_CB_TONE_THRESHOLD` | 🕊️ DOVISH (Accommodative) | Bullish |
| within threshold | ⚖️ NEUTRAL | No change |

Result is dispatched to Nextcloud Talk and logged to `system_notifications`.

---

## 4. HuggingFace Token Configuration

### Why it matters

Without a token, every `pipeline()` call that contacts the Hub emits:

```
Warning: You are sending unauthenticated requests to the HF Hub.
```

And the Hub applies stricter rate limits. Providing a token suppresses this and enables faster downloads during first-time model setup.

### How to configure

**Settings UI:** Open Settings → Machine Learning & AI Engine → **🤗 HuggingFace API Token**. Enter your token and click **💾 Save HF Token**. You can verify it first with **🧪 Verify Token**, which calls the HuggingFace `/api/whoami` endpoint server-side and displays the authenticated username.

**Storage:** The token is written to `.env` (never to `config.json`) via `python-dotenv.set_key()` and immediately applied to `os.environ["HF_TOKEN"]`. Since `config.py` calls `load_dotenv()` at import time, the token is available on subsequent restarts without any manual action.

**API endpoints:**

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/save-hf-token` | POST | Persist token to `.env`; an empty token keeps the stored one, `CLEAR_HF_TOKEN` clears it |
| `/api/test-hf-token` | POST | Verify the posted token, or the stored one when none is posted, via `huggingface_hub.whoami()` |

Both require the `X-Confirm-Token` header.

---

## 5. The Yield Exclusion Thesis (Critical Safeguard)

Sovereign bond yields (`^TNX`, `^TYX`, UK Gilts) are **excluded from NLP scoring**.

**Why:** FinBERT classifies action verbs like "surging" or "soaring" as positive. In fixed income, surging yields are bearish for equities — they compress multiples and raise the cost of capital. A headline like *"Bond yields surge to 5% on inflation fears"* would incorrectly map to an Euphoria badge during a toxic equity sell-off.

Yield metrics rely entirely on quantitative rate-of-change math and bypass the NLP pipeline completely.

---

## 6. Test Coverage

`tests/test_yahoo_engine.py::TestGetNews` covers the no-traceback log for expected Yahoo/network failures and traceback logging for unexpected exceptions.

`tests/test_huggingface_engine.py` — 34 tests, fully offline (no model loaded, no network, no DB).

| Class | Tests |
|---|---|
| `TestGetFinbertAnalyzer` | Singleton guarantee, `local_files_only` first, OSError fallback, HF_TOKEN injection, failure → `None` |
| `TestScoreText` | Positive / negative / neutral mapping, truncation at `NLP_TEXT_TRUNCATE_CHARS` |
| `TestFetchAndScoreNews` | Empty / null news, `NLP_NEWS_FETCH_LIMIT` cap, blank-item skipping, nested vs flat Yahoo payloads, per-item error resilience |
| `TestUpdateAllSentiment` | Macro ticker injection, early-exit on no analyzer, UPDATE vs upsert path, connection always closed |
| `TestRunCentralBankNlpAlert` | Unsupported currency, no news, keyword filter, HAWKISH/DOVISH/NEUTRAL tone, correct ticker proxy per currency, Nextcloud dispatch + DB INSERT |
