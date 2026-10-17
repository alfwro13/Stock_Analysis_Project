"""Settings editor for the investment policy: GET/PUT /api/investment-policy, size cap, atomic write, auth, and the MCP resource reflecting a save."""
import os

import pytest

import constants
import utils
from mcp_server.resources import investment_policy as policy_resource

from .helpers import rpc, table_counts

URL = "/api/investment-policy"
MAX = constants.INVESTMENT_POLICY_MAX_CHARS


@pytest.fixture
def policy_path(tmp_path, monkeypatch):
    path = tmp_path / "data" / "investment_policy.md"
    monkeypatch.setattr(policy_resource, "POLICY_PATH", path)
    return path


@pytest.fixture(autouse=True)
def _fresh_rate_limit():
    from api_deps import limiter
    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture
def headers(auth_cookies):
    return {"Cookie": "; ".join(f"{k}={v}" for k, v in auth_cookies.items()), "X-CSRFToken": auth_cookies["csrftoken"]}


def _resource_text(client):
    response = rpc(client, "resources/read", {"uri": policy_resource.POLICY_URI})
    assert response.status_code == 200, response.text
    return response.json()["result"]["contents"][0]["text"]


class TestGet:
    def test_missing_file_returns_the_template(self, client, policy_path):
        data = client.get(URL).json()
        assert data["status"] == "success"
        assert data["configured"] is False
        assert data["text"] == policy_resource.policy_template()
        assert data["load_error"] is None
        assert data["max_chars"] == MAX

    @pytest.mark.parametrize("content", ["", "  \n\t\n"])
    def test_blank_file_returns_the_template(self, client, policy_path, content):
        policy_path.parent.mkdir(parents=True)
        policy_path.write_text(content, encoding="utf-8")
        data = client.get(URL).json()
        assert data["configured"] is False
        assert data["text"] == policy_resource.policy_template()

    def test_saved_file_is_returned_as_written(self, client, policy_path):
        policy_path.parent.mkdir(parents=True)
        policy_path.write_bytes(b"\xef\xbb\xbf# Policy \xe2\x80\x94 Za\xc5\xbc\xc3\xb3\xc5\x82\xc4\x87\n")
        data = client.get(URL).json()
        assert data["configured"] is True
        assert data["text"] == "# Policy — Zażółć\n"

    def test_oversize_file_gives_the_template_and_a_reason(self, client, policy_path):
        policy_path.parent.mkdir(parents=True)
        policy_path.write_text("x" * (MAX + 1), encoding="utf-8")
        data = client.get(URL).json()
        assert data["configured"] is False
        assert str(MAX) in data["load_error"]
        assert "xxxx" not in data["text"]

    def test_file_that_is_not_utf8_gives_the_template_and_a_reason(self, client, policy_path):
        policy_path.parent.mkdir(parents=True)
        policy_path.write_bytes("£5,000".encode("latin-1"))
        data = client.get(URL).json()
        assert data["configured"] is False
        assert "UTF-8" in data["load_error"]
        assert "5,000" not in data["text"]

    def test_get_needs_a_session(self, raw_client):
        assert raw_client.get(URL).status_code in (302, 401, 403)

    def test_get_does_not_write(self, client, policy_path):
        before = table_counts()
        client.get(URL)
        assert not policy_path.exists()
        assert table_counts() == before


