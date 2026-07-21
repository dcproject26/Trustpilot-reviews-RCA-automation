"""
Zendesk service — connector-auth'd (Replit Zendesk connection, OAuth).

Data path per the 2026-07 wiring brief:
1. Search tickets: fieldvalue:<bid> first, free-text "<bid>" fallback (logged).
2. Extract tgid/tid from confirmed custom field IDs (2024 Retool workflow).
3. Surface ticket_mail_seen tag on the booking.
4. Fetch ALL comments per ticket (public + private notes), paginated by zenpy.
5. Brand split: guest-brand tickets -> guest timeline, SP-brand -> sp thread
   (draft.sp_interaction_frames). If brands unset, everything is guest.
6. Merge multi-ticket comments chronologically, label prefixed [ZD-<id>].
7. Timeline events use the demo-v15 renderer shape (time/thread/actor/label/summary),
   with the raw body kept in a parallel timeline_raw list.
8. >40 comments -> keep first 20 + last 20 with one "[N comments elided]" event.
"""
import html as _html
import logging
import re
from datetime import datetime, timedelta, timezone

from server.config import (
    is_live, ZENDESK_SUBDOMAIN, ZENDESK_EMAIL, ZENDESK_API_TOKEN,
    ZENDESK_TGID_FIELD, ZENDESK_TID_FIELD,
    ZENDESK_BRAND_GUEST, ZENDESK_BRAND_SP, ZENDESK_BOT_TAGS,
)
from server.services.mock_data import MOCK_TIMELINES, MOCK_BOOKINGS

log = logging.getLogger(__name__)

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


async def get_timeline(booking_id: str, review_id: str = None) -> tuple[list, dict, dict]:
    """
    Returns (timeline, extracted_booking_fields, meta).

    timeline: chronological events shaped for the demo-v15 renderer:
        {time, thread, actor, label, summary}
    extracted_booking_fields: {tgid, tid, ticket_mail_seen} from custom fields/tags.
    meta: {"ticket_ids": [str, ...], "timeline_raw": [str, ...]}  # raw bodies,
          same length/order as timeline.
    """
    if not is_live("zendesk"):
        if review_id and review_id in MOCK_TIMELINES:
            tl = MOCK_TIMELINES[review_id]
            return tl, {}, {"ticket_ids": [], "timeline_raw": [""] * len(tl)}
        for rid, b in MOCK_BOOKINGS.items():
            if b.get("id") == booking_id:
                tl = MOCK_TIMELINES.get(rid, [])
                return tl, {}, {"ticket_ids": [], "timeline_raw": [""] * len(tl)}
        return [], {}, {"ticket_ids": [], "timeline_raw": []}

    _z = _get_client()
    if _z is None:
        log.warning("[zendesk] no client available")
        return [], {}, {"ticket_ids": [], "timeline_raw": []}

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
            return [], {}, {"ticket_ids": [], "timeline_raw": []}

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

    # ── Fetch comments per ticket (zenpy paginates), build events ────────────
    events = []   # (sort_dt, event_dict, raw_body)
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
            body = getattr(c, "body", "") or getattr(c, "html_body", "") or ""
            via_ch = getattr(getattr(c, "via", None), "channel", "") or ""
            author_id = getattr(c, "author_id", None)
            actor = _detect_actor(author_id, ticket, _role(author_id), is_sp, tags)
            thread = "sp" if is_sp else _map_channel(via_ch)
            actor_desc = {
                "guest": "Guest wrote",
                "co":    "CE responded to " + _map_channel(via_ch),
                "sp":    "SP responded",
                "ai":    "AI responded",
                "system": "System event",
            }[actor]
            created = getattr(c, "created_at", None)
            events.append((
                _sort_key(created),
                {
                    "time":    _to_ist(created),
                    "thread":  thread,
                    "actor":   actor,
                    "label":   f"[ZD-{ticket.id}] {actor_desc}",
                    "summary": _clean_summary(body),
                },
                body,
            ))

    events.sort(key=lambda e: e[0])

    # ── Truncation: keep first 20 + last 20 if >40 comments ─────────────────
    if len(events) > 40:
        elided = len(events) - 40
        placeholder = (
            events[19][0],
            {"time": "", "thread": "email", "actor": "system",
             "label": f"[{elided} comments elided]", "summary": ""},
            "",
        )
        events = events[:20] + [placeholder] + events[-20:]

    timeline = [e[1] for e in events]
    timeline_raw = [e[2] for e in events]
    ticket_ids = [str(t.id) for t in tickets]

    return timeline, extracted, {"ticket_ids": ticket_ids, "timeline_raw": timeline_raw}
