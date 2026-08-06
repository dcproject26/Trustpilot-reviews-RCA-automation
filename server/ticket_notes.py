"""Which internal ticket notes belong on the timeline, and which are admin.

Internal notes were hidden wholesale behind a toggle. That was wrong in both
directions: a reschedule that FAILED is the case itself and was buried, while
"please close this once the guest confirms" is noise that told a reader
nothing about the booking.

THE TEST IS WHAT THE NOTE IS FOR, NOT WHAT IT MENTIONS.

  * A note that records something that HAPPENED to the booking is kept —
    including one that carries the fact in passing. "NAR, tix are already
    rescheduled for +45 mins" is a disposition instruction wrapped around a
    real outcome, and the outcome is the only record we have of it. It is
    kept, and rendered as the fact rather than as the instruction.
  * A note that only tells the team how to HANDLE THE TICKET is dropped.
    Close it, move it to pending, assign it, add a tag, pick a macro, mind
    the SLA, a signature block, an empty comment. Nothing happened to the
    booking; nothing goes on the booking's timeline.

WHY THE FORMULAIC HALF IS CODE. These are stock phrases and machine-generated
strings — enumerable, testable, and high-volume: one real ticket fired
"Customer Reschedule Request can't be pushed to Pending" four times. A rule
also cannot be talked out of it, and this is a case where that matters: shown
a note that mentions a real booking fact, a model is tempted to keep it
whatever the note is for. The judgement that CANNOT be enumerated — a novel
phrasing of "the automation failed" — is left to the model, which is given
this rule in words rather than asked to guess.

ON UNCERTAINTY, KEEP. A kept clutter row is visible and arguable; a dropped
event is unrecoverable. So the patterns below are narrow and certain, and
anything they do not match survives to be judged.
"""
import re

# Pure ticket administration. Every one of these is an instruction about the
# TICKET, and none of them records anything about the booking.
_ADMIN = [
    # Disposition: what to do with the ticket.
    (re.compile(r"\b(?:please\s+)?close\s+(?:this\s+)?ticket\b"
                r"|\bmark(?:ing)?\s+(?:this\s+)?(?:as\s+)?solved\b"
                r"|\bmov(?:e|ing|ed)\s+to\s+pending\b"
                r"|\bset(?:ting)?\s+to\s+(?:pending|solved|open|on-hold)\b"
                r"|\bre-?assign(?:ing|ed)?\s+to\b"
                r"|\bassign(?:ing|ed)?\s+to\s+\w+"
                r"|\btransferr?(?:ing|ed)?\s+to\s+(?:the\s+)?\w+\s+(?:team|queue)\b",
                re.I), "disposition"),
    # Field, tag and macro housekeeping.
    # Up to two words may sit between the verb and "tag": "added the vip tag",
    # "applied a priority tag". Bounded rather than open, so a sentence that
    # merely contains both words far apart does not match.
    (re.compile(r"\b(?:add(?:ed|ing)?|appl(?:y|ied|ying)|remov(?:e|ed|ing))\s+"
                r"(?:\w+\s+){0,2}tags?\b"
                r"|\bmacro\s+(?:applied|used|selected)\b"
                r"|\bfill(?:ed|ing)?\s+(?:in\s+)?the\s+\w+\s+field\b"
                r"|\bfield\s+updated\b", re.I), "field-or-tag"),
    # SLA and hygiene reminders. About our handling clock, not the booking.
    (re.compile(r"\bSLA\s+(?:breach|reminder|warning|clock|timer)\b"
                r"|\bfirst\s+reply\s+time\b"
                r"|\bplease\s+(?:update|respond\s+to)\s+the\s+ticket\b"
                r"|\bticket\s+hygiene\b", re.I), "sla-reminder"),
    # Signature blocks and empties.
    (re.compile(r"^\s*(?:--+|__+)\s*$"
                r"|^\s*(?:thanks|regards|best|cheers)[,!.]?\s*$"
                r"|^\s*\[?image[^\]]*\]?\s*$", re.I | re.M), "signature-or-empty"),
]

