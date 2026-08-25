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


# ── the booking-date key, under whatever name the match path used ────────────

def test_booking_date_reads_bookedon():
    assert Z._booking_date({"bookedOn": "2026-08-21"}) == "2026-08-21"


def test_booking_date_falls_back_to_date_of_booking():
    # verify_bid (the direct/confirmed-BID path) names it date_of_booking, NOT
    # bookedOn — reading only bookedOn is what made the filter silently skip.
    assert Z._booking_date(
        {"date_of_booking": "2026-08-21 10:34:00+00:00"}) == "2026-08-21 10:34:00+00:00"


def test_booking_date_prefers_bookedon_when_both_present():
    assert Z._booking_date(
        {"bookedOn": "2026-08-21", "date_of_booking": "2026-07-01"}) == "2026-08-21"


def test_booking_date_is_empty_when_no_date_key():
    assert Z._booking_date({"id": "123"}) == ""
    assert Z._booking_date(None) == ""


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


def test_a_direct_bid_booking_with_only_date_of_booking_still_filters(monkeypatch):
    # The regression: verify_bid gives `date_of_booking`, not `bookedOn`. This
    # is exactly what get_timeline does — derive the date with _booking_date,
    # then run the sync. A July ticket must still drop even though the booking
    # dict has no `bookedOn` key at all.
    jul = _Ticket("33535069", [_Cmt("2026-07-06T17:42:00Z", "old antelope trip")])
    aug = _Ticket("34863785", [_Cmt("2026-08-21T10:36:00Z", "this booking")])
    client = _wire(monkeypatch, [jul, aug])

    booking = {"id": "33587369", "date_of_booking": "2026-08-21 10:34:00+00:00"}
    raw, _extracted, meta = Z._get_timeline_sync(
        client, "33587369", booked_on=Z._booking_date(booking))

    tids = {e["ticket_id"] for e in raw}
    assert "33535069" not in tids, "prior-trip ticket leaked (date_of_booking not read)"
    assert "34863785" in tids
    assert {e["ticket_id"] for e in meta["prior_trip_excluded"]} == {"33535069"}
    assert meta["prior_trip_reason"] == ""      # the filter RAN


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


# ── the ticket's own booking field ──────────────────────────────────────────
# The date cutoff can only catch a trip that ENDED before this booking existed.
# It is blind to the guest's other booking made in the same week, or after —
# the dates overlap, nothing fires, and the requester search puts that
# booking's tickets in this booking's timeline. That is the case still reported
# after the cutoff shipped.

class _FieldTicket(_Ticket):
    def __init__(self, tid, comments, names_booking=None):
        super().__init__(tid, comments)
        self.custom_fields = ([] if names_booking is None else
                              [{"id": 360021524471, "value": names_booking}])


def test_a_ticket_naming_another_booking_is_not_ours():
    assert Z.other_booking_named(
        _FieldTicket("1", [], names_booking="32938379"), "33543686") == "32938379"


def test_a_ticket_naming_this_booking_is_ours():
    assert Z.other_booking_named(
        _FieldTicket("1", [], names_booking="33543686"), "33543686") == ""


def test_an_empty_booking_field_is_not_evidence():
    """It is frequently empty — bids_from_ticket_text exists because of that.
    "No id on the ticket" means we do not know, not "it belongs elsewhere"."""
    assert Z.other_booking_named(_FieldTicket("1", []), "33543686") == ""
    assert Z.other_booking_named(
        _FieldTicket("1", [], names_booking=""), "33543686") == ""


def test_with_no_booking_id_of_our_own_nothing_is_excluded():
    """Nothing to compare against — excluding on that would drop every ticket."""
    assert Z.other_booking_named(
        _FieldTicket("1", [], names_booking="32938379"), "") == ""


