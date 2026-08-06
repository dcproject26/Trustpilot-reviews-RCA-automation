"""When the warehouse has no usable guest name, Zendesk does.

The guest name is the second strongest identifier after the booking id, and
the only one that separates two bookings at the same venue on the same date —
exactly where venue and date have nothing left to say. And the column we read
it from is unusable in two large classes of row:

  * HASHED. `fct_bookings.primary_guest_name` is a base64 PII hash on a large
    share of rows.
  * AN INTERNAL LABEL. Desk-made and corporate bookings carry "Customer Ops
    Lead" and the like — our own wording, which no guest ever types.

In both cases the guest still raised a support ticket, and that ticket carries
their real name: in its guest-name custom field, or as the requester on the
account. So the strongest signal after the BID was being abandoned while a
readable copy sat one field away.

THREE RULES THIS FILE EXISTS TO HOLD:

  1. The warehouse name WINS whenever it is readable. It is the booking's own
     record. The fallback runs only where that record has nothing to give.
  2. The reader is always told WHICH source settled it. "The names agree" off
     a Zendesk ticket is a different claim from the same words off the booking
     record, and a reader who cannot tell them apart cannot weigh it.
  3. "Zendesk had nothing either" must not look like "Zendesk was not asked" —
     CLAUDE.md §1, the failure this project keeps repeating.
"""
import pytest

from server.bid_indicator_check import check

HASH = "ka5YFyVDPTb8Izueol+UqKl1JMDgL78s8ZO6ntx/LA0="
AUTHOR = "Mariana Campos"


def _check(booking, author=AUTHOR):
    base = {"experienceName": "Swiss Travel Pass", "primary_guest_name": ""}
    return check("the tickets never arrived", {**base, **booking},
                 author=author, received_at="2026-08-05")


def _guest(got):
    return next(s for s in got["signals"] if s["name"] == "guest")


# ── the fallback fires, and only when it should ────────────────────────────

def test_an_internal_label_falls_back_to_the_zendesk_name():
    """The reported case. 'Customer Ops Lead' on the booking, the guest's real
    name on the ticket they raised."""
    got = _check({"primary_guest_name": "Customer Ops Lead",
                  "zendesk_guest_name": "Mariana Campos"})
    assert _guest(got)["state"] == "match", _guest(got)


def test_a_hashed_name_falls_back_to_the_zendesk_name():
    """The larger class by row count."""
    got = _check({"primary_guest_name": HASH,
                  "zendesk_guest_name": "Mariana Campos"})
    assert _guest(got)["state"] == "match", _guest(got)


def test_a_blank_name_falls_back_to_the_zendesk_name():
    got = _check({"primary_guest_name": "",
                  "zendesk_guest_name": "Mariana Campos"})
    assert _guest(got)["state"] == "match", _guest(got)


def test_the_requester_name_is_used_when_there_is_no_guest_field():
    """Two Zendesk sources: the ticket's guest-name custom field, and the
    requester on the account. The second is what get_timeline already
    returns, so a re-run has it even when the first is empty."""
    got = _check({"primary_guest_name": "Customer Ops Lead",
                  "zendesk_requester_name": "Mariana Campos"})
    assert _guest(got)["state"] == "match", _guest(got)


def test_the_guest_field_is_preferred_over_the_requester():
    """The requester is whoever owns the Zendesk account — an assistant, a
    parent, a colleague. The ticket's own guest-name field is about the
    booking, so it is the better of the two.

    Asserted on the outcome and on the LOSING name's absence: the winning
    name here is also the author's, so its presence in the sentence proves
    nothing about which source was read."""
    got = _check({"primary_guest_name": "",
                  "zendesk_guest_name": "Mariana Campos",
                  "zendesk_requester_name": "Someone Else"})
    g = _guest(got)
    assert g["state"] == "match", g
    assert "Someone Else" not in g["why"], g["why"]


def test_a_readable_warehouse_name_is_not_overridden():
    """RULE 1. The booking's own record is authoritative wherever it is
    readable; a Zendesk name must never displace it. If it could, a ticket
    raised by a travel agent would rewrite the guest on the booking."""
    got = _check({"primary_guest_name": "Fredrik Andersson",
                  "zendesk_guest_name": "Mariana Campos"})
    g = _guest(got)
    assert "Zendesk" not in g["why"], g["why"]
    assert "Fredrik Andersson" in g["why"], g["why"]


# ── it does not launder rubbish ────────────────────────────────────────────

