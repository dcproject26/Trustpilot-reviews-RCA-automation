"""The CSV export: every review and its RCA, behind a key, no Google account.

WHY IT EXISTS. The sheet export needed GCP_SERVICE_ACCOUNT_JSON — a credential
this project has never had, since BigQuery and Zendesk both authenticate
through Replit connectors. It was the one feature wired to a secret nobody set
and the one feature that never wrote a row. This needs nothing but a key.

The columns, the row builder and the cell coercion are the SAME ones the sheet
uses. A second row builder would be a second place for a column to be
forgotten, which is the defect this codebase keeps finding.
"""
import csv
import io
from datetime import datetime

import pytest

from server.services import sheet_export as SX


class R:
    def __init__(self, rid="tp_1", **kw):
        self.id = rid
        self.received_at = datetime(2026, 8, 2, 12, 6)
        self.author = "Roisin"
        self.rating = 1
        self.language = "en"
        self.body_english = "the pickup time changed"
        self.body_original = "the pickup time changed"
        self.status = "new"
        self.slack_channel = "C123"
        self.slack_ts = "1690000000.123456"
        self.sent_route = None
        self.close_reason = None
        self.__dict__.update(kw)


def _read(text):
    return list(csv.DictReader(io.StringIO(text)))


# ── the file itself ─────────────────────────────────────────────────────────

def test_the_header_is_the_same_columns_the_sheet_uses():
    """One definition. Two would drift, and a column added to one and not the
    other is invisible until someone compares two exports side by side."""
    rows = _read(SX.to_csv([SX.arrival_row(R())]))
    assert list(rows[0].keys()) == list(SX.COLUMNS)


def test_review_text_with_commas_quotes_and_newlines_stays_in_one_field():
    """THE REASON csv.writer IS USED. A hand-rolled ",".join turns any of
    these into a row that opens shifted by a column and still looks like
    data — the same silent corruption check_header exists to prevent."""
    nasty = 'He said "no refund", then\nhung up'
    got = _read(SX.to_csv([SX.arrival_row(R(body_english=nasty,
                                            body_original=nasty))]))
    assert len(got) == 1, "the embedded newline split the row"
    assert got[0]["review_text"] == nasty


def test_a_row_that_cannot_be_built_is_kept_and_labelled():
    """NOT DROPPED. A review missing from the file and a review whose row blew
    up look identical once it is open in Excel, and only one of them is a bug
    in here."""
    class Exploding:
        id = "tp_bad"

        @property
        def received_at(self):
            raise ValueError("boom")

    rows, failed = SX.rows_for_all([(R("tp_ok"), None), (Exploding(), None)])
    assert failed == 1
    out = {r["review_id"]: r for r in _read(SX.to_csv(rows))}
    assert out["tp_bad"]["export_error"].startswith("ValueError")
    assert out["tp_ok"]["export_error"] == "", "a healthy row was labelled"


def test_a_healthy_export_reports_zero_failures():
    """The other half of the rule: 'looked and found nothing' has to be
    sayable, or every export reads as partially broken."""
    rows, failed = SX.rows_for_all([(R("tp_1"), None), (R("tp_2"), None)])
    assert failed == 0 and len(rows) == 2


# ── which phase a row is in, decided rather than passed ─────────────────────

def test_the_stage_follows_the_review_status_not_the_draft():
    """A review carries a draft from the moment someone opens it, days before
    anything is sent. Keying on the draft would report work as finished on
    first open."""
    assert SX.stage_for(R(status="draft"), object()) == SX.ARRIVED
    assert SX.stage_for(R(status="sent"), None) == SX.DONE


def test_the_three_kinds_of_sent_are_told_apart():
    """db.py records sent_route precisely because three different pieces of
    work end at status="sent". An export showing only "sent" merges a reply
    that went out with a review closed out having nothing to send, and
    overstates how many replies were written."""
    closed = R(status="sent", sent_route="closed",
               close_reason="Untraceable booking.")
    replied = R("tp_2", status="sent", sent_route="reply")
    rows, _ = SX.rows_for_all([(closed, None), (replied, None)])
    out = {r["review_id"]: r for r in _read(SX.to_csv(rows))}
    assert out["tp_1"]["sent_route"] == "closed"
    assert out["tp_1"]["close_reason"] == "Untraceable booking."
    assert out["tp_2"]["sent_route"] == "reply"
    assert out["tp_2"]["close_reason"] == ""


