"""mcp_audit_log: what /mcp writes per request, what it never stores, and the single writer queue that keeps the event loop free."""
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

import db_mcp_audit
import db_mcp_keys
from database import get_connection
from mcp_server import audit

from .helpers import MCP_KEY, RPC_HEADERS, call, get_prompt, rpc


def audit_rows():
    conn = get_connection()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM mcp_audit_log ORDER BY id")]
    finally:
        conn.close()


def only_row():
    rows = audit_rows()
    assert len(rows) == 1, rows
    return rows[0]


def current_key_id():
    return next(row["id"] for row in db_mcp_keys.list_keys() if row["name"] == "Test key")


class TestDescribeRequest:
    def test_tool_call_gives_the_tool_name_and_never_the_arguments(self):
        body = b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_holdings","arguments":{"account":"acct:7"}}}'
        assert audit.describe_request(body) == ("tools/call", "get_holdings")

    def test_prompt_and_resource_names(self):
        assert audit.describe_request(b'{"method":"prompts/get","params":{"name":"review_portfolio","arguments":{"x":"1"}}}') == ("prompts/get", "review_portfolio")
        assert audit.describe_request(b'{"method":"resources/read","params":{"uri":"policy://investment-policy"}}') == ("resources/read", "policy://investment-policy")

    def test_other_methods_have_no_name_even_when_params_carry_one(self):
        body = b'{"method":"initialize","params":{"clientInfo":{"name":"SecretClient"},"name":"leak"}}'
        assert audit.describe_request(body) == ("initialize", None)

    def test_a_name_that_is_not_text_is_dropped(self):
        assert audit.describe_request(b'{"method":"tools/call","params":{"name":{"nested":"value"}}}') == ("tools/call", None)
        assert audit.describe_request(b'{"method":"tools/call","params":"x"}') == ("tools/call", None)

    def test_a_batch_is_one_word(self):
        assert audit.describe_request(b'[{"method":"tools/call","params":{"name":"a"}}]') == ("batch", None)

    @pytest.mark.parametrize("body", [b"", b"{not json", b"\xff\xfe", b'"text"', b"42", b'{"method":5}', b"[" * 50_000])
    def test_unreadable_bodies_give_nothing(self, body):
        assert audit.describe_request(body) in {(None, None), ("batch", None)}


