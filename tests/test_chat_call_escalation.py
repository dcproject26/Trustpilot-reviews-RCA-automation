"""A guest who chats or phones support about a problem, then reviews that same
problem, has told us which booking it is - in their own timeline.

These drive the REAL ticket_signals / shortlist / escalation_evidence /
compose_tier2_score with synthetic tickets; only the Zendesk transport
(is_live, the client, the search call) is stubbed. Nothing here asserts that a
string exists in source.

The invariants held:
  * it is ADDITIVE - an existing name+venue match is neither demoted nor
    duplicated, and the extra step cannot remove a candidate;
  * the name is never decisive - same-name alone, same-problem alone, a
    chat alone, or an out-of-window chat each score nothing;
  * "ran and found nothing" is distinguishable from "did not run".
"""
import asyncio
import types

import pytest

from server.services import zendesk as zd
from server import pipeline as pl

REVIEW_DATE = "2026-10-25"
IND = {
    "guest_name": "Jane Doe",
    "experience_or_venue": "Colosseum",
    "city_or_country": None,
    "visit_date_hint": "2026-10-20",
    "pax": None,
    "issue_terms": ["no guide showed up"],
    "dates_mentioned": [],
}
ISSUE_TEXT = "Our tour started and no guide showed up at the meeting point."


def _ticket(tid, bid, *, channel, created="2026-10-20T10:00:00Z",
            guest="Jane Doe", experience="Vatican Museums",
            subject="Tour problem", body=ISSUE_TEXT):
    fields = [{"id": zd._BOOKING_FIELD, "value": bid}]
    if guest:
        fields.append({"id": zd._F_GUEST_NAME, "value": guest})
    if experience:
        fields.append({"id": zd._F_EXPERIENCE, "value": experience})
    via = types.SimpleNamespace(channel=channel) if channel is not None else None
    return types.SimpleNamespace(
        id=tid, subject=subject, description=body, created_at=created,
        custom_fields=fields, requester_id=1, via=via)


class _Queries:
    """Returns tickets for the first fragment the query contains."""
    def __init__(self, mapping):
        self.mapping, self.seen = mapping, []

    def get_for(self, q):
        self.seen.append(q)
        for fragment, tickets in self.mapping.items():
            if fragment.lower() in q.lower():
                return tickets
        return []


@pytest.fixture
def zendesk(monkeypatch):
    """Stub ONLY the transport. ticket_signals and matches_indicators are real."""
    def _install(mapping):
        qs = _Queries(mapping)
        monkeypatch.setattr(zd, "is_live", lambda name: True)
        monkeypatch.setattr(zd, "_get_client", lambda: object())
        monkeypatch.setattr(zd, "_search_with_retry", lambda _z, q: qs.get_for(q))
        return qs
    return _install


def _run(ind=IND, notes=None, first="Jane", last="Doe"):
    return asyncio.run(zd.shortlist(ind, first, last, review_date=REVIEW_DATE,
                                    notes=notes))


# ── step 1: the signal carries the channel and created_at ──────────────────

@pytest.mark.parametrize("via,expected", [
    ("native_messaging", "chat"), ("chat", "chat"), ("web_messaging", "chat"),
    ("voice", "call"), ("phone", "call"),
    ("email", "email"),
])
def test_ticket_signals_surfaces_the_channel(via, expected):
    sig = zd.ticket_signals(_ticket("1", "11111111", channel=via))
    assert sig["channel"] == expected
    assert sig["via_channel"] == via


def test_ticket_signals_surfaces_created_at():
    sig = zd.ticket_signals(_ticket("1", "11111111", channel="chat",
                                    created="2026-10-20T10:00:00Z"))
    assert sig["created_at"] == "2026-10-20T10:00:00Z"


def test_a_ticket_with_no_via_has_no_channel_not_email():
    """Unknown is not email: the two must stay distinguishable."""
    sig = zd.ticket_signals(_ticket("1", "11111111", channel=None))
    assert sig["channel"] == ""


