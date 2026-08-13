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
    # THE DIAGNOSIS AND THE REMEDY ARE ASSERTED SEPARATELY, and the second one
    # is why. Deleting the remedy clause SURVIVED a mutation, because the
    # original assertion looked for "RCA_EXPORT_KEY" — which still appears in
    # the diagnosis half of the same sentence. It was matching a string
    # elsewhere in the same output, which is a check on nothing.
    # "X-Export-Key" is the header name and appears ONLY in the remedy.
    assert "RCA_EXPORT_KEY is unset" in said, "it did not name the cause"
    assert "X-Export-Key" in said, \
        f"it named the cause but not what would fix it: {said!r}"


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


# ── the vendor column matched nothing ──────────────────────────────────────

def _bare_review():
    from types import SimpleNamespace as NS
    from datetime import datetime
    return NS(id="tp_v", received_at=datetime(2026, 8, 1), author="A", rating=1,
              language="English", status="draft", slack_channel="C1",
              slack_ts="1.0", close_reason=None, sent_route=None,
              body_original="x", body_english="x", reference_number="32728059")


def _bare_draft(booking):
    from types import SimpleNamespace as NS
    return NS(booking=booking, rca_v3={}, insights={}, scenarios=[],
              sub_themes=[], l1="", l2="", match_tier=None, match_method=None,
              resolution="", final_response="", suggested_response="",
              rca_prompt_version="", zendesk_ticket_ids=[], rca_posted_at=None,
              sent_at=None, guest_issues=[], flags=[], takedown=None,
              primary_scenario=None, overlay_scenarios=[])


def test_a_warehouse_booking_still_names_its_vendor():
    """THE JOIN THAT MATCHED NOTHING. `bigquery._row_to_dict` writes `partner`;
    this export read `vendorName`, which only `verify_bid` writes. So whenever
    that second lookup returned nothing the cell was blank and read as "no
    vendor" — indistinguishable from a booking with none.

    `experienceName` beside it DOES match, which is why the column looked
    healthy in every eyeball check."""
    from server.services.sheet_export import row_for
    row = row_for(_bare_review(), _bare_draft(
        {"id": "32728059", "partner": "Vendor Ltd",
         "experienceName": "Colosseum Tour"}))
    assert row["vendor"] == "Vendor Ltd", row
    assert row["experience"] == "Colosseum Tour", row


def test_the_verified_shape_still_names_its_vendor():
    """The other builder, so the fix cannot regress into reading only
    `partner` — two shapes reach this function and both must work."""
    from server.services.sheet_export import row_for
    row = row_for(_bare_review(), _bare_draft(
        {"id": "32728059", "vendorName": "Vendor Ltd"}))
    assert row["vendor"] == "Vendor Ltd", row


def test_a_booking_with_no_vendor_at_all_is_still_empty():
    """Paired, so the cell cannot be made unconditionally non-empty."""
    from server.services.sheet_export import row_for
    row = row_for(_bare_review(), _bare_draft({"id": "32728059"}))
    assert row["vendor"] == ""


# ── insights: five keys that never matched ─────────────────────────────────

REAL_INSIGHTS = {"similar_reviews_30d": 4, "rating_tgid": {"avg": 4.2, "n": 31},
                 "tgid_completion_rate": 0.87, "redemption": "QR",
                 "similar_support_queries_30d": 2, "review_ratio": 0.1}


def test_the_insights_cell_reads_the_payload_that_is_actually_stored():
    """IT READ NONE OF IT. The five keys were camelCase — `tgidRating`,
    `completion`, `sameDayIssues`, `similarReviews`, `similarQueries` — and
    `insights.compute()` stores snake_case. Not one matched, so the cell was
    "" on every real draft, which reads as "the warehouse had nothing".

    The contact-note join from CLAUDE.md, again: "ZD-4491" against "4491"."""
    from server.services.sheet_export import _insights_cell
    cell = _insights_cell(REAL_INSIGHTS)
    assert "TGID rating 4.2 (n=31)" in cell, cell
    assert "Similar reviews 30d 4" in cell, cell
    assert "Redemption QR" in cell, cell


def test_an_empty_payload_is_an_empty_cell():
    from server.services.sheet_export import _insights_cell
    assert _insights_cell({}) == ""
    assert _insights_cell(None) == ""


def test_a_payload_that_matches_nothing_says_so_rather_than_looking_empty():
    """THE INVERSE BUG, and the one that hid this for so long. A stored
    payload whose keys we cannot read is a BROKEN JOIN; an absent payload is a
    booking the warehouse knew nothing about. They must not produce the same
    cell — that is the whole first rule of this codebase."""
    from server.services.sheet_export import _insights_cell
    cell = _insights_cell({"tgidRating": {"value": 4.2}, "completion": "87%"})
    assert cell != "", "a broken join produced an honest-looking empty cell"
    assert "unreadable" in cell, cell


# ── the parallel lists lined up ────────────────────────────────────────────