def test_a_closed_review_with_no_draft_still_carries_its_route():
    """Read off the REVIEW, not the draft — a review closed out with no RCA
    has no draft, and that is exactly the case the column distinguishes."""
    row = SX.row_for(R(status="sent", sent_route="closed"), None)
    assert row["sent_route"] == "closed"


# ── the endpoint ────────────────────────────────────────────────────────────

@pytest.fixture()
def client(live_db):
    from fastapi.testclient import TestClient
    from server.main import app
    from server.db import get_session
    app.dependency_overrides[get_session] = lambda: live_db.SessionLocal()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _seed(db, rid, **kw):
    s = db.SessionLocal()
    s.add(db.Review(id=rid, rating=1, author="A", body_original="b",
                    status=kw.pop("status", "draft"),
                    received_at=datetime.utcnow(), **kw))
    s.add(db.RcaDraft(id=f"d_{rid}", review_id=rid, booking={"id": "1"}))
    s.commit(); s.close()


def test_no_key_configured_serves_it_but_says_so(client, monkeypatch):
    """OPEN, AND VISIBLE. Nothing else in this app authenticates —
    /api/reviews, the dashboard and every draft endpoint are unguarded, CORS
    is "*" — so a key here and nowhere else was friction, not protection: the
    same data is one open endpoint away.

    What is NOT copied from _vs_auth is the silence. That helper reads
    `if expected and ...` and an outsider cannot tell a guarded endpoint from
    an unguarded one — this codebase's oldest failure wearing a security hat.
    The mode rides back on the response instead.
    """
    monkeypatch.delenv("RCA_EXPORT_KEY", raising=False)
    r = client.get("/api/export.csv")
    assert r.status_code == 200
    assert r.headers["X-Export-Auth"] == "open"


def test_a_configured_key_reports_the_guarded_mode(client, live_db, monkeypatch):
    """The converse, and the reason the header is worth having: "open" must be
    distinguishable from "key" from the outside, or the field says nothing."""
    monkeypatch.setenv("RCA_EXPORT_KEY", "s3cret")
    r = client.get("/api/export.csv", headers={"X-Export-Key": "s3cret"})
    assert r.status_code == 200
    assert r.headers["X-Export-Auth"] == "key"


def test_a_wrong_key_is_rejected(client, monkeypatch):
    monkeypatch.setenv("RCA_EXPORT_KEY", "s3cret")
    assert client.get("/api/export.csv",
                      headers={"X-Export-Key": "nope"}).status_code == 401
    assert client.get("/api/export.csv").status_code == 401, "no header passed"


def test_the_key_is_only_demanded_when_one_is_configured(client, monkeypatch):
    """The whole point of the change: no key set means no key asked for."""
    monkeypatch.delenv("RCA_EXPORT_KEY", raising=False)
    assert client.get("/api/export.csv").status_code == 200


