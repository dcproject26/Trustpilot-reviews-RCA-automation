"""
Zendesk service — connector-auth'd (Replit Zendesk connection, OAuth).

Data path per the 2026-07 wiring brief:
1. Search tickets: fieldvalue:<bid> first, free-text "<bid>" fallback (logged).
2. Extract tgid/tid from confirmed custom field IDs (2024 Retool workflow).
3. Surface ticket_mail_seen tag on the booking.
4. Fetch PUBLIC comments per ticket (internal notes skipped), paginated by zenpy.
5. Brand split: guest-brand tickets -> guest timeline, SP-brand -> sp thread
   (draft.sp_interaction_frames). If brands unset, everything is guest.
6. Merge multi-ticket comments chronologically; _get_timeline_sync produces raw
   events {idx, time, time_sort, thread, actor, ticket_id, is_internal,
   internal_reason, raw_body}.
7. get_timeline passes raw events to Claude (_shape_via_claude) which returns
   clean {time, time_sort, thread, actor, label, summary, ticket_id,
   is_internal, internal_reason} events with bookend injection, noise-drop, and
   macro-flood collapsing. On failure, _fallback_shape is used. Claude writes
   the prose; provenance (ticket id, timestamps, machinery classification) is
   restored from the raw events afterwards rather than trusted to the model.
8. >40 raw comments -> keep first 20 + last 20 with one "[N comments elided]" placeholder.
"""
import asyncio
import html as _html
import logging
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone

from server.config import (
    is_live, MOCK_MODE,
    ZENDESK_SUBDOMAIN, ZENDESK_EMAIL, ZENDESK_API_TOKEN,
    ZENDESK_TGID_FIELD, ZENDESK_TID_FIELD, ZENDESK_BOOKING_FIELD_ID,
    ZENDESK_FIELD_GUEST_NAME, ZENDESK_FIELD_GUEST_EMAIL, ZENDESK_FIELD_EXPERIENCE,
    ZENDESK_FIELD_CITY, ZENDESK_FIELD_VISIT_DATE, ZENDESK_FIELD_PAX,
    ZENDESK_FIELD_VENDOR_NAME, ZENDESK_FIELD_ITINERARY,
    ZENDESK_BRAND_GUEST, ZENDESK_BRAND_SP, ZENDESK_BOT_TAGS,
)
from server.services.mock_data import MOCK_TIMELINES, MOCK_BOOKINGS

log = logging.getLogger(__name__)

# Loop-local: a bare module-level Semaphore binds to the first loop that
# awaits it and raises from any later loop (see server/aio.py).
from server.aio import LoopLocalSemaphore
_ZD_SEM = LoopLocalSemaphore(10)

IST = timezone(timedelta(hours=5, minutes=30))

# Search-path hit counters (reported in delta verification)
SEARCH_COUNTERS = {"fieldvalue": 0, "free_text": 0, "requester": 0}


def requester_email(tickets, signals=None) -> str:
    """The guest's email, off the tickets we already found.

    Two sources, and the custom field goes first because it is the one the
    desk fills in per booking — "Customer Email" on the ticket, sitting beside
    Tour Name and City. The requester's own address is the fallback: it is
    structural, always present, and occasionally the address of whoever
    forwarded the mail rather than the guest.

    Returns "" when neither is readable, which is NOT an error — some tickets
    genuinely have no requester email, and the caller reports the route as
    unattempted rather than as having found nothing.
    """
    read = signals or ticket_signals
    for t in (tickets or []):
        try:
            got = str((read(t) or {}).get("guest_email") or "").strip()
        except Exception:
            got = ""
        if "@" in got:
            return got
    for t in (tickets or []):
        got = ""
        for attr in ("requester", "via"):
            obj = getattr(t, attr, None)
            got = str(getattr(obj, "email", "") or "").strip()
            if "@" in got:
                return got
    return ""


def collect_tickets(booking_id, search, email_of=None) -> tuple:
    """Every ticket on this case, from ALL routes, deduplicated by ticket id.

    THE SHORT-CIRCUIT THIS REPLACES:

        tickets = search(f"type:ticket fieldvalue:{bid}")
        if tickets: ...
        else:       tickets = search(f'type:ticket "{bid}"')

    Free-text ran only when the custom-field search found NOTHING. Measured on
    one real case, the booking-id custom field is set on ONE ticket of four:

        fieldvalue:33202346   -> 1   (the refund mail)
        "33202346"            -> 3   (+ the chat, + a system ticket)
        requester:<email>     -> 4   (+ an On-hold contact about this booking)

    So fieldvalue returned something, the cascade stopped, and the card said
    "one contact" — truthfully reporting what it found, having looked in the
    one place that did not have the answer. Two real guest conversations were
    invisible, one of them still open.

    Stopping at the first route that returns ANYTHING rather than the first
    that returns EVERYTHING is the same defect the booking cascade had.

    `search` and `email_of` are injected so this is driven in tests without
    Zendesk.

    Returns `(tickets, tally)`. The tally counts what EACH route contributed
    after dedupe, so "one contact" can only ever be printed when all three
    ran — and `requester_skipped` says so when there was no email to search
    on, which is not the same as a search that found nothing.
    """
    _email_of = email_of or requester_email
    tally = {"fieldvalue": 0, "free_text": 0, "requester": 0,
             "duplicates": 0, "id_collision": 0, "requester_skipped": False}
    out, seen = [], set()

    _bid = str(booking_id or "").strip()

    def _take(rows, route):
        for t in (rows or []):
            tid = str(getattr(t, "id", "") or "")
            if tid and tid in seen:
                tally["duplicates"] += 1
                continue
            # THE ID COLLISION. Zendesk ticket ids and Headout booking ids
            # share the same numeric space — both are commonly eight digits —
            # so a free-text search for "32885089" matches TICKET #32885089,
            # which has nothing to do with booking 32885089.
            #
            # That is not hypothetical: it put a German-language chat from
            # 11 Jun at the top of a timeline whose booking was not confirmed
            # until 21 Jul. A month before the booking existed, wrecking the
            # chronology and the case findings built on it.
            #
            # `bids_from_ticket_text` already guards this in the other
            # direction ("a ticket body referencing ANOTHER ticket would
            # otherwise be harvested as a booking id"). The search needed the
            # same guard and did not have it.
            #
            # Only the TEXT routes are affected. `fieldvalue:` matched a custom
            # field, which is a statement about the booking; a requester hit is
            # about the person. Neither can collide this way.
            if route == "free_text" and tid and tid == _bid:
                tally["id_collision"] += 1
                continue
            if tid:
                seen.add(tid)
            out.append(t)
            tally[route] += 1

    _take(search(f"type:ticket fieldvalue:{booking_id}"), "fieldvalue")
    # NOT an elif. Both run, always.
    _take(search(f'type:ticket "{booking_id}"'), "free_text")

    # The requester route needs an address, and the address comes off the
    # tickets the first two routes found — so it runs last and only when they
    # found something to read it from.
    email = _email_of(out) if out else ""
    if email:
        _take(search(f"type:ticket requester:{email}"), "requester")
    else:
        tally["requester_skipped"] = True
    return out, tally


def collect_trail(booking_id, tally) -> dict:
    """One line saying which routes ran and what each added, or None.

    None only when a single route found everything and the others added
    nothing — there is no news in "and two other searches agreed". Anything
    else is said out loud, because the failure being fixed here was a card
    that reported one contact with total confidence.
    """
    extra = tally["free_text"] + tally["requester"] + tally.get("id_collision", 0)
    if not (extra or tally["requester_skipped"]):
        return None
    bits = [f"{tally['fieldvalue']} by booking-id field"]
    if tally["free_text"]:
        bits.append(f"{tally['free_text']} more by searching the text")
    if tally["requester"]:
        bits.append(f"{tally['requester']} more from the same requester "
                    f"(these may include another trip)")
    if tally.get("id_collision"):
        bits.append(f"{tally['id_collision']} ticket whose own NUMBER equals "
                    f"this booking id was excluded — same numeric space, "
                    f"different thing")
    if tally["requester_skipped"]:
        bits.append("the requester search did NOT run — no email on any "
                    "ticket found, so contacts filed under another booking "
                    "id would not be here")
    return {"mark": "warn" if tally["requester_skipped"] else "pass",
            "text": f"<strong>Zendesk contacts for {booking_id}:</strong> "
                    + "; ".join(bits) + "."}

_TAG_RE = re.compile(r"<[^>]+>")
_SIG_SPLIT_RE = re.compile(r"(?:^--\s*$|\bBest regards\b)", re.I | re.M)


def _get_client():
    """Zenpy client — Replit Zendesk connector first (OAuth, auto-refreshed),
    env-var email/API-token pair only as fallback. None when not live."""
    if not is_live("zendesk"):
        return None
    from server.services import zd_connector
    if zd_connector.available():
        return zd_connector.get_client()
    if ZENDESK_SUBDOMAIN and ZENDESK_API_TOKEN:
        from zenpy import Zenpy
        return Zenpy(subdomain=ZENDESK_SUBDOMAIN, email=ZENDESK_EMAIL,
                     token=ZENDESK_API_TOKEN)
    return None


def _search_with_retry(_z, query: str):
    """Run a Zendesk search; on 401, refresh the connector token and retry once."""
    try:
        return list(_z.search(query=query, type="ticket"))
    except Exception as e:
        from server.services import zd_connector
        if zd_connector.is_auth_error(e) and zd_connector.available():
            _z = zd_connector.retry_client_on_auth_error()
            return list(_z.search(query=query, type="ticket"))
        raise


try:
    _BOOKING_FIELD = int(str(ZENDESK_BOOKING_FIELD_ID).strip())
except (TypeError, ValueError):
    _BOOKING_FIELD = None


def _fid(v):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


_F_GUEST_NAME  = _fid(ZENDESK_FIELD_GUEST_NAME)
_F_GUEST_EMAIL = _fid(ZENDESK_FIELD_GUEST_EMAIL)
_F_EXPERIENCE  = _fid(ZENDESK_FIELD_EXPERIENCE)
_F_CITY        = _fid(ZENDESK_FIELD_CITY)
_F_VISIT_DATE  = _fid(ZENDESK_FIELD_VISIT_DATE)
_F_PAX         = _fid(ZENDESK_FIELD_PAX)
_F_VENDOR_NAME = _fid(ZENDESK_FIELD_VENDOR_NAME)
_F_ITINERARY   = _fid(ZENDESK_FIELD_ITINERARY)


def ticket_signals(ticket) -> dict:
    """
    The booking's own facts, straight off the ticket's custom fields.

    A Zendesk ticket already knows the guest's full name, the experience, the
    city, the visit date and the party size. Matching therefore does not depend
    on the guest having named the venue in their review — a one-line review with
    no venue in it can still be matched against the ticket's own experience.

    Field ids confirmed against a live ticket with tools/zd_field_discovery.py
    and overridable per environment.
    """
    def _v(fid):
        if fid is None:
            return ""
        val = get_custom_field(ticket, fid)
        return str(val).strip() if val not in (None, "") else ""

    pax_raw = _v(_F_PAX)
    pax_total = sum(int(n) for n in re.findall(r"(\d+)\s*(?:adult|child|infant|senior|youth)",
                                               pax_raw, re.I)) or None
    return {
        "booking_id":  booking_id_from_ticket(ticket) or "",
        "guest_name":  _v(_F_GUEST_NAME),
        "guest_email": _v(_F_GUEST_EMAIL),
        "experience":  _v(_F_EXPERIENCE),
        "city":        _v(_F_CITY),
        "visit_date":  _v(_F_VISIT_DATE),
        "pax_raw":     pax_raw,
        "pax":         pax_total,
        "vendor_name": _v(_F_VENDOR_NAME),
        "itinerary_id": _v(_F_ITINERARY),
    }


def booking_id_from_ticket(ticket) -> str | None:
    """
    The booking id from the ticket's dedicated booking-id custom field.

    This is the authoritative source and it is what should be used: Zendesk
    stores the booking id in its own field, so there is no need to guess which
    of the numbers in a ticket body is a booking id versus a ticket id, an
    amount, or a phone number. Scraping digits out of prose was always a
    fallback dressed up as the primary path.
    """
    if _BOOKING_FIELD is None:
        return None
    val = get_custom_field(ticket, _BOOKING_FIELD)
    if val is None:
        return None
    val = str(val).strip()
    m = re.search(r"\b\d{7,12}\b", val)
    return m.group(0) if m else None


# Words that precede a booking id in ticket prose. A number introduced by one
# of these is a claim about what the number IS; a bare number in a paragraph is
# a number. Both are used, but the labelled ones win outright when present —
# that is what keeps a phone number or an amount out of the candidate list.
_BID_LABEL = re.compile(
    r"(?:booking|bkg|bid|order|reservation|ref(?:erence)?|conf(?:irmation)?)"
    # "no." and "no" both occur, so the separator class has to admit a full
    # stop. Without it "booking no. 33118844" fell through to the bare pass
    # and a labelled id was scored as an unexplained number.
    r"\s*(?:id|no|num(?:ber)?|#)?\s*[.:#-]*\s*(\d{7,12})\b", re.I)
_ANY_NUMBER = re.compile(r"\b\d{7,12}\b")

# More than this many distinct numbers in one ticket is prose, not a record.
# Admitting them all would turn one ticket into a page of candidates, each
# indistinguishable from the others.
_MAX_TEXT_BIDS = 3


def bids_from_ticket_text(ticket, exclude: set | None = None) -> tuple[list, str]:
    """Booking ids written in the ticket's SUBJECT and BODY, not its field.

    THE FALLBACK ONLY. `booking_id_from_ticket` reads the dedicated custom
    field and that stays authoritative — this runs when the field is empty,
    which it frequently is, and which used to end the ticket's life: shortlist
    did `if not bid: continue`, so a ticket found by a name+venue search and
    carrying the booking id in its first line was dropped without a word. The
    same file's `find_bids_by_requester_name` has always scraped subject, body
    and custom fields, so the two paths disagreed about what counts as a
    booking id, and a ticket the search FOUND was discarded by the shortlist.

    Returns (bids, provenance) where provenance is "labelled" (a booking-ish
    word introduced the number), "bare" (a number in the text with nothing
    saying what it is), or "" when there is nothing. The caller needs the
    distinction because these are worth different amounts and collapsing them
    would hide that a number was a guess.

    Numbers are NOT verified here. shortlist does no BigQuery by design, so
    what protects against a phone number becoming a candidate is the caller
    requiring the ticket's other indicators to agree — see shortlist.
    """
    exclude = {str(x) for x in (exclude or set()) if x}
    subject = str(getattr(ticket, "subject", "") or "")
    body    = str(getattr(ticket, "description", "") or "")[:4000]
    hay     = f"{subject}\n{body}"

    def _clean(nums):
        out = []
        for n in nums:
            if n in exclude or n in out:
                continue
            out.append(n)
        return out[:_MAX_TEXT_BIDS]

    labelled = _clean(_BID_LABEL.findall(hay))
    if labelled:
        return labelled, "labelled"
    bare = _clean(_ANY_NUMBER.findall(hay))
    if bare:
        return bare, "bare"
    return [], ""


def _zd_get(path: str):
    """
    Raw authenticated GET against the Zendesk REST API.

    Zenpy has no side-conversation support, so those endpoints are called
    directly, reusing whichever auth is already configured — the Replit
    connector's OAuth bearer token first, else the email/API-token pair.
    Returns parsed JSON, or None if unavailable or the call fails.
    """
    import requests
    try:
        from server.services import zd_connector
        if zd_connector.available():
            token, subdomain = zd_connector._settings()
            r = requests.get(f"https://{subdomain}.zendesk.com{path}",
                             headers={"Authorization": f"Bearer {token}"}, timeout=20)
        elif ZENDESK_SUBDOMAIN and ZENDESK_API_TOKEN:
            r = requests.get(f"https://{ZENDESK_SUBDOMAIN}.zendesk.com{path}",
                             auth=(f"{ZENDESK_EMAIL}/token", ZENDESK_API_TOKEN), timeout=20)
        else:
            return None
        if r.status_code != 200:
            log.info(f"[zendesk] GET {path} -> {r.status_code}")
            return None
        return r.json()
    except Exception as e:
        log.warning(f"[zendesk] GET {path} failed: {e}")
        return None


_SC_CACHE: dict = {}