def test_existing_signal_keys_are_untouched():
    sig = zd.ticket_signals(_ticket("1", "11111111", channel="chat"))
    for k in ("booking_id", "guest_name", "guest_email", "experience", "city",
              "visit_date", "pax_raw", "pax", "vendor_name", "itinerary_id"):
        assert k in sig
    assert sig["booking_id"] == "11111111"
    assert sig["guest_name"] == "Jane Doe"


# ── step 2: time proximity ────────────────────────────────────────────────

@pytest.mark.parametrize("created,verdict", [
    ("2026-10-19T09:00:00Z", True),    # one day before the visit: the edge
    ("2026-10-18T09:00:00Z", False),  # two days before
    ("2026-10-20T09:00:00Z", True),   # the day itself
    ("2026-11-10T09:00:00Z", True),   # 21 days after: the edge
    ("2026-11-11T09:00:00Z", False),  # 22 days after
])
def test_window_around_the_named_visit_date(created, verdict):
    got, why = zd.ticket_in_window(created, IND, REVIEW_DATE)
    assert got is verdict, why


def test_window_anchors_on_the_review_when_no_date_was_named():
    ind = {**IND, "visit_date_hint": None}
    assert zd.ticket_in_window("2026-10-10", ind, REVIEW_DATE)[0] is True
    assert zd.ticket_in_window("2026-08-01", ind, REVIEW_DATE)[0] is False


def test_the_review_date_is_not_a_reference_day():
    ind = {**IND, "visit_date_hint": REVIEW_DATE}
    # With the review's own day discounted there is no reference left, so a
    # ticket from August is judged against the review and is out.
    assert zd.ticket_in_window("2026-08-01", ind, REVIEW_DATE)[0] is False


def test_an_unreadable_date_is_not_judged_not_out_of_window():
    got, why = zd.ticket_in_window("", IND, REVIEW_DATE)
    assert got is None and "no readable created date" in why
    ev = zd.escalation_evidence(
        {"channel": "chat", "guest_name": "Jane Doe", "created_at": ""},
        ISSUE_TEXT, IND, "Jane", "Doe", REVIEW_DATE)
    assert ev["in_window"] is None and ev["qualifies"] is False


# ── step 3: the chat/call ticket creates a candidate ──────────────────────

def test_a_chat_ticket_creates_a_candidate_venue_alone_would_not(zendesk):
    """The ticket's experience (Vatican) disagrees with the review's venue
    (Colosseum), so the direct indicators reject it. Same guest + chat + in
    window + same problem is what makes it a candidate."""
    zendesk({"Jane": [_ticket("t1", "50000001", channel="native_messaging")]})
    out = _run()
    assert [s["booking_id"] for s in out] == ["50000001"]
    sig = out[0]
    assert "chat ticket" in sig["matched_on"]
    assert sig["escalation_promoted"] is True
    assert not sig.get("weak")
    assert sig["escalation"]["qualifies"] and sig["escalation"]["channel"] == "chat"


def test_a_call_ticket_creates_a_candidate(zendesk):
    zendesk({"Jane": [_ticket("t1", "50000002", channel="voice")]})
    out = _run()
    assert [s["booking_id"] for s in out] == ["50000002"]
    assert "call ticket" in out[0]["matched_on"]
    assert not out[0].get("weak")


def test_the_same_ticket_as_email_is_only_a_weak_name_match(zendesk):
    """Control: the channel is what makes the difference."""
    zendesk({"Jane": [_ticket("t1", "50000003", channel="email")]})
    out = _run()
    assert out and out[0].get("weak") is True
    assert not out[0].get("escalation_promoted")
    assert "email ticket" not in out[0]["matched_on"]


def test_an_out_of_window_chat_does_not_create_a_candidate(zendesk):
    zendesk({"Jane": [_ticket("t1", "50000004", channel="chat",
                              created="2026-07-01T10:00:00Z")]})
    out = _run()
    assert not any(s.get("escalation_promoted") for s in out)
    assert all(s.get("weak") for s in out)
    assert not any("chat ticket" in s["matched_on"] for s in out)


