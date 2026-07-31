"""The problem a guest describes is an identifier.

Sven's review is the case this exists for: a German review, no booking id, no
recognisable venue ("Musicalkarte" is a product type, not a place), a name so
common it matches half of Zendesk. What it DOES carry is a specific problem -
a voucher showing 20.06 for a performance bought for 20.10 - and the guest
raised that same problem with support before writing the review. So the
ticket holding the booking id is findable by the problem.

Before this, shortlist() searched name / name+venue / name+city / venue only,
and a review like Sven's produced nothing to pick from.

Fixtures monkeypatch the Zendesk client, so these run anywhere.
"""
import asyncio
import types

import pytest

from server.services import zendesk as zd


SVEN = {
    "guest_name": "Sven",
    "experience_or_venue": None,          # "Musicalkarte" is not a venue
    "city_or_country": None,
    "visit_date_hint": "2026-10-20",
    "pax": None,
    "issue_terms": ["falsches Datum", "wrong date", "Voucher"],
    "dates_mentioned": ["2026-10-20", "2026-06-20"],
    "outcome": "refund_denied",
}


def _ticket(tid, bid, guest, subject, body):
    return types.SimpleNamespace(
        id=tid, subject=subject, description=body, created_at="2026-07-01",
        custom_fields=[], requester_id=1, via=None,
    )


@pytest.fixture
def zendesk_with(monkeypatch):
    """Wire a fake Zendesk whose search returns the given tickets per query."""
    def _install(tickets_by_query, signals):
        monkeypatch.setattr(zd, "is_live", lambda name: True)
        monkeypatch.setattr(zd, "_get_client", lambda: object())
        monkeypatch.setattr(zd, "_search_with_retry",
                            lambda _z, q: tickets_by_query.get_for(q))
        # Shaped like the real ticket_signals(): the booking's FACTS off the
        # custom fields. It has never returned the subject or the body, and a
        # fixture that hands them over hides code that expects them to be there.
        _REAL_KEYS = ("booking_id", "guest_name", "guest_email", "experience",
                      "city", "visit_date", "pax_raw", "pax", "vendor_name",
                      "itinerary_id")
        monkeypatch.setattr(zd, "ticket_signals", lambda t: {
            k: v for k, v in signals[t.id].items() if k in _REAL_KEYS})
        # Indicator agreement is exercised by its own test module; here the
        # question is only whether the issue-led query runs and is scored.
        monkeypatch.setattr(zd, "matches_indicators",
                            lambda sig, ind, f, l: (False, []))
        monkeypatch.setattr(zd, "name_matches",
                            lambda cand, f, l: bool(f) and f.lower() in (cand or "").lower())
    return _install


class _Queries:
    """Returns tickets for whichever query mentions the given fragment."""
    def __init__(self, mapping):
        self.mapping = mapping
        self.seen = []

    def get_for(self, q):
        self.seen.append(q)
        for fragment, tickets in self.mapping.items():
            if fragment.lower() in q.lower():
                return tickets
        return []


def test_issue_terms_produce_a_query(zendesk_with):
    """The problem must reach Zendesk as a search, not sit unused in the JSON."""
    qs = _Queries({})
    zendesk_with(qs, {})
    asyncio.run(zd.shortlist(SVEN, "Sven", ""))
    issue_queries = [q for q in qs.seen if "falsches Datum" in q or "wrong date" in q]
    assert issue_queries, f"no issue-led query was made; queries were {qs.seen}"


def test_name_plus_issue_beats_name_alone(zendesk_with):
    """Two tickets share the name. Only one talks about the same problem, and
    that one must be returned as a real match rather than a weak fallback."""
    right = _ticket("t1", "88001", "Sven Bauer", "Falscher Voucher",
                    "Der Voucher enthielt das falsche Datum 20.06.")
    wrong = _ticket("t2", "88002", "Sven Meier", "Frage zum Parken",
                    "Wo kann ich parken?")
    qs = _Queries({"Sven": [right, wrong]})
    zendesk_with(qs, {
        "t1": {"booking_id": "88001", "guest_name": "Sven Bauer"},
        "t2": {"booking_id": "88002", "guest_name": "Sven Meier"},
    })
    out = asyncio.run(zd.shortlist(SVEN, "Sven", ""))
    bids = [s["booking_id"] for s in out]
    assert bids[0] == "88001", f"the ticket about the same problem must rank first, got {bids}"
    assert any("issue:" in m for m in out[0]["matched_on"])
    assert not out[0].get("weak"), "a name+issue agreement is not a weak match"


