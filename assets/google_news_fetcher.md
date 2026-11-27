# Google News Fetcher

The Google News Fetcher (code: the `news/gfetcher/` package) reads the news list that Google Finance shows on each instrument's quote page, downloads the article text from the publisher, and stores the articles in `news_articles` next to the Yahoo News Feed's, marked `feed_source = 'google'`. The News page, `GET /api/news-feed`, the MCP Server's `get_news` and the article sentiment scorer read that one table, so the articles need no reader of their own.

Google Finance has no public API and its terms do not allow automated access. The fetcher therefore keeps its volume low, stops at Google's first refusal and never tries to get around a block. It is built to be removable: everything except the seams listed under **Removal** lives in `news/gfetcher/`.

It is off by default. Settings → **News & RSS** → **Google News Fetcher** turns it on and holds its schedule and limits; the scheduled job `gfetcher_job` (Workflow Monitor label "Google News Fetcher", fed by the external node "Google Finance") calls `news.gfetcher.run_fetch(settings_from_config(SCHEDULING.GFETCHER))`.

## Settings, job and notifications

| Setting (`SCHEDULING.GFETCHER`) | Default | Meaning |
|---|---|---|
| `ENABLED` | off | registers the job; `POST /api/gfetcher/run-now` answers `409` while it is off |
| `FREQUENCY`, `START_TIME`, `END_TIME` | Mon-Fri, 07:00-22:00 | `USER_TIMEZONE` wall-clock window (the hours, inclusive) |
| `INTERVAL_MINUTES` | 30 | 15, 20, 30 or 60; runs start at minute 11 of the hour and every interval after it, each moved by up to 2 minutes at random (`jitter`) so Google never sees a fixed clock |
| `SCOPES` | Portfolio, Watchlist, Indexes | which lists are fetched |
| `MAX_TICKERS_PER_RUN` | 12 | tickers per run (1-50) |
| `MAX_REQUESTS_PER_DAY` | 300 | rolling 24-hour budget over every route (1-1,000) |
| `GOOGLE_DELAY_SECONDS` | 3 | shortest gap; each gap is drawn from [gap, 2 × gap] (3-60) |
| `FETCH_FULL_TEXT` | on | download the article text |
| `MAX_PER_TICKER` | 10 | articles kept per ticker and run (1-30) |
| `MAX_AGE_DAYS` | 7 | articles older than this are not stored and are pruned (1-30) |

`POST /api/settings` refuses any other value with `422`; the limits are the `GFETCHER_*` constants in `constants.py`. The panel's health table (`GET /api/gfetcher/status`) shows the last run, each route's state (available, or paused until when and why), the requests and refusals of the last 24 hours, the stored articles and the publisher rules; **Run Now** queues a run (`409` while one runs).

The job runner (`scheduler_jobs.run_gfetcher_job()`) records every run in the scheduler run history like any job and sends notifications only for events, each once unless the table says otherwise, through the job's own row in Settings → Notification Settings (`news.gfetcher.run_notices()`):

| Event | Type |
|---|---|
| Google refused one route (it pauses; the run carried on over the other) | Warning |
| Google refused every route (no request until the earliest pause ends) | Warning |
| No route could connect | Error |
| The daily budget was spent during the run | Warning |
| A valid page showed no news three runs in a row (fired on the third run only) | Warning |
| Article downloads failed unexpectedly: 3 or more in the run raised an exception and no download returned text (**One run**, step 8). Sent by every such run; it gives the number of failures and of articles left for a later run | Error |
| A route that had been refused answered again | Info |
| The run failed with an exception | Error |

A normal run, a run inside a cooldown and a run with nothing due send nothing. A failed run also logs its traceback.

**Symbol mappings** (a collapsible section of the panel, `GET /api/gfetcher/symbols`) list each ticker's Google symbol and exchange, whether Google has a page for it, the page title that confirmed it and when. **Probe Symbols** (`POST /api/gfetcher/probe`, the on-demand `gfetcher_probe_source`, Workflow Monitor label "Google News Fetcher (Symbol Probe)") requests the page of every index in scope and every ticker without a found mapping once, skipping the week a not-found ticker is otherwise left alone, under the same pacing, budget and route pauses as a run; it stores mappings and no news, and its summary arrives as a notification. It refuses (`409`) while the fetcher is off, while a run or another probe is running.

