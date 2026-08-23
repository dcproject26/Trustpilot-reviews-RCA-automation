"""The requester search casts by the guest's email, so it also pulls the
guest's earlier trips. A July ticket about another booking then sat at the top
of an August booking's timeline — wrong ticket, and a chronology that reads as
"this happened before the booking was made".

The fix: a ticket whose newest activity predates booking.bookedOn is a prior
trip. It is dropped from the timeline and REPORTED (never silently), and when
the booking has no usable date the filter says it did not run rather than
looking like it found nothing to drop.
"""
from datetime import datetime, timezone

from server.services import zendesk as Z


# ── pure helpers ─────────────────────────────────────────────────────────────

def test_cutoff_parses_a_real_booking_date():
    cut, reason = Z._booking_cutoff("2026-08-19T05:26:00Z")
    assert reason == ""
    assert cut == datetime(2026, 8, 19, 5, 26, tzinfo=timezone.utc)


def test_cutoff_date_only_is_midnight_utc():
    cut, reason = Z._booking_cutoff("2026-08-19")
    assert reason == ""
    assert cut == datetime(2026, 8, 19, 0, 0, tzinfo=timezone.utc)


def test_a_missing_date_is_did_not_run_not_found_nothing():
    cut, reason = Z._booking_cutoff("")
    assert cut is None
    assert "did not run" in reason
    # Distinct from the unparseable-date branch below: an ABSENT date and a
    # date we could not read are different facts, and the empty branch must not
    # collapse into the parse branch's wording.
    assert "has no booked-on date" in reason
    assert "could not" not in reason


def test_an_unparseable_date_says_so_distinctly():
    cut, reason = Z._booking_cutoff("last tuesday")
    assert cut is None
    assert "could not" in reason and "last tuesday" in reason


def test_a_july_ticket_predates_an_august_booking():
    cut = Z._sort_key("2026-08-19T00:00:00Z")
    assert Z._is_prior_trip(Z._sort_key("2026-07-06T17:42:00Z"), cut) is True


def test_a_ticket_after_the_booking_is_not_a_prior_trip():
    cut = Z._sort_key("2026-08-19T00:00:00Z")
    assert Z._is_prior_trip(Z._sort_key("2026-08-20T10:00:00Z"), cut) is False


def test_an_undatable_ticket_is_kept_not_guessed():
    cut = Z._sort_key("2026-08-19T00:00:00Z")
    assert Z._is_prior_trip(None, cut) is False
    assert Z._is_prior_trip(Z._SORT_MAX, cut) is False


def test_no_cutoff_never_calls_anything_a_prior_trip():
    assert Z._is_prior_trip(Z._sort_key("2026-07-06T00:00:00Z"), None) is False


# ── the wiring: _get_timeline_sync actually drops and reports ────────────────

class _Cmt:
    def __init__(self, ts, body):
        self.created_at = ts
        self.body = body
        self.public = True
        self.author_id = -1                       # system id: no role lookup
        self.via = type("V", (), {"channel": "email"})()


class _Ticket:
    def __init__(self, tid, comments):
        self.id = tid
        self._comments = comments
        self.tags = []
        self.brand_id = None
        self.requester_id = None                   # no requester-name lookup
        self.custom_fields = []


class _Client:
    def __init__(self, tickets):
        self._by_id = {str(t.id): t for t in tickets}
        self.tickets = self                        # _z.tickets.comments(...)

    def comments(self, ticket=None):
        return list(self._by_id[str(ticket)]._comments)

    def users(self, id=None):
        return type("U", (), {"name": "", "role": ""})()

    def search(self, query="", type="ticket"):
        return []


def _wire(monkeypatch, tickets):
    ids = list(tickets)
    monkeypatch.setattr(Z, "_search_with_retry",
                        lambda _z, q: ids if "fieldvalue:" in q else [])
    monkeypatch.setattr(Z, "side_conversations", lambda tid: [])
    return _Client(tickets)


def test_a_prior_trip_ticket_is_dropped_and_reported(monkeypatch):
    jul = _Ticket("33535069", [_Cmt("2026-07-06T17:42:00Z",
                                     "invalid QR, refund on wrong booking 32358141")])
    aug = _Ticket("34806407", [_Cmt("2026-08-19T05:26:00Z",
                                     "guest submitted BMS link")])
    client = _wire(monkeypatch, [jul, aug])

    raw, _extracted, meta = Z._get_timeline_sync(
        client, "33543686", booked_on="2026-08-19T05:26:00Z")

    tids = {e["ticket_id"] for e in raw}
    assert "34806407" in tids, "the current ticket's events went missing"
    assert "33535069" not in tids, "the July prior-trip ticket leaked in"

    excluded = {e["ticket_id"] for e in meta["prior_trip_excluded"]}
    assert excluded == {"33535069"}, meta["prior_trip_excluded"]
    assert meta["prior_trip_excluded"][0]["last_activity"].startswith("2026-07-06")
    assert meta["prior_trip_reason"] == ""          # the filter ran
    assert "33535069" not in meta["ticket_ids"]
    assert "34806407" in meta["ticket_ids"]


def test_without_a_booking_date_nothing_is_dropped_and_it_is_said(monkeypatch):
    jul = _Ticket("33535069", [_Cmt("2026-07-06T17:42:00Z", "x")])
    client = _wire(monkeypatch, [jul])

    raw, _extracted, meta = Z._get_timeline_sync(client, "33543686", booked_on="")

    assert {e["ticket_id"] for e in raw} == {"33535069"}   # kept — can't judge
    assert meta["prior_trip_excluded"] == []
    assert meta["prior_trip_reason"]                       # did-not-run, said


def test_a_ticket_straddling_the_booking_is_kept_whole(monkeypatch):
    # Old first comment, new last comment: the newest is after the booking, so
    # the ticket is live around it and stays entirely.
    strad = _Ticket("111", [_Cmt("2026-07-01T00:00:00Z", "old"),
                            _Cmt("2026-08-25T00:00:00Z", "new")])
    client = _wire(monkeypatch, [strad])

    raw, _extracted, meta = Z._get_timeline_sync(
        client, "33543686", booked_on="2026-08-19")

    assert "111" in {e["ticket_id"] for e in raw}
    assert meta["prior_trip_excluded"] == []


def test_a_prior_trip_side_conversation_is_also_dropped(monkeypatch):
    # Side conversations are gathered in a SEPARATE loop; a prior-trip ticket's
    # SP thread is not this booking's either and must be dropped too.
    jul = _Ticket("900", [_Cmt("2026-07-06T00:00:00Z", "old chat")])
    monkeypatch.setattr(Z, "_search_with_retry",
                        lambda _z, q: [jul] if "fieldvalue:" in q else [])
    monkeypatch.setattr(Z, "side_conversations", lambda tid: [
        {"subject": "vendor", "participants": "", "messages": [
            {"_raw_ts": "2026-07-06T01:00:00Z", "time": "",
             "actor": "sp", "body": "vendor msg"}]}])

    raw, _extracted, meta = Z._get_timeline_sync(
        _Client([jul]), "33543686", booked_on="2026-08-19")

    assert all(e["thread"] != "sp" for e in raw), "prior-trip SP thread leaked in"
    assert "900" in {e["ticket_id"] for e in meta["prior_trip_excluded"]}
