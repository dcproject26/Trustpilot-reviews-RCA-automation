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


# A run that has not advanced a stage in this long is treated as dead.
#
# This is a JUDGEMENT and not a signal. Nothing reports that a run has died: a
# process killed mid-run, or a thread blocked inside a synchronous HTTP call,
# leaves its progress entry exactly as it was and never comes back to correct
# it. The only evidence available is that the entry has stopped moving, so the
# threshold is announced in the sentence rather than applied quietly.
#
# Ten minutes is longer than the slowest healthy STAGE observed (RCA
# generation, two to three minutes) by a wide margin, and shorter than the
# batch runner's own RUN_TIMEOUT_S of twelve minutes — so a run the runner
# will kill reads as dead slightly before it is killed, never after.
STALL_AFTER_S = 10 * 60

# A queued review that has not started in this long is not waiting its turn:
# the runner ahead of it is gone. Longer than STALL_AFTER_S because waiting IS
# what a queued review is supposed to be doing, and a full batch legitimately
# takes a while to reach the back of the queue.
QUEUE_STALL_AFTER_S = 30 * 60


def liveness(entry, now: float | None = None) -> tuple[str, int]:
    """(state, seconds since the run last moved) for a PIPELINE_PROGRESS entry.

    One judgement, used by the inbox row and by the re-run button's poll. Both
    used to answer "is this alive?" with "is there an entry?", which is a
    different question — and the whole reason a dead run reported itself as
    still searching.

      ""        no entry: this function was asked about a run nobody started.
      queued    handed to the runner, not started yet.
      running   the entry moved recently enough to be believed.
      stalled   the entry has stopped moving, or a queued review never started.
    """
    import time as _t
    if not entry:
        return "", 0
    now = _t.time() if now is None else now
    # updated_at is the heartbeat; started_at is the fallback for an entry
    # written by an older build, which is the honest floor — it can only make
    # a run look older than it is, never younger.
    last = entry.get("updated_at") or entry.get("started_at") or now
    since = max(0, int(now - last))
    if entry.get("queued"):
        return ("stalled" if since >= QUEUE_STALL_AFTER_S else "queued"), since
    return ("stalled" if since >= STALL_AFTER_S else "running"), since


def _mins(seconds: int) -> str:
    """'40 seconds' / '12 minutes'. A duration a reader can act on."""
    if seconds < 90:
        return f"{seconds} second{'' if seconds == 1 else 's'}"
    m = round(seconds / 60)
    return f"{m} minute{'' if m == 1 else 's'}"


