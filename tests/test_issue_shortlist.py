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


def test_a_booking_both_searches_found_outranks_one_only_one_found(zendesk_with,
                                                                   monkeypatch):
    """The searches run separately - by name, and by name+venue. A booking
    both of them return has two independent searches agreeing on it. That
    agreement used to be thrown away: the second sighting was skipped as a
    duplicate, so the booking looked exactly like one only the name found."""
    both = _ticket("t1", "70001", "Anna Klein", "Booking", "—")
    once = _ticket("t2", "70002", "Anna Berg", "Booking", "—")
    qs = _Queries({'requester:"Anna"': [once, both], "Anna Louvre": [both]})
    zendesk_with(qs, {
        "t1": {"booking_id": "70001", "guest_name": "Anna Klein"},
        "t2": {"booking_id": "70002", "guest_name": "Anna Berg"},
    })
    monkeypatch.setattr(zd, "matches_indicators",
                        lambda sig, ind, f, l: (True, ["name"]))
    ind = dict(SVEN, guest_name="Anna", experience_or_venue="Louvre",
               dates_mentioned=[], issue_terms=[])
    out = asyncio.run(zd.shortlist(ind, "Anna", ""))
    assert out[0]["booking_id"] == "70001", \
        "the booking two separate searches agreed on must rank first"
    assert "venue" in out[0]["matched_on"], \
        "and the card must show the venue search also found it"


def test_at_most_five_candidates_come_back(zendesk_with, monkeypatch):
    """Thirteen cards is a list to read, not a choice to make. The cap used to
    apply only when the name was the sole indicator, so a review naming a
    venue was uncapped."""
    tickets = [_ticket(f"t{i}", f"9000{i}", f"Anna {i}", "Booking", "—")
               for i in range(13)]
    qs = _Queries({"Anna": tickets})
    zendesk_with(qs, {f"t{i}": {"booking_id": f"9000{i}",
                                "guest_name": f"Anna {i}"} for i in range(13)})
    monkeypatch.setattr(zd, "matches_indicators",
                        lambda sig, ind, f, l: (True, ["name", "venue"]))
    ind = dict(SVEN, guest_name="Anna", experience_or_venue="Louvre")
    out = asyncio.run(zd.shortlist(ind, "Anna", ""))
    assert len(out) == 5, f"expected the total cap of 5, got {len(out)}"


def test_the_five_kept_are_the_best_not_the_newest(zendesk_with, monkeypatch):
    """Capping before ranking would throw away the right booking to keep a
    newer irrelevant one."""
    good = _ticket("t9", "80009", "Anna Right", "Booking", "—")
    others = [_ticket(f"t{i}", f"8000{i}", f"Anna {i}", "Booking", "—")
              for i in range(8)]
    qs = _Queries({"Anna": others + [good]})
    sigs = {f"t{i}": {"booking_id": f"8000{i}", "guest_name": f"Anna {i}"}
            for i in range(8)}
    sigs["t9"] = {"booking_id": "80009", "guest_name": "Anna Right",
                  "visit_date": "2026-10-20"}          # the date the review named
    zendesk_with(qs, sigs)
    monkeypatch.setattr(zd, "matches_indicators",
                        lambda sig, ind, f, l: (True, ["name"]))
    out = asyncio.run(zd.shortlist(SVEN, "Anna", ""))
    assert len(out) == 5
    assert out[0]["booking_id"] == "80009", \
        "the booking on the date the review named must survive the cap, first"


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


# ── the real thing, end to end ──────────────────────────────────────────────

def test_svens_review_end_to_end(zendesk_with, monkeypatch):
    """Sven's actual review, his actual two bookings, and the two searches
    that really run. The right booking must come first, say why, and the
    output must be capped and complete.

    High School Musical on the 23rd and Sinatra on the 20th, both under
    variants of his name. His review names 20.10. Sinatra is the answer.
    """
    hsm = _ticket("t1", "32365808", "Sven Lützeler", "Booking question", "—")
    sin = _ticket("t2", "32077652", "Sven Luetzeler", "Wrong voucher date",
                  "Der Voucher enthielt das falsche Datum 20.06.2026")
    # The name search finds both; the name+venue search finds only Sinatra.
    qs = _Queries({'requester:"Sven"': [hsm, sin], "Sven Musical": [sin]})
    zendesk_with(qs, {
        "t1": {"booking_id": "32365808", "guest_name": "Sven Lützeler",
               "visit_date": "2026-10-23", "experience": "High School Musical"},
        "t2": {"booking_id": "32077652", "guest_name": "Sven Luetzeler",
               "visit_date": "2026-10-20", "experience": "Sinatra The Musical"},
    })
    monkeypatch.setattr(zd, "matches_indicators",
                        lambda sig, ind, f, l: (True, ["name"]))

    ind = dict(SVEN, experience_or_venue="Musical")
    out = asyncio.run(zd.shortlist(ind, "Sven", ""))

    assert len(out) == 2, "both of his bookings must be offered"
    assert out[0]["booking_id"] == "32077652", (
        "Sinatra is on the date his review names and was found by both "
        f"searches; got {[s['booking_id'] for s in out]}")
    reasons = out[0]["matched_on"]
    assert any("visit date" in x for x in reasons), "the card must say why"
    assert "venue" in reasons, "the venue search also found it — that must show"
    assert zd._evidence(out[0]) > zd._evidence(out[1]), \
        "the better-evidenced booking must score higher, not just sort first"


