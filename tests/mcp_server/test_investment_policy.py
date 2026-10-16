"""Investment policy resource: listing, auth, missing/empty/oversize/unreadable file, fixed path, read-only."""
import logging

import pytest

import constants
from mcp_server.resources import investment_policy as policy_resource

from .helpers import rpc, table_counts


POLICY_URI = "policy://investment-policy"


@pytest.fixture
def policy_path(tmp_path, monkeypatch):
    path = tmp_path / "investment_policy.md"
    monkeypatch.setattr(policy_resource, "POLICY_PATH", path)
    return path


def _read_policy(client, uri=POLICY_URI, **kwargs):
    return rpc(client, "resources/read", {"uri": uri}, **kwargs)


def _policy_text(client):
    response = _read_policy(client)
    assert response.status_code == 200, response.text
    contents = response.json()["result"]["contents"]
    assert len(contents) == 1 and contents[0]["uri"] == POLICY_URI
    return contents[0]["text"]


class TestInvestmentPolicy:
    def test_resource_is_listed_and_is_the_only_one(self, mcp_client):
        resources = rpc(mcp_client, "resources/list").json()["result"]["resources"]
        assert [r["uri"] for r in resources] == [POLICY_URI]
        listed = resources[0]
        assert listed["mimeType"] == "text/markdown"
        assert "BEFORE any analysis" in listed["description"]
        assert "instead of inventing one" in listed["description"]

    def test_resource_needs_the_mcp_key(self, mcp_client, policy_path):
        policy_path.write_text("secret policy", encoding="utf-8")
        assert _read_policy(mcp_client, key=None).status_code == 401
        assert _read_policy(mcp_client, key="wrong").status_code == 401

    def test_missing_file_gives_the_notice_and_the_template(self, mcp_client, policy_path):
        assert not policy_path.exists()
        text = _policy_text(mcp_client)
        assert text.startswith("POLICY NOT CONFIGURED")
        assert "Do not invent goals, limits or rules" in text
        assert policy_resource.POLICY_TEMPLATE_PATH.read_text(encoding="utf-8") in text

    def test_template_covers_every_suggested_section(self):
        template = policy_resource.POLICY_TEMPLATE_PATH.read_text(encoding="utf-8").lower()
        for expected in (
            "goals and horizon", "risk tolerance", "max weight of one position", "max weight of one sector",
            "max weight of one country", "max weight of one currency", "tax wrappers and constraints",
            "never-do list", "profit-taking style", "preferred response format",
        ):
            assert expected in template

    @pytest.mark.parametrize("content", ["", "   \n\t\n"])
    def test_empty_file_is_treated_as_missing(self, mcp_client, policy_path, content):
        policy_path.write_text(content, encoding="utf-8")
        assert _policy_text(mcp_client).startswith("POLICY NOT CONFIGURED")

    def test_present_file_is_served_as_written(self, mcp_client, policy_path):
        content = "# My policy\n\n- Max position: 10% — never above £5,000\n- Zażółć gęślą jaźń\n"
        policy_path.write_text(content, encoding="utf-8")
        assert _policy_text(mcp_client) == content

    def test_utf8_bom_is_dropped(self, mcp_client, policy_path):
        policy_path.write_bytes(b"\xef\xbb\xbf# Policy\n")
        assert _policy_text(mcp_client) == "# Policy\n"

    def test_edits_show_up_on_the_next_read(self, mcp_client, policy_path):
        policy_path.write_text("first", encoding="utf-8")
        assert _policy_text(mcp_client) == "first"
        policy_path.write_text("second", encoding="utf-8")
        assert _policy_text(mcp_client) == "second"

    def test_file_at_the_cap_is_served(self, mcp_client, policy_path):
        content = "x" * policy_resource.INVESTMENT_POLICY_MAX_CHARS
        policy_path.write_text(content, encoding="utf-8")
        assert _policy_text(mcp_client) == content

    def test_oversize_file_is_refused_not_cut(self, mcp_client, policy_path):
        policy_path.write_text("x" * (policy_resource.INVESTMENT_POLICY_MAX_CHARS + 1), encoding="utf-8")
        text = _policy_text(mcp_client)
        assert text.startswith("POLICY NOT LOADED")
        assert str(policy_resource.INVESTMENT_POLICY_MAX_CHARS) in text
        assert "xxxx" not in text
        assert "Do not invent goals, limits or rules" in text

    def test_cap_comes_from_the_request_limits_block(self):
        assert policy_resource.INVESTMENT_POLICY_MAX_CHARS is constants.INVESTMENT_POLICY_MAX_CHARS

    def test_file_that_is_not_utf8_is_reported_not_guessed(self, mcp_client, policy_path, caplog):
        policy_path.write_bytes("Max position £5,000".encode("latin-1"))
        with caplog.at_level(logging.ERROR, logger="mcp_server.resources.investment_policy"):
            text = _policy_text(mcp_client)
        assert text.startswith("POLICY NOT LOADED")
        assert "Max position" not in text
        assert "could not be read" in caplog.text

    def test_unreadable_path_is_reported(self, mcp_client, policy_path):
        policy_path.mkdir()
        assert _policy_text(mcp_client).startswith("POLICY NOT LOADED")

    def test_no_caller_supplied_path_exists(self, mcp_client, policy_path, tmp_path):
        import inspect
        assert not inspect.signature(policy_resource.investment_policy).parameters
        assert rpc(mcp_client, "resources/templates/list").json()["result"]["resourceTemplates"] == []
        elsewhere = tmp_path / "elsewhere.md"
        elsewhere.write_text("do not leak", encoding="utf-8")
        policy_path.write_text("the policy", encoding="utf-8")
        for uri in (
            f"{POLICY_URI}?path={elsewhere}", f"{POLICY_URI}/{elsewhere}", f"{POLICY_URI}/../../etc/passwd",
            f"file://{elsewhere}", "policy://other",
        ):
            response = _read_policy(mcp_client, uri=uri)
            assert "error" in response.json(), uri
            assert "do not leak" not in response.text, uri

    def test_reading_never_writes_the_file_or_the_database(self, mcp_client, policy_path):
        before = table_counts()
        _policy_text(mcp_client)
        assert not policy_path.exists()
        policy_path.write_text("keep me", encoding="utf-8")
        stat = policy_path.stat()
        _policy_text(mcp_client)
        assert policy_path.read_bytes() == b"keep me"
        assert policy_path.stat().st_mtime_ns == stat.st_mtime_ns
        assert table_counts() == before

    def test_no_write_method_is_offered(self, mcp_client):
        assert rpc(mcp_client, "resources/subscribe", {"uri": POLICY_URI}).json().get("error")
        capabilities = rpc(mcp_client, "initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"},
        }).json()["result"]["capabilities"]
        assert not capabilities.get("resources", {}).get("subscribe")
