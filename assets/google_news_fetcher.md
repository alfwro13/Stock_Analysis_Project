# Google News Fetcher

The Google News Fetcher (code: the `news/gfetcher/` package) reads the news list that Google Finance shows on each instrument's quote page, downloads the article text from the publisher, and stores the articles in `news_articles` next to the Yahoo News Feed's, marked `feed_source = 'google'`. The News page, `GET /api/news-feed`, the MCP Server's `get_news` and the article sentiment scorer read that one table, so the articles need no reader of their own.

Google Finance has no public API and its terms do not allow automated access. The fetcher therefore keeps its volume low, stops at Google's first refusal and never tries to get around a block. It is built to be removable: everything except the seams listed under **Removal** lives in `news/gfetcher/`.

No scheduled job, route or Settings panel calls it; `news.gfetcher.run_fetch(FetchSettings(...))` is the entry point.

## Modules

| Module | Role |
|---|---|
| `pipeline.py` | One run: gate, ticker selection, fetch, merge, publisher policy, store, prune, sentiment. `FetchSettings` holds the knobs, `RunResult` reports the outcome. |
| `client.py` | Every request to google.com: through `outbound_fetch_helpers.fetch_body()` over the IPv4/IPv6 routes of Settings → Advanced Network, spaced at random, budgeted, logged in `gfetcher_request_log`, and turned into a per-route cooldown on refusal. |
| `parse.py` | The quote page's news cards (`lxml.html` + `cssselect`). The four CSS class names Google uses are constants at the top of the file. |
| `symbols.py` | App ticker → Google `symbol:exchange`, remembered in `gfetcher_symbol_map`. |
| `publishers.py` | Per-domain policy: fetch the text, keep the headline only, or drop the article. |
| `article_text.py` | The publisher download through `fetch_body()` (2 redirects) and `trafilatura.bare_extraction()`, with a `text_status` and the page's own publication date. |
| `store.py` | All SQL of the package. |

## One run

1. **Gate.** A run while every route is inside its cooldown (`gfetcher_state.cooldown_until`) sends nothing and reports `cooling_down`.
2. **Tickers.** The Portfolio and Watchlist come from `news_feed_engine._build_ticker_source_map()` (the same lists, with `portfolio`/`watchlist`/`both`, as the Yahoo News Feed); the Indexes are the enabled `market_ticker_registry` rows (`market_pulse.get_index_tickers()`) that have a Google code. Synthetic tickers (`utils.is_synthetic_ticker()`), mutual funds (`db.helpers.get_mutual_fund_tickers()`) and Ignored Tickers are never requested. A ticker fetched within `min_refetch_minutes` is not due. Never-fetched tickers go first (Portfolio, then Watchlist, then Indexes), then the oldest fetch, up to `max_tickers_per_run`; a short cadence with a cap spreads the requests instead of sending them in one burst.
3. **Quote page.** One request per ticker once its exchange is known, alternating between the usable routes (below). The gap before each request is drawn at random between the delay and twice the delay (3–6 s by default; the delay is never below `client.MIN_DELAY_SECONDS`, 3 s), across all routes together, so adding IPv6 halves the load on each address without raising the total rate. A rolling 24-hour budget (`max_requests_per_day`) counts every logged request on every route.
4. **Refusal.** HTTP 429 or 503, a redirect to Google's `/sorry/` page or a page with the "unusual traffic" text puts the route it came on into a cooldown at once, with no retry on that route: every further request into a flagged address prolongs the flag. The run carries on over the other route at the same pace and ends only when every route is cooling. The cooldown is 1 hour, then 6 hours, then 24 hours for consecutive refusals of that route, or longer when `Retry-After` asks for it; a route that answers in a later run without being refused starts again from 1 hour. A connection failure (no route to the host, an IPv6 address that cannot be bound, a timeout) drops that route for the rest of the run without a cooldown; when no route is left the run reports `unreachable`. Other failures (another HTTP error, any other redirect) are logged as `error` and the run continues.
5. **Merge.** One entry per article URL with every ticker it was listed under. Articles older than `max_age_days` and headlines mostly in non-Latin script are dropped.
6. **Market-wide news.** Google shows the same generic headlines for instruments with no news of their own (LSE-listed ETFs). Two tickers whose lists of at least 5 articles overlap by 80% or more are treated as showing that fallback list, and an article seen only in such lists is stored under the pseudo ticker `MARKET` with `source_list = 'market'`. One such ticker on its own cannot be detected.
7. **Already stored.** Articles whose `article_id` exists are skipped before any publisher request.
8. **Publisher policy** (below), then the text download for the articles that pass, at most one per `publisher_delay_seconds`.
9. **Store** with `INSERT OR IGNORE`, one row per (ticker, article). The first-seen `published_at` is kept.
10. **Prune** Google rows older than `max_age_days` (Yahoo rows are pruned by the News Feed), then score new rows with the News Feed's FinBERT scorer (`news_feed_engine._score_unscoredrows()`, headline plus summary).

## Stored article