def processing_state(review, draft) -> tuple[str, str]:
    """(state, sentence) for a review with no draft row. ("", "") otherwise.

    Four things wear the same blank card, and they do not want the same
    response:

      queued   the runner has this review and has not started it. Wait, and
               know that something is ahead of it.
      running  the pipeline is working on it. Wait. Re-running now would only
               start a second one.
      stalled  the run ended, or stopped moving, without writing a draft row.
               That is a BUG — the draft is written before anything that can
               fail — so it needs a re-run and probably a look at the log.

    THE REPORTED BUG. This used to read `if p:` — the presence of a progress
    entry WAS the definition of running. An entry is written at step 1 and
    removed in the run's `finally`, so the only case that reached "stalled"
    was a run that had already finished dying tidily. A run wedged inside a
    blocking model call (the Anthropic client defaults to a 600s read timeout
    and two retries, so half an hour per call) never reaches its `finally`,
    keeps its entry, and reported itself as "Step 1 of 8" for as long as the
    server stayed up. Elapsed time is the only evidence there is, so it is now
    what the answer turns on.

    PIPELINE_PROGRESS is in-process, so after a server restart a run that was
    genuinely in flight reads as stalled. That is the safe direction: it says
    "re-run it", and re-running a finished review is cheap while waiting
    forever on a dead one is not. It is also stated, rather than presented as
    a diagnosis.
    """
    # A FINISHED run flips the review to "draft"/"sent". A draft present while
    # the review is still "new" is a run that wrote its early draft (at the
    # match, pipeline.py) and then DIED before the end — Zendesk, insights and
    # the RCA are all missing. That used to return ("", "") right here on the
    # strength of "there is a draft", so a dead run and a finished one rendered
    # as the identical clean card. Only the finished case bails now; a draft
    # with the review still "new" falls through to the liveness check below,
    # exactly like a review with no draft row.
    status = getattr(review, "status", None)
    if draft is not None and status not in ("new", None, ""):
        return "", ""
    try:
        from server.pipeline import PIPELINE_PROGRESS
        p = PIPELINE_PROGRESS.get(getattr(review, "id", None))
    except Exception:
        p = None
    state, since = liveness(p)

    if state == "queued":
        pos, size = p.get("queue_position"), p.get("queue_size")
        where = (f"{pos} of {size} in a {p.get('queue_reason') or 'batch'} batch"
                 if pos and size else "in a batch")
        return "queued", (
            f"Queued for a run and not started yet — {where}, waiting "
            f"{_mins(since)}. Runs go one at a time, so this is normal until "
            f"it is not: after {QUEUE_STALL_AFTER_S // 60} minutes without "
            f"starting it is reported as stopped instead.")

    if state == "running":
        return "running", (
            f"Step {p.get('step', '?')} of {p.get('total', '?')} — "
            f"{p.get('stage', 'working')}, last moved {_mins(since)} ago. "
            f"Nothing has been searched for yet, so this is not a failed match.")

    if state == "stalled" and p:
        if p.get("queued"):
            return "stalled", (
                f"Queued {_mins(since)} ago and never started. We treat "
                f"{QUEUE_STALL_AFTER_S // 60} minutes in the queue as a runner "
                f"that is gone — that is a judgement from elapsed time, not "
                f"something the run reported. Nothing was searched for, so "
                f"this is not a failed match. Re-run it.")
        return "stalled", (
            f"Stopped at step {p.get('step', '?')} of {p.get('total', '?')} — "
            f"{p.get('stage', 'working')}. It has not moved for "
            f"{_mins(since)}, and we treat {STALL_AFTER_S // 60} minutes "
            f"without progress as a dead run — that is a judgement from "
            f"elapsed time, not something the run reported. No draft row was "
            f"written, so this is not a booking we could not find. Re-run it.")

    if draft is not None:
        # A draft was written (the run reached the match) but the review is
        # still "new" and nothing is in progress here: the run died after the
        # match, before the analysis. Surface what it recorded about its own
        # death rather than a blank card. (A1 + A2)
        recorded = _recorded_run_failure(draft)
        return "stalled", (recorded or (
            "A draft was written at the match, but the run did not finish and "
            "no run is in progress on this server — the Zendesk timeline, "
            "insights and RCA after the match were never written. Re-run it."))

    return "stalled", (
        "No draft row was ever written, and no run is in progress on this "
        "server. The draft is saved before anything that can fail, so this is "
        "a run that died early or a server that restarted mid-run — not a "
        "booking we could not find. Re-run it.")


def _recorded_run_failure(draft) -> str:
    """The reason a dead run left on its own trail, as a plain sentence, or "".

    A run records its death in one of two shapes, and both live on
    `confidence_trail` where the inbox list never looks: `record_run_failure`
    writes a `fail` entry naming the exception, and a hard kill (the container
    reclaimed mid-run) leaves only the partial-persist marker "This run has not
    finished". Surface whichever is there, so the death is said where the reader
    is rather than buried on a card they have to open. (A2)
    """
    import re
    trail = getattr(draft, "confidence_trail", None) or []
    for e in reversed(trail):
        if not isinstance(e, dict):
            continue
        if e.get("mark") == "fail":
            txt = re.sub("<[^>]+>", "", e.get("text") or e.get("title") or "").strip()
            return (f"The last run failed and did not finish — {txt} Re-run it."
                    if txt else "")
        if e.get("mark") == "warn" and "has not finished" in (e.get("text") or ""):
            return ("The last run reached the match and then stopped without "
                    "finishing — the Zendesk timeline, insights and RCA after "
                    "it were never written. Re-run it.")
    return ""


def tier_label(draft) -> str:
    """T1 / T2 / — for display. Never invents a tier a draft does not have."""
    t = getattr(draft, "match_tier", None) if draft is not None else None
    return f"T{t}" if t else "—"


def is_unverified(draft) -> bool:
    """True when the booking came from the review's own BID but could not be
    verified in BigQuery. Identified, but the UI has to say so."""
    return bool((getattr(draft, "booking", None) or {}).get("_unverified"))