def side_conversations(ticket_id) -> list[dict]:
    """
    Side conversations on a ticket, with their messages.

    A side conversation is always the SP thread — the agent talking to the
    supply partner about this booking. They are a separate Zendesk object from
    ticket comments and were not being fetched at all, so SP interactions never
    reached the RCA and any booking id mentioned only there was invisible.

    Returns [{id, subject, participants, text, messages: [{time, actor, body}]}].
    """
    key = str(ticket_id)
    if key in _SC_CACHE:
        return _SC_CACHE[key]
    data = _zd_get(f"/api/v2/tickets/{ticket_id}/side_conversations.json")
    if not data:
        _SC_CACHE[key] = []
        return []
    out = []
    for sc in (data.get("side_conversations") or []):
        sc_id = sc.get("id")
        subject = sc.get("subject") or ""
        # name OR email discarded the address whenever a participant had a
        # name, which is most of them - and the address is the supply
        # partner's escalation contact, the thing that says who was actually
        # reached about this booking. Keep both: the names read better on the
        # timeline, the addresses are the evidence.
        _parts = [p for p in (sc.get("participants") or []) if isinstance(p, dict)]
        parts = ", ".join((p.get("name") or p.get("email") or "") for p in _parts)
        emails = [e for e in ((p.get("email") or "").strip() for p in _parts) if e]
        msgs = []
        ev = _zd_get(f"/api/v2/tickets/{ticket_id}/side_conversations/{sc_id}/events.json")
        for e in ((ev or {}).get("events") or []):
            msg = e.get("message") or {}
            body = (msg.get("body") or msg.get("preview_text") or "").strip()
            if not body:
                continue
            actor = ((e.get("actor") or {}).get("name")
                     or (msg.get("from") or {}).get("name") or "")
            msgs.append({"time": _to_ist(e.get("created_at")),
                         "_raw_ts": e.get("created_at"),
                         "actor": actor, "body": body[:2000]})
        blob = "\n".join([subject] + [m["body"] for m in msgs]).strip()
        out.append({"id": sc_id, "subject": subject, "participants": parts,
                    "participant_emails": emails,
                    "text": blob, "messages": msgs})
    if out:
        log.info(f"[zendesk] ZD-{ticket_id}: {len(out)} side conversation(s), "
                 f"{sum(len(o['messages']) for o in out)} message(s)")
    _SC_CACHE[key] = out
    return out


def get_custom_field(ticket, field_id: int):
    """Value of a ticket custom field by ID, or None."""
    for f in (getattr(ticket, "custom_fields", None) or []):
        fid = f.get("id") if isinstance(f, dict) else getattr(f, "id", None)
        if fid == field_id:
            val = f.get("value") if isinstance(f, dict) else getattr(f, "value", None)
            return val if val not in ("", None) else None
    return None


def _to_ist(dt) -> str:
    """created_at -> '03 May 00:38 IST'."""
    if dt is None:
        return ""
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except ValueError:
            return dt[:16]
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST).strftime("%d %b %H:%M IST")


def _to_iso(dt) -> str:
    """created_at -> '2026-05-02T19:08:00+00:00' (UTC, lexicographically sortable)."""
    if dt is None:
        return ""
    dt = _sort_key(dt)
    if dt == datetime.max.replace(tzinfo=timezone.utc):
        return ""
    return dt.astimezone(timezone.utc).isoformat()


_ISO_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _normalize_time(value) -> tuple[str, str]:
    """
    A time string from anywhere -> (display, sortable).

    Two formats used to coexist in the stored timeline: real comments carried
    _to_ist output ('22 Jul 14:03 IST') while the injected bookends carried
    whatever date they were handed - the review publication date arrives as a
    bare ISO '2026-07-22'. Mixed formats meant the renderer had to reformat
    dates itself, and nothing could sort the list because neither string sorts.

    So one representation each, for two different jobs:
      display  - what a human reads. 'DD Mon HH:MM IST', or 'DD Mon' when the
                 source only gave a date and inventing a clock time would lie.
                 NOT sortable: it carries no year.
      sortable - ISO 8601 in UTC. Never displayed; sorts correctly as a plain
                 string. '' when the source time cannot be parsed at all, in
                 which case the display string is passed through untouched
                 rather than dropped.
    """
    s = str(value or "").strip()
    if not s or not _ISO_TS_RE.match(s):
        return s, ""
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return s, ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    iso = dt.astimezone(timezone.utc).isoformat()
    # A bare 'YYYY-MM-DD' has no clock in it. Rendering it as '22 Jul 05:30 IST'
    # would be the timezone conversion of a midnight nobody recorded.
    if len(s) <= 10:
        return dt.astimezone(IST).strftime("%d %b"), iso
    return _to_ist(dt), iso


