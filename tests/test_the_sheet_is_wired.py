"""Every review reaches the sheet, in two phases, from both endings.

WHAT WAS WRONG WITH IT. `sheet_export.py` had seven functions, 27 passing
tests and ZERO callers. No endpoint, no pipeline hook, no config key. It was
built and wired into nothing — the failure CLAUDE.md opens with, sitting in
the tree while its own suite went green.

TWO PHASES, AND THE ORDER IS LOad-BEARING:

  arrival     the row is created with the id, the arrival time and the Slack
              link. Everything else is blank and `stage` says "received".
  completion  the same row is filled in when the RCA is sent or the review is
              closed out, and `stage` says "sent".

THAT ORDER IS WHAT MAKES IT SAFE FOR SEVERAL PEOPLE AT ONCE. Appends are the
racy operation: two processes reading column A, neither seeing the other, both
appending. Arrival creates the row, so every completion is an UPDATE against a
row number nothing shifts — and the completion write REFUSES to append, so a
missing arrival row is reported rather than papered over and then raced into a
duplicate.
"""
from datetime import datetime

import pytest

from server.services import sheet_export as X


class FakeIO:
    """The Sheets API as three methods, which is all `export` uses."""

    def __init__(self, header=None, ids=None):
        self.header = list(header) if header is not None else list(X.COLUMNS)
        self.ids = list(ids or [])
        self.updated, self.appended, self.header_written = [], [], False

    def read_column_a_and_header(self):
        return self.header, self.ids

    def write_header(self):
        self.header_written = True

    def update_rows(self, updates):
        self.updated.extend(updates)

    def append_rows(self, rows):
        self.appended.extend(rows)


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
        self.__dict__.update(kw)


class D:
    booking = {"id": "32885089", "tid": "19354", "vid": "4045",
               "tgid": "15406", "experienceName": "Auschwitz tour",
               "vendorName": "Krakville"}
    insights = {"tgidRating": {"value": "4.2"},
                "similarReviews": {"value": "41"}}
    rca_v3 = {"what_went_wrong": {"guest_issues": []}, "flags": [],
              "takedown": {"verdict": "No"}}
    l1, l2 = "Operations Issue", "Ticket Issues"
    sub_themes, scenarios = [], []
    match_tier, match_method = 2, "name"
    resolution = "wallet credit"
    zendesk_ticket_ids = ["33978941"]
    rca_posted_at = None
    sent_at = datetime(2026, 8, 3, 9, 0)
    final_response = "sorry"
    suggested_response = "sorry"
    rca_prompt_version = "rca_v4+abc"


def _cell(row, col):
    return X.to_cells(row)[X.COLUMNS.index(col)]


# ── phase one: what a review has the moment it lands ───────────────────────

def test_the_arrival_row_carries_id_time_and_link():
    row = X.arrival_row(R())
    assert _cell(row, "review_id") == "tp_1"
    assert "2026-08-02 12:06" in _cell(row, "received_at")
    assert _cell(row, "slack_link").endswith("/p1690000000123456")


def test_the_arrival_row_says_it_is_one():
    """A row a person opens ten minutes after the review landed must say
    "received", not read like an export that gave up halfway through a
    completed one."""
    assert _cell(X.arrival_row(R()), "stage") == "received"


def test_the_arrival_row_leaves_the_rca_columns_empty():
    row = X.arrival_row(R())
    for col in ("booking_id", "l1", "resolution", "sent_at", "insights"):
        assert _cell(row, col) == "", col


def test_a_review_that_did_not_come_from_slack_gets_no_link():
    """C_MANUAL and VECTORSHIFT are synthetic channels. A link into them 404s,
    which is worse than no link."""
    assert _cell(X.arrival_row(R(slack_channel="C_MANUAL")), "slack_link") == ""


# ── phase two: the same row, filled in ─────────────────────────────────────

def test_the_completed_row_carries_the_booking_and_the_classification():
    row = X.row_for(R(), D(), stage=X.DONE)
    assert _cell(row, "booking_id") == "32885089"
    assert _cell(row, "experience") == "Auschwitz tour"
    assert _cell(row, "vendor") == "Krakville"
    assert _cell(row, "l1") == "Operations Issue"
    assert _cell(row, "stage") == "sent"


def test_the_insights_are_a_cell_a_person_can_read():
    """A JSON dump is not something anyone pivots on."""
    got = _cell(X.row_for(R(), D(), stage=X.DONE), "insights")
    assert got == "TGID rating 4.2; Similar reviews 41", got