| Column | Google value |
|---|---|
| `article_id` | `sha256("google|" + ticker + "|" + url)` |
| `source_list` | `portfolio`, `watchlist`, `both`, `index` or `market` |
| `headline`, `url`, `publisher` | from the news card |
| `full_text` | the trafilatura text, `NULL` when there is none |
| `summary` | the first 300 characters of the text, `NULL` without text |
| `published_at` | estimated from Google's relative age ("3 hours ago"), or noon UTC of the page's own date when the age is in days or coarser and the page states a date |
| `published_precision` | the unit of the estimate (`minute` … `year`), or `day` when the page date was used |
| `feed_source` | `google` |

## Publisher policy

`gfetcher_publishers` holds one row per domain; a rule for `investing.com` also covers `uk.investing.com`, and the most specific rule wins.

| Policy | Article | Seeded examples |
|---|---|---|
| `block_paywall` | dropped: no text, and the headline is usually a teaser | barrons.com, marketwatch.com, reuters.com, wsj.com |
| `block_bots` | kept with the headline only, no download | benzinga.com, bloomberg.com, ft.com, investing.com, seekingalpha.com |
| `allow` | text downloaded | zacks.com, cnbc.com, fool.com, tradingview.com |

Seeded rows are `manual` and never change policy. A domain the fetcher meets for the first time is learned (`auto`): two refused downloads (HTTP 401/402 paywall, 403/429 blocked, connection reset) with no success block it, as `block_paywall` when paywall answers dominate, otherwise `block_bots`; one success allows it again. A learned block is retried with one download after 14 days. An article whose download answers with a paywall is not stored; a page whose `<html lang>` is not English is dropped.

## Symbols

- **Listings.** A Yahoo-style suffix maps to the Google exchange (`.L` → `LON`, `.DE` → `ETR`, `.PA` → `EPA`, `.AS` → `AMS`, `.MI` → `BIT`, `.MC` → `BME`, `.SW` → `SWX`, `.ST` → `STO`, `.TO` → `TSE`, `.AX` → `ASX`, `.HK` → `HKG`, `.T` → `TYO`); a ticker without a suffix tries NASDAQ, NYSE, NYSEARCA, NYSEAMERICAN and BATS in that order. The exchange that answered is stored and tried first next time; a ticker Google has no page for is not asked again for 7 days. Another suffix has no rule and is skipped.
- **Indexes.** `symbols.INDEX_SYMBOLS` maps registry tickers to Google index codes, each checked against the live page: `^FTSE` `UKX:INDEXFTSE`, `^FTMC` `MCX:INDEXFTSE`, `^GSPC` `.INX:INDEXSP`, `^NDX` `NDX:INDEXNASDAQ`, `^DJI` `.DJI:INDEXDJX`, `^RUT` `RUT:INDEXRUSSELL`, `^VIX` `VIX:INDEXCBOE`, `^N225` `NI225:INDEXNIKKEI`, `^HSI` `HSI:INDEXHANGSENG`, `000001.SS` `000001:SHA`, `^AXJO` `XJO:INDEXASX`, `^STOXX50E` `SX5E:INDEXSTOXX`, `^GDAXI` `DAX:INDEXDB`, `^FCHI` `PX1:INDEXEURO`. `^KS200` (KOSPI 200) and `^IBEX` have no known code and are not fetched; FX, commodity and rate rows are not fetched either.
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

`gfetcher_request_log` (every google.com request with its route, kept 30 days and pruned at the start of each run), `gfetcher_state` (one row per route: cooldown and last success), `gfetcher_symbol_map`, `gfetcher_publishers`, `gfetcher_ticker_state` (last fetch, last card count and the empty streak per ticker). Columns: `assets/db_schema_and_architecture.md`.

## Layout change

The quote page's class names are obfuscated and can change. A ticker whose valid page shows no news cards three runs in a row is returned in `RunResult.layout_suspects`; when that happens for tickers that normally have news, check the four selectors at the top of `parse.py`.

## Removal

Delete `news/gfetcher/` and `tests/news/gfetcher/`, drop the five `gfetcher_*` tables and delete the `feed_source = 'google'` rows of `news_articles` through a `db/schema.py:migrate_db()` step, and delete this file. The `feed_source` and `published_precision` columns of `news_articles` can stay. Other seams: `FetchRedirected` and the `family`/`source_address` arguments of `fetch_body()` in `outbound_fetch_helpers.py`, and `network_routing()` in `tools/network_engine.py` (all harmless to keep; the last is shared with Yahoo) and the `news` entries in the folder lists of `tests/test_architecture_yfinance.py` and `tests/test_notification_engine.py`.

## Tests

`tests/news/gfetcher/`: one file per module plus `test_pipeline.py` for whole runs. The package conftest replaces `fetch_body` in both fetching modules with a function that fails the test, so no test can reach Google or a publisher, and stubs the FinBERT scorer. Quote pages are built from the four selectors (`helpers.quote_page()`). `test_imports.py` keeps trafilatura out of every module-level import under `news/`: importing it (its `downloads` module) calls `urllib3.disable_warnings(InsecureRequestWarning)` for the whole process, so `article_text.fetch_article()` imports it when it runs, as the News Feed does.