def _sort_key(dt):
    if dt is None:
        return datetime.max.replace(tzinfo=timezone.utc)
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except ValueError:
            return datetime.max.replace(tzinfo=timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _clean_summary(body: str) -> str:
    """Strip HTML + email signatures, truncate to 200 chars with an ellipsis."""
    text = _TAG_RE.sub(" ", body or "")
    text = _html.unescape(text)
    text = _SIG_SPLIT_RE.split(text)[0]
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > 200:
        text = text[:199].rstrip() + "…"
    return text


# The whole vocabulary the renderer knows, for both stored enums. "booking" /
# "review" and "creation" / "review" only ever appear on the injected bookends.
_THREADS = {"email", "chat", "call", "sp", "booking", "review"}
# The actors that are a PERSON. `is_conversation` calls itself "an exchange a
# person took part in" and then never asked who took part — it tested the
# thread and the internal flag only, both of which a booking row can pass.
#
# THE ROW THAT GOT THROUGH: "General Admission, 1-day pass; 2 Adults, 2
# Children, 2 Seniors; EUR 203.35 paid; no add-ons selected | we: —", rendered
# under "Customer / CE interactions" in a Slack post. It is a booking-detail
# event. It reached the guest-contact list because `_map_channel` returns
# "email" for any via.channel it does not recognise, so its thread was not in
# NON_CONTACT_THREADS, and nothing had marked it internal.
#
# `actor` is the fact that was there all along: `_detect_actor` had already
# called it "system". Machinery cannot be a party to a conversation whatever
# channel it arrived on.
#
# Over-filtering fails SAFE here and under-filtering does not: everything this
# excludes is counted by `moved_frames_note` and said out loud ("N system
# events moved to the timeline"), so a wrongly excluded contact shows up as a
# number that does not match, while a wrongly included one is a booking dump
# presented to the team as something the guest said.
_PERSON_ACTORS = {"guest", "co", "sp", "ai"}

_ACTORS  = {"guest", "co", "sp", "ai", "system", "creation", "review"}

# ── Guest ↔ support is CONVERSATIONS ONLY (HANDOFF §4) ──────────────────────
#
# A contact is an exchange a PERSON took part in: chat, call, email, web form,
# in-app. Everything else on a booking is machinery wearing a contact's
# clothes — the booking thread Zendesk files as a task, the API posts and bot
# notes, the review itself — and each one counted as a contact raised the
# count, so a guest nobody spoke to read as a guest who was handled.
#
# They are not dropped. Each has a home that already renders it: booking-created
# on the booking timeline, API and system notes on the events timeline marked
# internal, the review as the closing bookend. What must never happen is a
# silent filter — a filtered list and a guest who never wrote in have to read
# differently, which is why the split is returned as two lists and counted.
CONTACT_THREADS = ("chat", "email", "call", "web", "app")
# What moves, named exactly. A DENYLIST and not the obvious allowlist, because
# the two differ on the case that matters: a thread nobody has classified yet.
# An allowlist would drop it — silently, on the reasoning that we cannot prove
# it is a conversation — and dropping a real contact because a channel name is
# new is the failure this section exists to stop. A new channel stays visible
# and wrong; an unclassified one never disappears.
NON_CONTACT_THREADS = ("booking", "review", "api", "sp")


def is_conversation(frame) -> bool:
    """Whether this frame is an exchange a person took part in.

    Internal machinery is not a conversation whatever thread it arrived on: the
    booking-in-progress mail is thread "email" and no human sent it, which is
    what `is_internal` already records.
    """
    if not isinstance(frame, dict):
        return False
    thread = str(frame.get("thread") or "").strip().lower()
    if thread in NON_CONTACT_THREADS or frame.get("is_internal"):
        return False
    # A NOTE PROMOTED OUT FROM BEHIND THE TOGGLE IS STILL AN INTERNAL NOTE.
    # `note_disposition` clears `is_internal` so a booking fact renders inline
    # on the timeline; this test used that flag to reject machinery, so the
    # promotion handed it a NAR disposition dressed as a guest conversation.
    # The two sections want different things from the same row, and the flag
    # cannot mean both — so the promotion is recorded separately and read here.
    if frame.get("promoted_from_internal"):
        return False
    # WHO TOOK PART. The two tests above are about the channel and the flag,
    # and a booking-detail row passes both when its via.channel is one
    # `_map_channel` does not recognise — it falls through to "email". An
    # actor of "system" or "creation" is machinery, and machinery is not a
    # party to a conversation whatever channel it arrived on.
    #
    # An ABSENT actor is left alone rather than excluded: frames written
    # before the actor was recorded carry none, and reading a missing field as
    # "machinery" would empty the section for every one of them.
    actor = str(frame.get("actor") or "").strip().lower()
    return not actor or actor in _PERSON_ACTORS


def guest_took_part(group) -> bool:
    """Whether the GUEST is in this exchange at all.

    THE RULE THAT SHOULD HAVE BEEN THERE. `is_conversation` judges one frame,
    and "the guest ↔ support" is a property of the EXCHANGE — so a ticket
    carrying only agent-side actions passed frame by frame and rendered as a
    contact. On a real booking that put an agent's NAR disposition and an ORM
    credit into "Customer / CE interactions" as contact 01, and the Slack post
    reported two contacts where the guest wrote once.

    Independent of `is_internal` ON PURPOSE. The regression that produced that
    row came from a flag being cleared for another section's benefit; a rule
    resting on the same flag can be re-broken the same way. Either the guest
    said something here or they did not, and that is readable from the frames
    themselves.

    A frame counts as the guest's if they are its actor OR it records
    something they said — an exchange logged agent-side with the guest's words
    quoted is still the guest taking part.
    """
    for f in (group or []):
        if not isinstance(f, dict):
            continue
        if str(f.get("actor") or "").strip().lower() == "guest":
            return True
        if str(f.get("guestSaid") or "").strip():
            return True
    return False


def _contact_key(f) -> str:
    """The exchange a frame belongs to: its ticket, or "" for the ungrouped."""
    return str((f or {}).get("ticket_id") or "").strip()


def split_contact_frames(frames) -> tuple[list, list]:
    """(conversations, moved). Never one list with the rest quietly gone.

    TWO TESTS, at their own grains. `is_conversation` drops machinery frame by
    frame; `guest_took_part` drops a whole TICKET whose frames are all
    agent-side, because a contact the guest was never in is not a contact.
    Ungrouped frames (no ticket id) are judged one by one — there is no
    exchange to reason about.
    """
    frames = [f for f in (frames or []) if isinstance(f, dict)]
    passed = [f for f in frames if is_conversation(f)]
    by_ticket: dict = {}
    for f in passed:
        by_ticket.setdefault(_contact_key(f), []).append(f)
    keep = []
    for key, group in by_ticket.items():
        if not key or guest_took_part(group):
            keep.extend(group)
    kept = {id(f) for f in keep}
    return ([f for f in frames if id(f) in kept],
            [f for f in frames if id(f) not in kept])


def moved_frames_note(moved) -> str:
    """What moved, and where to, in one clause — or "" when nothing moved.

    Empty string only for a genuinely empty split. "0 system events moved" on
    every clean booking is the noise that makes a reader stop reading the
    counts that matter.
    """
    moved = [f for f in (moved or []) if isinstance(f, dict)]
    if not moved:
        return ""
    # TWO KINDS OF MOVED ROW NOW, and calling both "system events" would be a
    # count that lies about what it counted. A promoted internal note or an
    # agent-only ticket is not machinery — it is OUR side of the record, and a
    # reader who sees "3 system events moved" on a card whose agent notes were
    # the thing dropped has been told the wrong fact.
    ours = [f for f in moved if f.get("promoted_from_internal")
            or (str(f.get("actor") or "").strip().lower() in ("co", "ai")
                and not str(f.get("guestSaid") or "").strip())]
    sys_n = len(moved) - len(ours)
    bits = []
    if sys_n:
        bits.append(f"{sys_n} system event{'' if sys_n == 1 else 's'} "
                    f"moved to the timeline")
    if ours:
        n = len(ours)
        bits.append(f"{n} agent-side note{'' if n == 1 else 's'} with no "
                    f"guest message {'is' if n == 1 else 'are'} on the "
                    f"timeline, not counted as contact"
                    f"{'' if n == 1 else 's'}")
    return "; ".join(bits)


# Zendesk's via.channel vocabulary for a conversation is far wider than the
# three names this function used to know. Messaging tickets arrive as
# "native_messaging", "messaging", "sunshine_conversations", "web_messaging",
# or as the social channel itself - "whatsapp", "facebook_messenger", "sms",
# "instagram_dm", "line". Every name outside the old three-item list fell
# through to the closing `return "email"`, so chat and WhatsApp turns were
# stored with thread "email" and could not group into the conversation they
# belonged to. That is why the renderer was re-guessing the channel from the
# words "chat"/"whatsapp"/"message" in the label: it was working around this
# default, not around a missing signal.
#
# Matched as families rather than an exact list, because Zendesk keeps adding
# channel names and an unrecognised one silently becomes "email" again.
_CHAT_CHANNELS  = ("chat", "messaging", "sunshine", "whatsapp", "widget",
                   "sms", "facebook", "instagram", "twitter", "wechat",
                   "telegram", "viber", "kakao", "social")
_CALL_CHANNELS  = ("voice", "phone", "call")
_EMAIL_CHANNELS = ("email", "mail")
# Zendesk's two most common non-conversational channels. Both were falling
# through to "email", so a help-centre form submission and an integration's
# API post both showed as mail in the timeline - two different things wearing
# a third thing's label, on every ticket.
_WEB_CHANNELS   = ("web", "web_form", "helpcenter", "help_center", "portal")
_API_CHANNELS   = ("api", "rule", "trigger", "automation", "system", "webhook")


def _map_channel(via_channel: str) -> str:
    """Zendesk via.channel -> the timeline's thread vocabulary."""
    ch = str(via_channel or "").strip().lower()
    if not ch:
        return "email"
    # LINE is compared whole: those three letters turn up inside unrelated
    # words, so it is the one channel a substring test cannot be trusted with.
    if ch == "line" or any(k in ch for k in _CHAT_CHANNELS):
        return "chat"
    if any(k in ch for k in _CALL_CHANNELS):
        return "call"
    if any(k in ch for k in _EMAIL_CHANNELS):
        return "email"
    if ch in _WEB_CHANNELS or any(k in ch for k in _WEB_CHANNELS):
        return "web"
    if ch in _API_CHANNELS or any(k in ch for k in _API_CHANNELS):
        return "api"
    # Anything still unrecognised stays "email" because that is the pill
    # vocabulary the renderer knows, but it is logged: a channel showing up
    # here is the next family to add, and silence is how the old default hid
    # the problem for so long.
    log.info(f"[zendesk] unmapped via.channel {ch!r} -> email")
    return "email"


def guest_words(frame) -> str:
    """What the guest actually said on this frame, from whichever field holds it.

    THE MODEL FILLS ONE OF TWO FIELDS AND EVERY RENDERER READ ONE. A frame
    carries `guestSaid` for a message the guest opened with and `guestReply`
    for one answering an agent — both correct, and the prompt asks for exactly
    that. Nine call sites then read `guestSaid` alone, so a guest's reply drew
    as an EMPTY line under their own name:

        07 Aug 18:32 IST  guest    (blank)      guestReply: "Thanked agent"
        09 Aug 11:36 IST  guest    (blank)      guestReply: "Stated prices
                                                paid do not match those on
                                                official castle websites"

    `guestReply` was carried all the way into the client and rendered by
    nothing. This was masked for months because the model used to write
    "N/A — this is the guest's reply event" into `guestSaid` — which was it
    telling us the content was in the other field, printed as if the guest had
    said it.

    guestSaid FIRST: on the rare frame carrying both, the opening message is
    what the row is about and the reply belongs to the next one.
    """
    if not isinstance(frame, dict):
        return ""
    return (str(frame.get("guestSaid") or "").strip()
            or str(frame.get("guestReply") or "").strip())


def sort_by_time_sort(rows) -> tuple[list, int]:
    """Chronological by `time_sort`. Returns (rows, how many had no key).

    THE LIST WAS NEVER SORTED. `time_sort` is built with care — bookends
    rescued from the booking date and the review's publication date, display
    strings normalised — and then the rows were returned in whatever order the
    shaping model emitted them. That is usually chronological, which is what
    made this survive: it looks correct until one run comes back out of order,
    and then the timeline reads as a sequence of events that did not happen in
    that sequence. Every case finding built on "X then Y" is downstream of it.

    ROWS WITH NO KEY KEEP THEIR NEIGHBOURS. A row whose time nothing in the
    record supports cannot be placed on the clock, and moving it to either end
    would be inventing a placement — the failure the bookend rescue above
    exists to prevent. So it inherits the key of the last row that had one,
    which keeps it exactly where the model put it relative to its neighbours,
    and a stable sort preserves that. The count comes back so the caller can
    say a judgement was made rather than implying the whole list is dated.
    """
    keyed, unsorted, last = [], 0, ""
    for r in rows:
        k = str((r or {}).get("time_sort") or "").strip()
        if k:
            last = k
        else:
            unsorted += 1
        keyed.append((last, r))
    # Stable: rows sharing a key — a collapsed run, or an undated row pinned to
    # its predecessor — stay in the order the model gave them.
    keyed.sort(key=lambda p: p[0])
    return [r for _, r in keyed], unsorted


def _brand_matches(ticket, brand_env: str) -> bool:
    if not brand_env:
        return False
    bid = getattr(ticket, "brand_id", None)
    return str(bid) == str(brand_env).strip()


# Bodies that are Headout machinery talking to itself, not anything the guest
# or an agent said. On one real review 8 of 14 timeline events were rows of
# this kind - a Selenium fulfilment run, an interaction tag written by the bot,
# a generated vendor pseudo-email and its password, a chat transcript dump -
# every one of them rendered as if it were part of the guest's story.
#
# They are matched here, on the RAW body, because by the time a label exists
# the evidence has been summarised away and only the wording is left to guess
# from. Machinery is MARKED, never dropped: a body that trips one of these by
# accident (a guest writing "I forgot my password") stays in the timeline with
# a reason attached, which can be found and argued with, instead of vanishing.
_MACHINE_BODY = [
    # The structured booking dump Zendesk auto-posts onto the ticket: pax,
    # price, vendor, instructions, escalation contacts. Machine-posted, so it
    # is not the guest's story - but it is also not a fulfilment attempt, and
    # it has to be matched BEFORE the selenium pattern. It mentions Selenium
    # twice, as fulfillmentType and in the instruction text, which was enough
    # to have it classified as a run that never happened and labelled
    # "Fulfilment run attempted" off a metadata dump.
    (re.compile(r"--\s*Booking\s+Info\b|\*\*Product\s+Details\*\*"
                r"|\bBooking_Id\s*:|\bItinerary\s+Id\s*:", re.I), "booking-info"),
    # Run language, not the bare word. "fulfillmentType: SELENIUM" describes
    # how this booking is fulfilled; it is not a record of anything running.
    (re.compile(r"selenium\s+(?:run|attempt|fulfil|script|job)"
                # An action verb near the word, in either order. "Fulfilment
                # ATTEMPTED via Selenium" is a run; "fulfilled BY Selenium" is
                # a description of how this product works and must not match,
                # which is why the verb has to be one of doing, not of being.
                r"|(?:attempt\w*|ran\b|retr\w+|triggered|failed)[^.\n]{0,30}selenium"
                r"|webdriver|headless\s+chrome|automation\s+(?:run|script|bot)"
                r"|\bbot\s+run\b", re.I), "selenium-run"),
    (re.compile(r"pseudo[\s.\-]?e?mail|vendor\s+login|login\s+credential"
                r"|credentials?\s*[:\-]|password\s*[:\-]", re.I), "credentials"),
    (re.compile(r"chat\s+(?:transcript|session)|transcript\s+log(?:ged)?"
                r"|conversation\s+(?:opened|closed|started|ended)"
                r"|session\s+(?:id|started|ended)", re.I), "chat-bookkeeping"),
    (re.compile(r"interaction\s+tags?|auto[\s\-]?tagged|ai[\s\-]?resolved"
                r"|bot\s+(?:tag|tagged|classification)"
                r"|tags?\s+(?:added|updated|applied)\s*[:\-]", re.I), "bot-tagging"),
]

# via.channel values only a trigger, an automation or a background job can
# produce. Nothing a person typed ever arrives on these.
_MACHINE_CHANNELS = {"rule", "system", "automation", "trigger", "batch"}


# A body this long is a CONVERSATION that mentions session boilerplate, not a
# bookkeeping line that consists of it.
#
# "chat-bookkeeping" matches "chat transcript", "conversation started/ended",
# "session id" — and a real chat transcript contains every one of those, at the
# top, before the guest says anything. So a genuine guest chat was classified
# as machinery: `_detect_actor` consults `_internal_reason` FIRST and returned
# "system", `is_internal` went true, `is_conversation` rejected it, and the
# ticket produced no contact frame at all. The model's note for that ticket
# then had nothing to join to and rendered badged "unmatched ZD reference" —
# a broken join reported as a reference the model got wrong.
#
# This module's own docstring already warned about the shape: "a real guest
# sentence containing the word 'credential' was dropped with no trace". Moving
# the decision earlier fixed WHERE it was made, not the over-match.
#
# 400 characters is a JUDGEMENT and only the bookkeeping patterns are subject
# to it. The others name things a person does not write — webdriver output,
# login credentials, a booking-info dump — and length says nothing about
# those.
_BOOKKEEPING_MAX_CHARS = 400
_LENGTH_SENSITIVE = {"chat-bookkeeping", "bot-tagging"}


# How many events one timeline may carry. The shaping call is sized for this.
_TIMELINE_CAP = 40


def _event_rank(ev: dict) -> int:
    """How much this event earns its place. Higher survives the cap.

    A guest contact is why the case exists. A note recording a booking fact —
    a reschedule, a cancellation, a refund — is what the reader is scanning
    for. Confirmation mail and payment reminders are the booking working
    normally, and pure machinery is the noise the toggle exists to hide.
    """
    from server.ticket_notes import note_disposition
    actor = str(ev.get("actor") or "").lower()
    body = ev.get("raw_body") or ev.get("summary") or ""
    if actor in ("guest", "co", "sp"):
        return 3
    if note_disposition(body)[0] == "keep":
        return 2
    if not ev.get("is_internal"):
        return 1
    return 0


def _trim_to_cap(events: list, cap: int) -> tuple:
    """(kept, note) — the cap applied by significance, order preserved.

    `events` is the (sort_key, dict, body) tuple list the fetch builds. What
    is dropped is REPORTED, because a timeline silently missing its middle is
    indistinguishable from a booking that quietly went well.
    """
    if len(events) <= cap:
        return events, ""
    ranked = sorted(range(len(events)),
                    key=lambda i: (-_event_rank(events[i][1]), i))
    keep_idx = sorted(ranked[:cap])
    dropped = len(events) - len(keep_idx)
    by_rank = {}
    for i in ranked[cap:]:
        by_rank[_event_rank(events[i][1])] = by_rank.get(_event_rank(events[i][1]), 0) + 1
    detail = ", ".join(f"{n} {'machinery' if r == 0 else 'routine'}"
                       for r, n in sorted(by_rank.items()))
    return ([events[i] for i in keep_idx],
            f"{dropped} of {len(events)} events did not fit the {cap}-event "
            f"cap and were left off ({detail}); the ones kept are the guest "
            f"contacts and the notes recording booking facts")


def _machine_body_reason(body: str) -> str:
    """Which machine pattern this body matches, or '' if it reads as human."""
    text = _html.unescape(_TAG_RE.sub(" ", body or ""))[:4000]
    for rx, reason in _MACHINE_BODY:
        if not rx.search(text):
            continue
        if reason in _LENGTH_SENSITIVE and len(text.strip()) > _BOOKKEEPING_MAX_CHARS:
            # The marker is in there, but so is a great deal of other text.
            # Keep looking: a later pattern may still make this machinery on
            # its own evidence.
            continue
        return reason
    return ""


def _internal_reason(body: str, via_channel: str = "", author_role: str = "") -> str:
    """
    Why this comment is internal machinery rather than part of the guest's
    story, or '' if it is guest-facing.

    Decided from the Zendesk signals on the comment itself - the channel it
    arrived on and what its body actually is - so the classification is made
    once, at the point where the evidence still exists, and stored. The
    renderer used to do this with a regex over the finished label, which is
    both too late (the body is gone) and too blunt (a real guest sentence
    containing the word "credential" was dropped with no trace).
    """
    ch = str(via_channel or "").strip().lower()
    if ch in _MACHINE_CHANNELS:
        return f"via:{ch}"
    return _machine_body_reason(body)


def _detect_actor(comment_author_id, ticket, author_role: str,
                  is_sp_ticket: bool, ticket_tags: list,
                  body: str = "", via_channel: str = "") -> str:
    # A machine-written body is not the guest speaking, whatever account it was
    # posted under. Zendesk attributes automation - booking confirmations,
    # pseudo-email logins, Selenium fulfilment runs, transcript dumps - to the
    # SAME user id as the ticket's requester, and the requester test below only
    # asks "is this the requester's account", not "did the guest write this".
    # So system mail was coming out as actor "guest", and a timeline that says
    # the guest said something they never said can end up quoted back to them.
    # Body evidence therefore settles it before account identity gets a vote.
    if _internal_reason(body, via_channel):
        return "system"
    if any(t in ticket_tags for t in ZENDESK_BOT_TAGS):
        return "ai"
    if comment_author_id == getattr(ticket, "requester_id", None):
        return "guest"
    if is_sp_ticket:
        return "sp"
    # via "api" means an integration posted this, not a person typing in
    # Zendesk. On booking 32908218 four consecutive comments came from the same
    # admin account - the booking-in-progress mail, the confirmation mail, the
    # cancellation and the refund - all via api, and every one was rendered as
    # "CE response" as though an agent had written it. The one comment a human
    # actually typed that day arrived via "web". So the channel separates the
    # template from the person, which nothing in the body reliably does.
    #
    # Restricted to staff accounts: a guest posting through the app also
    # arrives via api, and that IS the guest.
    if via_channel == "api" and author_role in ("agent", "admin"):
        return "system"
    if author_role in ("agent", "admin"):
        return "co"
    # An end-user in Zendesk IS a customer - that is what the role means. The
    # requester test above only recognises the guest when they are the
    # requester of THIS ticket, and a booking routinely spans several tickets,
    # so the guest's own chat message on a second ticket fell past every branch
    # into "system". On booking 32908218 that put the guest's live-chat
    # complaint under the label "Chat transcript logged" and left the timeline
    # with no guest row at all - the one person the RCA is about never appeared
    # in it.
    if author_role == "end-user":
        return "guest"
    # A CHAT TRANSCRIPT IS A CONVERSATION, whoever Zendesk files it under.
    #
    # Zendesk posts a transcript under its own system account — author_id -1,
    # which `_role` correctly refuses to look up — so the guest's live chat
    # fell past every branch above into "system". `is_conversation` then
    # excluded it, and the Guest ↔ Support section reported "2 contacts" that
    # were two AGENT INTERNAL NOTES (actor "co"), while the one real
    # conversation on the booking sat in "22 system events moved to the
    # timeline".
    #
    # The channel is the evidence: nothing but a person produces a chat, and
    # `_map_channel` has already resolved the whole messaging family — chat,
    # native_messaging, whatsapp, sms — to "chat". Attributed to the guest
    # because a transcript is the guest's conversation; both sides are in the
    # body, and the row exists to say the guest talked to us.
    if _map_channel(via_channel) == "chat":
        return "guest"
    return "system"


async def get_timeline(
    booking_id: str,
    review_id: str = None,
    booking: dict = None,
    review_body: str = "",
    review_pub_date: str = "",
) -> tuple[list, dict, dict]:
    """
    Returns (timeline, extracted_booking_fields, meta).

    timeline: chronological events shaped for the demo-v15 renderer:
        {time, time_sort, thread, actor, label, summary, ticket_id,
         is_internal, internal_reason}
    time is for display and does not sort; time_sort is ISO-8601 UTC and does.
    is_internal marks Headout machinery (Selenium, bot tagging, credentials,
    transcript logs) so the renderer can hide it without the event being lost.
    extracted_booking_fields: {tgid, tid, ticket_mail_seen} from custom fields/tags.
    meta: {"ticket_ids": [str, ...], "timeline_raw": [str, ...],
           "zendesk_requester_name": str}

    booking, review_body, review_pub_date are passed to Claude for intelligent
    shaping (bookend injection, noise-drop, macro-flood collapsing).
    """
    if not is_live("zendesk"):
        if review_id and review_id in MOCK_TIMELINES:
            tl = MOCK_TIMELINES[review_id]
            return tl, {}, {"ticket_ids": [], "timeline_raw": [""] * len(tl), "timeline_raw_ticket_ids": [""] * len(tl)}
        for rid, b in MOCK_BOOKINGS.items():
            if b.get("id") == booking_id:
                tl = MOCK_TIMELINES.get(rid, [])
                return tl, {}, {"ticket_ids": [], "timeline_raw": [""] * len(tl), "timeline_raw_ticket_ids": [""] * len(tl)}
        # Mock synthesis: activates in MOCK_MODE for review IDs not in fixtures.
        # Enables manual testing without real service calls.
        # Mock events go through the same time helpers as real ones. They used
        # to be written as "2026-07-29 09:00", a third time format on top of the
        # two the renderer already had to cope with, which made mock mode a bad
        # place to develop the renderer against.
        from datetime import date
        today = date.today().isoformat()
        synth_tl = [
            {
                "time":   _to_ist(f"{today}T09:00:00+00:00"),
                "time_sort": _to_iso(f"{today}T09:00:00+00:00"),
                "thread": "email",
                "actor":  "guest",
                "label":  "Guest contacted support",
                "summary": "[Mock] Guest emailed support about their experience.",
                "ticket_id": "",
                "is_internal": False,
                "internal_reason": "",
            },
            {
                "time":   _to_ist(f"{today}T10:30:00+00:00"),
                "time_sort": _to_iso(f"{today}T10:30:00+00:00"),
                "thread": "email",
                "actor":  "co",
                "label":  "CE responded",
                "summary": "[Mock] CE acknowledged the guest's concern and reviewed the booking.",
                "ticket_id": "",
                "is_internal": False,
                "internal_reason": "",
            },
        ]
        return synth_tl, {"ticket_mail_seen": False}, {"ticket_ids": [], "timeline_raw": ["", ""], "timeline_raw_ticket_ids": ["", ""]}

    _z = _get_client()
    if _z is None:
        log.warning("[zendesk] no client available")
        return [], {}, {"ticket_ids": [], "timeline_raw": []}

    t0 = time.time()
    async with _ZD_SEM:
        waited = time.time() - t0
        if waited > 2.0:
            log.warning(f"[zendesk] wait time exceeded 2s: {waited:.1f}s")
        raw_events, extracted, meta = await asyncio.get_running_loop().run_in_executor(
            None, _get_timeline_sync, _z, booking_id)

    try:
        timeline = await _shape_via_claude(
            raw_events, booking or {}, review_body, review_pub_date)
    except Exception as e:
        log.warning(f"[zendesk] Claude shaping failed — using fallback: {e}")
        timeline = _fallback_shape(raw_events)

    return timeline, extracted, meta


def _get_timeline_sync(_z, booking_id: str):
    """Synchronous Zendesk work — called from get_timeline via run_in_executor.

    Returns (raw_events, extracted, meta) where raw_events is a list of:
        {idx, time, thread, actor, ticket_id, raw_body}
    meta contains ticket_ids, timeline_raw (parallel raw bodies), and
    zendesk_requester_name.
    """
    # ── Search: ALL THREE ROUTES, unioned ────────────────────────────────────
    # Not fieldvalue-then-fallback. The custom field is set on a minority of a
    # case's tickets, so a hit there used to end the search with the chat and
    # the open contact still unfound. See `collect_tickets`.
    tickets, _search_tally = collect_tickets(
        booking_id, lambda q: _search_with_retry(_z, q))
    for _route in ("fieldvalue", "free_text", "requester"):
        if _search_tally[_route]:
            SEARCH_COUNTERS[_route] += 1
    log.info(f"[zendesk] BID {booking_id}: {len(tickets)} ticket(s) — {_search_tally}")
    if not tickets:
        log.info(f"[zendesk] no tickets for BID {booking_id} (all search routes)")
        return [], {}, {"ticket_ids": [], "timeline_raw": [],
                        "zendesk_requester_name": "",
                        "search_tally": _search_tally}

    # ── Extract booking fields from custom fields + tags ─────────────────────
    extracted = {}
    ticket_mail_seen = False
    # COUNTED. A private note that was read and set aside and one that was
    # never fetched produce the same timeline, and only the count tells them
    # apart.
    _private_kept = _private_dropped = 0
    for t in tickets:
        if not extracted.get("tgid"):
            v = get_custom_field(t, ZENDESK_TGID_FIELD)
            if v:
                extracted["tgid"] = str(v)
        if not extracted.get("tid"):
            v = get_custom_field(t, ZENDESK_TID_FIELD)
            if v:
                extracted["tid"] = str(v)
        if "ticket_mail_seen" in (getattr(t, "tags", None) or []):
            ticket_mail_seen = True
    extracted["ticket_mail_seen"] = ticket_mail_seen

    # ── Requester name from first ticket ─────────────────────────────────────
    zendesk_requester_name = ""
    if tickets:
        try:
            requester_id = getattr(tickets[0], "requester_id", None)
            if requester_id:
                u = _z.users(id=requester_id)
                zendesk_requester_name = getattr(u, "name", "") or ""
        except Exception:
            pass

    # ── User role cache for actor detection ──────────────────────────────────
    _role_cache: dict = {}

    def _role(author_id) -> str:
        if author_id in _role_cache:
            return _role_cache[author_id]
        # -1 is Zendesk's marker for a comment the system posted (a chat
        # transcript, an automation), not a user id. Asking the API for it
        # always fails with "id must be >= 0" and logged a warning on every
        # single timeline - noise that buried the lookups that really did fail.
        try:
            if int(author_id) <= 0:
                _role_cache[author_id] = ""
                return ""
        except (TypeError, ValueError):
            pass
        role = ""
        try:
            u = _z.users(id=author_id)
            role = getattr(u, "role", "") or ""
        except Exception as e:
            # An empty role is not a neutral default - it means every
            # actor branch below misses and the comment falls through to
            # "system", so a failed lookup silently reattributes a person's
            # message to a machine. It has to be visible.
            log.warning(f"[zendesk] role lookup failed for author {author_id}: "
                        f"{type(e).__name__}: {e}")
        if not role:
            log.warning(f"[zendesk] no role for author {author_id} - actor "
                        f"detection will fall through to system")
        _role_cache[author_id] = role
        return role

    # ── Fetch comments per ticket (zenpy paginates), build raw events ─────────
    events = []   # (sort_dt, raw_event_dict, raw_body)
    for ticket in tickets:
        is_sp = bool(ZENDESK_BRAND_GUEST and ZENDESK_BRAND_SP
                     and _brand_matches(ticket, ZENDESK_BRAND_SP))
        tags = getattr(ticket, "tags", None) or []
        try:
            comments = list(_z.tickets.comments(ticket=ticket.id))
        except Exception as e:
            from server.services import zd_connector
            if zd_connector.is_auth_error(e) and zd_connector.available():
                _z2 = zd_connector.retry_client_on_auth_error()
                comments = list(_z2.tickets.comments(ticket=ticket.id))
            else:
                log.warning(f"[zendesk] comments fetch failed for ZD-{ticket.id}: {e}")
                continue

        for c in comments:
            # PRIVATE COMMENTS ARE KEPT, AND MARKED. This read:
            #
            #     if getattr(c, "public", True) is False:
            #         continue
            #
            # so a Zendesk internal note never became a raw event, and the
            # note recording that the guest rescheduled was absent from the
            # timeline and from case findings alike.
            #
            # Everything downstream then ran correctly on nothing, which is
            # why it looked like a prompt failure. The shaping prompt says
            # "KEEP EVERY EVENT … do NOT drop machinery" and the model kept
            # every event it was shown. `select_internal_notes` — with
            # `note_disposition`, `collapse_repeats` and `ping_summary` behind
            # it, all written to sort exactly these notes into keep/drop/judge
            # — opens with `if not internal: return rows` and returned
            # unchanged. A whole subsystem for handling internal notes, fed by
            # a filter that removed them first.
            #
            # It also hid itself: the toggle counts what it withheld, and with
            # nothing marked internal there was no toggle and no count. Some
            # machinery still rendered — automated senders post PUBLIC
            # comments — so the timeline looked like it carried internal rows
            # while the private ones were gone.
            #
            # The original reasoning ("summarising them produced entries that
            # read as guest-facing events but are not") was written before
            # `is_internal` and the toggle existed. Both do now, and marking
            # is what they are for: `_internal_reason` below already computes
            # it, and `select_internal_notes` promotes a booking fact inline
            # while leaving ticket housekeeping behind the toggle.
            #
            # SP side conversations are handled separately and are unaffected.
            _is_private = getattr(c, "public", True) is False
            body = getattr(c, "body", "") or getattr(c, "html_body", "") or ""
            via_ch = getattr(getattr(c, "via", None), "channel", "") or ""
            author_id = getattr(c, "author_id", None)
            actor = _detect_actor(author_id, ticket, _role(author_id), is_sp, tags,
                                  body=body, via_channel=via_ch)
            thread = "sp" if is_sp else _map_channel(via_ch)
            # ONLY THE PRIVATE NOTES THAT RECORD A BOOKING FACT.
            #
            # Keeping every private comment was wrong twice over. What was
            # asked for was the notes that say what happened to the booking —
            # rescheduled, cancelled, refunded — and what arrived was all of
            # them: agent macros, queue moves, and full HTML mail bodies
            # complete with ![Logo](https://cdn-imgix-open...). That payload
            # went to the shaping call, which came back unparseable, and
            # `_fallback_shape` rendered RAW BODIES with generic labels. The
            # entire timeline regressed and nothing said why.
            #
            # `note_disposition` already encodes the rule — reschedule,
            # cancellation, refund, tickets sent, payment are booking facts;
            # ticket administration is not. It was written for exactly this
            # and was being applied after shaping, too late to keep the
            # payload sane.
            #
            # "judge" is KEPT, per its own contract: unsure means show it.
            reason = _internal_reason(body, via_ch, _role(author_id))
            # THE GATE IS "IS THIS INTERNAL", NOT "IS THIS PRIVATE".
            #
            # It read `if _is_private`, and the Booking Info dump, the
            # ITINERARY MARGIN dump and the Overall Support Summary are PUBLIC
            # comments that `_internal_reason` marks as machinery. So the
            # furniture rule never saw them and they kept rendering as
            # "Booking details posted to ticket" and "Booking status snapshot
            # posted" — measured on 32885089 after the private half was
            # already fixed.
            #
            # Whether Zendesk marked a comment private is a fact about who can
            # SEE it. Whether it is machinery is a fact about what it IS, and
            # that is the question this rule answers.
            if _is_private or reason:
                from server.ticket_notes import note_disposition
                _verdict, _why_note = note_disposition(body)
                if _verdict == "drop":
                    _private_dropped += 1
                    continue
                _private_kept += 1

            # A private comment IS internal, whatever its text looks like.
            # `_internal_reason` reads the body for machinery patterns and a
            # hand-typed agent note has none of them — so without this, an
            # internal note would render inline as though the guest could see
            # it, which is the failure the old `continue` was avoiding.
            if _is_private and not reason:
                reason = "Zendesk internal note — not visible to the guest"
            created = getattr(c, "created_at", None)
            events.append((
                _sort_key(created),
                {
                    # The inputs _detect_actor decided from. Without them an
                    # actor that looks wrong cannot be told apart from an actor
                    # that was derived from bad inputs, which is the difference
                    # between a logic bug and a lookup failure.
                    "author_id":   author_id,
                    "author_role": _role(author_id) or "(none)",
                    "via_channel": via_ch or "(none)",
                    "requester_id": getattr(ticket, "requester_id", None),
                    "time":      _to_ist(created),
                    "time_sort": _to_iso(created),
                    "thread":    thread,
                    "actor":     actor,
                    "ticket_id": str(ticket.id),
                    "is_internal":     bool(reason),
                    "internal_reason": reason,
                    "raw_body":  body,
                },
                body,
            ))

    # ── Side conversations → SP thread ───────────────────────────────────────
    # A side conversation is the agent talking to the supply partner about this
    # booking, so every message in one belongs in the SP interaction thread of
    # the RCA. These are a separate Zendesk object from ticket comments and were
    # never fetched, which is why SP interactions were routinely empty.
    for ticket in tickets:
        for sc in side_conversations(getattr(ticket, "id", "")):
            for m in sc.get("messages", []):
                # Machinery reaches the SP thread too - the vendor-portal login
                # and password that fulfilment generates is a side-conversation
                # message like any other. It is marked, not dropped, and only on
                # the message body: the side conversation itself is the SP's half
                # of the story and stays in the timeline either way.
                sc_reason = _internal_reason(m.get("body", ""))
                events.append((
                    _sort_key(m.get("_raw_ts")),
                    {
                        "time":      m.get("time", ""),
                        "time_sort": _to_iso(m.get("_raw_ts")),
                        "thread":    "sp",
                        "actor":     "sp",
                        "ticket_id": str(getattr(ticket, "id", "")),
                        "is_internal":     bool(sc_reason),
                        "internal_reason": sc_reason,
                        "raw_body":  (f"[Side conversation: {sc.get('subject', '')}"
                                      f"{' · ' + sc['participants'] if sc.get('participants') else ''}]\n"
                                      f"{m.get('actor', '')}: {m.get('body', '')}"),
                    },
                    m.get("body", ""),
                ))

    events.sort(key=lambda e: e[0])

    # ── Truncation: drop the LEAST significant, never the middle ────────────
    #
    # This was `events[:20] + marker + events[-20:]`, which elides the MIDDLE.
    # The middle of a booking's life is where the booking's life happens — the
    # reschedule, the cancellation, the failed automation. The first twenty are
    # confirmation mail and payment reminders and the last twenty are the
    # aftermath, so a busy booking kept its two quiet ends and threw away its
    # story.
    #
    # It only started biting when the private notes came in and pushed real
    # cases past forty. Position was never the right axis; it was just never
    # tested by a long enough case.
    #
    # Significance, not position: a guest contact and a note recording a
    # booking fact are what the reader is here for. Pure machinery goes first.
    # Whatever survives is re-sorted into time order, so the chronology is
    # never what pays for the cap.
    if len(events) > _TIMELINE_CAP:
        events, _elided_note = _trim_to_cap(events, _TIMELINE_CAP)
        log.info("[zendesk] %s", _elided_note)
    else:
        _elided_note = ""

    # ── Assign sequential idx ─────────────────────────────────────────────────
    raw_events = []
    for i, (_, ev, _body) in enumerate(events):
        raw_events.append({"idx": i, **ev})

    timeline_raw = [e[2] for e in events]
    timeline_raw_ticket_ids = [e[1].get("ticket_id", "") for e in events]
    ticket_ids = [str(t.id) for t in tickets]

    return raw_events, extracted, {
        "ticket_ids": ticket_ids,
        "timeline_raw": timeline_raw,
        "timeline_raw_ticket_ids": timeline_raw_ticket_ids,
        "zendesk_requester_name": zendesk_requester_name,
        # WHICH ROUTES RAN AND WHAT EACH FOUND. Carried out so the trail can
        # say it: "one contact" is only honest when all three searched.
        "search_tally": _search_tally,
        "internal_notes": {"kept": _private_kept, "dropped": _private_dropped},
        "elided_note": _elided_note,
    }


def _fold(s: str) -> str:
    """Lowercase + strip diacritics so 'Jörg' and 'Jorg' compare equal."""
    return "".join(c for c in unicodedata.normalize("NFKD", s or "")
                   if not unicodedata.combining(c)).lower()


# Nickname / formal-name pairs. Deliberately short: only forms common enough in
# guest names to be worth the false-positive risk. Matching is symmetric.
_NICKNAMES = [
    ("joe", "joseph"), ("chris", "christopher"), ("dave", "david"),
    ("bob", "robert"), ("rob", "robert"), ("bill", "william"),
    ("will", "william"), ("mike", "michael"), ("nick", "nicholas"),
    ("tom", "thomas"), ("jim", "james"), ("dan", "daniel"),
    ("dick", "richard"), ("rick", "richard"), ("steve", "stephen"),
    ("steve", "steven"), ("tony", "anthony"), ("alex", "alexander"),
    ("sam", "samuel"), ("ben", "benjamin"), ("matt", "matthew"),
    ("andy", "andrew"), ("kate", "katherine"), ("cathy", "catherine"),
    ("liz", "elizabeth"), ("beth", "elizabeth"), ("sue", "susan"),
    ("pat", "patricia"), ("peggy", "margaret"), ("meg", "margaret"),
]


def _token_match(want: str, tokens: set) -> bool:
    """
    One name token against a candidate's tokens.

    Exact first, then prefix -- which covers initials ("C." -> "Catherine") and
    the common nickname/formal pair ("Joe" -> "Joseph"). Prefix only applies in
    that direction: a two-character stem must not swallow unrelated names, so a
    single letter matches only as an initial.
    """
    want = want.strip(". ").lower()
    if not want:
        return False
    if want in tokens:
        return True
    # Initials: a 1-2 letter stem matches a longer token it begins.
    # "C." -> "Catherine". Prefix alone does NOT cover nicknames -- "joseph"
    # does not start with "joe" -- so those need the table below.
    if len(want) <= 2 and any(t.startswith(want) for t in tokens):
        return True
    for a, b in _NICKNAMES:
        if want == a and b in tokens:
            return True
        if want == b and a in tokens:
            return True
    return False


def name_matches(candidate: str, first: str | None, last: str | None) -> bool:
    """
    Does this booking's guest name refer to the reviewer?

    BOTH names must be present. A surname alone is not a match: the reviewer
    "Joe Christopher" must not pull in "Christopher McCardle" or
    "Christopher E. Maclin", where Christopher is the FIRST name and Joe appears
    nowhere. Middle names are ignored, so "Fredrik Martin Olsen" matches
    "Fredrik Olsen" while "Fredrik Rostvold" does not.
    """
    tokens = set(re.findall(r"[a-z0-9]+", _fold(candidate)))
    if not tokens:
        return False
    want = [w for w in (first, last) if w and str(w).strip()]
    if not want:
        return False
    return all(_token_match(_fold(w), tokens) for w in want)


def _name_score(candidate: str, first: str | None, last: str | None) -> float:
    """
    How strongly a name refers to the review's author — 0.0 to 1.0.

    A CONFIDENCE, not a gate. Names legitimately differ from the booking: middle
    names, married names, nicknames, initials, or a booking made under a
    partner's name. A boolean test throws away the real booking in all of those
    cases, so this scores instead and lets the other indicators carry the rest.

    Surname is weighted 0.7 and first name 0.3, because a surname is far more
    distinctive than a shared first name:

      "Fredrik Olsen" vs "Fredrik Martin Olsen"     -> 1.0  (middle name)
      "Fredrik Olsen" vs "Olsen, Fredrik"           -> 1.0  (reordered)
      "Fredrik Olsen" vs "Fredrik Birkelund Holvik" -> 0.3  (first name only)
      "Fredrik Olsen" vs "Fredrik Rostvold"         -> 0.3  (another Fredrik)
      "Fredrik Olsen" vs "F. Olsen"                 -> 0.7  (surname carries it)

    Tokens, not substrings, so "Ole Berg" does not match "Olsen Bergman".
    Diacritics are folded, so "Jorg" matches "Jörg".
    """
    cand_tokens = set(re.findall(r"[a-z0-9]+", _fold(candidate)))
    if not cand_tokens:
        return 0.0
    score = weight = 0.0
    for part, w in ((first, 0.3), (last, 0.7)):
        if part and str(part).strip():
            weight += w
            if _fold(part) in cand_tokens:
                score += w
    return (score / weight) if weight else 0.0


# Venue-TYPE nouns. These appear in thousands of unrelated experience names, so
# an overlap consisting only of these is not evidence of the same venue: a
# review about the "palace of culture and science" must not match Pena Palace,
# Buckingham Palace or Doge's Palace. They still count once a distinctive word
# agrees ("palace" + "culture" + "science").
_VENUE_GENERIC = {
    "palace", "museum", "castle", "park", "tower", "cathedral", "church",
    "garden", "gardens", "zoo", "bridge", "square", "house", "hall", "centre",
    "center", "gallery", "temple", "arena", "stadium", "cruise", "river",
    "basilica", "chapel", "fortress", "monument", "aquarium", "observatory",
    "market", "island", "beach", "lake", "mountain", "valley", "national",
    "royal", "grand", "central", "old", "new", "great",
}


def _venue_tokens(s: str) -> set:
    """Significant words of a venue/experience name, accents folded."""
    stop = {"tour", "tours", "pass", "ticket", "tickets", "entry", "visit",
            "trip", "city", "day", "guided", "skip", "line", "with", "from",
            "and", "the", "experience", "admission", "access", "combo",
            "package", "hours", "hour", "half", "full", "private", "group",
            "small", "guide", "self", "audio", "optional", "direct"}
    return {t for t in re.findall(r"[a-z]{4,}", _fold(s)) if t not in stop}


def matches_indicators(sig: dict, ind: dict, first, last) -> tuple[bool, list]:
    """
    Does this ticket satisfy EVERY indicator the review actually gave us?

    AND across what is present; absent indicators are skipped, never blocking.
    Returns (ok, which indicators were used) so the trail can say why.
    """
    used = []

    # THE NAME REJECTS ONLY WHEN THE TICKET HAS ONE AND IT DISAGREES.
    #
    # This read `if not name_matches(sig["guest_name"], ...)` and returned
    # False, so a ticket whose guest-name CUSTOM FIELD is empty was rejected
    # outright — an ABSENT field treated as a DISAGREEMENT, which is the one
    # thing the venue check ten lines below is careful not to do:
    #
    #     "A ticket that records NO experience cannot contradict the review's
    #      venue - it simply has nothing to say."
    #
    # Exactly the same is true of the guest name, and getting it wrong here
    # costs more, because the tickets with an empty guest-name field are the
    # SAME sparse tickets that have an empty booking-id field — the ones the
    # body-BID fallback exists for. They were being thrown away before that
    # fallback could ever look at them: found by a `requester:"..."` search
    # that matched the guest exactly, then rejected for not repeating the name
    # in a custom field nobody filled in.
    #
    # An unnamed ticket does NOT count as agreement either — "name" is only
    # appended when a name was compared and matched, so a ticket carried this
    # far on venue and city alone cannot claim the name as evidence.
    sig["name_checked"] = False
    if first or last:
        _tname = (sig.get("guest_name") or "").strip()
        if _tname:
            if not name_matches(_tname, first, last):
                return False, used
            sig["name_checked"] = True
            used.append("name")

    venue = (ind.get("experience_or_venue") or "").strip()
    if venue:
        want = _venue_tokens(venue)
        got  = _venue_tokens(sig.get("experience") or "")
        overlap = want & got
        # A ticket that records NO experience cannot contradict the review's
        # venue - it simply has nothing to say. Rejecting on it threw away
        # tickets whose requester name matched exactly, which is how a review
        # naming a guest, a venue and a city ended up untraceable. Only an
        # experience that is present AND disagrees rejects.
        if got:
            # At least one DISTINCTIVE word must agree. Overlapping only on
            # venue-type nouns ("palace") matches half the catalogue.
            if not (overlap and (overlap - _VENUE_GENERIC)):
                return False, used
            used.append("venue")

    # City only filters when there is NO venue. The extractor returns whatever
    # the review gives it -- sometimes a city ("Krakow"), sometimes a country
    # ("Poland") -- and a country never token-matches the city on the ticket
    # ("Warsaw"), even though they agree. With a venue already matched, city can
    # only reject correct bookings, so it is recorded and not enforced.
    city = (ind.get("city_or_country") or "").split(",")[0].strip()
    if city:
        want = {t for t in re.findall(r"[a-z]{3,}", _fold(city))}
        got  = {t for t in re.findall(r"[a-z]{3,}", _fold(sig.get("city") or ""))}
        if want & got:
            used.append("city")
        elif got and not venue:
            return False, used

    # Pax NARROWS, it does not reject. It was decisive for a review naming
    # "9 combo tickets" -- 9 == 9 cut thirteen name+venue+city matches to one.
    # But a review saying "two tickets" against a ticket recording pax 1 is a
    # counting difference, not a different booking, and rejecting on it loses
    # the right one. Agreement is recorded here; shortlist() uses it to choose
    # between candidates when there is more than one.
    pax = ind.get("pax")
    if pax and sig.get("pax"):
        try:
            if int(pax) == int(sig["pax"]):
                used.append("pax")
        except (TypeError, ValueError):
            pass

    return True, used


_MONTHS = ("january", "february", "march", "april", "may", "june", "july",
           "august", "september", "october", "november", "december")


# Zendesk's search API returns at most this many results and silently drops
# the rest. Hitting it exactly is the only signal available that a search was
# truncated - the response says nothing.
_ZD_RESULT_CAP = 1000

_INDICATOR_KINDS = ("name", "venue", "city", "pax", "date")


def _merge_via(sig: dict, label: str) -> None:
    """Record that another search also found this booking.

    The searches run separately - by name, by name+venue, by name+city, by
    venue - and a booking more than one of them returns has independent
    agreement behind it. That used to be discarded as a duplicate.
    """
    vias = sig.setdefault("found_via_all", [sig.get("found_via")])
    if label in vias:
        return
    vias.append(label)
    for kind in label.split("+"):
        if kind in _INDICATOR_KINDS and kind not in (sig.get("matched_on") or []):
            sig.setdefault("matched_on", []).append(kind)


def _evidence(sig: dict) -> int:
    """How many distinct indicators point at this booking.

    Two sources, unioned. The indicators the TICKET itself satisfied, which
    matches_indicators reports, and the QUERIES that surfaced it - a booking
    returned by the name search and again by the venue search has two
    independent searches agreeing on it, which is not the same as one search
    liking it twice.
    """
    kinds = set()
    for tok in (sig.get("matched_on") or []):
        head = str(tok).split(":")[0].strip().lower()
        head = head.split(" ")[0]
        if head in _INDICATOR_KINDS:
            kinds.add(head)
    for via in (sig.get("found_via_all") or [sig.get("found_via")]):
        for part in str(via or "").split("+"):
            if part in _INDICATOR_KINDS:
                kinds.add(part)
    return len(kinds)


def _is_review_date(d, review_date: str | None) -> bool:
    """Is this date simply the day the review was written?

    Such a date is not evidence, wherever it came from. A review posted on the
    30th agrees with every booking visiting on the 30th - hundreds worldwide -
    so it discriminates nothing while reading on the card as corroboration.
    Amanda's review says "I am at the venue" and names no date at all; five
    Amandas in Glasgow, Singapore, San Diego, Darwin and Modena came back as
    confident matches on "visit date 2026-07-30".

    An earlier version of this asked where the date came from - it only
    discounted visit_date_hint, and only when dates_mentioned was empty, on
    the assumption that a populated dates_mentioned meant the guest had
    written a date. Extraction disproved that between two runs of the same
    review: the second put its own inferred "today" into dates_mentioned as
    well. So the test is the date itself, not its provenance.

    A guest who really did visit the day they reviewed loses nothing worth
    having: that date agrees with everyone, so it could never separate them.
    """
    return bool(review_date) and str(d)[:10] == str(review_date)[:10]


def _dates_agree(a: str, b: str) -> bool:
    """Two dates naming the same day, allowing the year to be off by one.

    The ticket's visit-date field against a date the review named. Guests
    write "20.10." and extraction resolves the year from the post date, so the
    day and month are observed and the year is inferred - the same reason the
    BigQuery search matches on day and month.
    """
    def _parts(s):
        m = re.search(r"(\d{4})-(\d{2})-(\d{2})", str(s or ""))
        return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None
    pa, pb = _parts(a), _parts(b)
    if not pa or not pb:
        return False
    return pa[1] == pb[1] and pa[2] == pb[2] and abs(pa[0] - pb[0]) <= 1


def _term_in_text(term: str, hay: str) -> bool:
    """Does the ticket describe this problem, in whatever words it used?

    A contiguous match on the extracted phrase is too strict for the same
    reason the quoted query was: "falsches Datum auf Voucher" against a ticket
    saying "Das Datum auf dem Voucher ist falsch" is the same problem written
    differently. The content words all have to be present; adjacency and order
    are not evidence of anything.
    """
    term = (term or "").strip().lower()
    if not term:
        return False
    if term in hay:
        return True
    words = [w for w in re.findall(r"\w+", term, re.UNICODE) if len(w) >= 4]
    return bool(words) and all(w in hay for w in words)


def _date_in_text(iso: str, hay: str) -> bool:
    """Does this ISO date appear in the text, however the guest wrote it?

    Extraction returns YYYY-MM-DD. Almost nobody writes a date that way in a
    support ticket, and the reviews this path exists for are the non-English
    ones - a German guest writes 20.06.2026, so a literal ISO substring test
    matched nothing outside a test fixture.
    """
    try:
        y, m, d = (int(x) for x in str(iso).strip()[:10].split("-"))
    except Exception:
        return False
    forms = {
        f"{y:04d}-{m:02d}-{d:02d}",
        f"{d:02d}.{m:02d}.{y:04d}", f"{d}.{m}.{y}", f"{d:02d}.{m:02d}.{y % 100:02d}",
        f"{d:02d}/{m:02d}/{y:04d}", f"{d}/{m}/{y}",
        f"{m:02d}/{d:02d}/{y:04d}", f"{m}/{d}/{y}",       # US ordering
        f"{d:02d}-{m:02d}-{y:04d}",
        f"{d:02d}.{m:02d}.", f"{d}.{m}.",                 # day.month, no year
    }
    if 1 <= m <= 12:
        mon = _MONTHS[m - 1]
        forms |= {f"{d} {mon}", f"{mon} {d}", f"{d} {mon[:3]}", f"{mon[:3]} {d}"}
    return any(f in hay for f in forms)


async def shortlist(indicators: dict, author_first, author_last,
                    limit_total: int = 5, since: str | None = None,
                    notes: list | None = None,
                    review_date: str | None = None) -> list[dict]:
    """
    The bookings a review's indicators actually point at.

    Search Zendesk with whatever indicators exist, then keep only the tickets
    that satisfy ALL of them. No BigQuery: the booking id and every fact needed
    to judge a match are on the ticket itself, and BQ is only needed once an
    associate confirms one.

    At most `limit_total` come back, ranked by how much of the review agrees
    with each: the indicators the ticket satisfies, whether more than one of
    the separate searches found it, and whether its visit date is a date the
    review named. Five bookings is a choice an associate can make; thirteen is
    a list they have to read.
    """
    if not is_live("zendesk"):
        return []
    _z = _get_client()
    if _z is None:
        return []

    name  = " ".join(x for x in (author_first, author_last) if x).strip()
    venue = (indicators.get("experience_or_venue") or "").strip()
    city  = (indicators.get("city_or_country") or "").split(",")[0].strip()
    # The problem the guest described is an identifier in its own right. A
    # guest who writes a review almost always contacted support about the
    # same thing first, so the ticket that carries the booking id is findable
    # by the problem even when the review has no booking id and names no
    # venue - which is exactly the case that used to land in Untraceable.
    issue_terms = [str(t).strip() for t in (indicators.get("issue_terms") or [])
                   if str(t).strip()][:5]
    if not (name or venue or issue_terms):
        return []

    # Query set is chosen so nothing is broad enough for Zendesk to truncate.
    # A bare `type:ticket <name>` matches every ticket mentioning that name
    # anywhere and reliably trips "more results than Zendesk allows" -- the
    # correct booking can then fall outside the window entirely. Likewise a
    # bare venue query returns everyone who booked that venue. Both are only
    # used when they are the ONLY indicator available; otherwise the combined
    # queries cover the same ground precisely.
    ORDER = "order_by:created_at sort:desc"
    # A date floor on the searches that would otherwise match every ticket
    # naming a common word. Zendesk returns at most _ZD_RESULT_CAP rows and
    # silently drops the rest, so on a broad query the right booking can fall
    # outside the window entirely and no amount of ranking gets it back.
    # Applied ONLY to the broad forms - the combined queries are already
    # narrow, and bounding them could drop a real match for nothing.
    BOUND = f" created>{since}" if since else ""
    queries = [] 
    if name:
        queries.append((f'type:ticket requester:"{name}"{BOUND} {ORDER}', "name"))
    if name and venue:
        queries.append((f'type:ticket {name} {venue}{BOUND} {ORDER}', "name+venue"))
    # THE CATALOGUE SPELLING, when the resolver produced one. The guest wrote
    # "collosseum"; the ticket the agent raised says "Colosseum". Searching
    # only what the guest typed is searching for a string nobody else wrote —
    # the correction was already computed for BigQuery and simply never
    # reached here. Kept as EXTRA queries rather than a replacement, because
    # the raw spelling is occasionally the one in the ticket (the agent copied
    # the guest's words) and dropping it would trade one blind spot for
    # another.
    for _fixed in (indicators.get("venue_names_resolved") or [])[:3]:
        _fixed = str(_fixed).strip()
        if not _fixed or _fixed.lower() == venue.lower():
            continue          # nothing was corrected; the query already exists
        if name:
            queries.append((f'type:ticket {name} "{_fixed}"{BOUND} {ORDER}',
                            "name+venue(corrected)"))
        else:
            queries.append((f'type:ticket "{_fixed}"{BOUND} {ORDER}',
                            "venue(corrected)"))
    if name and city:
        queries.append((f'type:ticket {name} {city}{BOUND} {ORDER}', "name+city"))
    if name and not venue and not city:
        # Name-only review: the requester search alone can miss tickets raised
        # under a different requester, so the free-text name is needed here.
        queries.append((f'type:ticket {name}{BOUND} {ORDER}', "name"))
    if venue and not name:
        queries.append((f'type:ticket "{venue}"{BOUND} {ORDER}', "venue"))
    # Issue-led queries are a SECOND PASS, not part of this list. The direct
    # indicators are the matcher and they stay untouched: when name, venue or
    # city produce a match, that is the answer and the problem text is never
    # searched. The second pass runs only when they produce nothing.
    issue_queries = []
    for term in issue_terms[:3]:
        # Quotes mean an exact contiguous phrase in Zendesk. Extraction returns
        # things like "falsches Datum auf Voucher", and the ticket it needs to
        # find says "Das Datum auf dem Voucher ist falsch" - same problem,
        # different word order, and the phrase search matches neither. Only
        # short terms stay quoted; anything longer is left as separate words,
        # which Zendesk ANDs, so the words must all appear but need not be
        # adjacent or in order.
        q_term = f'"{term}"' if len(term.split()) <= 2 else term
        if name:
            issue_queries.append((f'type:ticket {name} {q_term}{BOUND} {ORDER}', "name+issue"))
        elif venue:
            issue_queries.append((f'type:ticket "{venue}" {q_term}{BOUND} {ORDER}', "venue+issue"))
        else:
            issue_queries.append((f'type:ticket {q_term}{BOUND} {ORDER}', "issue"))

    loop = asyncio.get_running_loop()
    # issue_pass=False reproduces the original behaviour exactly: corroboration
    # is not computed, nothing is promoted, and the weak fallback is the same
    # name-only fallback it always was.
    seen_tickets, by_bid, weak_by_bid = set(), {}, {}
    ticket_bid = {}          # ticket id -> booking id, for repeat sightings
    # Tickets with no name agreement but whose text matches the problem
    # AND a date the review named. Two independent signals with no name
    # is still worth a human's glance.
    issue_by_bid = {}
    async def _scan(qs, issue_pass):
        for q, label in qs:
            try:
                hits = await loop.run_in_executor(None, lambda qq=q: _search_with_retry(_z, qq))
            except Exception as e:
                log.warning(f"[shortlist] query failed ({label}): {e}")
                if notes is not None:
                    notes.append({"kind": "failed", "label": label,
                                  "detail": str(e)[:120]})
                continue
            # Zendesk returns at most _ZD_RESULT_CAP results and drops the
            # rest without saying so in the response. Coming back with exactly
            # the cap means the search was too broad and the right booking may
            # never have been in what we got - which the associate has to be
            # told, because five candidates from a truncated search does not
            # mean five exist.
            if len(hits or []) >= _ZD_RESULT_CAP:
                log.warning(f"[shortlist] query '{label}' hit the Zendesk result "
                            f"cap ({_ZD_RESULT_CAP}) - results are incomplete")
                if notes is not None:
                    notes.append({"kind": "truncated", "label": label,
                                  "detail": q})
            for t in hits or []:
                tid = str(getattr(t, "id", ""))
                if tid in seen_tickets:
                    # Same ticket, found again by a different search. That is
                    # still both indicators locating the same booking, so the
                    # label is recorded before moving on - skipping outright
                    # lost the agreement whenever one ticket answered both
                    # queries, which is the common case.
                    _b = ticket_bid.get(tid)
                    _prior = (by_bid.get(_b) or weak_by_bid.get(_b)) if _b else None
                    if _prior is not None:
                        _merge_via(_prior, label)
                    continue
                seen_tickets.add(tid)
                sig = ticket_signals(t)
                bid = sig.get("booking_id")
                # THE FIELD IS OFTEN EMPTY. `if not bid: continue` ended the
                # ticket's life here — a ticket this very search FOUND, whose
                # first line carried the booking id, discarded in silence and
                # indistinguishable from a ticket that was never found. The
                # other extraction path in this file has always read the text.
                bid_source = "field"
                if not bid:
                    _not_bids = set(seen_tickets) | {tid}
                    if sig.get("itinerary_id"):
                        # 8 digits in its own field; scraping mistakes it for a
                        # booking id every time.
                        _not_bids.add(str(sig["itinerary_id"]))
                    _text_bids, _prov = bids_from_ticket_text(t, _not_bids)
                    if not _text_bids:
                        # FOUND AND UNUSABLE is not the same as never found,
                        # and it is the distinction that cost three rounds to
                        # locate. Counted so the trail can say how many.
                        if notes is not None:
                            notes.append({"kind": "no_bid", "label": label,
                                          "detail": tid})
                        continue
                    bid = _text_bids[0]
                    bid_source = f"text:{_prov}"
                    sig["booking_id"] = bid
                    sig["bid_from_text"] = True
                    sig["bid_text_provenance"] = _prov
                    if len(_text_bids) > 1 and notes is not None:
                        # A judgement, announced. The first number is used and
                        # the others are not, and a reader must not have to
                        # infer that from a candidate list that shows one.
                        notes.append({"kind": "ambiguous_bid", "label": label,
                                      "detail": f"{tid}:{','.join(_text_bids)}"})
                sig["bid_source"] = bid_source
                # The same booking, surfaced again by a DIFFERENT query. That
                # is the strongest thing this function learns and it used to be
                # thrown away: the name search and the venue search were run
                # separately, and a booking both of them returned was skipped
                # the second time as a duplicate. Agreement between two
                # independent searches is exactly what says which booking best
                # matches the review, so it is recorded instead of discarded.
                ticket_bid[tid] = bid
                if bid in by_bid:
                    _merge_via(by_bid[bid], label)
                    continue
                # A weak candidate still accumulates evidence. It is judged
                # again below - a later ticket may satisfy every indicator and
                # promote it - but the fact that another search also found it
                # counts either way, and the weak list is what an associate
                # sees when nothing stronger survives.
                if bid in weak_by_bid:
                    _merge_via(weak_by_bid[bid], label)
                ok, used = matches_indicators(sig, indicators, author_first, author_last)

                # Does the ticket's own visit-date field land on a date the
                # review named? This is a field comparison, not a text search,
                # and it runs in BOTH passes because it is free and it is the
                # thing that separates two bookings by one guest.
                #
                # It changes ORDER and what the card says, never membership:
                # the same candidates are returned either way. Sven's review
                # named 20.10 and produced two bookings by the same person -
                # High School Musical on the 23rd and Sinatra on the 20th -
                # shown as equally good, with the date he gave us unused.
                # visit_date_hint belongs here as much as dates_mentioned, and
                # was being ignored. Amanda's review says "I am at the venue"
                # and named no date, so dates_mentioned was empty and
                # visit_date_hint held the one date that mattered - the day she
                # was standing there. Ranking looked at neither, and five
                # unrelated Amandas came back in ticket order.
                _cand_dates = [d for d in ([indicators.get("visit_date_hint")]
                                           + list(indicators.get("dates_mentioned") or []))
                               if d and not _is_review_date(d, review_date)]
                _visit_hit = next(
                    (d for d in _cand_dates
                     if _dates_agree(sig.get("visit_date"), d)), None)
                if _visit_hit:
                    used = list(used) + [f"visit date {_visit_hit}"]
                sig["visit_hit"] = _visit_hit

                # Corroboration is computed only in the issue pass. In the direct
                # pass these stay empty, so every downstream branch behaves
                # exactly as it did before this existed.
                hit_terms, hit_dates = [], []
                if issue_pass:
                    # Read the ticket's own words. ticket_signals() returns the
                    # booking's FACTS off the custom fields - booking id, guest,
                    # experience, city, visit date - and has never carried the
                    # subject or body. Looking for the guest's problem in there
                    # found nothing, every time, silently.
                    hay = " ".join((
                        str(getattr(t, "subject", "") or ""),
                        str(getattr(t, "description", "") or "")[:4000],
                    )).lower()
                    hit_terms = [term for term in issue_terms if _term_in_text(term, hay)]
                    hit_dates = [d for d in (indicators.get("dates_mentioned") or [])
                                 if _date_in_text(d, hay)]
                    if hit_terms:
                        used = list(used) + [f"issue:{hit_terms[0]}"]
                    if hit_dates:
                        used = list(used) + [f"date:{hit_dates[0]}"]
                sig["issue_hits"] = hit_terms
                sig["date_hits"]  = hit_dates
                sig["found_via"]  = label
                sig["found_via_all"] = [label]
                sig["created_at"] = str(getattr(t, "created_at", "") or "")
                sig["ticket_id"]  = tid
                # A NUMBER READ OUT OF PROSE MUST EARN ITS PLACE. The custom
                # field is Zendesk asserting "this is the booking id"; a number
                # in a sentence is us deciding it looks like one, and nothing
                # here verifies it — shortlist does no BigQuery by design.
                #
                # So the two are held to different standards. A field BID may
                # sit in the weak list on a name agreement alone, because the
                # id itself is not in doubt and only the LINK to this review
                # is. For a text BID both are in doubt at once, and "a guest
                # called Amanda, and some 8-digit number in the ticket" is not
                # a candidate — it is two guesses presented as one lead.
                #
                # The whole indicator set agreeing is what separates them, and
                # it is exactly the ticket the search was built to find: the
                # name AND the venue AND the date line up, and the id is
                # written in the body because nobody filled the field in.
                if sig.get("bid_from_text") and not ok:
                    if notes is not None:
                        notes.append({"kind": "text_bid_unconfirmed",
                                      "label": label, "detail": tid})
                    continue

                if ok:
                    if not sig.get("name_checked") and (author_first or author_last):
                        # SURVIVING WITHOUT A NAME COMPARISON IS NEW, and the
                        # card must not present it as one. The ticket carried
                        # no guest name, so whatever "name" reaches matched_on
                        # came from the QUERY that found it — Zendesk matching
                        # a requester, or the name appearing somewhere in the
                        # text — not from the ticket agreeing about the guest.
                        # Different strengths, and the reader is choosing a
                        # booking on them.
                        if notes is not None:
                            notes.append({"kind": "name_unverified",
                                          "label": label, "detail": tid})
                    sig["matched_on"] = used
                    # "Satisfies every indicator" is vacuous when the review
                    # gave us one. Amanda's review has a first name and nothing
                    # else, so every ticket for a guest called Amanda satisfies
                    # every indicator there is - five different people on five
                    # continents, each labelled a confident match. One agreement
                    # is a lead, not an identification, and the card has to say
                    # which it is.
                    if _evidence(sig) < 2 and not _visit_hit:
                        sig["weak"] = True
                    by_bid[bid] = sig
                    weak_by_bid.pop(bid, None)   # promoted: drop the weak reading
                elif name and name_matches(sig.get("guest_name") or "",
                                           author_first, author_last):
                    # The name matched but another indicator disagreed. That is a
                    # weaker signal, not a refutation - the guest may have written
                    # about one leg of a multi-experience trip, or the ticket may
                    # carry a different experience name than the review's wording.
                    # Held back, and used only if nothing stronger survives:
                    # showing a human two plausible bookings to choose from beats
                    # filing a review with a name, a venue and a city as
                    # unidentifiable.
                    # A common first name plus ONE issue phrase is not a match.
                    #
                    # "Tom Tom" against five unrelated guests - James Thomas
                    # Hamill, Tom Putzke, Tom Wammes, Tom Maksimov - each
                    # returned as a confident match on "no guide found", a
                    # phrase that appears in a great many tickets. Presenting
                    # those as matches is worse than presenting nothing: an
                    # associate has no way to see they are unrelated.
                    #
                    # Promotion out of the weak list needs the name plus TWO
                    # independent agreements: two distinct problem phrases, or
                    # a phrase and a date, or a phrase and the ticket's own
                    # visit date landing on a date the review named.
                    # KINDS of agreement, not phrases. Extraction returns 2-5
                    # ways of saying the same problem - "guided tour no guide",
                    # "tour guide not present", "guided tour not provided" -
                    # and counting each as its own corroboration made one
                    # complaint look like three. That promoted four bookings in
                    # Athens and Rome to matches for a review about France,
                    # because a Tom matched a Thomas and the ticket said the
                    # guide did not show up. The problem, a date, and the visit
                    # date are three different kinds of evidence; three
                    # paraphrases are one.
                    _corroborations = ((1 if hit_terms else 0)
                                       + (1 if hit_dates else 0)
                                       + (1 if _visit_hit else 0))
                    if issue_pass and _corroborations >= 2:
                        sig["matched_on"] = ["name"] + (
                            [f"issue:{hit_terms[0]}"] if hit_terms else []) + (
                            [f"date:{hit_dates[0]}"] if hit_dates else []) + (
                            [f"visit date {_visit_hit}"] if _visit_hit else [])
                        by_bid[bid] = sig
                        weak_by_bid.pop(bid, None)
                    elif bid not in weak_by_bid:
                        sig["matched_on"] = ["name"]
                        sig["weak"] = True
                        weak_by_bid[bid] = sig
                elif issue_pass and hit_terms and hit_dates and bid not in issue_by_bid:
                    # No name to agree on - an anonymous review, or a ticket
                    # raised under someone else's account. The problem and a date
                    # both lining up is not proof, but it is two independent
                    # agreements, which beats filing the review as untraceable.
                    sig["matched_on"] = [f"issue:{hit_terms[0]}", f"date:{hit_dates[0]}"]
                    issue_by_bid[bid] = sig

    # PASS 1 - the direct indicators. Unchanged, and authoritative.
    await _scan(queries, issue_pass=False)

    # PASS 2 - the problem the guest described. Runs ONLY when the direct
    # indicators produced nothing to show, so a matcher that is working is
    # never second-guessed by a text search.
    if not by_bid and issue_queries:
        log.info(f"[shortlist] direct indicators found nothing; trying "
                 f"{len(issue_queries)} issue-led query(s)")
        # Pass 1 may already have seen the right ticket and set it aside as a
        # weak name match. The dedupe would then skip it here and its issue
        # agreement would never be counted, so the ticket that answers the
        # review stays weak. Clearing the seen set lets pass 2 re-judge those
        # tickets WITH corroboration; by_bid is empty by definition here, so
        # nothing already decided can be disturbed.
        seen_tickets.clear()
        await _scan(issue_queries, issue_pass=True)

    # Preference order: everything agreed > problem and date agreed > only
    # the name agreed. Each step down is a weaker claim, so a stronger tier
    # is never diluted by a weaker one.
    if not by_bid and issue_by_bid:
        by_bid = issue_by_bid
        log.info(f"[shortlist] no name agreement; {len(by_bid)} ticket(s) matched "
                 f"on problem + date")

    out = list(by_bid.values())
    if not out and weak_by_bid:
        out = list(weak_by_bid.values())
        log.info(f"[shortlist] no ticket satisfied every indicator; falling back "
                 f"to {len(out)} name-matching ticket(s) as candidates")
    # In the direct pass every corroboration count is zero, so this is the
    # original "newest first". In the issue pass, a ticket agreeing on name,
    # problem and date outranks one that merely shares a name and is newer.
    # Rank by how much of the review each booking actually agrees with, not by
    # which ticket happens to be newest.
    #
    # The searches run separately - by name, by name+venue, by name+city, by
    # venue - so the same booking coming back from more than one of them is
    # independent agreement and counts for more than any single search's
    # opinion. _evidence() counts the distinct indicator kinds behind a
    # booking, from both the indicators the ticket itself satisfied and the
    # queries that surfaced it.
    out.sort(key=lambda s: (_evidence(s),
                            2 if s.get("visit_hit") else 0,
                            len(s.get("issue_hits") or []) + len(s.get("date_hits") or []),
                            s.get("created_at") or ""), reverse=True)

    # Pax as a narrowing step: only when it actually separates the candidates.
    # If some agree on pax and others do not, keep the agreeing ones. If none
    # agree, pax tells us nothing here and everything stays.
    if len(out) > 1 and indicators.get("pax"):
        exact = [s for s in out if "pax" in (s.get("matched_on") or [])]
        if exact and len(exact) < len(out):
            log.info(f"[shortlist] pax={indicators['pax']}: {len(out)} -> {len(exact)}")
            out = exact

    # Five, always. The cap used to apply only to name-only reviews, so a
    # review that named a venue could return thirteen cards - which is a list
    # to read, not a choice to make. Ranked first, so the five kept are the
    # five with the most of the review agreeing with them, not the five whose
    # tickets happen to be newest.
    if len(out) > limit_total:
        log.info(f"[shortlist] {len(out)} candidates -> best {limit_total} "
                 f"(evidence {[_evidence(s) for s in out[:limit_total]]})")
        out = out[:limit_total]

    log.info(f"[shortlist] {len(seen_tickets)} ticket(s) searched -> {len(out)} match "
             f"(indicators: name={bool(name)} venue={bool(venue)} city={bool(city)} "
             f"pax={indicators.get('pax')} issue_terms={len(issue_terms)} "
             f"dates={len(indicators.get('dates_mentioned') or [])})")
    return out


async def find_bids_by_requester_name(
    author_first: str,
    author_last: str | None,
    lookback_days: int | None = None,
    with_context: bool = False,
    full_name: str | None = None,
):
    """
    Search Zendesk for tickets by requester name.
    Default window: since the start of the current year (guests often review
    months after the visit). Pass lookback_days to override.
    Returns candidate booking numbers from subject + custom fields + body.

    with_context=True returns (bids, ticket_records) instead of just bids, where
    each record is {ticket_id, subject, body, text, bids}. The caller needs that
    text to decide WHICH of a requester's bookings a review refers to — the
    review itself is frequently a single line with no venue or date in it.

    Handles all name shapes:
    - Full name: quoted phrase search
    - Single name: unquoted token search
    - Non-Latin / special chars: URL-encoded, quoted
    - Empty / null: returns []
    """
    _empty = ([], []) if with_context else []
    if not is_live("zendesk") or not author_first:
        return _empty

    _z = _get_client()
    if _z is None:
        log.warning("[zendesk] find_bids_by_requester_name: no client available")
        return _empty

    def _as_query(name: str) -> str:
        if any(c in name for c in [" ", "-", "'", "."]) or not name.isascii():
            return f'"{name}"'
        return name

    # Every name token, not two of them.
    #
    # "Bhayani Salim F" was searched as "Bhayani F": the parser kept the first
    # and last tokens, so the middle name never reached Zendesk and a single
    # letter stood in for the surname. Where the caller passes the display
    # name, all its tokens are used — a middle name is often the most
    # distinctive thing about a guest, and it was the one part being discarded.
    from server.names import search_tokens as _stoks
    _all = _stoks(full_name) if full_name else []
    # Two or more, only. Dropping to a SINGLE token would undo a decision
    # already made above and paid for: requester:Bhayani matches every user
    # carrying that token, their tickets yield real booking ids that verify in
    # BigQuery, and strangers' bookings become indistinguishable candidates.
    # "A Bhayani" must not become "Bhayani" just because the first token is an
    # initial. This only ever ADDS tokens the old split threw away.
    if len(_all) >= 2:
        name_str = " ".join(_all)
    else:
        name_str = (f"{author_first} {author_last}".strip()
                    if author_last else author_first)
    # No created> clause by default. A date window silently drops the ticket
    # that actually matches — guests review long after the visit, and the window
    # cannot know how long. lookback_days is honoured only if explicitly passed.
    since = ((datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
             if lookback_days else None)

    # The FULL name only. A single-token fallback (requester:Fredrik) matches
    # every user carrying that token; their tickets yield real booking ids that
    # verify in BigQuery, so strangers' bookings become indistinguishable
    # candidates. Where the display name does not match the Zendesk requester
    # name, find_bids_by_text() covers it by searching the venue instead.
    # SEARCH BROAD, MATCH STRICT.
    #
    # The review only gives "Fredrik Olsen"; Zendesk holds "Fredrik Martin
    # Olsen". There is no way to know about the middle name before searching, so
    # an exact requester:"Fredrik Olsen" phrase match finds nothing. Equally, a
    # loose search alone drags in every other Fredrik. The answer is to cast a
    # wide net and then MATCH the results on the requester's real name.
    #
    # Every query below is a recall attempt; _name_matches is the precision
    # step, and nothing reaches the caller without passing it.
    queries = [
        f"type:ticket requester:{_as_query(name_str)}",   # exact, cheapest hit
        f"type:ticket {name_str}",                        # free text, both names
    ]
    if author_last:
        queries.append(f"type:ticket requester:{_as_query(author_last)}")
    if since:
        queries = [f"{q} created>{since}" for q in queries]

    _user_cache: dict = {}

    def _requester_name(tk) -> str:
        rid = getattr(tk, "requester_id", None)
        if rid not in _user_cache:
            try:
                _user_cache[rid] = getattr(_z.users(id=rid), "name", "") or ""
            except Exception:
                _user_cache[rid] = ""
        return _user_cache[rid]

    tickets, seen_ids = [], set()
    for query in queries:
        log.info(f"[zendesk] requester search: {query}")
        try:
            hits = await asyncio.get_running_loop().run_in_executor(
                None, lambda q=query: _search_with_retry(_z, q)
            )
        except Exception as e:
            log.warning(f"[zendesk] requester search failed: {e}")
            continue

        def _keep(ts):
            out = []
            for t in ts or []:
                tid = str(getattr(t, "id", "") or "")
                if tid in seen_ids:
                    continue
                rname = _requester_name(t)
                ns = _name_score(rname, author_first, author_last)
                # Only a total non-match is dropped — nothing in the name lines
                # up at all, so it is noise from a broad query, not a candidate.
                # Everything else is kept WITH its score; the caller ranks on it
                # together with venue, date and ticket-text confidence.
                if ns <= 0.0:
                    continue
                seen_ids.add(tid)
                setattr(t, "_name_score", ns)
                setattr(t, "_requester_name", rname)
                out.append(t)
            return out

        kept = await asyncio.get_running_loop().run_in_executor(
            None, lambda h=hits: _keep(h))
        if hits:
            log.info(f"[zendesk]   {len(hits)} hit(s) → {len(kept)} scored")
        tickets.extend(kept)

    # Strongest name confidence first, so the per-ticket cap keeps the best.
    tickets.sort(key=lambda t: getattr(t, "_name_score", 0.0), reverse=True)
    if not tickets:
        log.info(f"[zendesk] nothing resembling '{name_str}'")

    # Harvest candidate booking numbers from subject + custom fields + BODY.
    # A ticket's own id shares the same number space as BIDs — exclude it;
    # every remaining number is verified against BigQuery downstream, which is
    # what actually distinguishes a booking id from a ticket id / other digits.
    #
    # The subject and body TEXT is retained alongside each BID, not just the
    # digits scraped out of it. A one-line review ("it is a scam") carries no
    # venue or date, but the ticket that BID came from describes the actual
    # problem — that text is what tells us which of a requester's bookings the
    # review is about.
    bids = []
    ticket_records = []
    # Zendesk ticket ids and Headout booking ids share the same numeric space
    # (both commonly 8 digits), so a ticket body referencing ANOTHER ticket
    # ("duplicate of 33979875") would otherwise be harvested as a booking id.
    # Excluding only the containing ticket's own id is not enough — exclude
    # every ticket id seen in this search.
    all_ticket_ids = {str(getattr(t, "id", "") or "") for t in tickets}
    for t in tickets[:15]:
        own_id = str(getattr(t, "id", "") or "")
        found = []
        subject = (getattr(t, "subject", "") or "")
        body = (getattr(t, "description", "") or "")
        found += re.findall(r"\b\d{7,12}\b", subject)
        for cf in getattr(t, "custom_fields", []) or []:
            val = cf.get("value") if isinstance(cf, dict) else None
            if val and re.fullmatch(r"\d{7,12}", str(val).strip()):
                found.append(str(val).strip())
        found += re.findall(r"\b\d{7,12}\b", body[:4000])
        # Every source, in trust order — the custom field is the most reliable
        # but is frequently left empty, so subject, body and side conversations
        # all contribute. Union, not either/or: a ticket can carry the booking
        # id in one place and not another.
        sig = ticket_signals(t)
        field_bid = sig["booking_id"] or None
        # The itinerary / payment id is 8 digits and sits in its own field, so
        # scraping happily mistakes it for a booking id. Exclude it explicitly.
        _not_bids = set(all_ticket_ids)
        if sig.get("itinerary_id"):
            _not_bids.add(sig["itinerary_id"])
        scraped   = [n for n in found if n not in _not_bids]

        # Side conversations are NOT fetched here. During candidate search this
        # would be 15 tickets x (1 + N) sequential HTTP calls for booking ids we
        # mostly discard, which is a large part of why re-runs were slow. The
        # booking field, subject and body already cover the search; side
        # conversations are read once the booking is known, when building the
        # timeline, where their content is actually used.
        sc_list, sc_bids = [], []

        # The booking-id field is definitive. When a ticket has it, scraping the
        # same ticket only adds noise -- phone numbers and payment refs are also
        # 7-12 digits, and each one costs a BigQuery verify round trip. Scraping
        # is the fallback for tickets where the field is empty, not an addition.
        t_bids = ([field_bid] if field_bid
                  else list(dict.fromkeys(scraped + sc_bids)))
        if t_bids:
            log.info(f"[zendesk] ZD-{own_id}: bids={t_bids} "
                     f"(field={field_bid or '-'}, scraped={scraped or '-'}, "
                     f"side_conv={sc_bids or '-'})")
        bids += t_bids
        ticket_records.append({
            "ticket_id":      own_id,
            "subject":        subject,
            "body":           body[:4000],
            "text":           f"{subject}\n{body[:4000]}".strip(),
            "bids":           list(dict.fromkeys(t_bids)),
            "bid_source":     ("zendesk_field" if field_bid
                               else "side_conversation" if sc_bids else "scraped"),
            "side_conversations": sc_list,
            "signals":        sig,
            "requester_name": getattr(t, "_requester_name", ""),
            "name_score":     round(getattr(t, "_name_score", 0.0), 2),
        })

    deduped = list(dict.fromkeys(bids))[:25]
    log.info(f"[zendesk] requester search for '{name_str}': {len(tickets)} tickets → "
             f"{len(deduped)} candidate numbers (subject+fields+body)")
    if with_context:
        return deduped, ticket_records
    return deduped


async def find_bids_by_text(
    terms: list[str],
    since: str | None = None,
    requester_hint: str | None = None,
) -> tuple[list[str], list[dict]]:
    """
    Search Zendesk by free text — the venue/experience the guest named — rather
    than by requester name.

    Why this works: Zendesk free-text search matches against the ticket SUBJECT
    and its COMMENTS (per Zendesk's ticket search reference), so a venue the
    guest mentioned in the review will match the ticket where they raised it.
    Terms are double-quoted, which Zendesk treats as an exact-phrase match.
    Zendesk respects word boundaries and does NOT do partial-word matching, so
    terms are passed verbatim rather than truncated or stemmed.

    Returns (bids, ticket_records) in the same shape as
    find_bids_by_requester_name(with_context=True).

    Result cap: Zendesk's Search API returns at most 1,000 results (100/page);
    we only ever consume the first page, which is far more than a single guest's
    ticket history needs.
    """
    if not is_live("zendesk"):
        return [], []
    terms = [str(t).strip() for t in (terms or []) if str(t).strip()]
    if not terms:
        return [], []

    _z = _get_client()
    if _z is None:
        log.warning("[zendesk] find_bids_by_text: no client available")
        return [], []

    # No created> clause unless one is explicitly supplied — see the note in
    # find_bids_by_requester_name. Fredrik's salt-mine ticket predates any
    # sensible window.
    all_bids, all_records, seen_tickets = [], [], set()
    search_terms = list(terms[:3])
    # Combined name + venue, the equivalent of typing "Fredrik Olsen, salt mine"
    # into Zendesk search: free text over both indicators at once. Run FIRST so
    # its hits are the ones that survive the per-term cap.
    if requester_hint:
        search_terms.insert(0, f"{requester_hint} {terms[0]}")

    for ti, term in enumerate(search_terms):
        combined = bool(requester_hint) and ti == 0
        # Combined search goes unquoted so the tokens can match wherever they
        # appear; a single venue goes quoted for exact-phrase precision.
        clauses = [f"type:ticket {term}" if combined else f'type:ticket "{term}"']
        if since:
            clauses.append(f"created>{since}")
        query = " ".join(clauses)
        log.info(f"[zendesk] text search: {query}")
        try:
            tickets = await asyncio.get_running_loop().run_in_executor(
                None, lambda q=query: _search_with_retry(_z, q)
            )
        except Exception as e:
            log.warning(f"[zendesk] text search failed for {term!r}: {e}")
            continue

        _batch_ids = {str(getattr(t, "id", "") or "") for t in (tickets or [])}
        for t in (tickets or [])[:15]:
            own_id = str(getattr(t, "id", "") or "")
            if own_id in seen_tickets:
                continue
            seen_tickets.add(own_id)
            subject = (getattr(t, "subject", "") or "")
            body    = (getattr(t, "description", "") or "")
            found   = re.findall(r"\b\d{7,12}\b", subject)
            for cf in getattr(t, "custom_fields", []) or []:
                val = cf.get("value") if isinstance(cf, dict) else None
                if val and re.fullmatch(r"\d{7,12}", str(val).strip()):
                    found.append(str(val).strip())
            found += re.findall(r"\b\d{7,12}\b", body[:4000])
            # Same union of sources as the requester search.
            field_bid = booking_id_from_ticket(t)
            scraped = [n for n in found
                       if n not in _batch_ids and n not in seen_tickets]
            sc_bids = []   # see note above — not fetched during search
            t_bids = ([field_bid] if field_bid
                      else list(dict.fromkeys(scraped + sc_bids)))
            all_bids += t_bids
            all_records.append({
                "ticket_id": own_id,
                "subject":   subject,
                "body":      body[:4000],
                "text":      f"{subject}\n{body[:4000]}".strip(),
                "bids":      list(dict.fromkeys(t_bids)),
                "matched_term": term,
            })

    deduped = list(dict.fromkeys(all_bids))[:25]
    log.info(f"[zendesk] text search {terms[:3]}: {len(seen_tickets)} tickets → "
             f"{len(deduped)} candidate numbers")
    return deduped, all_records


def _clip(text: str, n: int) -> str:
    """
    Backstop against runaway model output. NOT a length policy.

    It was a length policy, at 110 characters for summaries, and it cut real
    sentences mid-word: "agent cited non-cancellable p...". The reader is then
    guessing at the half that is missing, which costs more than a long line
    ever did — and the justification (a narrow column) had stopped being true;
    .tl-summary wraps.

    So the caps are now set where only genuinely runaway output reaches them,
    and three things changed about how a cut is done:

      * it breaks at a word boundary, because a word cut in half is unreadable
        in a way a sentence cut between words is not;
      * it says the cut is OURS. A bare "…" is exactly how a model trails off,
        so a truncation was indistinguishable from the model's own phrasing —
        the reader could not tell whether there was more text or not;
      * a label still gets a short cap, because a label IS a header and a
        forty-word one breaks the row it sits in.
    """
    text = str(text or "").strip()
    if len(text) <= n:
        return text
    cut = text[:n]
    space = cut.rfind(" ")
    if space > n * 0.6:            # only if it does not gut the string
        cut = cut[:space]
    return cut.rstrip(" ,;:-") + f" […cut at {n} chars]"


# 120, not the original 60. A label is a DESCRIPTIVE line now — "Duplicate-
# booking cancellation request sent to the supply partner" — and 60 was set
# when labels were category words like "Tickets sent". Every descriptive label
# overflowed it and the reader got "[…cut at 60 chars]" inside a header.
LABEL_CAP   = 120
# Not a house style: a backstop against a model pasting a whole transcript.
SUMMARY_CAP = 600


def clip_shaped_text(ev: dict) -> dict:
    """{label, summary} for one shaped event, each within its own cap.

    DRIVABLE ON PURPOSE. The caps used to be literals inside an async function
    that calls Claude, so the only thing a test could reach was `_clip` — which
    takes the cap as an argument and therefore passes for any value the caller
    might be using. A mutation dropping the label cap back to 60 survived
    exactly that test.
    """
    e = ev if isinstance(ev, dict) else {}
    return {"label":   _clip(e.get("label", ""), LABEL_CAP),
            "summary": _clip(e.get("summary", ""), SUMMARY_CAP)}


_MONEY_IN_NOTE = re.compile(
    r"(?:[A-Z]{3}\s*\d|[£€$₹]\s*\d)", re.I)


def select_internal_notes(events: list) -> list:
    """Keep the internal notes that say something; collapse repeated pings.

    WIRED HERE BECAUSE IT WAS WIRED NOWHERE. `server/ticket_notes.py` has held
    `note_disposition`, `collapse_repeats` and `ping_summary` with tests since
    they were written, and no code path called any of them — so the rule
    "drop ticket housekeeping, keep booking facts, collapse repeated system
    pings" existed only as a passing test suite. Every internal note reached
    the card and the client hid the lot behind a toggle.

    THREE OUTCOMES, and they must not look alike on the row:
      keep  — a booking fact. It stops being internal: `is_internal` is
              cleared so it renders inline rather than behind the toggle,
              which is the whole point of keeping it.
      drop  — ticket administration. Stays marked internal and stays behind
              the toggle. NOT deleted: the toggle already says how many it
              hid, and an event nobody can reach is one nobody can check.
      judge — no certain signal, so it is KEPT. Unsure means show it; hiding
              a booking fact is the expensive direction and hiding it
              silently is worse.

    A collapsed run becomes ONE row carrying its count and span, because with
    four identical automated messages the repetition is the signal and the
    individual lines are not.
    """
    from server.ticket_notes import collapse_repeats, note_disposition, ping_summary

    rows = [e for e in (events or []) if isinstance(e, dict)]
    internal = [e for e in rows if e.get("is_internal")]
    if not internal:
        return rows

    # NEVER COLLAPSE A ROW WHOSE DIGITS ARE THE POINT. `_ping_key` normalises
    # digits to "#" — right for an automated ping, where a ticket number or a
    # timestamp is boilerplate, and wrong where the number IS the fact:
    # "Refund of GBP 5.12 processed" and "Refund of GBP 9.99 processed" share
    # a key and would render as one repeated row, losing a second refund.
    #
    # Exempting every booking fact was tried and is too wide — three identical
    # "Reschedule cannot be actioned" notes are a booking fact AND a repeated
    # ping, and those are exactly what this is meant to collapse. Money is the
    # narrow case where the digits carry the meaning.
    facts = {id(e) for e in internal
             if _MONEY_IN_NOTE.search(e.get("summary") or e.get("raw_body") or "")}
    kept_pings, collapsed = collapse_repeats(
        [e for e in internal if id(e) not in facts])
    dropped_ids = ({id(e) for e in internal} - facts
                   - {id(e) for e in kept_pings})

    out, ids = [], {}
    for e in rows:
        if e.get("is_internal") and id(e) in dropped_ids:
            continue                       # folded into its run's first row
        original = id(e)
        if e.get("is_internal"):
            verdict, why = note_disposition(
                e.get("summary") or e.get("raw_body") or "")
            if verdict in ("keep", "judge"):
                # PROMOTED, AND IT SAYS SO. Clearing `is_internal` is what
                # moves a booking fact out from behind the toggle and onto the
                # timeline — the whole point of keeping it. But
                # `is_conversation` rejects machinery USING that same flag, so
                # clearing it silently turned an agent's NAR disposition into
                # a "Customer / CE interaction" the guest never took part in.
                # The Slack post reported two contacts on a booking with one.
                #
                # On `keep` this used to write internal_reason="", leaving a
                # promoted note indistinguishable from a row that was never
                # internal — so nothing downstream COULD tell them apart even
                # if it wanted to. The marker is its own key rather than
                # words in internal_reason, which the card renders.
                e = dict(e, is_internal=False, promoted_from_internal=True,
                         internal_reason=("" if verdict == "keep"
                                          else f"kept because {why}"))
        ids[len(out)] = original
        out.append(e)

    # The first row of each collapsed run carries the whole run. Keyed on the
    # ORIGINAL object identity, which is what `collapse_repeats` returns — the
    # loop above rebuilt some rows with dict(), so the lookup has to happen
    # against the identities recorded before that.
    runs = {id(c["first"]): c for c in collapsed}
    out = [dict(e, summary=ping_summary(runs[k]), is_internal=False,
                promoted_from_internal=True,
                internal_reason=f"{runs[k]['count']} identical pings collapsed")
           if (k := ids.get(i)) in runs else e
           for i, e in enumerate(out)]
    return out


def _fallback_shape(raw_events: list) -> list:
    """
    Mechanical fallback when Claude shaping fails.
    Produces {time, time_sort, thread, actor, label, summary, ticket_id,
    is_internal, internal_reason} without [ZD-###] prefixes, with HTML-stripped
    one-line summaries. Every field except label/summary is copied from the raw
    event, so provenance survives a Claude outage unchanged.
    """
    actor_labels = {
        "guest":  "Guest contacted support",
        "co":     "CE responded",
        "sp":     "SP responded",
        "ai":     "Bot auto-reply",
        "system": "System event",
    }
    shaped = []
    for ev in raw_events:
        actor = ev.get("actor", "system")
        label = actor_labels.get(actor, "Event")
        raw_body = ev.get("raw_body", "")
        if raw_body.startswith("[") and "comments elided" in raw_body:
            label = raw_body
            summary = ""
        else:
            summary = _clean_summary(raw_body)
        shaped.append({
            "time":    ev.get("time", ""),
            "time_sort": ev.get("time_sort", ""),
            "thread":  ev.get("thread", "email"),
            "actor":   actor,
            "label":   label,
            "summary": summary,
            # Carried from the raw event. Without the ticket id a timeline entry
            # cannot be traced back to the Zendesk ticket it came from, so an
            # associate reading the RCA has no way to check any of it.
            "ticket_id":       ev.get("ticket_id", ""),
            "is_internal":     bool(ev.get("is_internal")),
            "internal_reason": ev.get("internal_reason", ""),
        })
    return shaped


def _strip_json_line_comments(text: str) -> str:
    """
    Remove // comments from Claude's JSON without touching string contents.

    This was a regex - `(?<!["\\w])//[^\\n]*` - and the character before the //
    in a URL is a colon, which that lookbehind does not exclude. So a summary
    quoting "https://vendor.example.com/orders/551" had everything from the //
    to the end of the line deleted, which took the closing quote and brace with
    it: the JSON then failed to parse, _safe_parse_events returned [], and the
    whole Claude-shaped timeline was silently replaced by the mechanical
    fallback. An agent reply or an SP side conversation quoting a booking or
    vendor URL is exactly the event an RCA turns on.

    No regex can do this, because whether // starts a comment depends on
    whether it is inside a string, which is not a local property of the text.
    So the string state is tracked instead: inside a JSON string everything is
    kept verbatim, outside one a // runs to the end of the line.
    """
    out, i, n = [], 0, len(text)
    in_str = esc = False
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
            out.append(ch)
        elif ch == "/" and i + 1 < n and text[i + 1] == "/":
            nl = text.find("\n", i)
            i = n if nl == -1 else nl
            continue
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _safe_parse_events(text: str) -> list:
    """
    Parse Claude's JSON response into a list of shaped event dicts.
    Strips markdown fences and JSON comments before parsing.
    Returns empty list on failure.
    """
    import json as _json
    cleaned = text.replace("```json", "").replace("```", "").strip()
    cleaned = _strip_json_line_comments(cleaned)
    try:
        parsed = _json.loads(cleaned)
        if isinstance(parsed, list):
            return parsed
    except Exception:
        pass
    # Try extracting array from response
    m = re.search(r'\[.*\]', cleaned, re.S)
    if m:
        try:
            parsed = _json.loads(m.group(0))
            if isinstance(parsed, list):
                return parsed
        except Exception:
            pass
    # A TRUNCATED ARRAY, SALVAGED. Both attempts above need a CLOSED `]`, so a
    # response cut mid-array returned nothing at all and the caller fell back
    # to raw ticket bodies for EVERY event — losing forty shaped entries
    # because the forty-first was incomplete.
    #
    # The RCA path already does this ("closing the open string and braces so a
    # long RCA degrades to a partial one instead of vanishing"); the timeline
    # path never learned it. Whole objects are kept, the incomplete tail is
    # dropped, and the caller is told how many so a short timeline cannot pass
    # for a complete one.
    salvaged = _salvage_objects(cleaned)
    if salvaged:
        log.warning("[zendesk] shaping response was truncated — salvaged %d "
                    "complete event(s); the tail was incomplete and dropped",
                    len(salvaged))
    return salvaged


def _salvage_objects(text: str) -> list:
    """Every complete top-level {...} in a truncated JSON array.

    Brace-counting rather than a regex, because a summary legitimately
    contains braces and quotes; the scan tracks string state so a brace inside
    a quoted value does not close an object.
    """
    import json as _json
    out, depth, start, in_str, esc = [], 0, None, False, False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    obj = _json.loads(text[start:i + 1])
                    if isinstance(obj, dict):
                        out.append(obj)
                except Exception:
                    pass
                start = None
            elif depth < 0:
                depth = 0
    return out


async def _shape_via_claude(
    raw_events: list,
    booking: dict,
    review_body: str,
    review_pub_date: str,
) -> list:
    """
    One Claude call that batch-shapes raw events into clean timeline entries.
    Filters keep=false events. Returns list of {time, time_sort, thread, actor,
    label, summary, ticket_id, is_internal, internal_reason}.
    """
    from server import prompts as _prompts
    from server.services import claude as _claude

    # THE PUBLICATION DATE REACHES THE MODEL IN IST, like every other time.
    #
    # It arrives as UTC — `review.received_at.strftime("%Y-%m-%d %H:%M")` —
    # and every raw event in the same prompt is an IST display string. The
    # model copied the digits and labelled them IST, so a review published at
    # 12:06 UTC rendered as "02 Aug 12:06 IST" instead of 17:36, and sorted
    # BEFORE the escalation and the guest chat that preceded it.
    #
    # One timezone in, one out. `_normalize_time` already owns the conversion;
    # it was simply never applied on the way in.
    _pub_disp, _ = _normalize_time(review_pub_date or "")
    prompt = _prompts.zendesk_timeline_shape_prompt(
        booking, review_body, _pub_disp or review_pub_date, raw_events)
    raw_text = await _claude.shape_timeline_events(prompt)

    shaped = _safe_parse_events(raw_text)
    if not shaped:
        # THE FALLBACK MUST NOT PASS FOR A TIMELINE. `_fallback_shape` renders
        # RAW BODIES with category labels — "System event" over
        # "![Logo](https://cdn-imgix-open...)" — and it rendered them under the
        # same heading, in the same rows, as a shaped timeline. A reader saw a
        # redesign; what had happened was that the shaping call failed.
        #
        # It failed because the payload grew: every private comment, HTML mail
        # bodies and all, went into one prompt. That is fixed at the fetch, but
        # the fallback being INVISIBLE is the reason it took a screenshot to
        # find out, so the rows now say what they are.
        log.warning("[zendesk] Claude returned unparseable shaping response — "
                    "falling back to RAW bodies for %d event(s)", len(raw_events))
        rows = _fallback_shape(raw_events)
        for r in rows:
            r["shaping_failed"] = True
        return rows

    # Claude returns prose, not provenance: it is asked to echo each raw event's
    # timestamp and to name the raw indices it collapsed, and everything else
    # about where an entry came from is lost unless it is put back here. The raw
    # event is the authority for time, ticket id and machinery classification -
    # idx_range is what maps a shaped entry back to it.
    by_idx = {e.get("idx"): e for e in raw_events if isinstance(e, dict)}
    # Bookends carry no idx_range, and Claude occasionally omits one. Their time
    # string is still one Claude copied from a raw event, so the display string
    # is used to recover that event's sortable value rather than giving up on it.
    by_display = {e.get("time"): e.get("time_sort", "")
                  for e in by_idx.values() if e.get("time")}

    kept = []
    _dropped_by_model = 0
    for ev in shaped:
        if not ev.get("keep", True):
            # COUNTED. The prompt says "KEEP EVERY EVENT — keep: false only
            # for an event with no readable content at all", and a model that
            # drops more than that leaves a timeline that simply looks
            # shorter. A payment reminder and a charge confirmation
            # disappearing takes the payment story with them, and nothing on
            # the card said the count had moved.
            _dropped_by_model += 1
            continue
        srcs = sorted((by_idx[i] for i in (ev.get("idx_range") or []) if i in by_idx),
                      key=lambda s: s.get("idx", 0))
        if srcs:
            time_disp = srcs[0].get("time", "")
            time_sort = srcs[0].get("time_sort", "")
        else:
            # The review-posted bookend is handed the publication date, which
            # arrives as a bare ISO "2026-07-22" and used to be stored verbatim
            # alongside "22 Jul 14:03 IST" comments - two formats in one list.
            time_disp, time_sort = _normalize_time(ev.get("time", ""))
            time_sort = time_sort or by_display.get(time_disp, "")
            # STAMPED FROM THE RECORD, not from what the model echoed back.
            #
            # A bookend has no idx_range, so there is no raw event to copy a
            # time from, and the model returned "unknown" for both. The client
            # sorts on the displayed time and sinks a row it cannot read to the
            # END — so "Booking created" rendered at the BOTTOM of the
            # timeline, under the review it precedes by two weeks. On screen
            # that is indistinguishable from an event that happened last, which
            # is why it read as the chronology being broken rather than as two
            # rows missing a value.
            #
            # Both dates are already in hand. A bookend is first or last BY
            # DEFINITION; asking a model to remember which is a question that
            # did not need asking.
            # ONLY WHEN THE MODEL GAVE NOTHING READABLE. "20 Jul 09:00 IST"
            # has no year, so `_normalize_time` returns no sortable value for
            # it — and stamping on an empty time_sort alone OVERWROTE a date
            # the model had got right. A display string carrying digits is a
            # date; "unknown" is not.
            if not time_sort and not re.search(r"\d", time_disp or ""):
                _bk = str(ev.get("thread") or "").strip().lower()
                _src = ""
                if _bk == "review":
                    _src = review_pub_date or ""   # _normalize_time below
                elif _bk == "booking":
                    _src = ((booking or {}).get("creationDate")
                            or (booking or {}).get("bookedOn")
                            or (booking or {}).get("date_of_booking") or "")
                if _src:
                    _d, _s = _normalize_time(_src)
                    if _s:
                        time_disp, time_sort = _d, _s
                    log.info("[zendesk] bookend %r had no readable time; "
                             "stamped from the record as %r", _bk, time_disp)

        thread = ev.get("thread", "email")
        # Zendesk's own channel beats Claude's reading of the body, but only
        # where Claude fell back to "email" - that is the value it emits when it
        # had nothing to go on, and it is how chat turns lost their thread.
        # "booking" and "review" are Claude's editorial calls on the bookends
        # and are left alone.
        if thread == "email" and srcs and srcs[0].get("thread"):
            thread = srcs[0]["thread"]
        # Nothing between here and the renderer checks these two values, and the
        # renderer turns both into CSS class names. A model-invented "whatsapp"
        # or "customer" is not a pill, it is an unstyled word, so anything
        # outside the agreed vocabulary falls back to what Zendesk said.
        if thread not in _THREADS:
            log.info(f"[zendesk] unknown shaped thread {thread!r} -> raw channel")
            thread = (srcs[0].get("thread") if srcs else "email") or "email"
        # The booking-created bookend is handed an already-formatted 'DD Mon
        # HH:MM IST' string, which carries no year and so cannot yield a
        # sortable value on its own. The booking metadata that string was made
        # from does, and it is right here.
        if not time_sort and thread == "booking":
            time_sort = _to_iso((booking or {}).get("date_of_booking")
                                or (booking or {}).get("creationDate") or "")
        # THE REVIEW BOOKEND NEEDS THE SAME RESCUE, and had none. The stamp
        # above only fires when the display string has NO digits — a guard
        # added so a good model time is never overwritten. "02 Aug 17:36 IST"
        # has digits and no year, so it passes that guard and yields no
        # sortable value: the row rendered with a perfectly readable time and
        # NO SORT KEY, landing wherever the list happened to put it.
        #
        # On a real card it landed correctly, which is the dangerous version —
        # a placement nothing in the record supports, that happens to look
        # right. The publication date is in hand, exactly as the booking date
        # is for the row above.
        if not time_sort and thread == "review":
            time_sort = _to_iso(review_pub_date or "")
        actor = ev.get("actor", "system")
        if actor not in _ACTORS:
            log.info(f"[zendesk] unknown shaped actor {actor!r} -> raw actor")
            actor = (srcs[0].get("actor") if srcs else "system") or "system"

        # Machinery only when EVERY raw event behind this entry was machinery:
        # a collapsed entry that mixes a real guest message with a system row is
        # still the guest's story.
        internal = [s for s in srcs if s.get("is_internal")]
        is_internal = bool(srcs) and len(internal) == len(srcs)

        kept.append({
            "time":    time_disp,
            "time_sort": time_sort,
            "thread":  thread,
            "actor":   actor,
            # 120, not 60. A label is a DESCRIPTIVE line now — "Booking
            # intimation sent to the supply partner", "Duplicate-booking
            # cancellation request sent to the supply partner" — and the old
            # cap was set when labels were category words like "Tickets sent".
            # Every descriptive label overflowed it, so the reader got
            # "[…cut at 60 chars]" rendered inside a header. The cap stays
            # because a forty-word label still breaks the row; it is now set
            # where only a runaway one reaches it.
            #
            # The summary is not a header, it is the content, and 110 was
            # cutting real sentences in half — "agent cited non-cancellable
            # p...". 600 is a backstop against a model that pastes a whole
            # transcript, not a house style; the prompt asks for short
            # telegraphic phrases and a well-behaved summary never comes near
            # it. A summary that reads badly should be fixed in the prompt,
            # where the model can write a shorter COMPLETE one, not with
            # scissors here, which only ever produces an incomplete one.
            **clip_shaped_text(ev),
            "ticket_id": next((s.get("ticket_id") for s in srcs if s.get("ticket_id")), ""),
            # THE ORIGINAL TEXT, carried for `collapse_repeats` to key on.
            #
            # It keys on `raw_body or summary`, and a shaped row had no
            # raw_body — so it fell back to the model's SUMMARY. The model
            # writes each repeat slightly differently ("SP confirmation
            # pending", "SP confirmation pending, 2 further"), so five
            # identical automated pings produced five different keys and none
            # of them collapsed. Five rows of "Reschedule blocked" on one
            # card, from one automated line firing five times.
            #
            # The raw body is identical every time by construction — it is the
            # same automation writing the same sentence. Keying on what the
            # SYSTEM wrote instead of on what the model wrote about it is the
            # difference between a deterministic collapse and one that depends
            # on the model phrasing two rows the same way twice.
            #
            # Not rendered: the card reads `summary`. This is a join key.
            "raw_body": (srcs[0].get("raw_body", "") if srcs else ""),
            "is_internal":     is_internal,
            "internal_reason": internal[0].get("internal_reason", "") if is_internal else "",
        })
    out = select_internal_notes(kept)
    out, _unsorted = sort_by_time_sort(out)
    # RAW IN, SHAPED OUT. Collapsing is legitimate — the prompt asks for it —
    # but "10 events became 8" is a judgement the model made and the reader
    # cannot see. Stamped on the first row so the pipeline can put it on the
    # trail; a shorter timeline and a complete one look identical otherwise.
    if out and len(raw_events) != len(out):
        out[0] = dict(out[0], _shape_counts={
            "raw": len(raw_events), "shown": len(out),
            "dropped_by_model": _dropped_by_model})
    return out


# The guest's real name for a booking id, and where it came from.
#
# THE RANKING PATH ALREADY DOES THIS AND THE TIER-1 GATE DOES NOT.
# `pipeline._score`'s `_name_pts` takes max(zendesk_ticket_name, bq_name,
# ticket_signals_name) and guards the BigQuery hash before scoring it. The
# Tier-1 gate — the branch that decides whether a booking id the guest quoted
# in their own review is trusted — scores the reviewer straight against
# `bq_row["primary_guest_name"]`, which is a PII hash on a large share of rows.
# Hash, internal desk label, blank and a genuinely different person all score
# 0.0, so the gate reads "we could not compare" as "they disagree" and sends a
# correctly-quoted booking id to manual confirmation.
#
# This is the missing source. Best-effort: it returns ("", reason) rather than
# raising, and the reason is written for the trail — "Zendesk had no readable
# guest name" and "Zendesk was never asked" must not read the same.
GUEST_NAME_UNAVAILABLE = {
    "not_live":   "Zendesk is not connected on this server, so the booking's "
                  "guest name could not be checked there",
    "no_client":  "the Zendesk client could not be built, so the booking's "
                  "guest name could not be checked there",
    "no_tickets": "no Zendesk ticket references this booking id, so there is "
                  "no ticket-side guest name to compare",
    "no_name":    "the Zendesk ticket for this booking carries no readable "
                  "guest name",
    "failed":     "the Zendesk lookup for this booking's guest name failed",
}


async def guest_name_for_bid(bid) -> tuple[str, str]:
    """(name, reason). `name` is "" whenever nothing usable was found.

    A hash or an internal desk label on the Zendesk side is rejected exactly
    as it is on the warehouse side — accepting one here would move the defect
    one layer out rather than fix it.
    """
    from server.names import is_internal_booking_name
    bid = str(bid or "").strip()
    if not bid:
        return "", GUEST_NAME_UNAVAILABLE["no_tickets"]
    if not is_live("zendesk"):
        return "", GUEST_NAME_UNAVAILABLE["not_live"]
    _z = _get_client()
    if _z is None:
        return "", GUEST_NAME_UNAVAILABLE["no_client"]
    try:
        loop = asyncio.get_running_loop()
        tickets = await loop.run_in_executor(
            None, lambda: _search_with_retry(_z, f"type:ticket fieldvalue:{bid}"))
        if not tickets:
            tickets = await loop.run_in_executor(
                None, lambda: _search_with_retry(_z, f'type:ticket "{bid}"'))
        if not tickets:
            return "", GUEST_NAME_UNAVAILABLE["no_tickets"]

        def _usable(v):
            v = str(v or "").strip()
            if not v or is_internal_booking_name(v):
                return ""
            # The same hash test the warehouse side uses. A base64 blob is not
            # a name whichever system it came out of.
            from server.services.bigquery import is_hashed_name
            return "" if is_hashed_name(v) else v

        # The ticket's own guest-name field first — it is about the BOOKING.
        for t in tickets[:5]:
            got = _usable(get_custom_field(t, _F_GUEST_NAME))
            if got:
                return got, "the Zendesk ticket's guest-name field"
        # Then the requester, which is whoever owns the account: an assistant,
        # a parent, a colleague. Weaker, and still far better than a hash.
        for t in tickets[:5]:
            rid = getattr(t, "requester_id", None)
            if not rid:
                continue
            try:
                got = _usable(getattr(
                    await loop.run_in_executor(None, lambda i=rid: _z.users(id=i)),
                    "name", ""))
            except Exception:
                continue
            if got:
                return got, "the Zendesk requester on the ticket for this booking"
        return "", GUEST_NAME_UNAVAILABLE["no_name"]
    except Exception as e:
        log.warning(f"[zendesk] guest_name_for_bid({bid}) failed: {e}")
        return "", f"{GUEST_NAME_UNAVAILABLE['failed']} ({type(e).__name__})"