def test_an_internal_label_on_the_zendesk_side_is_rejected_too():
    """Zendesk carries desk labels as well. Accepting one here would
    reintroduce the exact bug on the fallback path — a meaningless
    disagreement, one layer further from anywhere a reader would look."""
    got = _check({"primary_guest_name": HASH,
                  "zendesk_guest_name": "Customer Ops Lead"})
    assert _guest(got)["state"] == "unchecked", _guest(got)
    assert "guest" not in (got.get("contradictions") or []), got


def test_a_hash_on_the_zendesk_side_is_rejected_too():
    got = _check({"primary_guest_name": "", "zendesk_guest_name": HASH})
    assert _guest(got)["state"] == "unchecked", _guest(got)


def test_a_fallback_disagreement_is_never_a_contradiction():
    """A disagreement on the BOOKING record is already not decisive on its
    own — bookings are legitimately made under other people's names. On a
    fallback name it is weaker still, so it is reported and never raises the
    flag."""
    got = _check({"primary_guest_name": "Customer Ops Lead",
                  "zendesk_guest_name": "Fredrik Andersson"})
    assert _guest(got)["state"] == "unchecked", _guest(got)
    assert got["contradictions"] == [], got


# ── and it always says which source answered ───────────────────────────────

def test_the_reason_names_zendesk_as_the_source():
    """RULE 2. Otherwise an agreement off a support ticket reads exactly like
    an agreement off the booking record."""
    got = _check({"primary_guest_name": "Customer Ops Lead",
                  "zendesk_guest_name": "Mariana Campos"})
    why = _guest(got)["why"]
    assert "Zendesk" in why, why
    assert "Customer Ops Lead" in why, why
    assert "Mariana Campos" in why, why


def test_the_reason_still_names_which_kind_of_unusable_the_booking_was():
    """A hash and a desk label call for different responses — one is a PII
    policy, the other a record that needs correcting. Collapsing them into
    'no readable name' loses that."""
    hashed = _guest(_check({"primary_guest_name": HASH,
                            "zendesk_guest_name": "Mariana Campos"}))["why"]
    label = _guest(_check({"primary_guest_name": "Customer Ops Lead",
                           "zendesk_guest_name": "Mariana Campos"}))["why"]
    blank = _guest(_check({"primary_guest_name": "",
                           "zendesk_guest_name": "Mariana Campos"}))["why"]
    assert "hash" in hashed, hashed
    assert "internal label" in label, label
    assert "no guest name recorded" in blank, blank
    assert len({hashed, label, blank}) == 3


def test_looked_and_found_nothing_is_not_the_same_as_did_not_look():
    """RULE 3, and the one this codebase gets wrong most often. With no
    Zendesk name at all the sentence must say Zendesk was consulted, or the
    reader cannot tell a missing fallback from a missing feature."""
    got = _guest(_check({"primary_guest_name": "Customer Ops Lead"}))
    assert got["state"] == "unchecked"
    assert "Zendesk" in got["why"], got["why"]
    assert "either" in got["why"], got["why"]


def test_the_two_empty_cases_are_distinguishable():
    """"The booking is a desk label and Zendesk has nothing" versus "the
    booking is a desk label and Zendesk gave us the guest" are different
    outcomes; so are the two ways of having no name at all."""
    no_zd = _guest(_check({"primary_guest_name": "Customer Ops Lead"}))["why"]
    with_zd = _guest(_check({"primary_guest_name": "Customer Ops Lead",
                             "zendesk_guest_name": "Mariana Campos"}))["why"]
    assert no_zd != with_zd


def test_a_review_with_no_author_never_reaches_the_fallback():
    """Nothing to compare against, so consulting Zendesk would be work done to
    reach the same answer by a longer route — and a sentence about Zendesk
    would imply a check that could not have decided anything."""
    got = _guest(_check({"primary_guest_name": "Customer Ops Lead",
                         "zendesk_guest_name": "Mariana Campos"}, author=""))
    assert got["state"] == "unchecked"
    assert "Zendesk" not in got["why"], got["why"]


# ── the pipeline actually supplies it ──────────────────────────────────────

def test_the_shortlist_candidates_carry_the_zendesk_name():
    """NEGATIVE-shaped source assertion, permitted by CLAUDE.md: the fallback
    can be perfect and still never fire if nothing ever sets the field. This
    is the wiring, and wiring that exists nowhere is the failure mode §1 opens
    with — a validator called by nothing."""
    import inspect
    from server import pipeline
    src = inspect.getsource(pipeline)
    assert '_c["zendesk_guest_name"] = _sig.get("guest_name", "")' in src, (
        "shortlist candidates no longer carry the Zendesk guest name, so the "
        "fallback can never fire on the path it was written for")