def test_the_arrival_facts_survive_the_second_write():
    """The completion row overwrites the whole row, so it has to carry the
    arrival columns too or they are blanked."""
    row = X.row_for(R(), D(), stage=X.DONE)
    assert "2026-08-02 12:06" in _cell(row, "received_at")
    assert _cell(row, "slack_link").endswith("/p1690000000123456")


# ── the completion write never appends ─────────────────────────────────────

def test_a_completion_with_no_arrival_row_is_reported_not_appended():
    """Appending would hide a missing arrival hook AND race a late arrival
    write into a duplicate."""
    io = FakeIO(ids=[])
    out = X.export(io, [X.row_for(R(), D(), stage=X.DONE)], apply=True,
                   require_existing=True)
    assert io.appended == [], io.appended
    assert out["orphans"] == ["tp_1"], out


def test_a_completion_with_an_arrival_row_updates_it():
    io = FakeIO(ids=["tp_1"])
    out = X.export(io, [X.row_for(R(), D(), stage=X.DONE)], apply=True,
                   require_existing=True)
    assert out["updated"] == 1 and out["orphans"] == []
    assert io.updated[0][0] == 2, "row 2 is the first row under the header"


def test_arrival_still_appends():
    """It is the write that CREATES the row; refusing here would mean nothing
    ever reaches the sheet."""
    io = FakeIO(ids=[])
    out = X.export(io, [X.arrival_row(R())], apply=True)
    assert out["appended"] == 1 and io.appended, out


# ── several people at once ─────────────────────────────────────────────────

def test_two_people_finishing_different_reviews_are_two_updates():
    """THE MULTI-USER CASE. Both are updates against row numbers nothing
    shifts, so neither can overwrite the other — which is the whole reason
    arrival creates the row first."""
    io = FakeIO(ids=["tp_1", "tp_2"])
    out = X.export(io, [X.row_for(R("tp_1"), D(), stage=X.DONE),
                        X.row_for(R("tp_2"), D(), stage=X.DONE)],
                   apply=True, require_existing=True)
    assert out["updated"] == 2 and out["appended"] == 0
    assert sorted(n for n, _ in io.updated) == [2, 3]


def test_a_duplicate_already_in_the_sheet_is_named_not_silently_half_updated():
    io = FakeIO(ids=["tp_1", "tp_1"])
    out = X.export(io, [X.row_for(R(), D(), stage=X.DONE)], apply=True,
                   require_existing=True)
    assert out["duplicates"] == ["tp_1"], out


# ── refusals are reports, never exceptions ─────────────────────────────────

def test_a_mismatched_header_refuses_before_a_single_cell_moves():
    io = FakeIO(header=["review_id", "something_else"])
    out = X.export(io, [X.arrival_row(R())], apply=True)
    assert out["refused"], out
    assert io.updated == [] and io.appended == []


def test_an_empty_sheet_gets_the_header():
    io = FakeIO(header=[])
    out = X.export(io, [X.arrival_row(R())], apply=True)
    assert out["header_written"] and io.header_written


# ── unconfigured is said, not silently skipped ─────────────────────────────

def test_an_unconfigured_export_says_so(monkeypatch):
    """The state every environment starts in. Unconfigured and broken must not
    look alike."""
    import server.config as cfg
    monkeypatch.setattr(cfg, "RCA_EXPORT_SHEET_ID", "")
    monkeypatch.setattr(cfg, "is_live", lambda svc: False)
    assert X.on_review_arrived(R()) == {"skipped": "not configured"}


def test_a_write_that_raises_does_not_reach_the_caller(monkeypatch):
    """A sheet that is unshared, renamed or rate-limited must not fail a send:
    the review going out matters and the row does not."""
    import server.config as cfg
    monkeypatch.setattr(cfg, "is_live", lambda svc: True)

    def _boom(*a, **k):
        raise RuntimeError("403 the sheet is not shared")
    monkeypatch.setattr(X, "SheetIO", _boom)
    got = X.on_review_finished(R(), D())
    assert "403" in got.get("failed", ""), got


# ── a gid is not a tab name ────────────────────────────────────────────────

def test_a_named_tab_is_left_alone():
    io = X.SheetIO("sid", "Reviews")
    assert io.resolve_tab() == "Reviews"