def test_issues_owners_and_accuracy_stay_in_step():
    """They did not. `owners` and `claim_accuracy` filtered blanks while
    `issues` kept every row, so three issues with a middle one lacking an
    owner exported 3, 2 and 2 — and a reader pairing them by position gave
    issue two's title issue three's owner."""
    from server.services.sheet_export import row_for
    v3 = {"what_went_wrong": {"guest_issues": [
        {"issue": "A", "owner": "OPS", "claim_accuracy": "Accurate"},
        {"issue": "B"},
        {"issue": "C", "owner": "TECH", "claim_accuracy": "Inaccurate"}]}}
    d = _bare_draft({})
    d.rca_v3 = v3
    row = row_for(_bare_review(), d)
    assert len(row["issues"]) == 3, row["issues"]
    assert len(row["owners"]) == 3, row["owners"]
    assert len(row["claim_accuracy"]) == 3, row["claim_accuracy"]
    assert row["owners"][1] == "(none)", row["owners"]
    assert row["owners"][2] == "TECH", row["owners"]


# ── a cell must not be executable ──────────────────────────────────────────

@pytest.mark.parametrize("evil", ["=cmd|' /C calc'!A0", "+1+1", "@SUM(A1)",
                                  "-1+1", "\t=1+1"])
def test_a_formula_in_a_guest_written_cell_is_not_executable(evil):
    """`author` is the reviewer's own display name and `review_text` is their
    review. Both reach the file unaltered, and Excel and Sheets evaluate a cell
    beginning `=`, `+`, `-`, `@` or a tab when the file is opened."""
    from server.services.sheet_export import _s
    out = _s(evil)
    assert out.startswith("'"), out
    assert evil in out, "the guest's text was altered, not just defused"


def test_ordinary_text_is_left_exactly_as_written():
    """Paired, so the guard cannot be made unconditional — an apostrophe on
    every cell would be a new kind of wrong."""
    from server.services.sheet_export import _s
    for ok in ["Ioan Popescu", "They never arrived", "4.2", ""]:
        assert _s(ok) == ok


def test_a_draft_with_only_the_v4_columns_still_exports_its_issues():
    """TWO STORES FOR ONE FACT, and the export read the one that is not
    always written. It took the raw `rca_v3` blob, so a draft carrying the v4
    projection COLUMNS and an empty blob exported issue_count 0 with blank
    flags and takedown — while the card and the Slack post showed all three
    off the same object. `_resolve_v3_sections`' own docstring names this
    export as a reader that must agree with them."""
    from server.services.sheet_export import row_for
    d = _bare_draft({})
    d.rca_v3 = {}                        # the blob is empty …
    d.guest_issues = [{"issue": "Tickets never arrived", "owner": "OPS",
                       "claim_accuracy": "Accurate"}]
    d.flags = [{"flag": "No follow-up", "team": "CO"}]
    d.takedown = {"verdict": "Yes"}      # … and the columns are not
    row = row_for(_bare_review(), d)
    assert row["issue_count"] == 1, row["issue_count"]
    assert row["issues"] == ["Tickets never arrived"], row["issues"]
    assert row["flags"] == ["No follow-up"], row["flags"]
    assert row["takedown"] == "Yes", row["takedown"]


def test_the_blob_still_wins_when_it_is_the_one_that_is_populated():
    """Paired: resolving must not start ignoring the blob, which is what
    every older draft has."""
    from server.services.sheet_export import row_for
    d = _bare_draft({})
    d.rca_v3 = {"what_went_wrong": {"guest_issues": [
        {"issue": "From the blob", "owner": "TECH"}]}}
    row = row_for(_bare_review(), d)
    assert row["issues"] == ["From the blob"], row["issues"]


# ── the card's own sections, which the export did not carry ────────────────

_FULL_V3 = {
    "stated_issue": "Tickets never arrived",
    "what_went_wrong": {
        "guest_issues": [{"issue": "Tickets late", "owner": "OPS"}],
        "case_findings": [{"time": "02 Aug 09:13",
                           "text": "Tickets sent to the wrong email",
                           "source": "bms"}],
        "fixes": [{"action": "Alert on delivery failure", "owner": "TECH"}],
        "gaps": [{"gap": "No alerting", "team": "TECH", "source_ref": "ZD-1"}]},
    "area_of_improving": [{"area": "Delivery checks", "team": "OPS"}],
    "issue_specific_answers": [{"question": "Was it resent?", "answer": "Yes"}],
    "support_interaction_notes": [{"zd_ref": "ZD-1", "note": "Agent resent"}],
    "sp_interaction_notes": {"records": [{"note": "Vendor confirmed"}]},
    "actions_taken": {"co": [{"action": "Apologised"}],
                      "tech": [{"action": "Raised alert"}]},
    "dss": {"prescribes": "Resend", "ref": "DSS-12", "followed": "followed"},
}


