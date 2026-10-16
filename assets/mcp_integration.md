# Claude MCP Server

A read-only [Model Context Protocol](https://modelcontextprotocol.io) endpoint at `/mcp` that lets Claude (Claude Code, Claude Desktop) query this dashboard in natural language, replacing the copy/paste of `ai_engine.py` prompts. Canonical name (AGENTS.md rule 8): **Claude MCP Server**. Code: `mcp_server.py`; the key check is in `main.py` `auth_middleware`.

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
| Errors | A tool failure answers with a fixed `Error executing tool <name>`. The exception text stays in the server log (rule 23). |
| Rate limiting | None. A 256-bit key cannot be guessed; use the generator above, not a short phrase. |

CSRF does not interfere: the CSRF middleware only enforces when a `session` cookie is present, and an MCP client sends none.

## Transport

Streamable HTTP in stateless JSON mode: each `POST /mcp` is one JSON-RPC request answered with one JSON body, with no session and no event stream. This suits a read-only tool server and survives app restarts without the client having to reconnect. Consequences: server-initiated messages (sampling, progress streams) are not available, and `GET /mcp` is refused (the SDK would otherwise hold it open forever).

- `/mcp/` (trailing slash) redirects to `/mcp` with `307` after authentication; `/mcp` itself never redirects.
- Synchronous tool functions run in a worker thread (the SDK uses `anyio.to_thread`), so a slow tool never blocks the event loop (rule 10). `tests/test_mcp_server.py` stalls a tool and checks an unrelated request still answers.
- The SDK's session manager can run only once per instance, so each app lifespan builds its own (`mcp_server.run()`); several lifespans in one process (the test suite) are safe.
- SDK: the official `mcp` package, 2.x (`mcp>=2.3,<3`). See `assets/dependencies.md`.

## Response envelope

Every tool returns the same wrapper (`mcp_server.envelope()`):

| Field | Meaning |
|---|---|
| `as_of` | UTC time (`YYYY-MM-DDTHH:MM:SSZ`) of the oldest data point the answer used; `null` when there is none. |
| `age_seconds` | Age of `as_of` when the answer was built. |
| `market_state` | `LSE` and `NYSE` each `open`, `pre`, `post` or `closed` (`market_session_helpers.get_exchange_session_state()`, holiday-aware). |
| `base_currency` | `BASE_CURRENCY`. Every amount is in it unless a field says otherwise. |
| `units` | What each field is: amount, fraction or percent, and which currency. |
| `stale` | `true` when something is `missing`, or when `as_of` is older than 15 minutes (`STALE_AFTER_SECONDS`) while LSE or NYSE is open. Old data with both markets closed is not stale: nothing was due to change. |
| `missing` | Plain-text list of what could not be provided and why. |
| `data` | The tool's payload. |

A value that is unavailable is `null`, never `0.0` (AGENTS.md). Non-finite numbers are sent as `null`.

## Tools

| Tool | Returns | Reads |
|---|---|---|
| `get_accounts` | Every account: Trading (cash, equity value, unrealised and realised P/L, dividend and interest income, 1d/1w/1m/3m/1y gain, MWRR), Pension and House (current value, 1m/YTD/1y performance, price date) and Ghostfolio accounts when enabled (value only). Each row has an `account_type` and a `scope_id`. | `portfolio_metrics_engine.account_metrics_list(refresh_missing=False)`, `other_accounts_list()`, `accounts_engine.list_scope_accounts_with_values()` |

`get_accounts` details:

- **Units.** Amounts are in `base_currency`. `gain_*` fields are amounts over the window excluding deposits and withdrawals, not percentages. `mwrr_pct` and `performance_pct` are percent.
- **One snapshot.** A Trading row comes from the single `account_performance_cache` snapshot (`snapshot_at`), so its figures are consistent with each other. Cash is taken from that snapshot, not recomputed.
- **Read-only.** The Home Assistant route fills a missing snapshot row on demand; this tool passes `refresh_missing=False`, so an account with no snapshot yet returns null metrics, appears in `missing`, makes `stale` true, and nothing is written.
- **`scope_id`.** The id later scope-aware tools will accept (`acct:<id>` for a Trading account, a UUID for a Ghostfolio account). It is `null` for an account with no holdings, and for Pension and House accounts.
- **FX.** The tool runs inside `portfolio_service.persisted_fx_reads()` like the Home Assistant read endpoints. A Pension or House account with no price history has a `null` `equity_value` and an entry in `missing`.
- **Known limit.** When an FX rate or price is unavailable the engines value a holding at cost basis (Trading) or `0.0` (a House valuation with no FX rate) instead of returning nothing. The tool cannot tell these apart from a real value without recomputing, so it does not flag them (tracked in `audit/issues_to_fix.md`).
- **Ghostfolio.** `list_scope_accounts_with_values()` calls the Ghostfolio API when Ghostfolio accounts are enabled; with Ghostfolio off, no network call is made.

## Workflow Monitor

The Claude MCP Server is a node on the Workflow Monitor graph (`scheduler_manifest.JOB_GRAPH["claude_mcp_server_source"]`, category `external`, cyan). It produces nothing and consumes the artifacts its tools read, so a disabled or failing upstream job shows as a warning on it. Each new tool adds the artifacts it reads to `consumes` (AGENTS.md rule 13). Unlike other external nodes the monitor now keeps this node's `consumes` (`scheduler_monitor._build_node`).

## Testing

`tests/test_mcp_server.py` covers the auth matrix (no header, wrong key, right key, unset key, Home Assistant key, Bearer on other routes, every HTTP method), host handling, the exact tool list, the envelope, `get_accounts` (snapshot, missing snapshot, unpriced Pension, soft-deleted accounts, Ghostfolio rows), the read-only guarantee (row counts of every table unchanged after calling every tool), the worker-thread behaviour and the Workflow Monitor entry. `tests/test_portfolio_metrics_engine.py` covers `refresh_missing`.