def test_a_gid_is_resolved_to_the_tab_name(monkeypatch):
    """A URL carries `#gid=0` and that is what anyone pastes. The values API
    takes a NAME and has no idea what a gid is, so a configured "0" would be
    read as a tab literally called 0."""
    io = X.SheetIO("sid", "0")
    monkeypatch.setattr(io, "_hdr", lambda: {})

    class _Resp:
        @staticmethod
        def raise_for_status():
            pass

        @staticmethod
        def json():
            return {"sheets": [{"properties": {"sheetId": 0, "title": "RCA log"}},
                               {"properties": {"sheetId": 9, "title": "Other"}}]}
    import httpx
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp())
    assert io.resolve_tab() == "RCA log"


def test_a_gid_that_is_not_there_is_not_guessed(monkeypatch):
    """Inventing a fallback tab is how an export writes a month of rows into
    the wrong one."""
    io = X.SheetIO("sid", "77")
    monkeypatch.setattr(io, "_hdr", lambda: {})

    class _Resp:
        @staticmethod
        def raise_for_status():
            pass

        @staticmethod
        def json():
            return {"sheets": [{"properties": {"sheetId": 0, "title": "RCA log"}}]}
    import httpx
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp())
    assert io.resolve_tab() == "77"


# ── the hooks are called by the endpoints ──────────────────────────────────
#
# The whole defect was a module nothing called. A test of the functions would
# have passed the entire time it was dead.

@pytest.fixture()
def client(live_db):
    from fastapi.testclient import TestClient
    from server.main import app
    from server.db import get_session
    app.dependency_overrides[get_session] = lambda: live_db.SessionLocal()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _seed(db, rid):
    s = db.SessionLocal()
    s.add(db.Review(id=rid, rating=1, author="A", body_original="b",
                    status="draft", received_at=datetime.utcnow()))
    s.add(db.RcaDraft(id=f"d_{rid}", review_id=rid, booking={"id": "1"}))
    s.commit(); s.close()


def test_send_calls_the_completion_hook(live_db, client, monkeypatch):
    seen = []
    monkeypatch.setattr(X, "on_review_finished",
                        lambda r, d: seen.append(getattr(r, "id", None)))
    _seed(live_db, "tp_send")
    client.post("/api/reviews/tp_send/send")
    assert seen == ["tp_send"], seen


def test_close_calls_it_too(live_db, client, monkeypatch):
    """A review closed out without posting is finished, and a sheet that only
    records the ones that were POSTED is a sheet missing every abandoned
    case — which is the half worth counting."""
    seen = []
    monkeypatch.setattr(X, "on_review_finished",
                        lambda r, d: seen.append(getattr(r, "id", None)))
    _seed(live_db, "tp_close")
    client.post("/api/reviews/tp_close/close", json={})
    assert seen == ["tp_close"], seen


def test_ingest_calls_the_arrival_hook(live_db, client, monkeypatch):
    """SURVIVED A MUTATION. Removing the ingest hook passed every test here,
    because they all drove the FUNCTIONS. That is exactly the shape the whole
    module was in — seven of them, 27 green tests, called by nothing — so a
    test of the hook that does not walk the endpoint repeats the defect it is
    meant to close.

    Drives /refresh-slack with a stubbed Slack read, which is the only path
    that creates a Review row."""
    import server.services.slack as slk
    from server.config import SLACK_CHANNEL_ORM  # noqa: F401

    seen = []
    monkeypatch.setattr(X, "on_review_arrived",
                        lambda r: seen.append(getattr(r, "id", None)))

    msg = {"ts": "1690000000.123456", "text": "1 star — tickets never came",
           "channel": "C_ORM"}

    class _Client:
        @staticmethod
        def conversations_history(**kw):
            return {"messages": [msg]}

    monkeypatch.setattr(slk, "_bot", _Client)
    monkeypatch.setattr(slk, "_user", None)
    monkeypatch.setattr("server.config.SLACK_CHANNEL_ORM", "C_ORM")
    monkeypatch.setattr(slk, "is_trustpilot_message", lambda m: True)
    monkeypatch.setattr(slk, "parse_review", lambda m: {
        "slack_ts": m["ts"], "slack_channel": "C_ORM", "rating": 1,
        "language": "en", "author": "Roisin",
        "body_original": "tickets never came", "reference_number": None,
        "published_at": None, "published_at_source": ""})

    r = client.post("/api/reviews/refresh-slack?hours=1")
    assert r.status_code == 200, r.text
    assert seen, "a review was ingested and no arrival row was written"
