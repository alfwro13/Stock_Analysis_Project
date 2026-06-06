# NLP Market Sentiment Engine Architecture

## 1. Overview

The NLP sentiment subsystem scores financial news headlines and summaries using **FinBERT** (`ProsusAI/finbert`), a transformer model fine-tuned on financial corpora. Unlike generalised models (VADER, TextBlob), FinBERT correctly handles domain-specific language such as "rate hike," "margin compression," and "dovish pivot."

The subsystem is split across two modules with distinct responsibilities:

| Module | Responsibility |
|---|---|
| `huggingface_engine.py` | Model lifecycle, text scoring, news fetching, DB writes, central bank NLP |
| `sentiment_engine.py` | Macro charts (Fear & Greed, VIX, yield spreads), Plotly HTML cache, Nextcloud alert dispatch |

This separation ensures the model singleton, HuggingFace credentials, and NLP I/O are isolated from the charting and visualisation layer.

---

## 2. Module: `huggingface_engine.py`

### 2.1 Public API

| Function | Signature | Description |
|---|---|---|
| `_get_finbert_analyzer()` | `() → Optional[pipeline]` | Thread-safe singleton loader |
| `_score_text()` | `(analyzer, text) → float` | Score one string, returns `[-1, 1]` |
| `fetch_and_score_news()` | `(ticker, analyzer) → float` | Fetch + score news for one ticker |
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

The resulting continuous float is stored in `quant_signals.sentiment_score`.

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
yahoo_engine.get_news(ticker)          ← up to NLP_NEWS_FETCH_LIMIT items
        ↓
_score_text() per headline+summary+publisher
        ↓
Compound average score
        ↓
UPDATE quant_signals SET sentiment_score = ?
   (upsert via INSERT ... ON CONFLICT if ticker has no existing row today)
```

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
| `/api/save-hf-token` | POST | Persist token to `.env` |
| `/api/test-hf-token` | POST | Verify token via `huggingface_hub.whoami()` |

Both require the `X-Confirm-Token` header.

---

## 5. The Yield Exclusion Thesis (Critical Safeguard)

Sovereign bond yields (`^TNX`, `^TYX`, UK Gilts) are **excluded from NLP scoring**.

**Why:** FinBERT classifies action verbs like "surging" or "soaring" as positive. In fixed income, surging yields are bearish for equities — they compress multiples and raise the cost of capital. A headline like *"Bond yields surge to 5% on inflation fears"* would incorrectly map to an Euphoria badge during a toxic equity sell-off.

Yield metrics rely entirely on quantitative rate-of-change math and bypass the NLP pipeline completely.

---

## 6. Test Coverage

`tests/test_huggingface_engine.py` — 34 tests, fully offline (no model loaded, no network, no DB).

| Class | Tests |
|---|---|
| `TestGetFinbertAnalyzer` | Singleton guarantee, `local_files_only` first, OSError fallback, HF_TOKEN injection, failure → `None` |
| `TestScoreText` | Positive / negative / neutral mapping, truncation at `NLP_TEXT_TRUNCATE_CHARS` |
| `TestFetchAndScoreNews` | Empty / null news, `NLP_NEWS_FETCH_LIMIT` cap, blank-item skipping, nested vs flat Yahoo payloads, per-item error resilience |
| `TestUpdateAllSentiment` | Macro ticker injection, early-exit on no analyzer, UPDATE vs upsert path, connection always closed |
| `TestRunCentralBankNlpAlert` | Unsupported currency, no news, keyword filter, HAWKISH/DOVISH/NEUTRAL tone, correct ticker proxy per currency, Nextcloud dispatch + DB INSERT |
