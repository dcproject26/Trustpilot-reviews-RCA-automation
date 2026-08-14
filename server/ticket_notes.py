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
# STRUCTURAL FURNITURE: a form, a dump, a header. Checked BEFORE the booking
# verbs, because these are shaped like a record of the ticket and routinely
# contain the words "cancellable" and "reschedulable" as FIELD NAMES.
#
# Measured on booking 32885089: a "**Booking Details**" snapshot listing
# "Is Cancellable: No / Is Reschedulable: Yes" matched `reschedul` and rendered
# as "Booking status snapshot posted"; a Booking Info dump rendered as
# "Booking details posted to ticket".
_FURNITURE = [
    (re.compile(r"\bsupport\s+history\s+thread\s+opened\b"
                r"|\bitinerary\s+(?:id|margin)\s*:"
                r"|--\s*booking\s+info\s*--"
                r"|\*\*booking\s+details\*\*"
                r"|\boverall\s+support\s+summary\b"
                r"|\bconversation\s+with\s+ios\s+user\b"
                # A rules card auto-attached to every BNPL ticket. It explains
                # how BNPL works; it is not something that happened to this
                # booking, and it rendered as "BNPL handling rules posted to
                # ticket".
                r"|book\s+now,?\s+pay\s+later\b"
                r"|\btickets?\s+visibility\b", re.I), "ticket furniture"),
]

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
    # A BOOKING ID IS NOT AN EVENT. `booking\s*id\s*\d{6,}` and `HEA-\d+`
    # were here, and every internal note on a booking cites the booking — so
    # an itinerary-margin dump and "Support history thread opened for Booking
    # ID: 32885089" both came back "keep" and rendered as timeline rows.
    #
    # Measured on booking 32885089: 29 raw events, 28 rows, of which
    # "Booking details posted", "Booking status snapshot posted", "Support
    # history thread opened" and "Credit refund comment logged" are the
    # ticket's own bookkeeping. The pattern that was supposed to find booking
    # FACTS was matching the reference number instead.
    #
    # What stays is the verbs: rescheduled, cancelled, refunded, tickets sent,
    # charged, confirmed. Those are things that happened. An id is a label.
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
    # THREE TESTS, AND THE ORDER IS THE WHOLE RULE.
    #
    # Neither plain order works, and both failures are real:
    #
    #   facts first  — furniture keeps its place the moment it contains a
    #                  booking verb. A "**Booking Details**" snapshot listing
    #                  "Is Cancellable: No / Is Reschedulable: Yes" matched
    #                  `reschedul` and rendered as a timeline row.
    #   admin first  — a real event wrapped in an instruction is dropped.
    #                  "[RESCHEDULE] Automation has failed … Assigning to the
    #                  supply team" is what happened, and the instruction
    #                  around it must not take it down.
    #
    # So the split is by SHAPE, not by order alone. Structural furniture — a
    # form, a dump, a header — is furniture whatever words it contains, and it
    # goes first. Instruction-style administration goes AFTER the booking
    # verbs, because an instruction can wrap an event.
    for rx, why in _FURNITURE:
        if rx.search(text):
            return "drop", f"ticket furniture ({why})"
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


# ── the cancellation policy ────────────────────────────────────────────────
#
# It is a property of the BOOKING, not an event, and it was being written onto
# every row of the timeline — on one real ticket it appeared on all of them,
# crowding out the fact each row existed to carry. Taking it off the timeline
# without putting it anywhere would lose it, so it is extracted once here and
# rendered as a booking detail.
#
# The source is the booking-info dump Zendesk posts onto the ticket. There is
# no cancellation column in the warehouse and none in the API payload, so this
# text is the only place the policy exists.

_POLICY = [
    # "non-cancellable", "non cancellable / non refundable"
    (re.compile(r"\bnon[\s-]?cancell?able\b(?:[\s/]*non[\s-]?refundable\b)?", re.I),
     lambda m: "Non-cancellable"),
    # "cancellable and reschedulable to 1440 min prior"
    (re.compile(r"\bcancell?able\b[^.\n]{0,40}?(\d{2,5})\s*min(?:ute)?s?\s*prior", re.I),
     lambda m: f"Cancellable up to {int(m.group(1))} minutes before start"),
    # "cancel/reschedule deadline 02 Aug 08:30"
    (re.compile(r"\bcancel(?:/|\s+or\s+|\s*&\s*)?(?:reschedule)?\s*deadline\s*[:\-]?\s*"
                r"([0-9]{1,2}\s+\w{3}[^;\n]{0,12})", re.I),
     lambda m: f"Cancel or reschedule by {m.group(1).strip()}"),
    # "free cancellation up to 24 hours before"
    (re.compile(r"\bfree\s+cancellation\b[^.\n]{0,30}?(\d{1,3})\s*hours?\b", re.I),
     lambda m: f"Free cancellation up to {int(m.group(1))} hours before start"),
]


