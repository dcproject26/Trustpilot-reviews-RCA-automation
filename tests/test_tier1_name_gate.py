"""A booking id the guest quoted is not refused over a name nobody compared.

THE REPORTED CASE. A Spanish review: "Hemos comprado unas entradas con reserva
33204378..." — the guest quoting their own booking id, in the first person.
The id was extracted, verified in BigQuery, and the card still routed it to
"Possible matches — associate to confirm", saying:

    BID 33204378 resolves to a booking that does not match this review —
    BigQuery returned 'Pena Palace & Park Tickets', whose guest name scores
    0.0 against the reviewer...

THE GATE IS CORRECT AND IS NOT WHAT CHANGED. A booking id in review text needs
the name or the venue behind it, because guests do quote reference numbers off
shared vouchers, forwarded emails and screenshots. Date alone is a 30-day
window and corroborates nothing. That stays.

WHAT WAS WRONG IS THAT 0.0 MEANT FOUR DIFFERENT THINGS. `_name_score`
tokenises and compares, with no guard for anything else, so a PII hash, an
internal desk label, a blank, and a genuinely different person all score 0.0.
The gate read "we could not compare" as "they disagree", and the card reported
a disagreement that never happened.

AND THE GUARD ALREADY EXISTED IN THE SAME FILE. `_is_hashed_name` sits at the
top of pipeline.py — "BigQuery returns primary_guest_name hashed, so any name
comparison against it is noise" — and was applied at exactly one place: the
candidate RANKING, which also falls back to the ticket's own guest name and
takes the best of three sources. So one file held two name comparisons under
different rules, and the weaker one made the more consequential decision.
"""
import pytest

from server.services.zendesk import _name_score, GUEST_NAME_UNAVAILABLE
from server.names import parse_author, is_internal_booking_name
from server.pipeline import _is_hashed_name

HASH = "ka5YFyVDPTb8Izueol+UqKl1JMDgL78s8ZO6ntx/LA0="


# ── the premise: 0.0 does not mean "disagrees" ─────────────────────────────

@pytest.mark.parametrize("pgn,label", [
    (HASH, "a warehouse PII hash"),
    ("Customer Ops Lead", "an internal desk label"),
    ("", "no name at all"),
    ("Maria Silva", "a genuinely different person"),
])
def test_four_different_situations_all_score_zero(pgn, label):
    """The whole reason the gate was wrong. Only the last of these is a
    disagreement; the score cannot tell them apart, so something above it
    must."""
    first, last = parse_author("Ioan")
    assert _name_score(pgn, first, last) == 0.0, label


def test_the_reviewers_own_name_still_scores_full():
    """The control. If this broke, the fix would be hiding a real signal."""
    first, last = parse_author("Ioan")
    assert _name_score("Ioan Popescu", first, last) == 1.0


# ── so the gate must classify BEFORE it scores ─────────────────────────────

@pytest.mark.parametrize("pgn", [HASH, "qS+BQFdVbq3NdZgQ/2tJj+aaaaaaaaaa"])
def test_a_warehouse_hash_is_recognised_as_uncomparable(pgn):
    assert _is_hashed_name(pgn) is True, pgn


@pytest.mark.parametrize("pgn", ["Customer Ops Lead", "internal booking"])
def test_a_desk_label_is_recognised_as_uncomparable(pgn):
    assert is_internal_booking_name(pgn) is True, pgn


@pytest.mark.parametrize("pgn", ["Maria Silva", "Ioan Popescu", "Anna Ops",
                                 "Ernesto Testa"])
def test_a_real_name_is_comparable(pgn):
    """A false positive here would turn a genuine disagreement into "could not
    compare" and let a quoted-from-elsewhere booking id through — the inverse
    bug, and the one the gate exists to prevent."""
    assert _is_hashed_name(pgn) is False, pgn
    assert is_internal_booking_name(pgn) is False, pgn