**Publisher rules** (the second collapsible section, `GET`/`POST /api/gfetcher/publishers`, `DELETE /api/gfetcher/publishers/{domain}`) list every rule with its download counts. Choosing a rule (or adding one for a new domain) makes it the operator's (`manual`) rule, which learning never changes; **Use learned** turns it back into a learned rule whose policy follows its own counts. Saving a rule needs no password step-up (operator decision: rules are not credentials).

## News page and MCP Server

- **News page** (`templates/news.html`, `static/js/news.js`): Google articles carry a **Google** badge (Yahoo ones a **Yahoo** badge), an estimated time is shown as `~3h ago` and, in the reader, "approx., to the hour"; **Indexes** and **General** filters show the `index` and `market` lists (General is the page's name for Google's market-wide list; its badge reads **General**). A Google article stored without text says the site is paywalled or blocks automated downloads, with a link to the original. The page loads the newest 500 articles of both feeds.
- **`GET /api/news-feed`** takes `feed` (`yahoo`, `google`, `all`) and the `source` values `index` and `market`; each row carries `feed_source` and `published_precision`.
- **MCP `get_news`** returns both feeds; each article has `article_id`, `feed` and `published_precision`, and `as_of` is the newer of the two jobs' last runs. **`get_article(article_id)`** returns one stored article's text, one line, cut to 8,000 characters, inside `external_content` (never the URL). Details: `assets/mcp_integration.md`.

## Modules

| Module | Role |
|---|---|
| `pipeline.py` | One run: gate, ticker selection, fetch, merge, publisher policy, text cleaning, store, prune, sentiment. `FetchSettings` holds the knobs, `RunResult` reports the outcome. |
| `client.py` | Every request to google.com: through `outbound_fetch_helpers.fetch_body()` over the IPv4/IPv6 routes of Settings → Advanced Network, spaced at random, budgeted, logged in `gfetcher_request_log`, and turned into a per-route cooldown on refusal. |
| `parse.py` | The quote page's news cards (`lxml.html` + `cssselect`). The four CSS class names Google uses are constants at the top of the file. |
| `symbols.py` | App ticker → Google `symbol:exchange`, remembered in `gfetcher_symbol_map`. |
| `publishers.py` | Per-domain policy: fetch the text, keep the headline only, or drop the article. |
| `article_text.py` | The publisher download through `fetch_body()` (2 redirects) and `trafilatura.bare_extraction()`, with a `text_status` and the page's own publication date. |
| `boilerplate.py` | Removes what a publisher puts in front of an article (cookie banner, menu, wall) from the downloaded text (**Text cleaning**). |
| `store.py` | All SQL of the package. |

## One run