def cancellation_policy(text: str) -> str:
    """The booking's cancellation terms in one phrase, or "" when absent.

    "" means the dump did not state it — NOT that the booking has no policy.
    The caller says which, because a blank field and "we could not find it"
    send a reader to different places.
    """
    body = str(text or "")
    if not body.strip():
        return ""
    for rx, fmt in _POLICY:
        m = rx.search(body)
        if m:
            return fmt(m)
    return ""


def policy_from_events(events) -> tuple[str, str]:
    """(policy, why) across every raw body on the timeline.

    Scans all of them rather than only the booking-info row: the terms turn up
    in confirmation emails too, and a ticket without the dump would otherwise
    report nothing when the answer was two rows away.
    """
    bodies = [str((e or {}).get("raw_body") or (e or {}).get("summary") or "")
              for e in (events or []) if isinstance(e, dict)]
    if not bodies:
        return "", "there were no ticket events to read it from"
    for b in bodies:
        got = cancellation_policy(b)
        if got:
            return got, ""
    return "", (f"none of the {len(bodies)} ticket event(s) state the "
                f"cancellation terms")


# ── the supply-partner escalation email ────────────────────────────────────
#
# The booking-info dump lists the SP's own contacts as labelled fields, and two
# of them look almost alike:
#   Booking Intimation Email  - where booking notifications go
#   Booking Escalation Email  - where a formal SP escalation is sent
# The escalation one is what an associate emails to raise a case with the
# partner. It was NEVER read: booking["escalationEmail"] came only off BigQuery
# dim_vendors (type ESCALATIONS), a different source, so a booking whose record
# carries the email but whose vendor row has no ESCALATIONS contact reported it
# blank - and the card then said "a formal SP escalation email could not be
# sent", which was false.

# The address only, and never the next label's value. A blank field followed by
# "Booking Escalation Number:" must parse as blank, not swallow what comes
# after - so the VALUE pattern requires an actual email, and the LABEL pattern
# proves the field was there even when empty.
# The email sub-pattern is the same one `_EMAIL` uses below; inlined because
# `_EMAIL` is defined further down and this block reads earliest.
_BOOKING_ESC_VALUE = re.compile(
    r"Booking\s+Escalation\s+Email\s*:\s*([\w.+-]+@[\w-]+\.[\w.-]+)", re.I)
_BOOKING_ESC_LABEL = re.compile(r"Booking\s+Escalation\s+Email\s*:", re.I)


def booking_record_escalation_email(timeline_raw) -> tuple[str, str]:
    """(email, state) for the SP escalation email as written on the booking-info
    dump carried in `timeline_raw`.

    state distinguishes four things a bare "" cannot, which is the whole point:
      present - a Booking Escalation Email field with an address (email is set)
      blank   - the field is on the record but empty (the SP has none there)
      absent  - the record was read and carries no such field
      no_text - there was no booking-info text to read it from at all

    'blank'/'absent' are facts about the supply partner; 'no_text' is a gap on
    our side. Merging them is the "ran and found nothing" vs "did not run" bug,
    so they are returned apart.
    """
    texts = [str(t) for t in (timeline_raw or []) if t]
    if not texts:
        return "", "no_text"
    saw_label = False
    for t in texts:
        m = _BOOKING_ESC_VALUE.search(t)
        if m:
            return m.group(1).strip().rstrip(".,;"), "present"
        if _BOOKING_ESC_LABEL.search(t):
            saw_label = True
    return "", ("blank" if saw_label else "absent")


