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
        monkeypatch.setattr(zd, "ticket_signals", lambda t: dict(signals[t.id]))
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
        "t1": {"booking_id": "88001", "guest_name": "Sven Bauer",
               "subject": "Falscher Voucher",
               "description": "Der Voucher enthielt das falsche Datum 20.06."},
        "t2": {"booking_id": "88002", "guest_name": "Sven Meier",
               "subject": "Frage zum Parken", "description": "Wo kann ich parken?"},
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
    zendesk_with(qs, {"t1": {"booking_id": "88003", "guest_name": "Sven Bauer",
                             "subject": "Voucher",
                             "description": "Datum 2026-06-20 statt 2026-10-20"}})
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
    zendesk_with(qs, {"t1": {"booking_id": "88004", "guest_name": "Somebody Else",
                             "subject": "Falscher Voucher",
                             "description": "falsches Datum, 2026-06-20 statt gebucht"}})
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


# ── the issue path must never disturb a matcher that is already working ─────

def test_issue_search_does_not_run_when_direct_indicators_match(zendesk_with,
                                                                monkeypatch):
    """The direct indicators are the matcher. When name/venue/city produce a
    match, that IS the answer - the problem text must never be searched, so a
    working match can never be second-guessed by a text search."""
    good = _ticket("t1", "77001", "Sven Bauer", "Booking question", "All fine")
    qs = _Queries({"Sven": [good]})
    zendesk_with(qs, {"t1": {"booking_id": "77001", "guest_name": "Sven Bauer",
                             "subject": "Booking question", "description": "All fine"}})
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
    zendesk_with(qs, {"t1": {"booking_id": "77002", "guest_name": "Sven Bauer",
                             "subject": "Anything", "description": "Nothing relevant"}})
    out = asyncio.run(zd.shortlist(no_issue, "Sven", ""))
    assert [s["booking_id"] for s in out] == ["77002"]
    assert out[0].get("weak") is True, "the name-only fallback must survive unchanged"
    assert out[0]["matched_on"] == ["name"]
