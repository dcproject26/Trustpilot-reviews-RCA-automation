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
# A review whose pipeline has not written a draft row. NOT untraceable: the
# search has not run, so nothing has been found or not found yet.
PROCESSING = "processing"

BUCKETS = (SENT, IDENTIFIED, CANDIDATES, UNTRACEABLE, PROCESSING)

# API tab name -> bucket. The tab names are the dashboard's, kept for the
# existing query parameters.
TAB_TO_BUCKET = {
    "bid": IDENTIFIED,
    "possible_matches": CANDIDATES,
    "untraceable": UNTRACEABLE,
    "processing": PROCESSING,
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
        # NOT untraceable. Untraceable is a RESULT — we looked for the booking
        # and did not find it — and it can only be reached by a run that got
        # as far as writing a draft row, which happens at step 5b, after BID
        # extraction and the BigQuery search.
        #
        # A review with no draft row has not been searched. Filing it under
        # Untraceable made "we are still working on this" and "we searched and
        # found nothing" the same tab, named after the second one. Press
        # Refresh from Slack and fifteen reviews appear in Untraceable at
        # once, then drain out as their runs finish — which reads as fifteen
        # failed matches, and was reported as one.
        #
        # Whether the run is still going or died before saving is a different
        # question, answered per review by is_running() below. Both belong
        # here rather than in Untraceable, because neither has looked yet.
        return PROCESSING

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


def processing_state(review, draft) -> tuple[str, str]:
    """(state, sentence) for a review with no draft row. ("", "") otherwise.

    Two things wear the same blank card, and they need opposite responses:

      running  the pipeline is working. Wait. Re-running now would only start
               a second one.
      stalled  the run ended without writing a draft row. That is a BUG — the
               draft is written before anything that can fail — so it needs a
               re-run and probably a look at the log.

    PIPELINE_PROGRESS is in-process, so after a server restart a run that was
    genuinely in flight reads as stalled. That is the safe direction: it says
    "re-run it", and re-running a finished review is cheap while waiting
    forever on a dead one is not. It is also stated, rather than presented as
    a diagnosis.
    """
    if draft is not None:
        return "", ""
    try:
        from server.pipeline import PIPELINE_PROGRESS
        p = PIPELINE_PROGRESS.get(getattr(review, "id", None))
    except Exception:
        p = None
    if p:
        return "running", (
            f"Step {p.get('step', '?')} of {p.get('total', '?')} — "
            f"{p.get('stage', 'working')}. Nothing has been searched for yet, "
            f"so this is not a failed match.")
    return "stalled", (
        "No draft row was ever written, and no run is in progress on this "
        "server. The draft is saved before anything that can fail, so this is "
        "a run that died early or a server that restarted mid-run — not a "
        "booking we could not find. Re-run it.")


def tier_label(draft) -> str:
    """T1 / T2 / — for display. Never invents a tier a draft does not have."""
    t = getattr(draft, "match_tier", None) if draft is not None else None
    return f"T{t}" if t else "—"


def is_unverified(draft) -> bool:
    """True when the booking came from the review's own BID but could not be
    verified in BigQuery. Identified, but the UI has to say so."""
    return bool((getattr(draft, "booking", None) or {}).get("_unverified"))
