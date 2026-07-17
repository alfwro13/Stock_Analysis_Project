"""
tests/test_ticker_notes.py  ── TICKER NOTES

Covers the Ticker Notes feature end to end:
  • db_helpers CRUD (add/get/update/delete, grouped listing for the report page)
  • API endpoints (create/edit/delete, validation, list-all)
  • Stock Detail page context wiring
  • /ticker-notes report page renders
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def _json(resp) -> dict:
    try:
        return resp.json()
    except Exception as exc:
        raise AssertionError(
            f"Response is not valid JSON.\nStatus: {resp.status_code}\nBody: {resp.text[:500]}"
        ) from exc


# ── db_helpers CRUD ───────────────────────────────────────────────────────────

@pytest.mark.db
def test_add_and_get_ticker_notes():
    from db_helpers import add_ticker_note, get_ticker_notes

    note_id = add_ticker_note("ZZNOTES1", "First observation.\n\nWith a blank line.")
    assert note_id is not None

    notes = get_ticker_notes("ZZNOTES1")
    assert len(notes) == 1
    assert notes[0]["note_text"] == "First observation.\n\nWith a blank line."
    assert notes[0]["created_at"]
    assert notes[0]["updated_at"] is None


@pytest.mark.db
def test_get_ticker_notes_orders_newest_first():
    from db_helpers import add_ticker_note, get_ticker_notes

    add_ticker_note("ZZNOTES2", "older note")
    add_ticker_note("ZZNOTES2", "newer note")

    notes = get_ticker_notes("ZZNOTES2")
    assert len(notes) == 2
    assert notes[0]["note_text"] == "newer note"
    assert notes[1]["note_text"] == "older note"


@pytest.mark.db
def test_update_ticker_note():
    from db_helpers import add_ticker_note, get_ticker_notes, update_ticker_note

    note_id = add_ticker_note("ZZNOTES3", "original text")
    updated = update_ticker_note(note_id, "ZZNOTES3", "edited text")
    assert updated is True

    notes = get_ticker_notes("ZZNOTES3")
    assert notes[0]["note_text"] == "edited text"
    assert notes[0]["updated_at"]


@pytest.mark.db
def test_update_ticker_note_missing_id_returns_false():
    from db_helpers import update_ticker_note

    assert update_ticker_note(999999999, "ZZNOTESNONE", "text") is False


@pytest.mark.db
def test_update_ticker_note_wrong_ticker_returns_false():
    from db_helpers import add_ticker_note, update_ticker_note

    note_id = add_ticker_note("ZZNOTES3B", "original text")
    assert update_ticker_note(note_id, "ZZWRONGTICKER", "edited text") is False


@pytest.mark.db
def test_delete_ticker_note():
    from db_helpers import add_ticker_note, delete_ticker_note, get_ticker_notes

    note_id = add_ticker_note("ZZNOTES4", "to be deleted")
    assert delete_ticker_note(note_id, "ZZNOTES4") is True
    assert get_ticker_notes("ZZNOTES4") == []


@pytest.mark.db
def test_delete_ticker_note_missing_id_returns_false():
    from db_helpers import delete_ticker_note

    assert delete_ticker_note(999999999, "ZZNOTESNONE") is False


@pytest.mark.db
def test_delete_ticker_note_wrong_ticker_returns_false():
    from db_helpers import add_ticker_note, delete_ticker_note, get_ticker_notes

    note_id = add_ticker_note("ZZNOTES4B", "keep me")
    assert delete_ticker_note(note_id, "ZZWRONGTICKER") is False
    assert len(get_ticker_notes("ZZNOTES4B")) == 1


@pytest.mark.db
def test_get_all_ticker_notes_grouped():
    from db_helpers import add_ticker_note, get_all_ticker_notes_grouped

    add_ticker_note("ZZNOTES5", "note a")
    add_ticker_note("ZZNOTES5", "note b")
    add_ticker_note("ZZNOTES6", "note c")

    grouped = get_all_ticker_notes_grouped()
    by_ticker = {e["ticker"]: e for e in grouped}
    assert len(by_ticker["ZZNOTES5"]["notes"]) == 2
    assert len(by_ticker["ZZNOTES6"]["notes"]) == 1


# ── API endpoints ─────────────────────────────────────────────────────────────

@pytest.mark.api
def test_api_add_ticker_note_success(client):
    resp = client.post("/api/ticker/ZZAPINOTE1/notes", json={"note_text": "hello world"})
    assert resp.status_code == 200
    data = _json(resp)
    assert data["status"] == "success"
    assert "id" in data


@pytest.mark.api
def test_api_add_ticker_note_rejects_empty_text(client):
    resp = client.post("/api/ticker/ZZAPINOTE2/notes", json={"note_text": ""})
    assert resp.status_code == 422


@pytest.mark.api
def test_api_add_ticker_note_rejects_over_1000_chars(client):
    resp = client.post("/api/ticker/ZZAPINOTE3/notes", json={"note_text": "x" * 1001})
    assert resp.status_code == 422


@pytest.mark.api
def test_api_update_and_delete_ticker_note(client):
    resp = client.post("/api/ticker/ZZAPINOTE4/notes", json={"note_text": "initial"})
    note_id = _json(resp)["id"]

    resp2 = client.put(f"/api/ticker/ZZAPINOTE4/notes/{note_id}", json={"note_text": "updated"})
    assert resp2.status_code == 200
    assert _json(resp2)["status"] == "success"

    from db_helpers import get_ticker_notes
    notes = get_ticker_notes("ZZAPINOTE4")
    assert notes[0]["note_text"] == "updated"

    resp3 = client.delete(f"/api/ticker/ZZAPINOTE4/notes/{note_id}")
    assert resp3.status_code == 200
    assert _json(resp3)["status"] == "success"
    assert get_ticker_notes("ZZAPINOTE4") == []


@pytest.mark.api
def test_api_update_ticker_note_not_found(client):
    resp = client.put("/api/ticker/ZZAPINOTE5/notes/999999999", json={"note_text": "text"})
    assert resp.status_code == 404


@pytest.mark.api
def test_api_delete_ticker_note_not_found(client):
    resp = client.delete("/api/ticker/ZZAPINOTE5/notes/999999999")
    assert resp.status_code == 404


@pytest.mark.api
def test_api_get_all_ticker_notes_shape(client):
    client.post("/api/ticker/ZZAPINOTE6/notes", json={"note_text": "a note"})
    resp = client.get("/api/ticker-notes")
    assert resp.status_code == 200
    data = _json(resp)
    assert data["status"] == "success"
    entry = next(e for e in data["tickers"] if e["ticker"] == "ZZAPINOTE6")
    assert entry["notes"][0]["note_text"] == "a note"
    assert "company_name" in entry


# ── Page routes ───────────────────────────────────────────────────────────────

@pytest.mark.pages
def test_stock_detail_page_includes_ticker_notes_context(client):
    from db_helpers import add_ticker_note

    add_ticker_note("ZZPAGENOTE1", "visible note")
    resp = client.get("/stock/ZZPAGENOTE1", follow_redirects=True)
    assert resp.status_code == 200
    assert "visible note" in resp.text


@pytest.mark.pages
def test_stock_detail_page_without_notes_does_not_crash(client):
    resp = client.get("/stock/ZZNONOTESTICKER", follow_redirects=True)
    assert resp.status_code == 200


@pytest.mark.pages
def test_stock_detail_notes_section_defaults_collapsed(client):
    from db_helpers import add_ticker_note

    add_ticker_note("ZZPAGENOTE2", "collapsed by default")
    resp = client.get("/stock/ZZPAGENOTE2", follow_redirects=True)
    assert resp.status_code == 200
    assert 'id="notesSectionBody" class="ticker-notes-list mt-10 d-none"' in resp.text


@pytest.mark.pages
def test_stock_detail_long_note_gets_truncate_toggle(client):
    from db_helpers import add_ticker_note

    add_ticker_note("ZZPAGENOTE3", "x" * 400)
    resp = client.get("/stock/ZZPAGENOTE3", follow_redirects=True)
    assert resp.status_code == 200
    assert "note-truncated" in resp.text
    assert "Show more" in resp.text


@pytest.mark.pages
def test_stock_detail_short_note_has_no_truncate_toggle(client):
    from db_helpers import add_ticker_note

    add_ticker_note("ZZPAGENOTE4", "short note")
    resp = client.get("/stock/ZZPAGENOTE4", follow_redirects=True)
    assert resp.status_code == 200
    assert "note-truncated" not in resp.text
    assert "Show more" not in resp.text


@pytest.mark.pages
def test_ticker_notes_report_page_renders(client):
    resp = client.get("/ticker-notes", follow_redirects=True)
    assert resp.status_code == 200