class TestPut:
    def test_save_writes_the_fixed_path_and_reads_back(self, client, policy_path, headers):
        text = "# My policy\n\n- Max position: 10%\n- Zażółć gęślą jaźń 📈\n"
        response = client.put(URL, json={"text": text}, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json() == {"status": "success", "configured": True, "length": len(text)}
        assert policy_path.read_bytes() == text.encode("utf-8")
        assert client.get(URL).json()["text"] == text

    def test_save_creates_the_data_folder(self, client, policy_path, headers):
        assert not policy_path.parent.exists()
        assert client.put(URL, json={"text": "rules"}, headers=headers).status_code == 200
        assert policy_path.read_text(encoding="utf-8") == "rules"

    def test_newlines_are_stored_unchanged(self, client, policy_path, headers):
        client.put(URL, json={"text": "a\r\nb\nc"}, headers=headers)
        assert policy_path.read_bytes() == b"a\r\nb\nc"

    def test_blank_text_makes_the_policy_unconfigured(self, client, policy_path, headers):
        client.put(URL, json={"text": "rules"}, headers=headers)
        response = client.put(URL, json={"text": "  \n"}, headers=headers)
        assert response.json()["configured"] is False
        assert client.get(URL).json()["configured"] is False

    def test_saving_replaces_an_unservable_file(self, client, policy_path, headers):
        policy_path.parent.mkdir(parents=True)
        policy_path.write_text("x" * (MAX + 1), encoding="utf-8")
        assert client.put(URL, json={"text": "short"}, headers=headers).status_code == 200
        data = client.get(URL).json()
        assert data["configured"] is True and data["load_error"] is None

    def test_text_at_the_cap_is_saved(self, client, policy_path, headers):
        assert client.put(URL, json={"text": "x" * MAX}, headers=headers).status_code == 200
        assert len(policy_path.read_text(encoding="utf-8")) == MAX

    def test_text_over_the_cap_is_refused_whole(self, client, policy_path, headers):
        client.put(URL, json={"text": "keep"}, headers=headers)
        response = client.put(URL, json={"text": "x" * (MAX + 1)}, headers=headers)
        assert response.status_code == 422
        assert policy_path.read_text(encoding="utf-8") == "keep"

    def test_cap_comes_from_the_request_limits_block(self):
        import api_routes_auth
        assert api_routes_auth.INVESTMENT_POLICY_MAX_CHARS is constants.INVESTMENT_POLICY_MAX_CHARS

    @pytest.mark.parametrize("body", [{}, {"text": None}, {"text": 5}, {"text": ["a"]}, {"body": "x"}])
    def test_malformed_body_is_refused(self, client, policy_path, headers, body):
        assert client.put(URL, json=body, headers=headers).status_code == 422
        assert not policy_path.exists()

    def test_lone_surrogate_is_refused_and_leaves_the_file(self, client, policy_path, headers):
        client.put(URL, json={"text": "keep"}, headers=headers)
        response = client.put(URL, content='{"text": "bad \\ud800"}', headers={**headers, "Content-Type": "application/json"})
        assert response.status_code == 422
        assert policy_path.read_text(encoding="utf-8") == "keep"

    def test_unwritable_path_answers_a_fixed_error(self, client, policy_path, headers):
        policy_path.mkdir(parents=True)
        response = client.put(URL, json={"text": "rules"}, headers=headers)
        assert response.status_code == 500
        assert str(policy_path) not in response.text

    def test_put_needs_a_session(self, raw_client, policy_path):
        assert raw_client.put(URL, json={"text": "x"}).status_code in (302, 401, 403)
        assert not policy_path.exists()

    def test_put_needs_the_csrf_token(self, client, policy_path, auth_cookies):
        response = client.put(URL, json={"text": "x"}, headers={"Cookie": "; ".join(f"{k}={v}" for k, v in auth_cookies.items())})
        assert response.status_code == 403
        assert not policy_path.exists()

    def test_home_assistant_api_key_cannot_save(self, raw_client, policy_path):
        from api_deps import API_KEY_ROUTES
        assert ("PUT", URL) not in API_KEY_ROUTES and ("GET", URL) not in API_KEY_ROUTES
        response = raw_client.put(URL, json={"text": "x"}, headers={"X-API-Key": os.environ["API_KEY"]})
        assert response.status_code == 403
        assert not policy_path.exists()

    def test_saving_needs_no_step_up_and_is_rate_limited(self, client, policy_path, headers):
        codes = [client.put(URL, json={"text": f"v{i}"}, headers=headers).status_code for i in range(12)]
        assert 403 not in codes
        assert codes[0] == 200
        assert codes[-1] == 429

    def test_save_changes_no_database_row(self, client, policy_path, headers):
        before = table_counts()
        client.put(URL, json={"text": "rules"}, headers=headers)
        assert table_counts() == before


class TestAtomicWrite:
    def test_failed_write_keeps_the_old_file_and_leaves_no_temp_file(self, tmp_path, monkeypatch):
        target = tmp_path / "policy.md"
        target.write_text("old", encoding="utf-8")

        def broken_replace(self, destination):
            raise OSError("disk full")

        monkeypatch.setattr(utils.Path, "replace", broken_replace)
        with pytest.raises(OSError):
            utils.write_text_atomic(target, "new")
        monkeypatch.undo()
        assert target.read_text(encoding="utf-8") == "old"
        assert list(tmp_path.glob("*.tmp")) == []

    def test_unencodable_text_never_touches_the_file(self, tmp_path):
        target = tmp_path / "policy.md"
        target.write_text("old", encoding="utf-8")
        with pytest.raises(UnicodeEncodeError):
            utils.write_text_atomic(target, "bad \ud800")
        assert target.read_text(encoding="utf-8") == "old"
        assert list(tmp_path.glob("*.tmp")) == []

    def test_successful_write_leaves_only_the_target(self, tmp_path):
        target = tmp_path / "policy.md"
        utils.write_text_atomic(target, "new")
        assert [p.name for p in tmp_path.iterdir()] == ["policy.md"]

    def test_the_route_goes_through_the_atomic_writer(self, client, policy_path, headers, monkeypatch):
        calls = []
        real = utils.write_text_atomic
        monkeypatch.setattr("api_routes_auth.write_text_atomic", lambda path, text: (calls.append(path), real(path, text)))
        client.put(URL, json={"text": "rules"}, headers=headers)
        assert calls == [policy_path]


class TestResourceReflectsTheSave:
    def test_saved_text_is_what_claude_reads(self, client, mcp_client, policy_path, headers):
        assert _resource_text(mcp_client).startswith("POLICY NOT CONFIGURED")
        client.put(URL, json={"text": "# Rules\n- Never sell in January\n"}, headers=headers)
        assert _resource_text(mcp_client) == "# Rules\n- Never sell in January\n"
        client.put(URL, json={"text": "# Rules v2"}, headers=headers)
        assert _resource_text(mcp_client) == "# Rules v2"

    def test_emptying_the_policy_brings_back_the_not_configured_notice(self, client, mcp_client, policy_path, headers):
        client.put(URL, json={"text": "# Rules"}, headers=headers)
        client.put(URL, json={"text": ""}, headers=headers)
        assert _resource_text(mcp_client).startswith("POLICY NOT CONFIGURED")

    def test_editor_and_resource_share_one_path_and_reader(self, client, policy_path, headers):
        import api_routes_auth
        assert api_routes_auth.investment_policy is policy_resource
        client.put(URL, json={"text": "shared"}, headers=headers)
        assert policy_resource.load_policy_file() == ("shared", None)

    def test_the_resource_still_cannot_write(self, mcp_client, policy_path):
        _resource_text(mcp_client)
        assert not policy_path.exists()
