"""
Which tab a review belongs in. One rule, used by everything.

This was derived twice - once in the API's tab filter and once again in the
dashboard from match_tier plus candidate_state plus whether the review text
carried a BID - and the two did not agree. Three mistakes came out of that:

  * a candidate an associate had CONFIRMED stayed in "possible matches",
    because the client's chain fell through to candidates for any tier that
    was not 1;
  * a Tier 1 booking found through Zendesk (no BID in the review text) was
    filed under "possible matches", where there were no candidates to pick;
  * the API and the dashboard disagreed about the same review, so a count and
    a list could contradict each other on screen.

The rule below is the only place this is decided. Everything else asks it.
"""

SENT = "sent"
IDENTIFIED = "identified"
CANDIDATES = "candidates"
UNTRACEABLE = "untraceable"

BUCKETS = (SENT, IDENTIFIED, CANDIDATES, UNTRACEABLE)

# API tab name -> bucket. The tab names are the dashboard's, kept for the
# existing query parameters.
TAB_TO_BUCKET = {
    "bid": IDENTIFIED,
    "possible_matches": CANDIDATES,
    "untraceable": UNTRACEABLE,
    "sent": SENT,
}


def classify(review, draft) -> str:
    """The one bucket this review belongs to.

    Precedence, and why:

    1. sent          - a sent review is done; it must not also appear under a
                       working tab, or the same card shows up twice.
    2. candidates    - the picker is open and nobody has chosen yet. This
                       outranks having a booking, because a Tier 2 shortlist
                       can carry a provisional booking that no human has
                       confirmed; showing it as identified would present a
                       guess as a fact.
    3. identified    - a booking is attached and nothing is pending. This is
                       deliberately about the BOOKING, not about match_tier:
                       an associate-confirmed Tier 2 and a Zendesk-found Tier
                       1 are both identified, and neither was before.
    4. untraceable   - nothing to work with.
    """
    if getattr(review, "status", "") == SENT:
        return SENT
    if draft is None:
        return UNTRACEABLE

    confirmed_bid = getattr(draft, "selected_candidate_bid", None)
    picker_open = bool(getattr(draft, "candidate_state", False)) and not confirmed_bid
    if picker_open:
        return CANDIDATES

    booking_id = (getattr(draft, "booking", None) or {}).get("id")
    if booking_id:
        return IDENTIFIED

    # No booking, but a shortlist exists: still a decision waiting for a human,
    # even if candidate_state was never set (an older draft, or a run that
    # stored candidates and then failed before setting the flag).
    if getattr(draft, "candidates_list", None):
        return CANDIDATES

    return UNTRACEABLE


def tier_label(draft) -> str:
    """T1 / T2 / — for display. Never invents a tier a draft does not have."""
    t = getattr(draft, "match_tier", None) if draft is not None else None
    return f"T{t}" if t else "—"


def is_unverified(draft) -> bool:
    """True when the booking came from the review's own BID but could not be
    verified in BigQuery. Identified, but the UI has to say so."""
    return bool((getattr(draft, "booking", None) or {}).get("_unverified"))