1. **Gate.** A run while every route is inside its cooldown (`gfetcher_state.cooldown_until`) sends nothing and reports `cooling_down`.
2. **Tickers.** The Portfolio and Watchlist come from `news.news_feed_engine._build_ticker_source_map()` (the same lists, with `portfolio`/`watchlist`/`both`, as the Yahoo News Feed); the Indexes are the enabled `market_ticker_registry` rows (`market_pulse.get_index_tickers()`) that have a Google code. Synthetic tickers (`utils.is_synthetic_ticker()`), mutual funds (`db.helpers.get_mutual_fund_tickers()`) and Ignored Tickers are never requested. A ticker fetched within `min_refetch_minutes` is not due. Never-fetched tickers go first (Portfolio, then Watchlist, then Indexes), then the oldest fetch, up to `max_tickers_per_run`; a short cadence with a cap spreads the requests instead of sending them in one burst.
3. **Quote page.** One request per ticker once its exchange is known, alternating between the usable routes (below). The gap before each request is drawn at random between the delay and twice the delay (3–6 s by default; the delay is never below `client.MIN_DELAY_SECONDS`, 3 s), across all routes together, so adding IPv6 halves the load on each address without raising the total rate. A rolling 24-hour budget (`max_requests_per_day`) counts every logged request on every route.
4. **Refusal.** HTTP 429 or 503, a redirect to Google's `/sorry/` page or a page with the "unusual traffic" text puts the route it came on into a cooldown at once, with no retry on that route: every further request into a flagged address prolongs the flag. The run carries on over the other route at the same pace and ends only when every route is cooling. The cooldown is 1 hour, then 6 hours, then 24 hours for consecutive refusals of that route, or longer when `Retry-After` asks for it; a route that answers in a later run without being refused starts again from 1 hour. A connection failure (no route to the host, an IPv6 address that cannot be bound, a timeout) drops that route for the rest of the run without a cooldown; when no route is left the run reports `unreachable`. Other failures (another HTTP error, any other redirect) are logged as `error` and the run continues.
5. **Merge.** One entry per article URL with every ticker it was listed under. Articles older than `max_age_days` and headlines mostly in non-Latin script are dropped.
6. **Market-wide news.** Google shows the same generic headlines for instruments with no news of their own (LSE-listed ETFs). Two tickers whose lists of at least 5 articles overlap by 80% or more are treated as showing that fallback list, and an article seen only in such lists is stored under the pseudo ticker `MARKET` with `source_list = 'market'` (shown as **General** on the News page). One such ticker on its own cannot be detected.
7. **Already stored.** Articles whose `article_id` exists are skipped before any publisher request.
8. **Publisher policy** (below), then the text download for the articles that pass, at most one per `publisher_delay_seconds`, and **Text cleaning** of each text (below). An unexpected exception while one article is downloaded or cleaned (a crash) is logged with its traceback and costs that article's text only (`text_status` `error`, headline kept); the run carries on. When 3 or more downloads of a run crash (`CRASH_ALARM`) and no download returns text (`full` or `short`), the fault is taken to be common to all of them rather than to the articles: the run sends the Error notification and leaves each crashed article unstored, so that a later run retries it if Google still lists it. `gfetcher_crashes` counts these held runs per article URL. A run with no article text and fewer than 3 crashes sends no notification; it holds and counts again an article that already has a counter, and stores a first-time crash headline-only. An article is retried at most 3 times in total (`CRASH_RETRIES`), and the next run that would hold it stores it headline-only. Any run with article text stores its crashed articles headline-only without a notification. Storing an article clears its counter. An HTTP error answer (404, 500, a refusal), a connection failure or a timeout is a download outcome, never a crash; an exception inside trafilatura's extraction is recorded as `no_text` and is not a crash either, so it raises no notification.
9. **Store** with `INSERT OR IGNORE`, one row per (ticker, article), except the articles step 8 leaves for a later run. The first-seen `published_at` is kept.
10. **Prune** Google rows older than `max_age_days` (Yahoo rows are pruned by the News Feed), then score new rows with the News Feed's FinBERT scorer (`news.news_feed_engine._score_unscoredrows()`, headline plus summary).

## Stored article

| Column | Google value |
|---|---|
| `article_id` | `sha256("google|" + ticker + "|" + url)` |
| `source_list` | `portfolio`, `watchlist`, `both`, `index` or `market` |
| `headline`, `url`, `publisher` | from the news card |
| `full_text` | the trafilatura text after **Text cleaning**, `NULL` when there is none |
| `summary` | the first 300 characters of `full_text`, `NULL` without text |
| `published_at` | estimated from Google's relative age ("3 hours ago"), or noon UTC of the page's own date when the age is in days or coarser and the page states a date; the date on a wall page (**Text cleaning**) is ignored |
| `published_precision` | the unit of the estimate (`minute` … `year`), or `day` when the page date was used |
| `feed_source` | `google` |

## Text cleaning

`boilerplate.clean()` removes what a publisher puts in front of an article before `full_text` and `summary` are written, so that the News page, FinBERT sentiment and the MCP Server read the article rather than a cookie banner, a menu or a wall. It works in two steps:

1. **Known preambles** (the `RULES` table). A rule covers its domains and their subdomains and, when it has a `first` pattern, only a text whose first line matches it. It cuts everything up to and including the first line that matches `until`, looked for in the first `within` lines (20 unless the rule says otherwise); without such a line the text stays whole. A rule with no `until` is a **wall**: the page holds no article. Rules apply in order, each to what the rules before it left.
2. **Shared openings.** The first 6,000 characters (`LEAD_CHARS`) of what the rules leave are saved in `gfetcher_leads`. A leading block of whole lines, at least 3 lines and 200 characters long, that the same publisher host (without `www.`) put on the article and on stored articles under at least two other headlines (3 different headlines in all, compared without case) is cut. Nobody has to know the block in advance.