def test_the_right_key_returns_the_rows(client, live_db, monkeypatch):
    monkeypatch.setenv("RCA_EXPORT_KEY", "s3cret")
    _seed(live_db, "tp_a")
    _seed(live_db, "tp_b", status="sent")
    r = client.get("/api/export.csv", headers={"X-Export-Key": "s3cret"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    got = {row["review_id"]: row for row in _read(r.text)}
    assert set(got) == {"tp_a", "tp_b"}
    assert got["tp_a"]["stage"] == SX.ARRIVED
    assert got["tp_b"]["stage"] == SX.DONE


def test_the_counts_travel_with_the_file(client, live_db, monkeypatch):
    """A reader who opens it in Excel cannot tell 40 rows from 40-of-45."""
    monkeypatch.setenv("RCA_EXPORT_KEY", "s3cret")
    _seed(live_db, "tp_a")
    r = client.get("/api/export.csv", headers={"X-Export-Key": "s3cret"})
    assert r.headers["X-Export-Rows"] == "1"
    assert r.headers["X-Export-Failed"] == "0"


def test_an_empty_database_is_a_header_and_no_rows(client, monkeypatch):
    """A legitimate empty, and it must not look like a failure: the header is
    still there, the counts still say zero."""
    monkeypatch.setenv("RCA_EXPORT_KEY", "s3cret")
    r = client.get("/api/export.csv", headers={"X-Export-Key": "s3cret"})
    assert r.status_code == 200
    assert _read(r.text) == []
    assert r.text.startswith("review_id,")
    assert r.headers["X-Export-Rows"] == "0"


# ── the dashboard button ────────────────────────────────────────────────────
#
# SOURCE ASSERTIONS, AND SAID SO. Per CLAUDE.md these are only acceptable for
# negative assertions and for client-side JavaScript, which has no test
# harness in this repo. This is the latter, and the limitation is real: these
# check the handler exists and is shaped right, not that clicking it works.
# The behaviour they stand in for is covered server-side by the endpoint tests
# above, which is where the decisions actually live.

PAGE = open("client/index.html", encoding="utf-8").read()


def test_the_button_reuses_the_existing_topbar_button_class():
    """The design system, not a new one. `.refresh-slack-btn` is the shared
    rule the other two topbar buttons use; a bespoke class would be a second
    place for the button styling to drift."""
    assert 'class="refresh-slack-btn" data-export-csv' in PAGE


def test_the_key_is_never_written_into_the_page_or_the_url():
    """NEGATIVE, so unreachability cannot defeat it. Baking the key into the
    page publishes it to anyone who opens devtools; putting it in the query
    string writes it into every proxy and access log between here and the
    browser."""
    assert "export.csv?key=" not in PAGE
    assert "RCA_EXPORT_KEY=" not in PAGE


def test_a_rejected_key_is_cleared_rather_than_retried_forever():
    assert "sessionStorage.removeItem('rcaExportKey')" in PAGE


def test_the_button_tries_bare_before_it_asks_for_anything():
    """Prompting up front would demand a key from everyone to protect a door
    in a building with no walls. The prompt is reached only from a 401, which
    the server returns only when a key really is configured."""
    assert "resp.status === 401" in PAGE
    assert "'This export needs a key" in PAGE


def test_the_button_reports_the_counts_it_was_given():
    """A file quietly holding 40 of 45 rows looks exactly like a complete one
    once it is open in Excel."""
    assert "X-Export-Rows" in PAGE and "X-Export-Failed" in PAGE
    assert "incomplete" in PAGE


def test_an_open_serve_is_announced_in_the_log(client, monkeypatch, caplog):
    """SURVIVED A MUTATION: deleting the warning killed nothing.

    That warning is the entire justification for serving without a key. The
    argument was never "an open export is fine" — it was "open, but ANNOUNCED
    rather than silent", which is what separates this from the _vs_auth
    pattern it deliberately does not copy. With the log gone, an unguarded
    export leaves no trace anywhere except a response header nobody reads, and
    a deployment that quietly lost its key looks exactly like one that never
    had it configured. Nothing was holding it.
    """
    import logging
    monkeypatch.delenv("RCA_EXPORT_KEY", raising=False)
    with caplog.at_level(logging.WARNING, logger="server.api"):
        assert client.get("/api/export.csv").status_code == 200
    said = " ".join(r.message % r.args if r.args else r.message
                    for r in caplog.records)
    assert "NO key" in said, f"an open serve logged nothing: {said!r}"
    assert "RCA_EXPORT_KEY" in said, "it did not name what would close it"


def test_a_guarded_serve_does_not_cry_wolf(client, monkeypatch, caplog):
    """The converse. A warning on every download regardless of mode is noise,
    and noise is how a real one gets scrolled past."""
    import logging
    monkeypatch.setenv("RCA_EXPORT_KEY", "s3cret")
    with caplog.at_level(logging.WARNING, logger="server.api"):
        assert client.get("/api/export.csv",
                          headers={"X-Export-Key": "s3cret"}).status_code == 200
    said = " ".join(r.message for r in caplog.records)
    assert "NO key" not in said, f"a guarded serve warned anyway: {said!r}"