def resolve_sp_escalation_email(booking: dict, timeline_raw) -> None:
    """Set booking['escalationEmail'] and booking['escalationEmailSource'] by
    precedence, in place.

    Precedence: the booking record's own field FIRST, then the BigQuery
    dim_vendors ESCALATIONS contact _get_booking_extra() attached, then none.

    escalationEmailSource is the fix for the failure that prompted this:
      booking_record     - read off the booking record's own field
      vendor_escalations - the vendor's ESCALATIONS contact in dim_vendors
      none_found         - BOTH sources were consulted and neither has an email
      not_fetched        - a source was never consulted (the warehouse
                           enrichment does not run on every match path, and it
                           swallows errors into {}), so we DID NOT LOOK

    'not_fetched' and 'none_found' must never collapse. "a formal SP escalation
    email could not be sent" is only true for 'none_found'; on 'not_fetched' the
    honest statement is that we did not retrieve it - our gap, not the SP's.
    """
    if not isinstance(booking, dict):
        return
    # Whether the warehouse enrichment ran, recorded BEFORE we overwrite the
    # key. _get_booking_extra() always sets escalationEmail (even to "") and
    # contactCount when it runs, and leaves both absent when it did not.
    bq_ran   = ("escalationEmail" in booking) or ("contactCount" in booking)
    bq_email = str(booking.get("escalationEmail") or "").strip()

    rec_email, rec_state = booking_record_escalation_email(timeline_raw)
    if rec_email:
        booking["escalationEmail"]       = rec_email
        booking["escalationEmailSource"] = "booking_record"
        return
    if bq_email:
        booking["escalationEmail"]       = bq_email
        booking["escalationEmailSource"] = "vendor_escalations"
        return

    booking["escalationEmail"] = ""
    # none_found only when we actually looked in BOTH places. The record counts
    # as read unless there was no text (no_text); the warehouse counts as read
    # only if it ran. Anything short of both is not_fetched - the safe
    # under-claim, so a blank is never asserted about a place we did not open.
    record_read = rec_state != "no_text"
    booking["escalationEmailSource"] = (
        "none_found" if (bq_ran and record_read) else "not_fetched")


# ── vendor names, phone numbers and addresses ──────────────────────────────
#
# The prompt tells the model to write "the supply partner" and never a trading
# name. That was the wrong mechanism twice over: an instruction can be ignored,
# and it does nothing at all for the drafts already stored — so a card still
# read "partner ref K507100323; RAIL EUROPE-CHF contact +41 33 828 72 33",
# which is a vendor's name and a phone number on a card about a guest.
#
# Scrubbed in code, at render, so it holds whatever the model wrote and
# whether the draft is new or a year old.
#
# THE REFERENCE STAYS. "partner ref K507100323" is what someone uses to find
# the booking on the partner's side; the NAME and the CONTACT DETAILS are what
# crowd out the fact the row exists to carry.

_PHONE = re.compile(r"(?<![\w])\+\d[\d\s().-]{7,}\d")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

# Words that make a vendor token too common to blank safely. Replacing "rail"
# or "tours" everywhere would eat the sentence around it.
_VENDOR_STOP = {"the", "and", "ltd", "llc", "inc", "gmbh", "srl", "sa", "sas",
                "bv", "ag", "co", "company", "tours", "tour", "travel",
                "rail", "bus", "boat", "group", "europe", "international"}


def scrub_vendor(text: str, vendor_name: str = "") -> str:
    """Replace the supply partner's identity with "the supply partner".

    The vendor's own name is matched from the booking record rather than
    guessed, so this cannot eat an unrelated proper noun. Phone numbers and
    email addresses go regardless of whose they are: neither belongs on a card
    about a guest's complaint, and a number left in is the one thing here that
    could be dialled by mistake.
    """
    out = str(text or "")
    if not out.strip():
        return out

    name = str(vendor_name or "").strip()
    if name:
        # The whole name first — "RAIL EUROPE- CHF" — tolerating the spacing
        # and punctuation drift between the warehouse and the ticket text.
        parts = [re.escape(p) for p in re.split(r"[^\w]+", name) if p]
        if parts:
            whole = r"[^\w]{0,3}".join(parts)
            out = re.sub(whole, "the supply partner", out, flags=re.I)
        # Then any distinctive token from it left standing on its own.
        for tok in re.findall(r"[A-Za-z]{4,}", name):
            if tok.lower() in _VENDOR_STOP:
                continue
            out = re.sub(rf"\b{re.escape(tok)}\b", "the supply partner",
                         out, flags=re.I)

    out = _PHONE.sub("[contact removed]", out)
    out = _EMAIL.sub("[email removed]", out)
    # "the supply partner contact [contact removed]" reads worse than the
    # fact it replaced; the contact detail is gone, so the lead-in goes too.
    out = re.sub(r"\s*(?:contact|tel|phone|email)\s*"
                 r"\[(?:contact|email) removed\]", "", out, flags=re.I)
    # A preposition left pointing at nothing ("emailed us at ") is worse than
    # the detail it replaced.
    out = re.sub(r"\s*\b(?:at|on|via)\s*\[(?:contact|email) removed\]",
                 "", out, flags=re.I)
    out = re.sub(r"\s*\[(?:contact|email) removed\]", "", out)
    # "vendor the supply partner" — the label and the replacement say the same
    # thing, and the pair reads as a bug.
    out = re.sub(r"\b(?:vendor|partner|supplier)\s+the supply partner",
                 "the supply partner", out, flags=re.I)
    # Collapse the double-naming the substitutions can leave behind.
    out = re.sub(r"(the supply partner)(?:[\s,-]+the supply partner)+",
                 r"\1", out, flags=re.I)
    return re.sub(r"[ \t]{2,}", " ", out).strip(" ;,-")
