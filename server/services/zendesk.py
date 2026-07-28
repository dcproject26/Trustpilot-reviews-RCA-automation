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
   events {idx, time, thread, actor, ticket_id, raw_body}.
7. get_timeline passes raw events to Claude (_shape_via_claude) which returns
   clean {time, thread, actor, label, summary} events with bookend injection,
   noise-drop, and macro-flood collapsing. On failure, _fallback_shape is used.
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

_ZD_SEM = asyncio.Semaphore(10)

IST = timezone(timedelta(hours=5, minutes=30))

# Search-path hit counters (reported in delta verification)
SEARCH_COUNTERS = {"fieldvalue": 0, "free_text": 0}

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
        parts = ", ".join(
            (p.get("name") or p.get("email") or "")
            for p in (sc.get("participants") or []) if isinstance(p, dict))
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


def _map_channel(via_channel: str) -> str:
    if via_channel == "email":
        return "email"
    if via_channel in ("chat", "native_messaging", "web_widget"):
        return "chat"
    if via_channel == "voice":
        return "call"
    return "email"


def _brand_matches(ticket, brand_env: str) -> bool:
    if not brand_env:
        return False
    bid = getattr(ticket, "brand_id", None)
    return str(bid) == str(brand_env).strip()


def _detect_actor(comment_author_id, ticket, author_role: str,
                  is_sp_ticket: bool, ticket_tags: list) -> str:
    if any(t in ticket_tags for t in ZENDESK_BOT_TAGS):
        return "ai"
    if comment_author_id == getattr(ticket, "requester_id", None):
        return "guest"
    if is_sp_ticket:
        return "sp"
    if author_role in ("agent", "admin"):
        return "co"
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
        {time, thread, actor, label, summary}
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
        from datetime import date
        today = date.today().isoformat()
        synth_tl = [
            {
                "time":   f"{today} 09:00",
                "thread": "email",
                "actor":  "guest",
                "label":  "Guest contacted support",
                "summary": "[Mock] Guest emailed support about their experience.",
            },
            {
                "time":   f"{today} 10:30",
                "thread": "email",
                "actor":  "co",
                "label":  "CE responded",
                "summary": "[Mock] CE acknowledged the guest's concern and reviewed the booking.",
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
    # ── Search: fieldvalue first, free-text fallback ─────────────────────────
    tickets = _search_with_retry(_z, f"type:ticket fieldvalue:{booking_id}")
    if tickets:
        SEARCH_COUNTERS["fieldvalue"] += 1
        log.info(f"[zendesk] fieldvalue: {len(tickets)} tickets for BID {booking_id}")
    else:
        tickets = _search_with_retry(_z, f'type:ticket "{booking_id}"')
        if tickets:
            SEARCH_COUNTERS["free_text"] += 1
            log.info(f"[zendesk] free-text: {len(tickets)} tickets for BID {booking_id}")
        else:
            log.info(f"[zendesk] no tickets for BID {booking_id} (both search paths)")
            return [], {}, {"ticket_ids": [], "timeline_raw": [], "zendesk_requester_name": ""}

    # ── Extract booking fields from custom fields + tags ─────────────────────
    extracted = {}
    ticket_mail_seen = False
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
        role = ""
        try:
            u = _z.users(id=author_id)
            role = getattr(u, "role", "") or ""
        except Exception:
            pass
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
            # Internal notes are Headout talking to itself — agent macros,
            # fulfilment bookkeeping, system bookkeeping. They are not part of
            # what happened to the guest, and summarising them produced timeline
            # entries like "Fulfilment attempted across multiple tries via the
            # vendor portal" that read as guest-facing events but are not.
            # Zendesk marks them public=False. SP side conversations are handled
            # separately and are NOT affected by this.
            if getattr(c, "public", True) is False:
                continue
            body = getattr(c, "body", "") or getattr(c, "html_body", "") or ""
            via_ch = getattr(getattr(c, "via", None), "channel", "") or ""
            author_id = getattr(c, "author_id", None)
            actor = _detect_actor(author_id, ticket, _role(author_id), is_sp, tags)
            thread = "sp" if is_sp else _map_channel(via_ch)
            created = getattr(c, "created_at", None)
            events.append((
                _sort_key(created),
                {
                    "time":      _to_ist(created),
                    "thread":    thread,
                    "actor":     actor,
                    "ticket_id": str(ticket.id),
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
                events.append((
                    _sort_key(m.get("_raw_ts")),
                    {
                        "time":      m.get("time", ""),
                        "thread":    "sp",
                        "actor":     "sp",
                        "ticket_id": str(getattr(ticket, "id", "")),
                        "raw_body":  (f"[Side conversation: {sc.get('subject', '')}"
                                      f"{' · ' + sc['participants'] if sc.get('participants') else ''}]\n"
                                      f"{m.get('actor', '')}: {m.get('body', '')}"),
                    },
                    m.get("body", ""),
                ))

    events.sort(key=lambda e: e[0])

    # ── Truncation: keep first 20 + last 20 if >40 comments ─────────────────
    if len(events) > 40:
        elided = len(events) - 40
        placeholder = (
            events[19][0],
            {
                "time":      "",
                "thread":    "email",
                "actor":     "system",
                "ticket_id": "",
                "raw_body":  f"[{elided} comments elided]",
            },
            "",
        )
        events = events[:20] + [placeholder] + events[-20:]

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

    if first or last:
        if not name_matches(sig.get("guest_name") or "", first, last):
            return False, used
        used.append("name")

    venue = (ind.get("experience_or_venue") or "").strip()
    if venue:
        want = _venue_tokens(venue)
        got  = _venue_tokens(sig.get("experience") or "")
        if not (want and got and (want & got)):
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


async def shortlist(indicators: dict, author_first, author_last,
                    limit_name_only: int = 5) -> list[dict]:
    """
    The bookings a review's indicators actually point at.

    Search Zendesk with whatever indicators exist, then keep only the tickets
    that satisfy ALL of them. No BigQuery: the booking id and every fact needed
    to judge a match are on the ticket itself, and BQ is only needed once an
    associate confirms one.

    When the guest name is the only indicator there is, the filter cannot
    discriminate beyond the name, so the most recent `limit_name_only` are
    returned rather than that guest's entire history.
    """
    if not is_live("zendesk"):
        return []
    _z = _get_client()
    if _z is None:
        return []

    name  = " ".join(x for x in (author_first, author_last) if x).strip()
    venue = (indicators.get("experience_or_venue") or "").strip()
    city  = (indicators.get("city_or_country") or "").split(",")[0].strip()
    if not (name or venue):
        return []

    ORDER = "order_by:created_at sort:desc"
    queries = []
    if name:           queries.append((f'type:ticket requester:"{name}" {ORDER}', "name"))
    if name:           queries.append((f'type:ticket {name} {ORDER}',            "name"))
    if venue:          queries.append((f'type:ticket "{venue}" {ORDER}',         "venue"))
    if name and venue: queries.append((f'type:ticket {name} {venue} {ORDER}',    "name+venue"))
    if name and city:  queries.append((f'type:ticket {name} {city} {ORDER}',     "name+city"))

    loop = asyncio.get_running_loop()
    seen_tickets, by_bid = set(), {}
    for q, label in queries:
        try:
            hits = await loop.run_in_executor(None, lambda qq=q: _search_with_retry(_z, qq))
        except Exception as e:
            log.warning(f"[shortlist] query failed ({label}): {e}")
            continue
        for t in hits or []:
            tid = str(getattr(t, "id", ""))
            if tid in seen_tickets:
                continue
            seen_tickets.add(tid)
            sig = ticket_signals(t)
            bid = sig.get("booking_id")
            if not bid or bid in by_bid:
                continue
            ok, used = matches_indicators(sig, indicators, author_first, author_last)
            if not ok:
                continue
            sig["matched_on"]  = used
            sig["found_via"]   = label
            sig["created_at"]  = str(getattr(t, "created_at", "") or "")
            sig["ticket_id"]   = tid
            by_bid[bid] = sig

    out = list(by_bid.values())
    out.sort(key=lambda s: s.get("created_at") or "", reverse=True)

    # Pax as a narrowing step: only when it actually separates the candidates.
    # If some agree on pax and others do not, keep the agreeing ones. If none
    # agree, pax tells us nothing here and everything stays.
    if len(out) > 1 and indicators.get("pax"):
        exact = [s for s in out if "pax" in (s.get("matched_on") or [])]
        if exact and len(exact) < len(out):
            log.info(f"[shortlist] pax={indicators['pax']}: {len(out)} -> {len(exact)}")
            out = exact

    name_only = bool(name) and not venue and not city
    if name_only and len(out) > limit_name_only:
        log.info(f"[shortlist] name-only: {len(out)} -> newest {limit_name_only}")
        out = out[:limit_name_only]

    log.info(f"[shortlist] {len(seen_tickets)} ticket(s) searched -> {len(out)} match "
             f"(indicators: name={bool(name)} venue={bool(venue)} city={bool(city)} "
             f"pax={indicators.get('pax')})")
    return out


async def find_bids_by_requester_name(
    author_first: str,
    author_last: str | None,
    lookback_days: int | None = None,
    with_context: bool = False,
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

    name_str = f"{author_first} {author_last}".strip() if author_last else author_first
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


def _fallback_shape(raw_events: list) -> list:
    """
    Mechanical fallback when Claude shaping fails.
    Produces {time, thread, actor, label, summary} without [ZD-###] prefixes,
    with HTML-stripped one-line summaries.
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
            "thread":  ev.get("thread", "email"),
            "actor":   actor,
            "label":   label,
            "summary": summary,
        })
    return shaped


def _safe_parse_events(text: str) -> list:
    """
    Parse Claude's JSON response into a list of shaped event dicts.
    Strips markdown fences and JSON comments before parsing.
    Returns empty list on failure.
    """
    import json as _json
    cleaned = text.replace("```json", "").replace("```", "").strip()
    # Remove // comments outside strings
    cleaned = re.sub(r'(?<!["\w])//[^\n]*', '', cleaned)
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
    return []


async def _shape_via_claude(
    raw_events: list,
    booking: dict,
    review_body: str,
    review_pub_date: str,
) -> list:
    """
    One Claude call that batch-shapes raw events into clean timeline entries.
    Filters keep=false events. Returns list of {time, thread, actor, label, summary}.
    """
    from server import prompts as _prompts
    from server.services import claude as _claude

    prompt = _prompts.zendesk_timeline_shape_prompt(
        booking, review_body, review_pub_date, raw_events)
    raw_text = await _claude.shape_timeline_events(prompt)

    shaped = _safe_parse_events(raw_text)
    if not shaped:
        log.warning("[zendesk] Claude returned unparseable shaping response — using fallback")
        return _fallback_shape(raw_events)

    kept = []
    for ev in shaped:
        if not ev.get("keep", True):
            continue
        kept.append({
            "time":    ev.get("time", ""),
            "thread":  ev.get("thread", "email"),
            "actor":   ev.get("actor", "system"),
            "label":   ev.get("label", ""),
            "summary": ev.get("summary", ""),
        })
    return kept