When nothing is left (a wall, known or learned), the article is stored headline-only with `text_status` `blocked` and counted as `blocked` for its publisher, so **Publisher policy** learning applies. Otherwise `full` or `short` is judged on the cleaned text. A fix for one publisher's preamble is one `RULES` entry; a new technique is a function that `clean()` calls.

Known limits:

- The first one or two articles that carry a block with no rule keep it, and stored rows are not rewritten; they leave with the age prune (`max_age_days`).
- A wall with no rule that stays below the shared-block thresholds is stored as article text.
- A block whose lines differ from article to article is cut only by a rule.
- Junk in the middle or at the end of an article stays.
- A publisher series that opens with the same long introduction under 3 or more headlines loses that introduction.
- Yahoo News Feed rows are not cleaned.

## Publisher policy

`gfetcher_publishers` holds one row per domain; a rule for `investing.com` also covers `uk.investing.com`, and the most specific rule wins.

| Policy | Article | Seeded examples |
|---|---|---|
| `block_paywall` | dropped: no text, and the headline is usually a teaser | barrons.com, marketwatch.com, reuters.com, wsj.com |
| `block_bots` | kept with the headline only, no download | benzinga.com, bloomberg.com, ft.com, investing.com, seekingalpha.com |
| `allow` | text downloaded | zacks.com, cnbc.com, fool.com, tradingview.com |

Seeded rows are `manual` and never change policy. A domain the fetcher meets for the first time is learned (`auto`): two refused downloads (HTTP 401/402 paywall, 403/429 blocked, connection reset, or a page that holds only a wall, counted as blocked) with no success block it, as `block_paywall` when paywall answers dominate, otherwise `block_bots`; one success allows it again. A learned block is retried with one download after 14 days. An article whose download answers with a paywall is not stored; a page whose `<html lang>` is not English is dropped.

## Symbols

- **Listings.** A Yahoo-style suffix maps to the Google exchange (`.L` → `LON`, `.DE` → `ETR`, `.PA` → `EPA`, `.AS` → `AMS`, `.MI` → `BIT`, `.MC` → `BME`, `.SW` → `SWX`, `.ST` → `STO`, `.TO` → `TSE`, `.AX` → `ASX`, `.HK` → `HKG`, `.T` → `TYO`); a ticker without a suffix tries NASDAQ, NYSE, NYSEARCA, NYSEAMERICAN and BATS in that order. The exchange that answered is stored and tried first next time; a ticker Google has no page for is not asked again for 7 days. Another suffix has no rule and is skipped.
- **Indexes.** `symbols.INDEX_SYMBOLS` maps registry tickers to Google index codes, each checked against the live page: `^FTSE` `UKX:INDEXFTSE`, `^FTMC` `MCX:INDEXFTSE`, `^GSPC` `.INX:INDEXSP`, `^NDX` `NDX:INDEXNASDAQ`, `^DJI` `.DJI:INDEXDJX`, `^RUT` `RUT:INDEXRUSSELL`, `^VIX` `VIX:INDEXCBOE`, `^N225` `NI225:INDEXNIKKEI`, `^HSI` `HSI:INDEXHANGSENG`, `000001.SS` `000001:SHA`, `^AXJO` `XJO:INDEXASX`, `^STOXX50E` `SX5E:INDEXSTOXX`, `^GDAXI` `DAX:INDEXDB`, `^FCHI` `PX1:INDEXEURO`, `^IBEX` `I:INDEXBME`, `^KS200` `KOSPI:KRX`. Google Finance has no page for the KOSPI 200 itself, so `^KS200` reads the KOSPI composite's page: the KOSPI 200 is most of the composite's value and Google lists the same Korean-market news for both. FX, commodity and rate rows are not fetched.
- **Not-found page.** Google answers an unknown symbol with HTTP 200 and the bare title "Google Finance"; that is what tells a wrong exchange from a page with no news.

## Routes