def test_a_chat_about_something_else_does_not_create_a_candidate(zendesk):
    zendesk({"Jane": [_ticket("t1", "50000005", channel="chat",
                              body="Please can I change my pickup time?")]})
    out = _run()
    assert not any(s.get("escalation_promoted") for s in out)


def test_the_name_alone_is_never_decisive(zendesk):
    """A chat, in window, by someone ELSE on the same problem; and a chat in
    window by the same guest on a different problem. Neither is promoted."""
    zendesk({"Jane": [
        _ticket("t1", "50000006", channel="chat", guest="Joan Smith"),
        _ticket("t2", "50000007", channel="chat", body="Where do I park?"),
    ]})
    out = _run()
    assert not any(s.get("escalation_promoted") for s in out)
    assert "50000006" not in [s["booking_id"] for s in out]


def test_a_ticket_with_no_guest_name_is_not_the_same_guest(zendesk):
    zendesk({"Jane": [_ticket("t1", "50000008", channel="chat", guest="")]})
    out = _run()
    assert not any(s.get("escalation_promoted") for s in out)


# ── step 3 regression: existing matches are preserved ─────────────────────

def _strong():
    return _ticket("s1", "60000001", channel="email", experience="Colosseum",
                   body="Booked the Colosseum tour.")


def test_a_strong_name_venue_match_is_unchanged(zendesk):
    """Same result with the correlation off (no issue terms) and on."""
    zendesk({"Jane": [_strong()]})
    off = _run({**IND, "issue_terms": []})
    zendesk({"Jane": [_strong()]})
    on = _run()
    assert [s["booking_id"] for s in on] == [s["booking_id"] for s in off] == ["60000001"]
    assert on[0]["matched_on"] == off[0]["matched_on"]
    assert on[0].get("weak") == off[0].get("weak")
    assert not on[0].get("escalation_promoted")


def test_a_strong_match_that_is_also_a_chat_is_strengthened_not_duplicated(zendesk):
    t = _ticket("s1", "60000002", channel="chat", experience="Colosseum")
    zendesk({"Jane": [t]})
    out = _run()
    assert [s["booking_id"] for s in out] == ["60000002"]          # one, not two
    assert "name" in out[0]["matched_on"] and "venue" in out[0]["matched_on"]
    assert "chat ticket" in out[0]["matched_on"]
    assert not out[0].get("escalation_promoted")   # it was already a match


def test_the_chat_candidate_is_added_beside_a_strong_match_not_over_it(zendesk):
    """The additive case: a name+venue booking exists AND the guest chatted
    about the problem on another booking (both come back from the name
    search). Both are offered; the strong one keeps first place; and no
    issue-led search is sent - a working matcher is not second-guessed."""
    qs = zendesk({"Jane": [_strong(), _ticket("c1", "60000004", channel="chat")]})
    out = _run()
    bids = [s["booking_id"] for s in out]
    assert bids[0] == "60000001", bids          # not demoted
    assert "60000004" in bids, bids             # and the chat one is added
    chat = next(s for s in out if s["booking_id"] == "60000004")
    assert chat["escalation_promoted"] and not chat.get("weak")
    assert not any("showed up" in q for q in qs.seen)


def test_pass_two_still_runs_when_the_direct_pass_found_nothing(zendesk):
    """The existing fallback gate is untouched."""
    qs = zendesk({"showed up": [_ticket("c1", "60000005", channel="email",
                                        experience="Colosseum")]})
    out = _run()
    assert any("showed up" in q for q in qs.seen)
    assert [s["booking_id"] for s in out] == ["60000005"]


def test_an_ordinary_email_beside_a_strong_match_is_not_added(zendesk):
    zendesk({"Jane": [_strong(), _ticket("e1", "60000006", channel="email")]})
    out = _run()
    assert [s["booking_id"] for s in out] == ["60000001"]


# ── ranking ────────────────────────────────────────────────────────────────