def test_a_truncated_search_is_reported(zendesk_with, monkeypatch):
    """Zendesk drops everything past its cap silently. Five candidates from a
    truncated search does not mean five exist."""
    many = [_ticket(f"t{i}", f"6{i:06d}", f"Sven {i}", "B", "—")
            for i in range(zd._ZD_RESULT_CAP)]
    qs = _Queries({"Sven": many})
    zendesk_with(qs, {f"t{i}": {"booking_id": f"6{i:06d}",
                                "guest_name": f"Sven {i}"}
                      for i in range(zd._ZD_RESULT_CAP)})
    monkeypatch.setattr(zd, "matches_indicators",
                        lambda sig, ind, f, l: (True, ["name"]))
    notes = []
    out = asyncio.run(zd.shortlist(SVEN, "Sven", "", notes=notes))
    assert len(out) == 5, "still capped"
    assert any(n["kind"] == "truncated" for n in notes), \
        "a search that hit the cap must be reported, not only logged"


def test_a_failed_query_is_reported_not_silently_dropped(zendesk_with):
    """One query failing while others succeed used to leave no trace, so a
    partial search looked like a complete one."""
    class _Boom:
        seen = []

        def get_for(self, q):
            self.seen.append(q)
            raise RuntimeError("Zendesk 503")

    qs = _Boom()
    zendesk_with(qs, {})
    notes = []
    assert asyncio.run(zd.shortlist(SVEN, "Sven", "", notes=notes)) == []
    assert any(n["kind"] == "failed" for n in notes)


def test_every_query_carries_the_date_floor(zendesk_with):
    """Including the combined ones. "Tom Tom guided tour" and "Tom Tom France"
    both hit Zendesk's cap on live data - Zendesk ANDs words that appear in
    almost every ticket, so a combined query is not automatically narrow."""
    qs = _Queries({})
    zendesk_with(qs, {})
    asyncio.run(zd.shortlist(dict(SVEN, experience_or_venue="Louvre",
                                  city_or_country="Paris"),
                             "Sven", "", since="2025-01-01"))
    assert qs.seen, "no queries ran"
    unbounded = [q for q in qs.seen if "created>2025-01-01" not in q]
    assert not unbounded, f"these queries can truncate silently: {unbounded}"


def test_a_common_name_and_one_generic_phrase_is_not_a_match(zendesk_with):
    """Live data: the reviewer "Tom Tom" produced five unrelated guests —
    James Thomas Hamill, Tom Putzke, Tom Wammes, Tom Maksimov — each returned
    as a confident match on "no guide found", a phrase in a great many
    tickets. Presenting those as matches is worse than presenting nothing:
    the associate cannot see they are unrelated."""
    t = _ticket("t1", "50001", "Tom Putzke", "Tour", "there was no guide found")
    qs = _Queries({"Tom": [t]})
    zendesk_with(qs, {"t1": {"booking_id": "50001", "guest_name": "Tom Putzke"}})
    ind = dict(SVEN, guest_name="Tom", dates_mentioned=[], visit_date_hint=None,
               issue_terms=["no guide found", "guided tour not provided"])
    out = asyncio.run(zd.shortlist(ind, "Tom", ""))
    assert out, "it should still be offered — as a weak candidate"
    assert out[0].get("weak") is True, \
        "a common name plus one generic phrase must not be labelled a match"


def test_two_agreements_still_promote(zendesk_with):
    """The rule is two corroborations, not zero. A ticket naming the problem
    AND a date the review named is still a real match."""
    t = _ticket("t1", "50002", "Sven Bauer", "Falscher Voucher",
                "falsches Datum 20.06.2026 auf dem Voucher")
    qs = _Queries({"Sven": [t]})
    zendesk_with(qs, {"t1": {"booking_id": "50002", "guest_name": "Sven Bauer"}})
    out = asyncio.run(zd.shortlist(SVEN, "Sven", ""))
    assert out and not out[0].get("weak"), \
        "problem + date is two independent agreements and must promote"


