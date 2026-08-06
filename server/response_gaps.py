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
import re
from datetime import datetime

# Who is who on the timeline. The vocabulary the shaping prompt writes.
_GUEST = {"guest"}
_US = {"co"}

# WHAT WE PROMISED IS THE CLOCK. There is no fixed number here on purpose.
#
# An earlier version flagged any silence over 12 hours, which is a rule nobody
# agreed to: it accuses us of lateness against a deadline we never set, and it
# stays silent when we promised two hours and took eight. The commitment we
# made to the guest is the only honest measure of whether we were late, and it
# is sitting in our own message.
#
# So three things raise a flag, and elapsed time alone is not one of them:
#   * we stated a timeframe and missed it;
#   * the guest had to follow up (they were plainly waiting, and said so);
#   * we never replied at all.
#
# A slow reply with no promise made and no follow-up raises nothing. That is
# deliberate: without a commitment there is no breach, and inventing one turns
# the flags section into a clock nobody set.

# "within 24 hours", "in 2-3 business days", "by tomorrow", "in the next 48 hrs"
_PROMISE = re.compile(
    r"\b(?:with(?:in)?|in|inside|no later than|by)\b[^.\n]{0,24}?"
    r"(\d{1,3})\s*(?:-|–|to)?\s*(\d{1,3})?\s*"
    r"(hour|hr|day|business\s+day|working\s+day|week)s?\b", re.I)

# A commitment with no number in it. It IS a promise — the guest is told to
# expect something soon — but it cannot be measured, so it is recorded and
# never used to accuse: "shortly" is not a deadline anyone can miss on paper.
_VAGUE_PROMISE = re.compile(
    r"\b(?:shortly|as soon as possible|asap|right away|straight away|"
    r"at the earliest|very soon)\b", re.I)

_UNIT_HOURS = {"hour": 1, "hr": 1, "day": 24, "business day": 24,
               "working day": 24, "week": 168}

# A guest who wrote twice needs no clock. They were plainly waiting, and said
# so by writing again — that is the guest telling us we were late, which beats
# any threshold we could pick for them.
CHASE_FLAG = 2


def promised_window(text: str) -> tuple:
    """(hours, phrase) we committed to, or (None, phrase) for a vague promise,
    or (None, "") when the message promised nothing.

    The OUTER bound of a range: "2-3 business days" is not missed until the
    third day is gone. Reading the inner bound would flag us for a reply that
    arrived inside what we told the guest.
    """
    body = str(text or "")
    m = _PROMISE.search(body)
    if m:
        lo, hi, unit = m.group(1), m.group(2), m.group(3).lower().strip()
        unit = re.sub(r"\s+", " ", unit)
        per = _UNIT_HOURS.get(unit, _UNIT_HOURS.get(unit.split()[-1], 1))
        n = int(hi or lo)
        return n * per, m.group(0).strip()
    v = _VAGUE_PROMISE.search(body)
    if v:
        return None, v.group(0).strip()
    return None, ""


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


def _body(e):
    return " ".join(str((e or {}).get(k) or "")
                    for k in ("raw_body", "summary", "detail", "label"))


def response_gaps(events) -> list:
    """Every stretch where the guest was waiting on us and we fell short.

    Returns [{check, detail, hours, chases, answered, promise}]. A stretch is
    only returned when it BREACHES something: a timeframe we stated, a guest
    who had to follow up, or a reply that never came. Elapsed time alone is
    not a finding — see the note above.

    TWO PASSES, because a promise and a silence are not the same shape. A
    silence is a guest waiting for us. A PROMISE is made INSIDE a reply — the
    reply that closes the guest's wait — and it governs the NEXT one, which no
    guest message opens a gap for. Tracked together, the promise was recorded
    and never checked: every "we'll revert within 24 hours" passed because the
    message carrying it had just answered the guest.

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
    last_known = timed[-1][0] if timed else None

    out = []

    # ── pass 1: the guest waiting on us ────────────────────────────────────
    open_at, chases = None, 0
    for when, e in timed:
        actor = str(e.get("actor") or "").strip().lower()
        if actor in _GUEST:
            if open_at is None:
                open_at, chases = when, 1
            else:
                chases += 1
        elif actor in _US and open_at is not None:
            out.append(_gap(open_at, when, chases, True))
            open_at, chases = None, 0
    if open_at is not None:
        out.append(_gap(open_at, last_known, chases, False))

    # ── pass 2: what we told the guest to expect ───────────────────────────
    ours = [(w, e) for w, e in timed
            if str(e.get("actor") or "").strip().lower() in _US]
    for i, (when, e) in enumerate(ours):
        hours, phrase = promised_window(_body(e))
        if not phrase:
            continue
        nxt = ours[i + 1][0] if i + 1 < len(ours) else None
        if hours is None:
            # A commitment with no number. It IS a promise — the guest was
            # told to expect something soon — but nothing can be MISSED on
            # paper, so it is only raised when nothing came at all. Inventing
            # a duration for "shortly" would be us setting the deadline and
            # then judging ourselves against it.
            if nxt is None:
                out.append(_promise_gap(when, None, None, phrase, last_known))
            continue
        if nxt is None or (nxt - when).total_seconds() / 3600.0 > hours:
            out.append(_promise_gap(when, nxt, hours, phrase, last_known))

    gaps = [g for g in out if g]
    for g in gaps:
        g["undated_events"] = undated
    return gaps


def _promise_gap(said_at, replied_at, hours, phrase, last_known):
    """A timeframe we stated and did not keep."""
    end = replied_at or last_known or said_at
    took = max(0.0, (end - said_at).total_seconds() / 3600.0)
    if replied_at is None:
        detail = (f"We told the guest {phrase} at {said_at:%d %b %H:%M} and "
                  f"nothing further from us appears on the timeline"
                  + (f" — {took:.0f}h to the last event on it" if took else "")
                  + ".")
    else:
        detail = (f"We told the guest {phrase} at {said_at:%d %b %H:%M} and "
                  f"replied at {replied_at:%d %b %H:%M} — {took:.0f}h against "
                  f"the {hours:.0f}h we stated.")
    return {"check": "Missed follow-ups or deadline crossed", "detail": detail,
            "hours": round(took, 1), "chases": 0,
            "answered": replied_at is not None, "promise": phrase}


def _gap(start, end, chases, answered):
    """A stretch with the guest waiting. Raised only when the guest had to
    follow up, or when no reply came at all — never on elapsed time, because
    without a commitment there is nothing to have missed."""
    hours = max(0.0, (end - start).total_seconds() / 3600.0) if end else 0.0
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
                  f"and {end:%d %b %H:%M} before we replied ({hours:.0f}h).")
    else:
        return None
    return {"check": check, "detail": detail, "hours": round(hours, 1),
            "chases": chases, "answered": answered, "promise": ""}


def gap_flags(events) -> list:
    """The gaps, as flags on the CO team.

    CO because these are all failures of OUR handling — the supply partner
    being slow is a different finding and routes elsewhere. One flag per
    stretch rather than one summary flag: two separate silences on one ticket
    are two things that went wrong, and merging them hides the second.
    """
    return [{"team": "CO", "flag": g["check"], "evidence": g["detail"]}
            for g in response_gaps(events)]
