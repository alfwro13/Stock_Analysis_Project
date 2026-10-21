import re
from contextlib import contextmanager

from database import get_connection

MCP_KEY = "test-mcp-key-do-not-use-in-production"
RPC_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
EXPECTED_TOOLS = {"get_accounts", "get_holdings", "get_xray", "get_heat_index", "get_market_regime", "get_ticker_snapshot", "get_alerts", "get_news", "get_watchlist", "pretrade_check", "get_calendar"}


def rpc(client, method, params=None, key=MCP_KEY, headers=None):
    request_headers = {**RPC_HEADERS, **(headers or {})}
    if key is not None:
        request_headers["Authorization"] = f"Bearer {key}"
    return client.post(
        "/mcp", headers=request_headers,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
    )


def call(client, name, arguments=None, **kwargs):
    return rpc(client, "tools/call", {"name": name, "arguments": arguments or {}}, **kwargs)


def tool_data(response):
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["isError"] is False, result
    return result["structuredContent"]


def table_counts():
    conn = get_connection()
    try:
        tables = [
            r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name != 'mcp_audit_log'")
        ]
        return {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
    finally:
        conn.close()


ACCOUNT_TABLES = (
    "account_transactions", "account_performance_cache", "account_price_history",
    "account_benchmark_tickers", "account_value_history", "account_value_history_currency",
)


def error_text(response):
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["isError"] is True, result
    return re.sub(r"^Error executing tool \w+: ", "", result["content"][0]["text"])


@contextmanager
def emptied_tables(*tables):
    conn = get_connection()
    try:
        saved = {t: [dict(r) for r in conn.execute(f"SELECT * FROM {t}")] for t in tables}
        for t in tables:
            conn.execute(f"DELETE FROM {t}")
        conn.commit()
    finally:
        conn.close()
    try:
        yield
    finally:
        conn = get_connection()
        try:
            for t in tables:
                conn.execute(f"DELETE FROM {t}")
                for row in saved[t]:
                    conn.execute(f"INSERT INTO {t} ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", list(row.values()))
            conn.commit()
        finally:
            conn.close()


TOOL_PROMPTS = {"review_portfolio", "pre_trade_review", "whats_coming_up"}
AI_ENGINE_PROMPTS = {"devils_advocate", "risk_reward_audit", "quantamental_deep_dive", "earnings_strategy"}
REGIME_PROMPTS = {"regime_plain_english_briefing", "regime_what_happens_next", "regime_how_should_i_position", "regime_red_flags_check"}
EXPECTED_PROMPTS = TOOL_PROMPTS | AI_ENGINE_PROMPTS | REGIME_PROMPTS


def get_prompt(client, name, arguments=None, **kwargs):
    return rpc(client, "prompts/get", {"name": name, "arguments": arguments or {}}, **kwargs)


def prompt_text(response):
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert len(result["messages"]) == 1 and result["messages"][0]["role"] == "user"
    return result["messages"][0]["content"]["text"]


def prompt_error(response):
    assert response.status_code == 200, response.text
    return response.json()["error"]["message"]
