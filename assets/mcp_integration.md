# MCP Server

A read-only [Model Context Protocol](https://modelcontextprotocol.io) endpoint at `/mcp` that lets Claude (Claude Code, Claude Desktop) query this dashboard in natural language, replacing the copy/paste of `ai_engine.py` prompts. Canonical name (AGENTS.md rule 8): **MCP Server**. Code: the `mcp_server/` package (layout below); the key check is in `main.py` `auth_middleware`.

It reads only. No tool changes a setting, places a trade or writes data, and none may be added that does (AGENTS.md rule 22). Tools are thin wrappers over existing engine functions; they add no calculations.

Build order, decisions and the task list live in `audit/MCP_implementation_plan.md` (local, Git-ignored). This page describes what is merged.

## Setup

1. Generate a key and put it in `.env` (not `config.json`), then restart the app:

   ```bash
   python -c "import secrets; print(secrets.token_hex(32))"
   ```

   ```
   MCP_API_KEY=<the 64 hex characters>
   ```

   The app never generates this key at first boot. Until it is set, every `/mcp` request is refused (fail closed). A Settings button to generate and rotate it is planned (task MCP-12).

2. Connect Claude Code (`claude mcp add` syntax verified against Claude Code 2.1.220):

   ```bash
   claude mcp add --transport http stock-dashboard http://localhost:8090/mcp --header "Authorization: Bearer <MCP_API_KEY>"
   ```

   The default scope (`local`) stores the header in your own Claude Code config. Do not use `--scope project`: that writes `.mcp.json` into the repository with the key in it. In a shared `.mcp.json` use `"Authorization": "Bearer ${MCP_API_KEY}"` so the key stays in the environment.

3. Check it: `claude mcp list` should show `stock-dashboard` as connected, and `/mcp` inside Claude Code lists the tools. A wrong or missing key shows as an authentication failure.

Only `http://localhost:8090` is documented here. Reaching the endpoint from another machine (LAN, reverse proxy, Claude Desktop, claude.ai) is out of scope for now (tasks MCP-14 and MCP-15). The bearer key must not travel over plain `http` beyond the machine running the app.

## Security model

| Property | Behaviour |
|---|---|
| Credential | `MCP_API_KEY` from `.env`, sent as `Authorization: Bearer <key>`. Compared in constant time. |
| Scope | `/mcp` only. The key never authenticates any other route or page. |
| Unset key | Every `/mcp` request answers `401` (fail closed). The log records that the key is unset, never a presented value. |
| Missing or wrong key | `401` with `WWW-Authenticate: Bearer`. The same reply for unset, missing and wrong, so it does not tell a caller which. |
| Home Assistant `X-API-Key` | `403` on `/mcp`, even next to a valid Bearer key. `/mcp` is not in `api_deps.API_KEY_ROUTES` and must never be added. |
| Session cookie | Does not authenticate `/mcp`. |
| Other routes | A Bearer MCP key there is ignored; the request is treated as unauthenticated (`302` to `/login`). |
| Methods | `POST` only. `GET`, `DELETE` and the rest answer `405` with `Allow: POST` after authentication. |
| Host and Origin | `TrustedHostMiddleware` and the SDK's own Host/Origin check use the same host list (`ALLOWED_HOSTS` plus `localhost` and `127.0.0.1`). A foreign `Origin` is refused with `403`. |
| Secrets in logs | The key is never logged. Log lines go through `log_config.SafeFormatter`, which also masks `token=` style query values. |
| Errors | An unexpected tool failure answers with a fixed `Error executing tool <name>`; the exception text stays in the server log (rule 23). An anticipated failure (unknown `account`, an empty X-ray scope, Ghostfolio not reachable) keeps a curated message after the same prefix, written for the client and never built from an exception. |
| Rate limiting | None. A 256-bit key cannot be guessed; use the generator above, not a short phrase. |

CSRF does not interfere: the CSRF middleware only enforces when a `session` cookie is present, and an MCP client sends none.

## Transport

Streamable HTTP in stateless JSON mode: each `POST /mcp` is one JSON-RPC request answered with one JSON body, with no session and no event stream. This suits a read-only tool server and survives app restarts without the client having to reconnect. Consequences: server-initiated messages (sampling, progress streams) are not available, and `GET /mcp` is refused (the SDK would otherwise hold it open forever).

- `/mcp/` (trailing slash) redirects to `/mcp` with `307` after authentication; `/mcp` itself never redirects.
- Synchronous tool functions run in a worker thread (the SDK uses `anyio.to_thread`), so a slow tool never blocks the event loop (rule 10). `tests/mcp_server/test_transport.py` stalls a tool and checks an unrelated request still answers.
- The SDK's session manager can run only once per instance, so each app lifespan builds its own (`mcp_server.transport.run()`); several lifespans in one process (the test suite) are safe.
- SDK: the official `mcp` package, 2.x (`mcp>=2.3,<3`). See `assets/dependencies.md`.

## Code layout

| Path | Holds |
|---|---|
| `mcp_server/__init__.py` | Imports every tool and resource module (importing one registers it) and exposes `MCP_PATH`, `auth_failure`, `endpoint` and `run` to `main.py`. |
| `mcp_server/server.py` | The `MCPServer` instance (`server`), `MCP_PATH` and the read-only tool annotations (`READ_ONLY`). |
| `mcp_server/auth.py` | The Bearer `MCP_API_KEY` check, `auth_failure()`. |
| `mcp_server/transport.py` | The POST-only ASGI `endpoint`, the SDK Host/Origin settings and `run()`. |
| `mcp_server/envelope.py` | `envelope()`, `iso_utc()`, `date_epoch()` (a `YYYY-MM-DD` data date as 00:00 UTC) and the stale limits. |
| `mcp_server/scope.py` | The `account` argument: `AccountArg`, `resolve_scope()`, `ACCOUNT_ALL`. |
| `mcp_server/tools/<tool_name>.py` | One file per tool: its `@server.tool` function, units table and shaping helpers. |
| `mcp_server/resources/<resource>.py` | One file per resource (`investment_policy.py`). |

A new tool is a new file in `tools/` plus its name in the import line of `tools/__init__.py`; a new resource is the same in `resources/`.

## Response envelope

Every tool returns the same wrapper (`mcp_server.envelope.envelope()`):

| Field | Meaning |
|---|---|
| `as_of` | UTC time (`YYYY-MM-DDTHH:MM:SSZ`) of the oldest data point the answer used; `null` when there is none. |
| `age_seconds` | Age of `as_of` when the answer was built. |
| `market_state` | `LSE` and `NYSE` each `open`, `pre`, `post` or `closed` (`market_session_helpers.get_exchange_session_state()`, holiday-aware). |
| `base_currency` | `BASE_CURRENCY`. Every amount is in it unless a field says otherwise. |
| `units` | What each field is: amount, fraction or percent, and which currency. |
| `stale` | `true` when something is `missing`, or when `as_of` is older than 15 minutes (`STALE_AFTER_SECONDS`) while LSE or NYSE is open. Old data with both markets closed is not stale: nothing was due to change. A tool whose figures come from a nightly job passes a longer limit (`stale_after_seconds`; `get_xray`, `get_heat_index` and `get_market_regime` use 5 days, `NIGHTLY_STALE_AFTER_SECONDS`), so it turns stale only when a nightly run was missed, not all session; Friday evening's run still counts as current on a Tuesday after a bank holiday. |
| `missing` | Plain-text list of what could not be provided and why. |
| `data` | The tool's payload. |

A value that is unavailable is `null`, never `0.0` (AGENTS.md). Non-finite numbers are sent as `null`.

## Tools

| Tool | Returns | Reads |
|---|---|---|
| `get_accounts` | Every account: Trading (cash, equity value, unrealised and realised P/L, dividend and interest income, 1d/1w/1m/3m/1y gain, MWRR), Pension and House (current value, 1m/YTD/1y performance, price date) and Ghostfolio accounts when enabled (value only). Each row has an `account_type` and a `scope_id`. | `portfolio_metrics_engine.account_metrics_list(refresh_missing=False)`, `other_accounts_list()`, `accounts_engine.list_scope_accounts_with_values()` |
| `get_holdings(account="all")` | One row per open position per built-in Trading account: shares, native price, cost basis, market value, P/L, allocation within the account, dividends, day change and extended-hours move. | `portfolio_metrics_engine.holdings_with_metrics_all_accounts()` (which calls `accounts_engine.holdings_with_market_value()` per account), `accounts_engine.oldest_quote_epoch()` |
| `get_xray(account="all")` | The Portfolio X-ray as conclusions: value and cost, concentration (HHI, top-5 and top-10 weight, largest positions), beta, volatility, drawdown, VaR, Sharpe, correlation, sector, continent and currency exposure, breaches of the X-Ray Allocation Targets and the report's own data warnings. | `xray_engine.assemble_xray_report()` |
| `get_heat_index()` | The Portfolio Heat Index for all accounts combined (0-100 score, tier, VaR, correlation and drawdown inputs, breakdown text) and the five positions with the highest Risk Contribution. | `page_helpers.get_portfolio_heat_row("all")`, `risk_orchestrator_engine.get_ticker_risk_contributions(5)` |
| `get_market_regime()` | Market-wide context in six parts, each with its own `as_of` and `stale` flag: Market Regime (HMM) with the US/UK turbulence class, Market Stress score, US and UK sovereign-yield threat levels, Treasury auction health, Fear & Greed and the Market Pulse tiles. | `regime_engine.current_hmm_regime()`, `market_stress_snapshot()`, `crash_veto_stress_threshold()`, `macro_conditions_snapshot()`; `market_pulse.get_all_cached_pulse()` and `get_pulse_index_tickers()` |
| `get_ticker_snapshot(ticker)` | One ticker the dashboard already tracks: regular-session price with extended hours kept apart, trend, technicals, valuation, fundamentals, forensic scores (Piotroski, Altman, Beneish), ML confidence and Quantile Price Band, Position Targets per account and next earnings. Each part has its own `as_of` and `stale` flag. | `page_data_stock.load_stock_row()` (the Stock Detail page's row), `market_pulse.get_all_cached_pulse()`, `accounts_engine.current_price_map()`, `oldest_quote_epoch()`, `native_currencies()`, `get_combined_holdings()`, `db_accounts.get_holding_price_limits_for_ticker()`, `db_helpers.get_next_earnings_dates()`, `utils.cached_fundamentals_extra()`; `yahoo_engine.get_quote_snapshot()` only when no cached price exists |

### The `account` argument

`get_holdings` and `get_xray` take `account`: `"all"`, or a `scope_id` exactly as `get_accounts` returns it (`acct:<id>` for a built-in Trading account). `get_xray` also takes the UUID of an active Ghostfolio account; `get_holdings` answers that with a curated "use get_xray" message because the per-position engine covers built-in accounts only. Anything else (a Pension or House account, a soft-deleted or unknown id, a different case such as `ALL`) is refused with `Unknown account. Valid values: ...`, listing the accepted values; the rejected text is not echoed. The argument is capped at 64 characters (`MCP_ACCOUNT_SCOPE_MAX_CHARS`, `constants.py`, request-limits block) and validated before any engine call, because the X-ray engine alone would treat an unrecognised id as "all".

`get_accounts` details:

- **Units.** Amounts are in `base_currency`. `gain_*` fields are amounts over the window excluding deposits and withdrawals, not percentages. `mwrr_pct` and `performance_pct` are percent.
- **One snapshot.** A Trading row comes from the single `account_performance_cache` snapshot (`snapshot_at`), so its figures are consistent with each other. Cash is taken from that snapshot, not recomputed.
- **Read-only.** The Home Assistant route fills a missing snapshot row on demand; this tool passes `refresh_missing=False`, so an account with no snapshot yet returns null metrics, appears in `missing`, makes `stale` true, and nothing is written.
- **`scope_id`.** The id the scope-aware tools accept as `account` (`acct:<id>` for a Trading account, a UUID for a Ghostfolio account). It is `null` for an account with no holdings, and for Pension and House accounts.
- **FX.** The tool runs inside `portfolio_service.persisted_fx_reads()` like the Home Assistant read endpoints. A Pension or House account with no price history has a `null` `equity_value` and an entry in `missing`.
- **Known limit.** When an FX rate or price is unavailable the engines value a holding at cost basis (Trading) or `0.0` (a House valuation with no FX rate) instead of returning nothing. The tool cannot tell these apart from a real value without recomputing, so it does not flag them (tracked in `audit/issues_to_fix.md`).
- **Ghostfolio.** `list_scope_accounts_with_values()` calls the Ghostfolio API when Ghostfolio accounts are enabled; with Ghostfolio off, no network call is made.

`get_holdings` details:

- **Rows.** The same per-(account, ticker) rows the Home Assistant `holdings-list` endpoint serves, cut down to what analysis needs. The same ticker in two accounts is two rows. Positions come only from the built-in Trading accounts (`accounts_engine`, AGENTS.md rule 14); Treasury Bills appear with their analytically accreted price.
- **Units.** Amounts are in `base_currency`. `market_price` is the instrument's own quote in `market_price_currency`, and `GBp` means pence, exactly as the engines hold it; `market_price_in_base_currency` converts it. `gain_pct` is percent. `allocation_pct` is the share of that row's own account equity, so an account's rows add up to 100; it is not a portfolio-wide weight (use `get_xray` for that). `day_change` is per share in the quote currency and `day_change_pct` a percent, both since the previous regular close.
- **Extended hours.** `pre_market_change_pct` and `post_market_change_pct` are separate fields and never change `market_price`, `day_change` or the P/L.
- **FX or price unavailable.** The engines then value the position at cost (`priced_at_cost: true`). The tool turns `market_value`, `gain_value`, `gain_pct` and `allocation_pct` into `null`, keeps `total_investment`, adds a line to `missing` and makes `stale` true, instead of showing a zero gain.
- **`as_of`.** The oldest market quote behind the prices (`accounts_engine.oldest_quote_epoch()`: the `market_pulse_cache` time where that price is used, else the `stock_signals` time, resolved by the same arbitration as `current_price_map()`). Treasury Bill prices are analytic and carry no quote time. `null` when the account has no positions.
- **Ghostfolio.** Not covered. With Ghostfolio accounts enabled, `account="all"` says so in `missing`; `get_xray` includes them.
- **FX.** Runs inside `portfolio_service.persisted_fx_reads()` like `get_accounts`.

`get_xray` details:

- **Conclusions, not the report.** A summary of `xray_engine.assemble_xray_report()`; the holdings table, the correlation matrix and the tooltips are not returned. Every weight is a fraction of invested capital (0.25 = 25%) with cash excluded; `max_drawdown` is a negative fraction; `risk.var_95_1d` is an amount in the base currency.
- **Exposure.** The top 8 of each: sectors (ETFs looked through), continents (not countries) and recorded currencies. A country appears only as a finding, when a country target is set in the X-Ray Allocation Targets.
- **Findings.** The targets from Settings that are exceeded or missed, in report order, with the engine's own message; at most 10 are returned and `findings_total` and `targets_checked` give the full counts. `data_warnings` repeats the report's warnings (stale or missing risk cache, uncovered holdings).
- **Nightly risk figures.** Beta, volatility, VaR, drawdown and correlation come from the nightly risk cache. When it has not run for these holdings they are `null`, each is listed in `missing`, and `as_of` is `null`. Otherwise `as_of` is the cache date at 00:00 UTC (`risk_cache_date`).
- **Errors.** An empty scope or a Ghostfolio problem (`xray_engine.XRayScopeUnavailable`) is returned as its own curated message; any other failure is the fixed text.
- **FX.** Runs inside `portfolio_service.persisted_fx_reads()`.

`get_heat_index` details:

- **What it reads.** The `"all"`-scope `portfolio_heat_index` row and the highest rows of `ticker_risk_contribution`, both written by the daily Risk Orchestrator Scan. Nothing is recomputed. `phi_score` is 0-100 (higher is hotter) with a `tier` of GREEN, YELLOW or RED; its three inputs are `var.pct_of_equity` (percent), `max_correlation.value` and `max_drawdown.pct` (percent), each with its own tier. The per-account Heat Index rows are not exposed.
- **Contributors.** The five positions with the highest `risk_score`, read through `risk_orchestrator_engine.get_ticker_risk_contributions()`, the same reader `GET /api/risk-orchestrator/status` uses. `marginal_var_contribution_pct` is the share of portfolio VaR and `stop_distance_pct` the percent of price above the ATR stop (negative means through it).
- **Not computed yet.** Before the first scan `heat_index` is `null`, the contributors are empty, `as_of` is `null` and `missing` says why. An input the scan could not compute is `null` and noted in `missing`, because the score counts it as zero risk.
- **`as_of`.** The older of the two table write times; `computed_at` is the Heat Index row's.

`get_market_regime` details:

- **Parts.** `market_regime` (HMM label Bull, Chop or Crash, its probability, the US and UK turbulence class Normal, Volatile or Crash, and the last label change within the stored 60 days), `market_stress` (Isolation Forest score 0-1, its six input features and `crash_veto_threshold`, the Settings value at or above which the Meta Score vetoes every signal), `macro_threat` (US 10Y/30Y and UK gilt yields in percent, 3-day velocity in basis points, raw GREEN, YELLOW or RED level), `treasury_auctions` (`healthy` plus up to 6 auctions of the last 30 days), `fear_greed` (0-100 value and label) and `market_pulse` (the registry's Market Pulse tiles). No series are returned: the stress history, the HMM history and the sparklines stay out.
- **Shared with the routes.** The first four parts and Fear & Greed come from `regime_engine.current_hmm_regime()`, `market_stress_snapshot()` and `macro_conditions_snapshot()`, the same functions `GET /api/market-regime/current`, `GET /api/market-stress` and `GET /api/macro-conditions` call. Their responses are unchanged, so the Home Assistant integration is unaffected.
- **HMM row.** The Market Regime (HMM), Market Stress and turbulence jobs each write their own columns of `market_regimes` on their own dates, so the tool takes the newest row that has an HMM state, not simply the newest row.
- **`as_of` and `stale`.** A dated part carries the data date (`YYYY-MM-DD`) and is `stale` when that date is older than 5 days, i.e. a nightly run was missed. `treasury_auctions.as_of` is the latest auction date and never turns stale, because auctions are irregular. The Market Pulse `as_of` is the UTC time of the oldest tile price (for a tile whose cache price was replaced by the slower `stock_signals` price, that price's time) and its `stale` is true when any tile is past its refresh interval. The envelope `as_of` is the oldest of the parts except `treasury_auctions`. A part's own `stale` flag is the one to read: the envelope `stale` turns on only when something is `missing` or the oldest part is overdue while LSE or NYSE is open.
- **Missing parts.** A part that has not been computed is `null` and has a line in `missing` (HMM not run, no stress score, no macro row, no auctions in 30 days, Fear & Greed not fetched since the app started, a Market Pulse ticker with no cached quote). `treasury_auctions.healthy` is `null` when there were no auctions, which is not the same as healthy.
- **Market Pulse.** Only the registry tiles flagged as Market Pulse tiles (`market_pulse.get_pulse_index_tickers()`, in registry order, every one of them rather than the first few the widget shows), never the held or watched tickers. Each tile has `price`, `change_pct` (percent since the previous regular close), `as_of`, `stale` and `extended`; the pre- or post-market move is only in `extended` and never changes `price` or `change_pct`. Units differ per tile (index points, a rate, a commodity price, a yield in percent).
- **Left out on purpose.** The macro row's `dxy_close` and `gbpusd_close` are not returned: the writer stores `0.0` for them when the fetch is empty (tracked in `audit/issues_to_fix.md`), and the tool must not present a zero as a reading. `GET /api/macro-conditions` still serves them.
- **Fear & Greed.** Read through `sentiment_engine.get_latest_fear_greed()`, which starts the background refresh when it is stale; it is `null` (and listed in `missing`) until the first refresh after a restart has finished.

### The `ticker` argument

`get_ticker_snapshot` takes one `ticker`. It is trimmed, checked with `utils.is_valid_ticker()` (1-64 characters from `A-Z a-z 0-9 ^ . = _ -`; capped by `MCP_TICKER_MAX_CHARS`, `constants.py`, request-limits block) and looked up as typed, then upper-cased. A malformed value is refused with `Invalid ticker: ...` before any lookup. A ticker the dashboard does not track is refused with `Unknown ticker. get_ticker_snapshot serves tickers the dashboard already tracks...`; the rejected text is not echoed.

`get_ticker_snapshot` details:

- **Which tickers.** Held (any Trading or Ghostfolio account), on the Watchlist, or present in `stock_signals` or `quant_signals` (the universe scan). `coverage` says how much there is: `full` (a `stock_signals` row), `universe_scan_only` (a `quant_signals` row only: price, trend and technicals; valuation, fundamentals, forensic scores, quote type, country and earnings are `null`) or `none` (held or watched but nothing computed yet: only the price and the position).
- **Same row as the Stock Detail page.** The row comes from `page_data_stock.load_stock_row()`, so the trend, the latest non-NULL ML confidence and the technicals match that page. That function fills display placeholders for a ticker without a `stock_signals` row (a `0.0` price, `"Unknown"` earnings, `"DOWN"` for a missing moving average); the tool turns them into `null`. `trend_50d` and `trend_200d` compare the nightly `stock_signals` price with the SMA, not the live price.
- **Price, cache first.** `price.value` is the regular-session price. It is the `market_pulse_cache` price (resolved against the nightly `stock_signals` price by the same arbitration as `get_holdings`; `source: shared_cache`). With no usable cache row the tool falls back to `accounts_engine.current_price_map()` (the nightly price, `day_change` and `day_change_pct` null). Only when neither has a price does it make one live `yahoo_engine.get_quote_snapshot()` request (`source: yahoo_live`, cached 5 minutes inside the Yahoo engine), never for a synthetic Treasury Bill or Pension ticker or one on the Settings Ignored Tickers list. The live quote is not stored: the tool writes nothing. A quote that fails leaves `price` `null` with a `missing` line. A cached price that is old is returned and marked `stale`; it does not trigger a Yahoo call.
- **Extended hours.** `extended_hours` is a separate object (`session`, `price`, `change`, `change_pct`) and never changes `price`. From the cache it is the stored extended-hours tick; from a live quote the pre- or post-market fields are chosen by Yahoo's own `marketState`, never by the clock.
- **Units.** The `units` map is authoritative. In short: `GBp` prices are pence; `roe`, `profit_margin`, `revenue_growth`, `dividend_yield`, `short_interest`, `institutional_ownership`, `atr_pct`, `mom_*`, `rel_strength_*` and `hist_vol_20` are fractions (0.15 = 15%); `debt_to_equity` is on Yahoo's scale where 30 means 30%, not 0.30; `ml.confidence` is 0-100; `rsi_14` is 0-100. `fundamentals.extra` holds raw numbers from the cached company dump: `market_cap` in the major unit of the quote currency (GBP for a GBp quote) and the other amounts in `financial_currency`.
- **Forensic scores.** Piotroski F-Score (0-9), Altman Z' (above 2.6 safe, 1.1 to 2.6 grey, below 1.1 distress) and Beneish M-Score (above -1.78 flags possible manipulation), each `null` when too few inputs could be computed, with the time of the last forensic run as `as_of`. A missing score on an equity is listed in `missing`.
- **ML.** `ml.confidence` is the latest non-NULL `quant_signals` value and has no timestamp of its own (`ml.as_of` is `null`). `price_q10` and `price_q90` are the ML Quantile Price Band.
- **Position Targets.** One row per Trading account that holds the ticker, plus the Watchlist when it is watched (the same accounts the Stock Detail page offers targets for). `low_limit` (buy) and `high_limit` (sell) are prices in the quote currency, `null` when unset. Ghostfolio-only holdings have no account to attach a target to and get no row; they still count in `position.total_shares`.
- **Next earnings.** The `stock_signals.next_earnings_date` value read through `db_helpers.get_next_earnings_dates()` (never a live Yahoo call). Anything that is not a `YYYY-MM-DD` date is `null`; a date already in the past is returned and listed in `missing`.
- **`as_of` and `stale`.** A part's `as_of` is the oldest source behind it (the `stock_signals` write time, the latest `quant_signals` date) and it is `stale` when older than 5 days, i.e. a nightly run was missed. The envelope `as_of` is the oldest part. `price.stale` is the Market Pulse display rule: a cached quote is stale only while a market is open and the quote is past its refresh interval.
- **Left out on purpose.** News, sentiment, VaR, Trap Monitor phase, patterns and the composite score: this tool is the one-ticker valuation and risk view; the other tools and later tasks cover those.

## Resources

| Resource | Returns | Reads |
|---|---|---|
| `policy://investment-policy` | The operator's investment policy as Markdown text, exactly as written. | `data/investment_policy.md` |

### Investment policy

The policy tells Claude what your rules are, so its analysis and suggestions are measured against them instead of against guesses. The resource description tells Claude to read it before any analysis and to apply it.

- **Where the file is.** `data/investment_policy.md` (UTF-8; a leading byte-order mark is dropped). You write it by hand for now; a Settings editor is planned (task MCP-13). The path is fixed in code: the resource takes no parameter, so a client cannot ask for another file, and `policy://investment-policy?path=...` or any other URI answers "Unknown resource".
- **Suggested sections.** Goals and horizon per account; risk tolerance; maximum weight of one position, sector, country and currency; tax wrappers and constraints; a never-do list; profit-taking style; preferred response format. `assets/investment_policy_template.md` holds this outline with placeholders: copy it to `data/investment_policy.md` and replace the placeholders. Do not save it unchanged, because Claude would then read the placeholders as your rules.
- **Missing or empty file.** The resource answers `POLICY NOT CONFIGURED`, tells Claude to say the policy is missing rather than invent one, and appends that template so Claude can show you what to write. A file holding only whitespace counts as empty.
- **Too long.** A file over `INVESTMENT_POLICY_MAX_CHARS` (50,000 characters, `constants.py`, request-limits block) is refused whole with `POLICY NOT LOADED` rather than cut, because a cut could drop a never-do rule at the end. A file at exactly the limit is served.
- **Unreadable.** A file that is not valid UTF-8, or cannot be opened, also answers `POLICY NOT LOADED` (the cause goes to the server log) and Claude is told not to guess.
- **Read-only.** The resource only reads. It never creates or changes the file, and it writes nothing to the database. The file is read on every request, so an edit shows up on the next read without a restart.
- **Backed up.** `data/` is inside the Backup & Recovery archive (the Data component, on by default; only `analysis.db` and its sidecars are left out of it), so the policy is backed up and restored with the rest of `data/`. `tests/test_backup_engine.py` pins this.
- **Workflow Monitor.** Shown as the manual node **Investment Policy** feeding the MCP Server (see below).

## Workflow Monitor

The MCP Server is a node on the Workflow Monitor graph (`scheduler_manifest.JOB_GRAPH["claude_mcp_server_source"]`, category `external`, cyan). It produces nothing and consumes the artifacts its tools and resources read, so a disabled or failing upstream job shows as a warning on it. Each new tool or resource adds the artifacts it reads to `consumes` (AGENTS.md rule 13). Unlike other external nodes the monitor now keeps this node's `consumes` (`scheduler_monitor._build_node`).

The investment policy file is hand-written, so it has a manual node of its own, **Investment Policy** (`JOB_GRAPH["investment_policy_source"]`, category `manual`, `non_job`), which produces the `investment_policy` artifact that the MCP Server consumes.

## Testing

The tests live in `tests/mcp_server/`, one file per module of the package (`test_auth.py`, `test_transport.py`, `test_envelope.py`, `test_scope.py`, `test_get_accounts.py`, `test_get_holdings.py`, `test_get_xray.py`, `test_get_heat_index.py`, `test_get_market_regime.py`, `test_get_ticker_snapshot.py`, `test_investment_policy.py`, `test_workflow_entry.py`), with the shared fixtures in `conftest.py` and the JSON-RPC helpers in `helpers.py`. A new tool adds `test_<tool_name>.py`. Together they cover the auth matrix (no header, wrong key, right key, unset key, Home Assistant key, Bearer on other routes, every HTTP method), host handling, the exact tool list, the envelope (including the per-tool stale limit), `get_accounts` (snapshot, missing snapshot, unpriced Pension, soft-deleted accounts, Ghostfolio rows), the `account` argument (unknown, Pension, soft-deleted, over-long, Ghostfolio), `get_holdings` (cost basis, P/L, allocation, day change, extended hours kept apart, pence quotes, unavailable FX, `as_of`, empty account), `get_xray` (summary, findings, missing risk cache, curated errors, caps), `get_heat_index` (score, contributors and their cap, not computed yet, missing inputs, nightly stale limit), `get_market_regime` (every part, nothing computed yet, the newest HMM row with a state, last change, stale parts, turbulence class, auctions, Settings threshold, Market Pulse scope, extended hours kept apart, tile `as_of`), `get_ticker_snapshot` (held, watchlist-only, universe-only and held-without-data tickers, unknown and malformed tickers, cache-first price and the Yahoo fallback, extended hours kept apart, units, stale and missing parts, no write to the database), the investment policy resource (listed, key required, missing and empty file, content served as written, byte-order mark, size limit, non-UTF-8 and unreadable file, no caller-supplied path, no write to the file or the database), the read-only guarantee (row counts of every table unchanged after calling every tool), the worker-thread behaviour and the Workflow Monitor entries. `tests/test_portfolio_metrics_engine.py` covers `refresh_missing`; `tests/test_risk_orchestrator_engine.py` covers `get_ticker_risk_contributions()` and the `/api/risk-orchestrator/status` route that shares it. `tests/test_05_config_and_utils.py` covers `utils.cached_fundamentals_extra()`. `tests/test_regime_engine.py` covers the three shared regime functions and the routes that wrap them; `tests/test_market_pulse_read.py` covers `price_as_of`.