def test_a_concurrent_other_booking_is_dropped_though_its_dates_overlap(monkeypatch):
    """THE GAP THE DATE CUTOFF LEAVES. Both tickets are active AFTER the
    booking was made, so the cutoff cannot fire on either — only the booking
    field tells them apart."""
    ours = _FieldTicket("111", [_Cmt("2026-08-22T10:00:00Z", "our issue")],
                        names_booking="33543686")
    theirs = _FieldTicket("222", [_Cmt("2026-08-23T10:00:00Z", "other trip")],
                          names_booking="32938379")
    client = _wire(monkeypatch, [ours, theirs])

    raw, _extracted, meta = Z._get_timeline_sync(
        client, "33543686", booked_on="2026-08-21")

    tids = {e["ticket_id"] for e in raw}
    assert "111" in tids, "this booking's own ticket went missing"
    assert "222" not in tids, \
        "a concurrent other-booking ticket is still in the timeline"
    assert meta["prior_trip_excluded"] == [], "it is not a date exclusion"
    assert {e["ticket_id"] for e in meta["other_booking_excluded"]} == {"222"}
    assert meta["other_booking_excluded"][0]["names_booking"] == "32938379"


def test_a_ticket_with_no_field_still_gets_the_date_test(monkeypatch):
    """The two filters compose: an empty field is not evidence, so such a
    ticket is still judged on its dates."""
    nofield = _FieldTicket("333", [_Cmt("2026-07-01T00:00:00Z", "old")])
    client = _wire(monkeypatch, [nofield])
    raw, _extracted, meta = Z._get_timeline_sync(
        client, "33543686", booked_on="2026-08-21")
    assert not raw
    assert {e["ticket_id"] for e in meta["prior_trip_excluded"]} == {"333"}
    assert meta["other_booking_excluded"] == []


# ── the subject fallback, when the booking field is empty ───────────────────
# MEASURED on the reported case. The free-text route — searching for the
# literal booking id — matched "[Support History] BID-32358051 — Mohammad
# Algouneh" while building 33543686's timeline: a digest about a different
# booking AND a different guest, which lists many booking ids in its body and
# so matches a text search for any of them. No date rule could catch that; the
# ticket is contemporaneous and belongs to a stranger.

class _SubjTicket(_Ticket):
    def __init__(self, tid, comments, subject="", names_booking=None):
        super().__init__(tid, comments)
        self.subject = subject
        self.custom_fields = ([] if names_booking is None else
                              [{"id": 360021524471, "value": names_booking}])


def other_booking_named_subject(t):
    """Every subject case below is judged against the same booking."""
    return Z.other_booking_named(t, "33543686")


def test_a_foreign_digest_with_no_field_is_caught_by_its_subject():
    """The hole the field check left: same digest, booking field unfilled."""
    t = _SubjTicket("1", [], subject="[Support History] BID-32358051 — Mohammad Algouneh")
    assert other_booking_named_subject(t) == "32358051"


def test_our_own_digest_is_kept():
    t = _SubjTicket("1", [], subject="[Support History] BID-33543686 — web User 6a8510b7")
    assert other_booking_named_subject(t) == ""


def test_a_phone_number_in_the_subject_is_not_read_as_a_booking_id():
    """One of this booking's OWN tickets is subject "Call with Caller +61 438
    474 311". A bare-digit rule reads a number like that as a booking id and
    throws away a real contact, which is why only LABELLED ids count.

    THE UNSPACED FORM IS THE ONE THAT DISCRIMINATES. The spaced original has no
    run of 7+ digits, so it survives a bare-number rule by accident and proves
    nothing about this guarantee — a mutation swapping the labelled pattern for
    a bare one passed against it. Phone numbers are written both ways, and the
    unspaced one is 11 straight digits: exactly a booking id's shape."""
    for subj in ("Call with Caller +61 438 474 311",
                 "Call with Caller +61438474311",
                 "Order 4471234567 shipped"):
        assert other_booking_named_subject(_SubjTicket("1", [], subject=subj)) == "", \
            f"a bare number in {subj!r} was read as another booking's id"


