import re

from database import get_connection

MCP_KEY = "test-mcp-key-do-not-use-in-production"
RPC_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
EXPECTED_TOOLS = {"get_accounts", "get_holdings", "get_xray", "get_heat_index", "get_market_regime", "get_ticker_snapshot"}


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
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
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