# Words that make a note a record of the BOOKING rather than of the ticket.
# Checked FIRST: a note carrying one of these is kept even when it also
# carries an admin phrase, because the booking fact is the part that cannot be
# recovered from anywhere else.
_BOOKING_FACT = re.compile(
    r"\breschedul\w+"
    r"|\bcancel(?:led|lation|ed)?\b"
    r"|\brefund\w*"
    r"|\bticket[s]?\s+(?:sent|issued|delivered|resent|reissued)\b"
    r"|\bvoucher\w*"
    r"|\bautomation\s+(?:has\s+)?failed\b"
    r"|\bfail(?:ed|ure)\b"
    r"|\bbooking\s*(?:id)?\s*[:#]?\s*\d{6,}"
    r"|\bHEA-\d+"
    r"|\bpickup\b|\btimeslot\b|\bdeparture\b"
    r"|\bcharged\b|\bpayment\b|\bpaid\b"
    r"|\bconfirm(?:ed|ation)\b"
    r"|\bvendor\s+(?:reference|ref)\b", re.I)


def note_disposition(body: str) -> tuple[str, str]:
    """(verdict, why) for one internal note.

    verdict is "keep", "drop" or "judge":
      keep  — it records a booking fact; render it as that fact.
      drop  — pure ticket administration.
      judge — the patterns are not certain; the model decides, and its
              instruction says to keep when unsure.
    """
    text = str(body or "").strip()
    if not text:
        return "drop", "empty comment"
    if _BOOKING_FACT.search(text):
        return "keep", "records something that happened to the booking"
    for rx, why in _ADMIN:
        if rx.search(text):
            return "drop", f"ticket administration ({why})"
    return "judge", "no certain signal either way"


# ── repeated system pings ──────────────────────────────────────────────────
#
# When the same automated message fires several times, the REPETITION is the
# signal and the individual lines are not. Four rows saying "Reschedule cannot
# be pushed to Pending" tell a reader one thing, four times, and push the
# events that matter off the screen.

def _ping_key(body: str) -> str:
    """What makes two pings 'the same'. Numbers and timestamps are stripped so
    a message differing only by an id still collapses."""
    t = re.sub(r"\d+", "#", str(body or "").lower())
    return re.sub(r"\s+", " ", re.sub(r"[^a-z# ]+", " ", t)).strip()[:120]


def collapse_repeats(events: list, min_run: int = 2) -> tuple[list, list]:
    """(kept, collapsed) where each collapsed group is one entry.

    Grouped across the WHOLE timeline rather than only consecutively: the
    pings interleave with other events, and requiring adjacency would leave
    four near-identical rows on screen whenever anything happened between
    them. Each group keeps its first event and records the count and span.

    `min_run` is 2 because two identical automated messages are already a
    repetition; one is an event.
    """
    events = [e for e in (events or []) if isinstance(e, dict)]
    groups: dict = {}
    for e in events:
        if not e.get("is_internal"):
            continue
        groups.setdefault(_ping_key(e.get("raw_body") or e.get("summary") or ""),
                          []).append(e)

    collapsed, drop_ids = [], set()
    for key, group in groups.items():
        if len(group) < min_run or not key:
            continue
        ordered = sorted(group, key=lambda x: str(x.get("time") or ""))
        first, last = ordered[0], ordered[-1]
        collapsed.append({
            "first": first, "count": len(ordered),
            "from": first.get("time") or "", "to": last.get("time") or "",
        })
        for e in ordered[1:]:
            drop_ids.add(id(e))

    kept = [e for e in events if id(e) not in drop_ids]
    return kept, collapsed


def ping_summary(group: dict) -> str:
    """The one line a collapsed group renders as. The count and the span ARE
    the finding — a reader needs to know it kept happening and for how long,
    which is exactly what four identical rows fail to say."""
    n, a, b = group.get("count", 0), group.get("from", ""), group.get("to", "")
    span = f"{a} to {b}" if a and b and a != b else (a or b or "an unknown span")
    return f"{n} system pings, {span}."