def test_a_named_date_corroborates(zendesk_with):
    """The review named 20.06 as the WRONG date. A ticket quoting that same
    date is talking about the same booking, and that must count."""
    t = _ticket("t1", "88003", "Sven Bauer", "Voucher",
                "Datum 2026-06-20 statt 2026-10-20")
    qs = _Queries({"Sven": [t]})
    zendesk_with(qs, {"t1": {"booking_id": "88003", "guest_name": "Sven Bauer"}})
    out = asyncio.run(zd.shortlist(SVEN, "Sven", ""))
    assert out, "a ticket quoting a date the review named must not be dropped"
    assert any("date:" in m for m in out[0]["matched_on"])


def test_no_name_still_matches_on_issue_and_date_together(zendesk_with):
    """An anonymous review is not hopeless: the problem AND a date agreeing is
    two independent signals, which is worth putting in front of a human."""
    anon = dict(SVEN, guest_name=None)
    t = _ticket("t1", "88004", "Somebody Else", "Falscher Voucher",
                "falsches Datum, 2026-06-20 statt gebucht")
    qs = _Queries({"falsches Datum": [t]})
    zendesk_with(qs, {"t1": {"booking_id": "88004", "guest_name": "Somebody Else"}})
    out = asyncio.run(zd.shortlist(anon, "", ""))
    assert out, "issue + date agreement should survive with no name at all"
    assert out[0]["booking_id"] == "88004"


def test_issue_only_review_is_not_searched_into_the_void(zendesk_with):
    """No name, no venue, no problem stated - there is nothing to search on
    and the function must say so rather than issuing a bare query."""
    empty = {"guest_name": None, "experience_or_venue": None,
             "city_or_country": None, "issue_terms": [], "dates_mentioned": []}
    qs = _Queries({})
    zendesk_with(qs, {})
    assert asyncio.run(zd.shortlist(empty, "", "")) == []
    assert qs.seen == [], "no query should be issued with nothing to search on"


def test_a_german_guest_writes_the_date_the_german_way(zendesk_with):
    """Sven's actual ticket says 20.06.2026, not 2026-06-20. Extraction gives
    ISO, so a literal substring test agreed with nothing outside a fixture -
    and the reviews this path exists for are precisely the non-English ones."""
    t = _ticket("t1", "88005", "Sven Bauer", "Falscher Voucher",
                "Auf dem Voucher steht 20.06.2026 statt 20.10.2026.")
    qs = _Queries({"Sven": [t]})
    zendesk_with(qs, {"t1": {"booking_id": "88005", "guest_name": "Sven Bauer"}})
    out = asyncio.run(zd.shortlist(SVEN, "Sven", ""))
    assert out, "a ticket naming the date in the guest's own format must count"
    assert any("date:" in m for m in out[0]["matched_on"])


def test_written_month_names_count_too(zendesk_with):
    t = _ticket("t1", "88006", "Sven Bauer", "Voucher",
                "the voucher says 20 June but we booked 20 October")
    qs = _Queries({"Sven": [t]})
    zendesk_with(qs, {"t1": {"booking_id": "88006", "guest_name": "Sven Bauer"}})
    out = asyncio.run(zd.shortlist(SVEN, "Sven", ""))
    assert any("date:" in m for m in out[0]["matched_on"])


def test_an_unrelated_number_is_not_read_as_a_date(zendesk_with):
    """The date forms must not be so loose that any ticket corroborates."""
    t = _ticket("t1", "88007", "Sven Bauer", "Parken",
                "Wir waren zu 4 Personen, Rechnung 1234567 vom 03.02.2025.")
    qs = _Queries({"Sven": [t]})
    zendesk_with(qs, {"t1": {"booking_id": "88007", "guest_name": "Sven Bauer"}})
    out = asyncio.run(zd.shortlist(SVEN, "Sven", ""))
    assert out and out[0].get("weak") is True, \
        "nothing in this ticket agrees with the review beyond the name"