def _full_row():
    from server.services.sheet_export import row_for
    d = _bare_draft({"id": "1", "partner": "Krakville", "bookedOn": "2026-07-30",
                     "pax": 2, "fulfilmentType": "QR", "vidName": "Main gate"})
    d.rca_v3 = _FULL_V3
    d.support_summary = "The arc"
    d.match_confidence = 0.9
    d.ticket_facts = {"booking_status": "CONFIRMED"}
    d.suggested_response = "We are sorry"
    d.response_english = "We are sorry"
    d.overlay_scenarios = ["Refund"]
    r = _bare_review()
    return row_for(r, d)


def test_the_rca_itself_reaches_the_export():
    """It carried issue TITLES and none of the RCA: no §1, no fixes, no gaps,
    no contact notes. A reader could count the issues on a review and not read
    one of them."""
    row = _full_row()
    assert "Tickets sent to the wrong email" in row["case_findings"], row["case_findings"]
    assert "02 Aug 09:13" in row["case_findings"]
    assert "Alert on delivery failure" in row["fixes"]
    assert "No alerting" in row["gaps"]
    assert "Delivery checks" in row["area_of_improving"]
    assert "Was it resent?" in row["issue_answers"]
    assert "Agent resent" in row["contact_notes"]
    assert "Vendor confirmed" in row["sp_notes"]
    assert row["support_summary"] == "The arc"
    assert row["stated_issue"] == "Tickets never arrived"


def test_actions_stay_attached_to_the_team_that_owns_them():
    """Flattening them loses the only thing that makes an action answerable.
    "Refund issued" means something different under FINANCE than under CO."""
    row = _full_row()
    assert "CO: Apologised" in row["actions_taken"], row["actions_taken"]
    assert "TECH: Raised alert" in row["actions_taken"], row["actions_taken"]


def test_the_booking_details_are_all_there():
    row = _full_row()
    assert row["booked_on"] == "2026-07-30"
    assert row["pax"] == 2
    assert row["fulfilment_type"] == "QR"
    assert row["vid_name"] == "Main gate"
    assert row["booking_status"] == "CONFIRMED"
    assert row["reference_number"] == "32728059"
    assert row["match_confidence"] == 0.9
    assert row["dss_prescribes"] == "Resend"
    assert row["dss_ref"] == "DSS-12"


def test_the_draft_and_the_sent_reply_are_both_kept():
    """The difference between them is the whole record of human review."""
    row = _full_row()
    assert row["suggested_response"] == "We are sorry"
    assert row["response_english"] == "We are sorry"


def test_a_digest_never_reaches_the_guest_name_column():
    """A hash in a column headed `guest_name` is worse than a blank: it looks
    like a value somebody could search on. One rule for that lives in
    names.looks_like_digest and this export goes through it."""
    from server.services.sheet_export import row_for
    d = _bare_draft({"id": "1", "primary_guest_name": "ab24TSVenneb4T3CkHFUFaGM"})
    assert row_for(_bare_review(), d)["guest_name"] == ""


def test_a_real_guest_name_does_reach_it():
    """Paired, so the column cannot be made unconditionally empty."""
    from server.services.sheet_export import row_for
    d = _bare_draft({"id": "1", "guestName": "Gianmarco Lucia"})
    assert row_for(_bare_review(), d)["guest_name"] == "Gianmarco Lucia"


def test_every_column_is_produced_by_the_row_builder():
    """A column in COLUMNS that row_for never writes shifts nothing — csv
    writes it blank — but it reads as a fact we did not have, on every row."""
    from server.services.sheet_export import COLUMNS
    row = _full_row()
    missing = [c for c in COLUMNS if c not in row and c != "export_error"]
    assert missing == [], missing


# ── every download carries the header ──────────────────────────────────────

def test_the_first_line_is_the_header_exactly():
    """The file gets imported into a sheet. Without a header row the columns
    are unlabelled and the first REVIEW becomes the header — 66 cells of
    someone's review text as column names."""
    import csv
    import io

    from server.services.sheet_export import COLUMNS, to_csv
    first = next(csv.reader(io.StringIO(to_csv([{"review_id": "tp_1"}]))))
    assert first == list(COLUMNS), first


def test_an_empty_export_is_still_a_header_not_an_empty_file():
    """A month with no reviews must download as COLUMNS and nothing else. An
    empty file imports as nothing at all, which reads as "the export is
    broken" rather than "there were no rows"."""
    import csv
    import io

    from server.services.sheet_export import COLUMNS, to_csv
    parsed = list(csv.reader(io.StringIO(to_csv([]))))
    assert len(parsed) == 1, parsed
    assert parsed[0] == list(COLUMNS)


def test_the_header_survives_a_row_that_failed_to_build():
    """The failed-row path writes fewer keys. The header is COLUMNS either
    way, or the file misaligns from that row onward."""
    import csv
    import io

    from server.services.sheet_export import COLUMNS, to_csv
    out = to_csv([{"review_id": "tp_1", "export_error": "ValueError: boom"}])
    parsed = list(csv.reader(io.StringIO(out)))
    assert parsed[0] == list(COLUMNS)
    assert len(parsed[1]) == len(COLUMNS), "the row is not header-width"
    assert parsed[1][COLUMNS.index("export_error")] == "ValueError: boom"
