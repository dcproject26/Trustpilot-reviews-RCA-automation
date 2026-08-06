"""Was the DSS path followed, on a case where the guest asked us first?

THE QUESTION THIS ANSWERS. A guest who contacts support, gets handled, and
then writes a review has already been through a decision sheet: there is a
prescribed path for their scenario and we either took it or we did not. A
review written with no prior contact has no such path to compare against —
nobody had the chance to follow or miss it.

Those two cases must not produce the same words. "The DSS path was not
followed" on a booking where the guest never wrote in is an accusation about
a step nobody was required to take; "not applicable" on a booking where they
wrote in three times and were mishandled hides the finding. `dss.prescribes`
already carries what the sheet says and rule 14 keeps compliance out of it,
which is right — the verdict belongs here, beside the evidence for it.

WHY THE PRECONDITION IS COMPUTED AND NOT ASKED FOR. "Did the guest reach out
before the review went up?" is a comparison between two timestamps we hold.
Asking the model to decide it invites the answer to drift with the narrative,
and a stored draft cannot be re-asked. The model still writes the verdict —
whether the prescribed path was taken is a reading of the case, not a
timestamp — but it is only allowed to write one where the timestamps say a
path existed.
"""
from datetime import datetime, timezone

# The four answers, and why each is distinguishable from the others:
#   followed        — the path existed and we took it.
#   not_followed    — the path existed and we did not. A finding.
#   no_prior_contact— nobody could have followed it. NOT a pass and NOT a miss.
#   unestablished   — there was prior contact but nothing settles what we did,
#                     or no DSS row matched the scenario at all.
APPLIES = "applies"
NO_PRIOR_CONTACT = "no_prior_contact"
NO_TIMELINE = "no_timeline"

VERDICTS = ("followed", "not_followed", "unestablished")


def _dt(value):
    """A timestamp from whatever the row holds, or None."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(text)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def guest_contacted_before(events, review_at) -> tuple:
    """(state, why) — did the guest write in before the review went up?

    Only GUEST-authored events count. An internal note or an automated ping
    before the review is us talking to ourselves, and reading one as "the
    guest reached out" would open a DSS verdict on a case where they never
    did.

    Returns NO_TIMELINE rather than NO_PRIOR_CONTACT when there is nothing to
    read: "we have no case history for this booking" and "the guest never
    contacted us" are different facts, and collapsing them makes an
    unfetched ticket look like a quiet guest.
    """
    rows = [e for e in (events or []) if isinstance(e, dict)]
    if not rows:
        return NO_TIMELINE, ("no support timeline was loaded for this booking, "
                             "so whether the guest wrote in first is unknown — "
                             "not answered as 'they did not'")
    posted = _dt(review_at)
    guest_rows = [e for e in rows
                  if str(e.get("actor") or "").strip().lower() == "guest"]
    if not guest_rows:
        return NO_PRIOR_CONTACT, ("the case history holds no guest-authored "
                                  "contact, so no DSS path was open to follow")
    if posted is None:
        # A timeline with guest contact and no review timestamp: the contact
        # is real, and refusing to say so because one field is missing would
        # lose a finding. Announced, because it is a judgement.
        return APPLIES, ("the guest contacted support; the review has no "
                         "timestamp, so the contact is assumed to precede it")
    earlier = [e for e in guest_rows if (_dt(e.get("time_sort") or e.get("time"))
                                         or posted) < posted]
    if not earlier:
        return NO_PRIOR_CONTACT, ("the guest contacted support only after the "
                                  "review was posted, so no DSS path was open "
                                  "to follow when they wrote it")
    return APPLIES, (f"the guest contacted support {len(earlier)} time(s) "
                     f"before the review was posted")


def gate_dss_followed(verdict, events, review_at) -> tuple:
    """(verdict, note) for `dss.followed`, or None when nothing needs saying.

    Policing, not deciding. The model reads the case and says whether the path
    was taken; this refuses a verdict the timeline gives no standing for, and
    reports a case that had standing and was left unanswered.
    """
    state, why = guest_contacted_before(events, review_at)
    said = str(verdict or "").strip().lower()

    if state != APPLIES:
        if said in ("followed", "not_followed"):
            return None, (f"dss.followed {said!r} → not applicable — {why}. A "
                          f"verdict on a path nobody could take reads as praise "
                          f"or blame for a step that was never owed")
        return None, None          # correctly silent; the ordinary case

    if said in ("followed", "not_followed", "unestablished"):
        return said, None          # answered, with standing. Nothing to report.
    return "unestablished", (f"dss.followed was not answered — {why}, so the "
                             f"decision sheet governed this contact and whether "
                             f"we took its path is unrecorded")
