"""Where the guest was left waiting, computed rather than noticed.

The timeline says what happened. It cannot say what DIDN'T — and a guest who
wrote three times over two days and got one reply on the third day is a
failure that lives entirely in the space between rows. The checklist has had
the words for it since v7.1 ("Delayed response to guest", "2+ non-autoresolved
queries from the guest", "Guest query not addressed / no response given"), and
nothing computed them: they were left for the model to spot in a list of forty
events, which it does when the gap is glaring and misses when it is merely
long.

THIS IS A FLAG, NOT A TIMELINE ROW. The timeline states what the events say;
being left unanswered is a finding about our handling, and findings go to
Flags where they route to a team.

WHAT COUNTS AS AN ANSWER. Only a human on our side — actor "co". Not a system
mail, not an automation, not the supply partner. An automated
"we've received your message" is exactly what the guest was not asking for,
and counting it as a reply is how a two-day silence reads as answered.
"""
from datetime import datetime

# Who is who on the timeline. The vocabulary the shaping prompt writes.
_GUEST = {"guest"}
_US = {"co"}

# A single silence has to be long before it is a finding: same-day handling is
# normal and flagging it makes the section noise people skim past. A guest who
# wrote TWICE needs no clock — they were plainly waiting, and said so by
# writing again.
SLOW_HOURS = 12
CHASE_FLAG = 2


def _when(e):
    """The event's moment, or None. ISO first, because that is what the
    pipeline attaches; a display string is not parsed here — a wrong time is
    worse than no time, since it produces a duration nobody can check."""
    for k in ("time_sort", "time"):
        v = str((e or {}).get(k) or "").strip()
        if not v:
            continue
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00").replace(" ", "T"))
        except ValueError:
            continue
    return None


def response_gaps(events, slow_hours: int = SLOW_HOURS) -> list:
    """Every stretch where the guest wrote and we had not yet replied.

    Returns [{check, detail, hours, chases, answered}] — one per stretch,
    already filtered to the ones worth raising. `answered` is False when the
    stretch never closed, which is the worst case and a different check.

    Events with no readable time are SKIPPED and counted, never guessed at: a
    duration computed from a time we invented is a number a reader cannot
    check and would act on.
    """
    rows = [e for e in (events or []) if isinstance(e, dict)]
    timed, undated = [], 0
    for e in rows:
        w = _when(e)
        if w is None:
            undated += 1
        else:
            timed.append((w, e))
    timed.sort(key=lambda x: x[0])

    out, open_at, chases = [], None, 0
    for when, e in timed:
        actor = str(e.get("actor") or "").strip().lower()
        if actor in _GUEST:
            if open_at is None:
                open_at, chases = when, 1
            else:
                chases += 1
        elif actor in _US and open_at is not None:
            out.append(_gap(open_at, when, chases, True, slow_hours))
            open_at, chases = None, 0
    if open_at is not None:
        # Never answered. Measured to the LAST event on the timeline, which is
        # the last moment we know about — not to now, which would grow every
        # time the card is opened and make the number meaningless.
        out.append(_gap(open_at, timed[-1][0], chases, False, slow_hours))

    gaps = [g for g in out if g]
    for g in gaps:
        g["undated_events"] = undated
    return gaps


def _gap(start, end, chases, answered, slow_hours):
    hours = max(0.0, (end - start).total_seconds() / 3600.0)
    if answered and hours < slow_hours and chases < CHASE_FLAG:
        return None
    if not answered:
        check = "Guest query not addressed / no response given"
        detail = (f"The guest wrote at {start:%d %b %H:%M} and no reply from us "
                  f"appears on the timeline at all"
                  + (f"; they followed up {chases - 1} more time"
                     f"{'' if chases == 2 else 's'}" if chases > 1 else "")
                  + ".")
    elif chases >= CHASE_FLAG:
        check = "2+ non-autoresolved queries from the guest"
        detail = (f"The guest wrote {chases} times between {start:%d %b %H:%M} "
                  f"and {end:%d %b %H:%M} before we replied "
                  f"({hours:.0f}h).")
    else:
        check = "Delayed response to guest"
        detail = (f"{hours:.0f}h between the guest's message at "
                  f"{start:%d %b %H:%M} and our reply at {end:%d %b %H:%M}.")
    return {"check": check, "detail": detail, "hours": round(hours, 1),
            "chases": chases, "answered": answered}


def gap_flags(events, slow_hours: int = SLOW_HOURS) -> list:
    """The gaps, as flags on the CO team.

    CO because these are all failures of OUR handling — the supply partner
    being slow is a different finding and routes elsewhere. One flag per
    stretch rather than one summary flag: two separate silences on one ticket
    are two things that went wrong, and merging them hides the second.
    """
    return [{"team": "CO", "flag": g["check"], "evidence": g["detail"]}
            for g in response_gaps(events, slow_hours)]