def test_an_ordinary_subject_names_nothing():
    t = _SubjTicket("1", [], subject="Tickets for Singapore River Sightseeing Cruise")
    assert other_booking_named_subject(t) == ""
    assert other_booking_named_subject(_SubjTicket("1", [], subject="")) == ""


def test_the_field_still_wins_over_the_subject():
    """The field is authoritative; the subject is only consulted when it is
    empty. A ticket correctly filed under THIS booking must not be dropped
    because its subject quotes another case."""
    t = _SubjTicket("1", [], subject="[Support History] BID-32358051 — someone",
                    names_booking="33543686")
    assert other_booking_named_subject(t) == ""


def test_a_foreign_digest_with_no_field_is_dropped_from_the_timeline(monkeypatch):
    """End to end: the free-text route's false match never reaches the events."""
    ours = _SubjTicket("34806407", [_Cmt("2026-08-22T10:00:00Z", "our issue")],
                       subject="Tickets for Singapore River Cruise",
                       names_booking="33543686")
    foreign = _SubjTicket("33535069", [_Cmt("2026-08-22T11:00:00Z", "other case")],
                          subject="[Support History] BID-32358051 — Mohammad Algouneh")
    client = _wire(monkeypatch, [ours, foreign])

    raw, _extracted, meta = Z._get_timeline_sync(
        client, "33543686", booked_on="2026-08-21")

    tids = {e["ticket_id"] for e in raw}
    assert "34806407" in tids
    assert "33535069" not in tids, \
        "a free-text false match with no booking field is still in the timeline"
    assert {e["ticket_id"] for e in meta["other_booking_excluded"]} == {"33535069"}


# ── epoch dates from the warehouse ──────────────────────────────────────────
# MEASURED on booking 33543686: booked_on arrived as "1.787097364E9". BigQuery
# hands the date back as a numeric and str() of it is scientific notation, which
# fromisoformat rejects — so the prior-trip filter reported "the date could not
# be parsed, so the filter did not run" on a booking with a perfectly good date
# (18 Aug 2026). The disclosure was right; the gap should not have existed.

def test_epoch_seconds_in_scientific_notation_parse():
    """The exact string the live diagnostic printed."""
    got = Z._sort_key("1.787097364E9")
    assert got.year == 2026 and got.month == 8 and got.day == 18, got


def test_epoch_parses_as_float_and_as_a_plain_string():
    want = Z._sort_key("1.787097364E9")
    assert Z._sort_key(1787097364.0) == want
    assert Z._sort_key("1787097364") == want
    assert Z._sort_key(1787097364) == want


def test_the_cutoff_now_runs_on_a_warehouse_date():
    cut, reason = Z._booking_cutoff("1.787097364E9")
    assert reason == "", f"the filter still refuses to run: {reason}"
    assert cut is not None
    assert Z._is_prior_trip(Z._sort_key("2026-07-06T00:00:00Z"), cut) is True


def test_iso_dates_still_parse():
    """The epoch fallback must not shadow the primary path."""
    got = Z._sort_key("2026-08-21T10:34:00Z")
    assert (got.year, got.month, got.day, got.hour) == (2026, 8, 21, 10)


def test_a_number_outside_the_epoch_window_is_still_unparseable():
    """Unbounded, every stray number becomes a date — which is how a phone
    number turns into a timestamp. Outside 2001-2033 it stays unreadable and
    says so."""
    assert Z._sort_key("42") == Z._SORT_MAX
    assert Z._sort_key("99999999999999") == Z._SORT_MAX
    assert Z._booking_cutoff("42")[0] is None


def test_real_junk_is_still_reported_as_unparseable():
    assert Z._sort_key("last tuesday") == Z._SORT_MAX
    cut, reason = Z._booking_cutoff("last tuesday")
    assert cut is None and "could not" in reason