def test_the_visit_date_hint_is_used_for_ranking(zendesk_with, monkeypatch):
    """Amanda's review says "I am at the venue" and names no date, so
    dates_mentioned was empty and visit_date_hint held the only date that
    mattered. Ranking looked at neither and returned five unrelated Amandas
    in ticket order."""
    right = _ticket("t1", "51001", "Amanda Lopes", "B", "—")
    wrong = _ticket("t2", "51002", "Amanda Harris", "B", "—")
    qs = _Queries({"Amanda": [wrong, right]})
    zendesk_with(qs, {
        "t1": {"booking_id": "51001", "guest_name": "Amanda Lopes",
               "visit_date": "2026-07-30"},
        "t2": {"booking_id": "51002", "guest_name": "Amanda Harris",
               "visit_date": "2026-08-22"},
    })
    monkeypatch.setattr(zd, "matches_indicators",
                        lambda sig, ind, f, l: (True, ["name"]))
    ind = {"guest_name": "Amanda", "experience_or_venue": None,
           "city_or_country": None, "visit_date_hint": "2026-07-30",
           "dates_mentioned": [], "issue_terms": []}
    out = asyncio.run(zd.shortlist(ind, "Amanda", ""))
    assert out[0]["booking_id"] == "51001", \
        "the booking on the day she was standing at the venue must rank first"
    assert any("visit date" in m for m in out[0]["matched_on"])


def test_a_date_the_model_inferred_from_today_is_not_evidence(zendesk_with,
                                                              monkeypatch):
    """Amanda's review says "I am at the venue" and names no date, so
    extraction resolved visit_date_hint to the post date. Every booking
    visiting that day then "agreed" — hundreds worldwide — and five Amandas on
    five continents each came back labelled a match on that date."""
    a = _ticket("t1", "52001", "Amanda Rogers", "B", "—")
    b = _ticket("t2", "52002", "Amanda Burgan", "B", "—")
    qs = _Queries({"Amanda": [a, b]})
    zendesk_with(qs, {
        "t1": {"booking_id": "52001", "guest_name": "Amanda Rogers",
               "visit_date": "2026-07-30"},
        "t2": {"booking_id": "52002", "guest_name": "Amanda Burgan",
               "visit_date": "2026-07-30"},
    })
    monkeypatch.setattr(zd, "matches_indicators",
                        lambda sig, ind, f, l: (True, ["name"]))
    ind = {"guest_name": "Amanda", "experience_or_venue": None,
           "city_or_country": None, "visit_date_hint": "2026-07-30",
           "dates_mentioned": [], "issue_terms": []}
    out = asyncio.run(zd.shortlist(ind, "Amanda", "", review_date="2026-07-30"))
    assert out, "they are still leads worth showing"
    for s in out:
        assert not any("visit date" in m for m in s["matched_on"]), \
            "the post date is not corroboration — it agrees with everyone"
        assert s.get("weak") is True, \
            "a first name and nothing else is a lead, not an identification"


def test_a_date_other_than_the_review_date_still_counts(zendesk_with,
                                                       monkeypatch):
    """The rule is about the post date, not about dates in general. A review
    posted in July naming a visit in October has given us something that
    separates bookings, and it still ranks."""
    t = _ticket("t1", "52003", "Sven Bauer", "B", "—")
    qs = _Queries({"Sven": [t]})
    zendesk_with(qs, {"t1": {"booking_id": "52003", "guest_name": "Sven Bauer",
                             "visit_date": "2026-10-20"}})
    monkeypatch.setattr(zd, "matches_indicators",
                        lambda sig, ind, f, l: (True, ["name"]))
    out = asyncio.run(zd.shortlist(SVEN, "Sven", "", review_date="2026-07-30"))
    assert any("visit date" in m for m in out[0]["matched_on"])