def test_a_qualifying_chat_outranks_an_equal_ticket_without_one(zendesk):
    a = _ticket("a", "70000001", channel="email", experience="Colosseum")
    b = _ticket("b", "70000002", channel="chat", experience="Colosseum",
                created="2026-10-19T10:00:00Z")      # older: tie-break would lose
    zendesk({"Jane": [a, b]})
    out = _run()
    assert [s["booking_id"] for s in out][0] == "70000002"


# ── rule 1: ran-and-found-nothing is not did-not-run ──────────────────────

def test_the_notes_say_it_ran_and_found_nothing(zendesk):
    zendesk({"Jane": [_ticket("t1", "80000001", channel="chat",
                              body="Please move my pickup.")]})
    notes = []
    _run(notes=notes)
    note = next(n for n in notes if n["kind"] == "escalation_checked")
    assert note["stats"]["conversations"] == 1
    assert note["stats"]["qualified"] == 0
    line = pl.escalation_trail(notes)
    assert line["mark"] == "warn" and "ran and found nothing" in line["text"]


def test_the_notes_say_it_did_not_run(zendesk):
    zendesk({"Jane": [_strong()]})
    notes = []
    _run({**IND, "issue_terms": []}, notes=notes)
    assert not any(n["kind"] == "escalation_checked" for n in notes)
    line = pl.escalation_trail(notes)
    assert line["mark"] == "warn" and "did not run" in line["text"]
    assert "no issue terms" in line["text"]


def test_a_review_with_no_name_says_why_it_did_not_run(zendesk):
    zendesk({})
    notes = []
    asyncio.run(zd.shortlist({**IND}, None, None, review_date=REVIEW_DATE,
                             notes=notes))
    note = next(n for n in notes if n["kind"] == "escalation_skipped")
    assert "no parseable guest name" in note["detail"]


def test_the_notes_pass_when_evidence_was_used(zendesk):
    zendesk({"Jane": [_ticket("t1", "80000002", channel="chat")]})
    notes = []
    _run(notes=notes)
    line = pl.escalation_trail(notes)
    assert line["mark"] == "pass" and "1 added as a candidate" in line["text"]


def test_an_errored_check_is_a_fail_not_a_silent_zero(monkeypatch, zendesk):
    zendesk({"Jane": [_ticket("t1", "80000003", channel="chat")]})
    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(zd, "escalation_evidence", boom)
    notes = []
    out = _run(notes=notes)
    assert out, "an erroring correlation must not remove the ordinary candidate"
    line = pl.escalation_trail(notes)
    assert line["mark"] == "fail" and "not judged" in line["text"]


def test_no_notes_means_no_trail_line():
    assert pl.escalation_trail([]) is None
    assert pl.escalation_trail(None) is None


# ── the Tier-2 legacy scorer ───────────────────────────────────────────────

QUAL = {"qualifies": True}


def test_escalation_points_only_for_a_full_qualification():
    assert pl.escalation_points(QUAL) == pl.ESCALATION_PTS > 0
    assert pl.escalation_points({"qualifies": False}) == 0.0
    assert pl.escalation_points({}) == 0.0
    assert pl.escalation_points(None) == 0.0


def test_the_score_is_unchanged_without_escalation():
    conf, corrob = pl.compose_tier2_score(2.0, 0.5, 1.0, 4.0, 3.0)
    assert conf == 2.0 + 0.5 + 1.0 + 4.0 + 3.0
    assert corrob == 2.0 + 0.5 + 1.0 + 4.0


def test_the_name_alone_still_does_not_promote_to_tier_1():
    conf, corrob = pl.compose_tier2_score(0, 0, 0, 0, 3.0, pl.escalation_points(None))
    ok, why = pl.tier1_promotable(conf, corrob)
    assert conf >= 3.0 and ok is False and "only agreement is the guest name" in why


def test_a_qualifying_chat_counts_as_corroboration_of_a_name():
    conf, corrob = pl.compose_tier2_score(0, 0, 0, 0, 3.0, pl.escalation_points(QUAL))
    assert corrob == pl.ESCALATION_PTS
    assert pl.tier1_promotable(conf, corrob)[0] is True