def test_the_booking_on_the_date_the_guest_named_comes_first(zendesk_with,
                                                            monkeypatch):
    """Sven's review named 20.10 and produced two bookings by the same person:
    High School Musical on the 23rd and Sinatra on the 20th. Both came back as
    'name, venue' — equally good — with the date he gave us unused. The one
    whose visit date is the date he named has to be first, and has to say so."""
    a = _ticket("t1", "32365808", "Sven Lützeler", "Booking", "—")
    b = _ticket("t2", "32077652", "Sven Luetzeler", "Booking", "—")
    qs = _Queries({"Sven": [a, b]})
    zendesk_with(qs, {
        "t1": {"booking_id": "32365808", "guest_name": "Sven Lützeler",
               "visit_date": "2026-10-23", "experience": "High School Musical"},
        "t2": {"booking_id": "32077652", "guest_name": "Sven Luetzeler",
               "visit_date": "2026-10-20", "experience": "Sinatra The Musical"},
    })
    # Both agree on the direct indicators, as they really did.
    monkeypatch.setattr(zd, "matches_indicators",
                        lambda sig, ind, f, l: (True, ["name", "venue"]))
    out = asyncio.run(zd.shortlist(SVEN, "Sven", ""))
    assert [s["booking_id"] for s in out][0] == "32077652", \
        "the booking on the date the review named must rank first"
    assert any("visit date" in m for m in out[0]["matched_on"]), \
        "and the card must say that is why"


def test_ranking_by_date_does_not_change_which_bookings_are_returned(
        zendesk_with, monkeypatch):
    """Order and labels only. The matcher decides membership; this decides
    which of its answers is shown first."""
    a = _ticket("t1", "111", "Sven A", "Booking", "—")
    b = _ticket("t2", "222", "Sven B", "Booking", "—")
    qs = _Queries({"Sven": [a, b]})
    zendesk_with(qs, {
        "t1": {"booking_id": "111", "guest_name": "Sven A", "visit_date": "2020-01-01"},
        "t2": {"booking_id": "222", "guest_name": "Sven B", "visit_date": "2020-02-02"},
    })
    monkeypatch.setattr(zd, "matches_indicators",
                        lambda sig, ind, f, l: (True, ["name", "venue"]))
    out = asyncio.run(zd.shortlist(SVEN, "Sven", ""))
    assert sorted(s["booking_id"] for s in out) == ["111", "222"], \
        "no candidate may be added or dropped by ranking"


def test_a_year_out_still_counts_as_the_same_day(zendesk_with):
    """"20.10." with the year inferred from the post date. The day and month
    are what the guest wrote."""
    assert zd._dates_agree("2025-10-20", "2026-10-20") is True
    assert zd._dates_agree("2026-10-23", "2026-10-20") is False
    assert zd._dates_agree("2021-10-20", "2026-10-20") is False
    assert zd._dates_agree("", "2026-10-20") is False


# ── the issue path must never disturb a matcher that is already working ─────

def test_issue_search_does_not_run_when_direct_indicators_match(zendesk_with,
                                                                monkeypatch):
    """The direct indicators are the matcher. When name/venue/city produce a
    match, that IS the answer - the problem text must never be searched, so a
    working match can never be second-guessed by a text search."""
    good = _ticket("t1", "77001", "Sven Bauer", "Booking question", "All fine")
    qs = _Queries({"Sven": [good]})
    zendesk_with(qs, {"t1": {"booking_id": "77001", "guest_name": "Sven Bauer"}})
    # This time the direct indicators DO agree.
    monkeypatch.setattr(zd, "matches_indicators",
                        lambda sig, ind, f, l: (True, ["name", "venue"]))
    out = asyncio.run(zd.shortlist(SVEN, "Sven", ""))

    assert [s["booking_id"] for s in out] == ["77001"]
    assert out[0]["matched_on"] == ["name", "venue"], \
        "the direct match must be reported exactly as the old matcher reported it"
    assert not [q for q in qs.seen if "falsches Datum" in q or "wrong date" in q], \
        f"no issue query may run once the direct indicators matched; ran {qs.seen}"


def test_direct_pass_keeps_the_original_weak_fallback(zendesk_with):
    """With no issue terms at all, behaviour is the old behaviour: a name-only
    agreement is still held as a weak candidate rather than discarded."""
    no_issue = dict(SVEN, issue_terms=[], dates_mentioned=[])
    t = _ticket("t1", "77002", "Sven Bauer", "Anything", "Nothing relevant")
    qs = _Queries({"Sven": [t]})
    zendesk_with(qs, {"t1": {"booking_id": "77002", "guest_name": "Sven Bauer"}})
    out = asyncio.run(zd.shortlist(no_issue, "Sven", ""))
    assert [s["booking_id"] for s in out] == ["77002"]
    assert out[0].get("weak") is True, "the name-only fallback must survive unchanged"
    assert out[0]["matched_on"] == ["name"]