# ── the source the DATA-SOURCE SPEC actually names ─────────────────────────
#
# "Primary guest name | Zendesk | ticket_facts.guest_full_name (BQ guestName
# is a hash — do NOT use)". The dashboard has followed that for a while. This
# check did not: it compared the reviewer against the hash and threw the
# comparison away, on exactly the rows where a real name was available one
# field over.

def test_ticket_facts_guest_full_name_is_used():
    got = check("the tickets never arrived",
                {"experienceName": "Swiss Travel Pass",
                 "primary_guest_name": HASH},
                author=AUTHOR, received_at="2026-08-05",
                ticket_facts={"guest_full_name": "Mariana Campos"})
    assert _guest(got)["state"] == "match", _guest(got)


def test_ticket_facts_outranks_the_other_two_zendesk_sources():
    """The spec's field is the source of truth, and it is the best of the
    three: the name a CE actually addressed the guest by, rather than whoever
    happens to own the Zendesk account.

    ASSERTED ON THE OUTCOME, NOT ON A NAME IN THE SENTENCE. This first checked
    that "Mariana Campos" appeared in `why` — which is the AUTHOR's name, and
    is printed in the disagreement sentence too, so the assertion held however
    the sources were ordered. A mutation reversing the order survived it. The
    state is the thing that actually differs: the spec's field agrees with the
    reviewer and the other two do not.
    """
    got = check("the tickets never arrived",
                {"experienceName": "Swiss Travel Pass",
                 "primary_guest_name": "Customer Ops Lead",
                 "zendesk_guest_name": "Someone Else",
                 "zendesk_requester_name": "Someone Else Again"},
                author=AUTHOR, received_at="2026-08-05",
                ticket_facts={"guest_full_name": "Mariana Campos"})
    g = _guest(got)
    assert g["state"] == "match", g
    assert "Someone Else" not in g["why"], (
        "a weaker source was consulted before the spec's field", g["why"])


def test_the_guest_field_outranks_the_requester_on_the_outcome():
    """The same ordering rule one rung down, and the same trap avoided: the
    ticket's guest-name field is about the BOOKING, the requester is whoever
    owns the account."""
    got = check("the tickets never arrived",
                {"experienceName": "Swiss Travel Pass",
                 "primary_guest_name": "Customer Ops Lead",
                 "zendesk_guest_name": "Mariana Campos",
                 "zendesk_requester_name": "Someone Else Again"},
                author=AUTHOR, received_at="2026-08-05")
    g = _guest(got)
    assert g["state"] == "match", g
    assert "Someone Else Again" not in g["why"], g["why"]


def test_a_hashed_ticket_fact_is_rejected_like_any_other():
    """The extraction prompt already rejects hashes, but a stale draft may
    carry one. The guard is applied to every source, not just the ones we
    expect to be dirty."""
    got = check("the tickets never arrived",
                {"experienceName": "Swiss Travel Pass",
                 "primary_guest_name": "Customer Ops Lead",
                 "zendesk_guest_name": "Mariana Campos"},
                author=AUTHOR, received_at="2026-08-05",
                ticket_facts={"guest_full_name": HASH})
    assert _guest(got)["state"] == "match", _guest(got)
    assert "Mariana Campos" in _guest(got)["why"], _guest(got)["why"]


def test_no_ticket_facts_is_not_an_error():
    """The pipeline's own call runs BEFORE the timeline is fetched, so it has
    no ticket_facts to give. That path must degrade to the other sources, not
    raise."""
    got = check("the tickets never arrived",
                {"experienceName": "Swiss Travel Pass",
                 "primary_guest_name": "Customer Ops Lead",
                 "zendesk_guest_name": "Mariana Campos"},
                author=AUTHOR, received_at="2026-08-05")
    assert _guest(got)["state"] == "match", _guest(got)


def test_the_card_passes_ticket_facts_in():
    """NEGATIVE-shaped source assertion: the chain can be perfect and never
    fire if the caller does not supply the field."""
    import inspect
    from server import api
    src = inspect.getsource(api)
    assert 'ticket_facts=getattr(d, "ticket_facts", None)' in src, (
        "the card's indicator check no longer passes ticket_facts, so the "
        "spec's guest-name source is unreachable from it")