def test_a_chat_without_the_name_cannot_reach_tier_1():
    """Escalation points alone are below the threshold: the guest must also
    agree, so a chat cannot promote a booking on its own."""
    conf, corrob = pl.compose_tier2_score(0, 0, 0, 0, 0.0, pl.escalation_points(QUAL))
    assert pl.tier1_promotable(conf, corrob)[0] is False


def test_an_existing_strong_tier_1_is_still_promotable():
    """Regression: venue + name + both-search agreement promotes exactly as
    before, with or without the new term."""
    for esc in (0.0, pl.ESCALATION_PTS):
        conf, corrob = pl.compose_tier2_score(4.0, 0.2, 0.5, 4.0, 3.0, esc)
        assert pl.tier1_promotable(conf, corrob)[0] is True


def test_an_escalation_only_candidate_survives_the_noise_filter():
    c = {"score_venue": 0, "score_name": 0, "score_ticket": 0,
         "score_escalation": 2.0}
    assert pl._candidate_agrees(c) is True
    assert pl._candidate_agrees({**c, "score_escalation": 0}) is False


# ── end to end: the legacy per-ticket judgement the scorer reads ──────────

def test_escalation_evidence_needs_all_four():
    sig = zd.ticket_signals(_ticket("1", "11111111", channel="chat"))
    args = (IND, "Jane", "Doe", REVIEW_DATE)
    assert zd.escalation_evidence(sig, ISSUE_TEXT, *args)["qualifies"] is True
    # drop each leg in turn
    assert not zd.escalation_evidence({**sig, "channel": "email"}, ISSUE_TEXT, *args)["qualifies"]
    assert not zd.escalation_evidence({**sig, "guest_name": "Bob Ray"}, ISSUE_TEXT, *args)["qualifies"]
    assert not zd.escalation_evidence({**sig, "created_at": "2026-01-01"}, ISSUE_TEXT, *args)["qualifies"]
    assert not zd.escalation_evidence(sig, "unrelated text", *args)["qualifies"]


# ── cases added after the first mutation run ──────────────────────────────

def test_a_ticket_after_the_review_is_not_in_the_timeline():
    ind = {**IND, "visit_date_hint": None}
    assert zd.ticket_in_window("2026-10-26", ind, REVIEW_DATE)[0] is True    # +1d
    assert zd.ticket_in_window("2026-10-30", ind, REVIEW_DATE)[0] is False   # +5d


def test_a_chat_strengthens_a_name_only_direct_match(zendesk):
    """With no venue in the review a name-only ticket is a weak lead. The same
    ticket as a qualifying chat is not - it is no longer the name alone."""
    ind = {**IND, "experience_or_venue": None}
    zendesk({"Jane": [_ticket("t1", "90000001", channel="email", experience="")]})
    assert _run(ind)[0].get("weak") is True
    zendesk({"Jane": [_ticket("t1", "90000001", channel="chat", experience="")]})
    out = _run(ind)
    assert not out[0].get("weak")
    assert "chat ticket" in out[0]["matched_on"]


def test_a_chat_on_a_booking_already_matched_by_an_email_is_attached(zendesk):
    """Two tickets, one booking: the email matches name+venue, the chat is the
    escalation. One candidate, carrying both facts."""
    email = _ticket("e1", "90000002", channel="email", experience="Colosseum")
    chat = _ticket("c1", "90000002", channel="voice", experience="Vatican Museums")
    zendesk({"Jane": [email, chat]})
    out = _run()
    assert [s["booking_id"] for s in out] == ["90000002"]
    assert out[0]["escalation"]["qualifies"] is True
    assert "call ticket" in out[0]["matched_on"] and "venue" in out[0]["matched_on"]


def test_the_confidence_includes_the_escalation_points():
    conf, _ = pl.compose_tier2_score(0, 0, 0, 0, 3.0, pl.ESCALATION_PTS)
    assert conf == 3.0 + pl.ESCALATION_PTS