class TestRows:
    def test_a_tool_call_is_one_row(self, mcp_client):
        before = datetime.now(timezone.utc).replace(microsecond=0)
        response = call(mcp_client, "get_accounts")
        assert response.status_code == 200
        row = only_row()
        assert row["credential_kind"] == "key" and row["key_id"] == current_key_id()
        assert row["client_address"] == "testclient"
        assert (row["mcp_method"], row["mcp_name"]) == ("tools/call", "get_accounts")
        assert row["status"] == 200
        assert row["response_bytes"] == len(response.content)
        assert 0 <= row["duration_ms"] < 30_000
        assert re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d", row["logged_at"])
        stamp = datetime.strptime(row["logged_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        assert 0 <= (stamp - before).total_seconds() < 60

    def test_list_prompt_and_resource_requests_are_named(self, mcp_client):
        rpc(mcp_client, "tools/list")
        get_prompt(mcp_client, "review_portfolio")
        rpc(mcp_client, "resources/read", {"uri": "policy://investment-policy"})
        assert [(r["mcp_method"], r["mcp_name"]) for r in audit_rows()] == [
            ("tools/list", None), ("prompts/get", "review_portfolio"), ("resources/read", "policy://investment-policy"),
        ]

    def test_argument_values_the_key_and_client_text_are_never_stored(self, mcp_client):
        call(mcp_client, "get_ticker_snapshot", {"ticker": "ARGVALUE-7731"})
        rpc(mcp_client, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "CLIENTNAME-4412", "version": "1"}})
        mcp_client.post("/mcp", headers={**RPC_HEADERS, "Authorization": f"Bearer {MCP_KEY}", "User-Agent": "UA-9912"}, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        stored = repr(audit_rows())
        for secret in ("ARGVALUE-7731", "CLIENTNAME-4412", "UA-9912", MCP_KEY):
            assert secret not in stored

    def test_request_text_is_one_clean_line_within_the_cap(self, mcp_client):
        call(mcp_client, "line\nbreak\u202eevil" + "x" * 500)
        row = only_row()
        assert "\n" not in row["mcp_name"] and "\u202e" not in row["mcp_name"]
        assert len(row["mcp_name"]) <= 100 and row["mcp_name"].startswith("line break")

    def test_the_address_is_cleaned_too(self):
        audit.record(None, "10.0.0.1\r\nforged" + "9" * 200, "tools/call", None, 401, 0, 10)
        address = only_row()["client_address"]
        assert "\r" not in address and "\n" not in address and len(address) <= 64

    def test_a_wrong_key_is_a_row_without_a_credential(self, mcp_client):
        response = rpc(mcp_client, "tools/list", key="WRONGKEY-5518")
        assert response.status_code == 401
        row = only_row()
        assert (row["credential_kind"], row["key_id"], row["status"]) == ("none", None, 401)
        assert row["client_address"] == "testclient" and row["mcp_method"] is None
        assert row["response_bytes"] == len(response.content)
        assert "WRONGKEY-5518" not in repr(row)

    def test_the_home_assistant_header_is_a_403_row_that_does_not_store_it(self, mcp_client):
        response = rpc(mcp_client, "tools/list", key=None, headers={"X-API-Key": "HAKEY-3321"})
        assert response.status_code == 403
        row = only_row()
        assert (row["credential_kind"], row["status"]) == ("none", 403)
        assert "HAKEY-3321" not in repr(row)

    def test_a_disabled_server_writes_nothing(self, mcp_client, monkeypatch):
        monkeypatch.setattr("mcp_server.auth.load_config", lambda: {"MCP_SERVER_ENABLED": False})
        assert rpc(mcp_client, "tools/list").status_code == 503
        assert audit_rows() == []

    def test_a_refused_method_is_a_row(self, mcp_client):
        response = mcp_client.get("/mcp", headers={"Authorization": f"Bearer {MCP_KEY}"})
        assert response.status_code == 405
        row = only_row()
        assert (row["status"], row["key_id"], row["mcp_method"]) == (405, current_key_id(), None)

    def test_an_oversize_body_is_413_and_a_row(self, mcp_client):
        response = mcp_client.post("/mcp", headers={**RPC_HEADERS, "Authorization": f"Bearer {MCP_KEY}"}, content=b"x" * (64 * 1024 + 1))
        assert response.status_code == 413
        assert (only_row()["status"], only_row()["mcp_method"]) == (413, None)

    def test_a_body_at_the_cap_still_reaches_the_server(self, mcp_client):
        padding = "p" * (64 * 1024 - 200)
        response = mcp_client.post(
            "/mcp", headers={**RPC_HEADERS, "Authorization": f"Bearer {MCP_KEY}"},
            content=('{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{"pad":"%s"}}' % padding).encode(),
        )
        assert response.status_code == 200
        assert only_row()["mcp_method"] == "tools/list"

    def test_an_unreadable_body_is_logged_with_the_servers_status(self, mcp_client):
        response = mcp_client.post("/mcp", headers={**RPC_HEADERS, "Authorization": f"Bearer {MCP_KEY}"}, content=b"{not json")
        row = only_row()
        assert row["status"] == response.status_code and row["mcp_method"] is None


class TestWriterQueue:
    def test_a_full_queue_drops_the_row_and_never_the_request(self, mcp_client, monkeypatch, caplog):
        monkeypatch.setattr(audit, "MCP_AUDIT_QUEUE_MAX", 0)
        monkeypatch.setattr(audit, "_dropped", 0)
        with caplog.at_level(logging.WARNING, logger="mcp_server.audit"):
            assert rpc(mcp_client, "tools/list").status_code == 200
        assert audit_rows() == []
        assert "audit queue is full" in caplog.text

    def test_a_failing_write_never_fails_the_request_and_frees_the_slot(self, mcp_client, monkeypatch, caplog):
        def broken(entry):
            raise RuntimeError("database is locked")

        monkeypatch.setattr(db_mcp_audit, "add_entry", broken)
        before = audit._pending
        with caplog.at_level(logging.WARNING, logger="mcp_server.audit"):
            assert rpc(mcp_client, "tools/list").status_code == 200
        assert audit._pending == before
        assert "Could not write an MCP audit entry" in caplog.text

    def test_one_worker_thread_writes_and_a_stalled_write_blocks_nothing(self, mcp_client, monkeypatch):
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mcp-audit")
        monkeypatch.setattr(audit, "_executor", executor)
        gate, threads = threading.Event(), []
        real_add = db_mcp_audit.add_entry

        def stalled(entry):
            threads.append(threading.current_thread().name)
            assert gate.wait(10)
            real_add(entry)

        monkeypatch.setattr(db_mcp_audit, "add_entry", stalled)
        try:
            started = time.monotonic()
            assert [rpc(mcp_client, "tools/list").status_code for _ in range(3)] == [200, 200, 200]
            assert mcp_client.get("/static/css/styles.css").status_code == 200
            assert time.monotonic() - started < 5
            assert audit_rows() == []
            assert audit._pending == 3
        finally:
            gate.set()
            executor.shutdown(wait=True)
        assert len(audit_rows()) == 3 and audit._pending == 0
        assert len(set(threads)) == 1 and threads[0].startswith("mcp-audit")
        assert threads[0] != threading.current_thread().name