The routes are the ones Settings → Advanced Network & Socket Binding sets for Yahoo, read through `tools.network_engine.network_routing()` (the one reader of that setting, also behind Yahoo's `yahoo_connection_boundary()` and `GET /api/settings/network-status`):

| Setting | Google requests |
|---|---|
| IPv4 only | IPv4 (`fetch_body(family=AF_INET)`) |
| IPv6 only | IPv6, bound to `YAHOO_IPV6_ADDRESS` (`family=AF_INET6`, `source_address`) |
| Both (dual) | alternate IPv4 and IPv6 request by request |

An IPv6 hard fault latched by the Yahoo connection (`GLOBAL_IPV6_STATUS`) takes IPv6 out of the routes too. Publisher downloads use the operating system's default route.

## Tables

`gfetcher_request_log` (every google.com request with its route, kept 30 days and pruned at the start of each run), `gfetcher_state` (one row per route: cooldown and last success), `gfetcher_symbol_map`, `gfetcher_publishers`, `gfetcher_ticker_state` (last fetch, last card count and the empty streak per ticker), `gfetcher_leads` (the opening of each downloaded article text, compared by **Text cleaning**; rows older than 30 days or beyond the newest 50 of their publisher host are pruned at the start of each run), `gfetcher_crashes` (the retry counter of each article left for a later run after a crash; counters older than 30 days are pruned at the start of each run). Columns: `assets/db_schema_and_architecture.md`.

## Layout change

The quote page's class names are obfuscated and can change. A ticker whose valid page shows no news cards three runs in a row is returned in `RunResult.layout_suspects`; when that happens for tickers that normally have news, check the four selectors at the top of `parse.py`.

## Removal

Turn it off in Settings. Delete `news/gfetcher/`, `tests/news/gfetcher/` and `api/routes_gfetcher.py`, the `gfetcher_probe_source` entry and `run_gfetcher_probe` runner, the `GFETCHER_DOMAIN_MAX_CHARS` constant, the Google badge, filters and estimate marker in `news.js`/`news.html` (the `feed_source` and `published_precision` columns of `GET /api/news-feed` and MCP `get_news`/`get_article` keep working with only Yahoo rows) (and its `include_router` line in `api/routes.py`), the `gfetcher_job` runner in `scheduler_jobs.py` and its `add_job` block in `scheduler_engine.py`, the `gfetcher_job` and `google_finance_source` entries and the `GFETCHER` key in `scheduler_manifest.py`, the `GFETCHER` block of `config.py` `DEFAULT_CONFIG`, `GfetcherScheduleConfig` in `api/routes_system.py`, the `GFETCHER_*` constants, the panel in `templates/settings/_data.html` with its `settings_data.js` functions and `settings_shared.js` block (bump `CSS_VERSION`), the `gfetcher_limits` context of `pages.page_routes.settings_page`, the README bullet, the glossary term-box and its Learning card; drop the seven `gfetcher_*` tables and delete the `feed_source = 'google'` rows of `news_articles` through a `db/schema.py:migrate_db()` step, and delete this file. The `feed_source` and `published_precision` columns of `news_articles` can stay. Other seams: `FetchRedirected` and the `family`/`source_address` arguments of `fetch_body()` in `outbound_fetch_helpers.py`, and `network_routing()` in `tools/network_engine.py` (all harmless to keep; the last is shared with Yahoo) and the `news` entries in the folder lists of `tests/test_architecture_yfinance.py` and `tests/test_notification_engine.py`.

## Tests

`tests/news/gfetcher/`: one file per module (`test_boilerplate.py` covers text cleaning) plus `test_pipeline.py` for whole runs. The package conftest replaces `fetch_body` in both fetching modules with a function that fails the test, so no test can reach Google or a publisher, and stubs the FinBERT scorer. Quote pages are built from the four selectors (`helpers.quote_page()`). `test_imports.py` keeps trafilatura out of every module-level import under `news/`: importing it (its `downloads` module) calls `urllib3.disable_warnings(InsecureRequestWarning)` for the whole process. `article_text.fetch_article()` and the News Feed therefore import it only when they run, inside `outbound_fetch_helpers.preserved_warning_filters()`, which restores the warning filters after the first import; a module-level import would bypass it.
