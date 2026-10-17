"""AI-prompt modes as MCP prompts: the app's own text is served unchanged after a short preamble, arguments are validated, nothing is written."""
import pytest

from ai_engine import AIPromptEngine
from ai_regime_engine import AIRegimePromptEngine
from database import get_connection
from mcp_server.prompts.market_regime import REGIME_MODES
from mcp_server.prompts.shared import policy_step
from mcp_server.prompts.stock_analysis import STOCK_MODES
from portfolio_service import persisted_fx_reads

from .helpers import AI_ENGINE_PROMPTS, REGIME_PROMPTS, get_prompt, prompt_error, prompt_text, rpc, table_counts

TICKER = "ZZAIPROMPT"
AI_TICKER_MARK = "AI Prompt Test Co"
PAGE_MODES = {
    "devils_advocate": "The Devil's Advocate analysis",
    "risk_reward_audit": "Risk/Reward Audit",
    "quantamental_deep_dive": "Quantamental Deep-Dive",
    "earnings_strategy": "Earnings Strategy",
}


@pytest.fixture
def ai_ticker():
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO stock_signals (ticker, company_name, sector, currency, current_price, overall_signal) "
            "VALUES (?, 'AI Prompt Test Co', 'Technology', 'GBP', 50.0, 'BUY')", (TICKER,),
        )
        conn.commit()
    finally:
        conn.close()
    yield TICKER
    conn = get_connection()
    try:
        conn.execute("DELETE FROM stock_signals WHERE ticker = ?", (TICKER,))
        conn.commit()
    finally:
        conn.close()


def _app_prompt(ticker, mode):
    with persisted_fx_reads():
        return AIPromptEngine().generate_prompt(ticker, mode)


def _listed(client):
    return {p["name"]: p for p in rpc(client, "prompts/list").json()["result"]["prompts"]}


class TestModeTables:
    def test_stock_modes_are_the_engines_known_modes(self):
        assert {mode for _, mode, _ in STOCK_MODES} == set(AIPromptEngine._KNOWN_MODES)
        assert {name: mode for name, mode, _ in STOCK_MODES} == PAGE_MODES

    def test_regime_modes_are_the_engines_allowed_modes(self):
        from ai_regime_engine import _ALLOWED_MODES
        assert {mode for _, mode, _ in REGIME_MODES} == set(_ALLOWED_MODES)


class TestListing:
    def test_stock_prompts_take_one_required_ticker(self, mcp_client):
        listed = _listed(mcp_client)
        for name in AI_ENGINE_PROMPTS:
            assert [(a["name"], a["required"]) for a in listed[name]["arguments"]] == [("ticker", True)]
            assert listed[name]["title"] == PAGE_MODES[name]
            assert listed[name]["arguments"][0]["description"]

    def test_regime_prompts_take_no_argument(self, mcp_client):
        listed = _listed(mcp_client)
        for name in REGIME_PROMPTS:
            assert not listed[name].get("arguments")
            assert listed[name]["title"].startswith("Market Regime: ")

    def test_descriptions_say_read_only_and_where_the_text_comes_from(self, mcp_client):
        listed = _listed(mcp_client)
        for name in AI_ENGINE_PROMPTS | REGIME_PROMPTS:
            assert "Read-only" in listed[name]["description"] and "AI prompt button" in listed[name]["description"]

    def test_key_is_required(self, mcp_client):
        assert get_prompt(mcp_client, "devils_advocate", {"ticker": "AAPL"}, key=None).status_code == 401
        assert get_prompt(mcp_client, "regime_red_flags_check", key="wrong").status_code == 401


class TestStockPrompts:
    @pytest.mark.parametrize("name", sorted(AI_ENGINE_PROMPTS))
    def test_serves_the_apps_prompt_unchanged_after_the_preamble(self, mcp_client, ai_ticker, name):
        text = prompt_text(get_prompt(mcp_client, name, {"ticker": ai_ticker}))
        app_prompt = _app_prompt(ai_ticker, PAGE_MODES[name])
        assert app_prompt and AI_TICKER_MARK in app_prompt
        assert text.endswith(app_prompt)
        assert text.startswith(policy_step(keep_sections=False))
        assert "You cannot trade or change anything" in text

    def test_ticker_is_trimmed_and_upper_cased(self, mcp_client, ai_ticker):
        text = prompt_text(get_prompt(mcp_client, "risk_reward_audit", {"ticker": f"  {ai_ticker.lower()} "}))
        assert text.endswith(_app_prompt(ai_ticker, "Risk/Reward Audit"))

    def test_unknown_ticker_is_refused_without_echoing_it(self, mcp_client):
        message = prompt_error(get_prompt(mcp_client, "devils_advocate", {"ticker": "ZZNOSUCH"}))
        assert "no stock data" in message and "ZZNOSUCH" not in message

    @pytest.mark.parametrize("ticker", ["", "bad ticker", "A" * 200, "AAPL; DROP"])
    def test_malformed_ticker_is_refused(self, mcp_client, ticker):
        response = get_prompt(mcp_client, "earnings_strategy", {"ticker": ticker}).json()
        assert "error" in response and "result" not in response

    def test_ticker_is_required(self, mcp_client):
        assert "Missing required arguments" in prompt_error(get_prompt(mcp_client, "quantamental_deep_dive"))

    def test_the_page_endpoint_still_returns_the_same_text(self, client, ai_ticker):
        body = client.get(f"/api/ai-prompt/{ai_ticker}", params={"mode": "Earnings Strategy"}).json()
        assert body["prompt"] == _app_prompt(ai_ticker, "Earnings Strategy")


class TestRegimePrompts:
    @pytest.mark.parametrize("name,mode", [(n, m) for n, m, _ in REGIME_MODES])
    def test_serves_the_apps_prompt_unchanged_after_the_preamble(self, mcp_client, name, mode):
        text = prompt_text(get_prompt(mcp_client, name))
        assert text.endswith(AIRegimePromptEngine().generate_prompt(mode))
        assert text.startswith(policy_step(keep_sections=False))

    def test_a_stray_argument_is_refused(self, mcp_client):
        assert "error" in get_prompt(mcp_client, "regime_red_flags_check", {"ticker": "AAPL"}).json()


@pytest.mark.parametrize("name", sorted(AI_ENGINE_PROMPTS | REGIME_PROMPTS))
def test_rendering_writes_nothing(mcp_client, ai_ticker, name):
    arguments = {"ticker": ai_ticker} if name in AI_ENGINE_PROMPTS else {}
    before = table_counts()
    prompt_text(get_prompt(mcp_client, name, arguments))
    assert table_counts() == before