def test_the_post_date_is_discounted_however_it_was_extracted(zendesk_with,
                                                              monkeypatch):
    """Extraction is not consistent about where it puts the inferred "today".
    Two runs of Amanda's review put it in visit_date_hint alone, then in
    dates_mentioned as well. Discounting it only in one field left the second
    run reporting five continents' worth of matches."""
    t = _ticket("t1", "52006", "Amanda Rogers", "B", "—")
    qs = _Queries({"Amanda": [t]})
    zendesk_with(qs, {"t1": {"booking_id": "52006", "guest_name": "Amanda Rogers",
                             "visit_date": "2026-07-30"}})
    monkeypatch.setattr(zd, "matches_indicators",
                        lambda sig, ind, f, l: (True, ["name"]))
    for ind in (
        {"visit_date_hint": "2026-07-30", "dates_mentioned": []},
        {"visit_date_hint": "2026-07-30", "dates_mentioned": ["2026-07-30"]},
        {"visit_date_hint": None,         "dates_mentioned": ["2026-07-30"]},
    ):
        full = {"guest_name": "Amanda", "experience_or_venue": None,
                "city_or_country": None, "issue_terms": [], **ind}
        out = asyncio.run(zd.shortlist(full, "Amanda", "",
                                       review_date="2026-07-30"))
        assert out, f"still a lead for {ind}"
        assert not any("visit date" in m for m in out[0]["matched_on"]), \
            f"the post date counted as corroboration for {ind}"
        assert out[0].get("weak") is True, \
            f"a first name and the post date is a lead, not a match ({ind})"


def test_one_indicator_is_a_lead_not_a_match(zendesk_with, monkeypatch):
    t = _ticket("t1", "52004", "Amanda Bell", "B", "—")
    qs = _Queries({"Amanda": [t]})
    zendesk_with(qs, {"t1": {"booking_id": "52004", "guest_name": "Amanda Bell"}})
    monkeypatch.setattr(zd, "matches_indicators",
                        lambda sig, ind, f, l: (True, ["name"]))
    ind = {"guest_name": "Amanda", "experience_or_venue": None,
           "city_or_country": None, "visit_date_hint": None,
           "dates_mentioned": [], "issue_terms": []}
    out = asyncio.run(zd.shortlist(ind, "Amanda", ""))
    assert out and out[0].get("weak") is True


def test_two_indicators_are_still_a_match(zendesk_with, monkeypatch):
    t = _ticket("t1", "52005", "Sven Bauer", "B", "—")
    qs = _Queries({"Sven": [t]})
    zendesk_with(qs, {"t1": {"booking_id": "52005", "guest_name": "Sven Bauer",
                             "experience": "Sinatra The Musical"}})
    monkeypatch.setattr(zd, "matches_indicators",
                        lambda sig, ind, f, l: (True, ["name", "venue"]))
    out = asyncio.run(zd.shortlist(dict(SVEN, experience_or_venue="Musical"),
                                   "Sven", ""))
    assert out and not out[0].get("weak"), \
        "name and venue agreeing is an identification"


def test_two_paraphrases_of_one_complaint_are_one_agreement(zendesk_with):
    """Live regression. Extraction returns several ways of saying the same
    thing — "guided tour no guide", "tour guide not present", "guided tour not
    provided" — and counting each as its own corroboration made one complaint
    look like three. Four bookings in Athens and Rome were promoted to matches
    for a review about France, because a Tom matched a Thomas and the ticket
    said the guide did not show up."""
    # "Tom Revell" rather than "Thomas Revell": the real matcher pairs Tom to
    # Thomas through the nickname table, which is correct and not what this
    # test is about. The fixture's name stub is a substring test, so a name it
    # matches keeps the test on the thing it is checking - how corroborations
    # are counted.
    t = _ticket("t1", "53001", "Tom Revell", "Tour",
                "the guided tour had no guide, the tour guide was not present")
    qs = _Queries({"Tom": [t]})
    zendesk_with(qs, {"t1": {"booking_id": "53001", "guest_name": "Tom Revell"}})
    ind = {"guest_name": "Tom", "experience_or_venue": None,
           "city_or_country": "France", "visit_date_hint": None,
           "dates_mentioned": [],
           "issue_terms": ["guided tour no guide", "tour guide not present",
                           "guided tour not provided"]}
    out = asyncio.run(zd.shortlist(ind, "Tom", "", review_date="2026-07-30"))
    assert out, "still a lead"
    assert out[0].get("weak") is True, (
        "three phrasings of one complaint are one agreement, not three — "
        f"got matched_on={out[0]['matched_on']}")


def test_a_problem_and_a_date_are_two_different_kinds(zendesk_with):
    """The rule is kinds, not count. A phrase plus a date the review named is
    two genuinely independent agreements and still promotes."""
    t = _ticket("t1", "53002", "Sven Bauer", "Voucher",
                "falsches Datum 20.06.2026 auf dem Voucher")
    qs = _Queries({"Sven": [t]})
    zendesk_with(qs, {"t1": {"booking_id": "53002", "guest_name": "Sven Bauer"}})
    out = asyncio.run(zd.shortlist(SVEN, "Sven", "", review_date="2026-07-30"))
    assert out and not out[0].get("weak")