# ── the gate applies them, and says which happened ─────────────────────────
#
# NEGATIVE source assertions, permitted by CLAUDE.md: the Tier-1 branch needs
# a live BigQuery and a live Zendesk to drive end to end, and unreachability
# cannot defeat "this string appears nowhere".

def _gate_src():
    import inspect
    from server import pipeline
    src = inspect.getsource(pipeline)
    head, _, tail = src.partition("pgn = bq_row.get(\"primary_guest_name\")")
    assert tail, "the Tier-1 name gate has been renamed or removed"
    return tail.split("# Date alone is not evidence")[0]


def test_the_gate_no_longer_scores_the_raw_warehouse_name():
    """The one line that caused this. `_nsc(pgn, ...)` scored the reviewer
    straight against a hash."""
    assert "_nsc(pgn," not in _gate_src(), (
        "the Tier-1 gate scores the raw warehouse name again — a hash, a desk "
        "label and a real disagreement are back to being one answer")


def test_the_gate_guards_the_hash_and_the_desk_label():
    src = _gate_src()
    assert "_is_hashed_name(pgn)" in src, "the hash guard is not applied"
    assert "_is_internal_booking_name(pgn)" in src, "the desk-label guard is not applied"


def test_the_gate_asks_zendesk_before_giving_up_on_the_name():
    """The fallback the ranking path already had. Without it the gate gives up
    while a readable name sits one lookup away."""
    assert "guest_name_for_bid" in _gate_src(), (
        "the Tier-1 gate no longer consults Zendesk for the guest name")


def test_the_gate_tracks_whether_a_comparison_HAPPENED():
    """`name_conf` alone cannot carry this: 0.0 is what both answers look
    like."""
    assert "name_checked" in _gate_src()


def test_the_card_does_not_report_a_score_for_a_comparison_that_never_ran():
    """The sentence the user was shown. "scores 0.0 against the reviewer"
    asserts a comparison; where none happened it is simply false."""
    import inspect
    from server import pipeline
    src = inspect.getsource(pipeline)
    # A contiguous fragment: the sentence is built from two adjacent string
    # literals, so the whole phrase never appears in the source as one run.
    assert "compared to the reviewer at all" in src, (
        "the trail reports a numeric score even when nothing was compared")
    # Again a fragment, for the same reason: the sentence is assembled from
    # adjacent literals across a line break.
    assert "disagreement: nothing was compared." in src


def test_the_headline_does_not_claim_a_mismatch_it_did_not_establish():
    """"does not match this review" is a finding. Where the name could not be
    compared and the venue is simply absent from the text, nothing was
    established either way — and the reader acts differently on the two."""
    import inspect
    from server import pipeline
    src = inspect.getsource(pipeline)
    assert "booking we could not tie to this reviewer" in src


# ── the Zendesk lookup itself ──────────────────────────────────────────────

def test_every_unavailable_reason_is_distinguishable():
    """"Zendesk had no name" and "Zendesk was never asked" must not read the
    same — CLAUDE.md §1, and the reason this lookup returns a sentence rather
    than just an empty string."""
    assert len(set(GUEST_NAME_UNAVAILABLE.values())) == len(GUEST_NAME_UNAVAILABLE)
    for k, v in GUEST_NAME_UNAVAILABLE.items():
        assert v.strip(), k


def test_a_lookup_with_no_bid_reports_rather_than_raising():
    """It runs inside matching, which must not die over a hint."""
    import asyncio
    from server.services.zendesk import guest_name_for_bid
    name, why = asyncio.run(guest_name_for_bid(""))
    assert name == ""
    assert why.strip()


def test_an_offline_zendesk_says_so_rather_than_reporting_no_name():
    """The distinction that decides whether anyone goes and looks."""
    import asyncio
    from server.services.zendesk import guest_name_for_bid
    name, why = asyncio.run(guest_name_for_bid("33204378"))
    assert name == ""
    # Whichever of the unavailable reasons applies here, it must be one of the
    # named ones rather than a bare empty string.
    assert why in GUEST_NAME_UNAVAILABLE.values() or "failed" in why, why
