"""
Zendesk service.

What this does:
1. Searches tickets by booking ID custom field (360021524471)
2. Falls back to requester email search to catch chat tickets not tagged with booking ID
3. Fetches all comments via Google Apps Script (single batched call, not per-ticket)
4. Filters out system/automation channel comments (Zendesk internal events, not real interactions)
5. Extracts booking details from the first comment (system-generated booking dump)
6. Classifies every comment: Guest | CO Agent | Minded AI | Supply Partner | System
7. Flags key moments: chargeback, refund, escalation, AI mishandle, frustration, SP issue, TAT breach
8. Sorts everything chronologically and returns a clean timeline
"""
import re, logging, httpx
from datetime import datetime
from server.config import (
    is_live, ZENDESK_SUBDOMAIN, ZENDESK_EMAIL, ZENDESK_API_TOKEN,
    ZENDESK_BOOKING_FIELD_ID, APPS_SCRIPT_URL,
)
from server.services.mock_data import MOCK_TIMELINES, MOCK_BOOKINGS

log = logging.getLogger(__name__)

# Subjects that are system-generated noise — skip these tickets entirely
NOISE_SUBJECTS = [
    "payment receipt", "booking confirmed", "booking confirmation",
    "automatic follow-up", "auto follow-up", "credits added",
    "refund processed", "booking canceled", "booking cancelled",
]

# Flag keywords — detected on every comment body
FLAG_RULES = {
    "chargeback":   ["chargeback", "dispute", "bank claim", "bank dispute"],
    "refund":       ["refund", "money back", "reimburse"],
    "escalation":   ["escalat", "manager", "legal", "trading standards", "complaint"],
    "AI mishandle": ["minded", "bot", "automated", "ai agent"],
    "frustration":  ["unacceptable", "disgusting", "scam", "fraud", "terrible",
                     "awful", "never again", "worst", "furious", "outraged", "arnaque"],
    "SP issue":     ["supplier", "supply partner", "operator", "venue",
                     "guide", "tour leader", "cancelled at", "canceled at"],
    "TAT breach":   ["still waiting", "no response", "hours ago", "days ago",
                     "haven't heard", "no reply"],
}

# Agent names that identify Minded AI / automation in Zendesk
BOT_AGENT_NAMES = ["minded", "bot", "automated", "automation"]

# Regex patterns to extract booking data from the first system comment
_TGID_RE   = re.compile(r'Tour_Group_Id[:\s]+(\d+)', re.I)
_TID_RE    = re.compile(r'Tour_Id[:\s]+(\d+)', re.I)
_TNAME_RE  = re.compile(r'Tour_Name[:\s]+(.*?),', re.I | re.S)
_DATE_RE   = re.compile(r'Booking Details.*?Date[:\s]+(\d{4}-\d{2}-\d{2})', re.I | re.S)
_VID_RE    = re.compile(r'Vendor.Id[:\s]+(\d+)', re.I)
_VNAME_RE  = re.compile(r'(?:Primary\s+)?Vendor.Name[:\s]+(.*?)[\n,]', re.I)
_PAX_RE    = re.compile(r'Guest_Numbers[:\s]+(.*?)[\n,]', re.I)


if is_live("zendesk"):
    from zenpy import Zenpy
    _z = Zenpy(
        subdomain=ZENDESK_SUBDOMAIN,
        email=ZENDESK_EMAIL,
        token=ZENDESK_API_TOKEN,
    )
else:
    _z = None


async def get_timeline(booking_id: str, review_id: str = None) -> tuple[list, dict]:
    """
    Returns (timeline, extracted_booking_fields).

    timeline: chronological list of events, each with:
        time, actor, actor_label, summary, public (bool), flag (optional),
        ticket_id, is_sp

    extracted_booking_fields: dict of booking data parsed from the first Zendesk comment.
        Keys: tgid, tid, experienceName, visitDate, vid, vendorName, pax
        Used as a fallback/supplement to BigQuery booking data.
    """
    if not is_live("zendesk"):
        if review_id and review_id in MOCK_TIMELINES:
            return MOCK_TIMELINES[review_id], {}
        for rid, b in MOCK_BOOKINGS.items():
            if b.get("id") == booking_id:
                return MOCK_TIMELINES.get(rid, []), {}
        return [], {}

    timeline = []
    extracted = {}

    try:
        # ── Step 1: find tickets by booking ID custom field ──────────────────
        query = f'type:ticket fieldvalue:"{booking_id}"'
        tickets = list(_z.search(query=query, type="ticket"))
        log.info(f"[ZD] booking {booking_id}: {len(tickets)} tickets by field search")

        # ── Step 2: email fallback — catches chat tickets not tagged with booking ID ──
        # Get requester email from the first ticket found, then search all their tickets
        requester_email = None
        if tickets:
            first = tickets[0]
            try:
                requester = getattr(first, "requester", None)
                if requester:
                    requester_email = getattr(requester, "email", None)
            except Exception:
                pass

        if requester_email:
            email_query = f'type:ticket requester:"{requester_email}"'
            email_tickets = list(_z.search(query=email_query, type="ticket"))
            # Merge — add any tickets not already in the list
            existing_ids = {t.id for t in tickets}
            for t in email_tickets:
                if t.id not in existing_ids:
                    tickets.append(t)
            log.info(f"[ZD] email fallback added {len(tickets) - len(existing_ids)} more tickets")

        if not tickets:
            log.info(f"[ZD] No tickets found for booking {booking_id}")
            return [], {}

        # ── Step 3: filter noise tickets ────────────────────────────────────
        real_tickets = []
        for t in tickets:
            subject = (t.subject or "").lower()
            if any(noise in subject for noise in NOISE_SUBJECTS):
                log.debug(f"[ZD] skipping noise ticket #{t.id}: {t.subject}")
                continue
            real_tickets.append(t)

        if not real_tickets:
            log.info(f"[ZD] all tickets were noise for booking {booking_id}")
            return [], {}

        # ── Step 4: fetch all comments via Apps Script (one batched call) ────
        ticket_ids = [t.id for t in real_tickets]
        comments_by_ticket = await _fetch_comments_apps_script(ticket_ids)

        # ── Step 5: build timeline ───────────────────────────────────────────
        first_comment_parsed = False

        for ticket in sorted(real_tickets, key=lambda t: str(t.created_at or "")):
            is_sp = _is_sp_ticket(ticket)
            label = "SP Ticket" if is_sp else f"Ticket #{ticket.id}"

            # Ticket-open event
            timeline.append({
                "time":        _fmt(ticket.created_at),
                "actor":       "sp" if is_sp else "system",
                "actor_label": label,
                "summary":     f"#{ticket.id} opened — {ticket.subject or '(no subject)'}",
                "public":      True,
                "ticket_id":   ticket.id,
                "is_sp":       is_sp,
            })

            raw_comments = comments_by_ticket.get(str(ticket.id), [])

            for i, comment in enumerate(raw_comments):
                body      = (comment.get("body") or "").strip()
                created   = comment.get("created_at", "")
                via_ch    = (comment.get("via") or {}).get("channel", "")
                is_public = comment.get("public", True)
                author_name = ((comment.get("author") or {}).get("name") or "").lower()

                # ── Drop pure system/automation channel events ──
                # These are Zendesk internal triggers, not real interactions
                if via_ch in ("rule", "system", "automation", "trigger"):
                    continue

                # ── Parse booking data from first system comment ──
                # The first comment is always the auto-generated booking dump
                if i == 0 and not first_comment_parsed and not is_sp:
                    extracted = _parse_booking_from_comment(body)
                    first_comment_parsed = True
                    # Skip this comment from the timeline — it's system data, not an interaction
                    continue

                # ── Also skip other system-generated emails ──
                # Booking confirmation, voucher emails etc. have no conversation value
                if _is_system_email(body):
                    continue

                actor, actor_label = _classify_comment(
                    via_ch, author_name, is_public, is_sp)
                flag = _detect_flag(body)

                timeline.append({
                    "time":        _fmt_str(created),
                    "actor":       actor,
                    "actor_label": actor_label,
                    "summary":     body,
                    "public":      is_public,
                    "ticket_id":   ticket.id,
                    "is_sp":       is_sp,
                    **({"flag": flag} if flag else {}),
                })

    except Exception as e:
        log.exception(f"[ZD] Timeline build failed for {booking_id}: {e}")

    # Sort chronologically
    timeline.sort(key=lambda e: e.get("time", ""))
    return timeline, extracted


async def _fetch_comments_apps_script(ticket_ids: list) -> dict:
    """
    Calls the Google Apps Script endpoint which returns comments for all
    ticket IDs in a single HTTP call.

    Returns dict: { "ticket_id_str": [comment, ...] }

    Falls back to direct Zendesk per-ticket calls if Apps Script fails.
    """
    if not APPS_SCRIPT_URL or not ticket_ids:
        return _fetch_comments_direct(ticket_ids)

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                APPS_SCRIPT_URL,
                json=ticket_ids,
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code == 200:
                data = resp.json()
                # Apps Script returns { comments: [ {ticket_id, body, ...} ] }
                # Group by ticket_id
                result = {}
                comments_list = data if isinstance(data, list) else data.get("comments", [])
                for c in comments_list:
                    tid = str(c.get("ticket_id", ""))
                    result.setdefault(tid, []).append(c)
                log.info(f"[AppsScript] got comments for {len(result)} tickets")
                return result
            else:
                log.warning(f"[AppsScript] HTTP {resp.status_code} — falling back to direct")
    except Exception as e:
        log.warning(f"[AppsScript] failed ({e}) — falling back to direct Zendesk calls")

    return _fetch_comments_direct(ticket_ids)


def _fetch_comments_direct(ticket_ids: list) -> dict:
    """
    Fallback: fetch comments from Zendesk directly, one ticket at a time.
    Slower but always works when Apps Script is unavailable.
    """
    if not _z:
        return {}
    result = {}
    for tid in ticket_ids:
        try:
            from zenpy.lib.api_objects import Ticket
            ticket = _z.tickets(id=tid)
            comments = list(_z.tickets.comments(ticket=ticket))
            result[str(tid)] = [
                {
                    "body":       c.body or "",
                    "created_at": str(c.created_at) if c.created_at else "",
                    "public":     c.public,
                    "via":        {"channel": getattr(getattr(c, "via", None), "channel", "")},
                    "author":     {"name": getattr(getattr(c, "author", None), "name", "")},
                    "ticket_id":  tid,
                }
                for c in comments
            ]
        except Exception as e:
            log.warning(f"[ZD] Could not fetch comments for ticket {tid}: {e}")
    return result


def _parse_booking_from_comment(text: str) -> dict:
    """
    Parses the auto-generated first Zendesk comment (booking dump) using regex.
    Returns whatever fields are found — used as a supplement to BigQuery data.
    """
    out = {}
    m = _TGID_RE.search(text)
    if m: out["tgid"] = m.group(1)
    m = _TID_RE.search(text)
    if m: out["tid"] = m.group(1)
    m = _TNAME_RE.search(text)
    if m: out["experienceName"] = m.group(1).strip()
    m = _DATE_RE.search(text)
    if m: out["visitDate"] = m.group(1)
    m = _VID_RE.search(text)
    if m: out["vid"] = m.group(1)
    m = _VNAME_RE.search(text)
    if m: out["vendorName"] = m.group(1).strip()
    m = _PAX_RE.search(text)
    if m: out["pax"] = m.group(1).strip()
    return out


def _is_system_email(body: str) -> bool:
    """
    Returns True for system-generated emails that have no conversation value
    (booking confirmations, vouchers, ticket emails etc.)
    These appear as public comments but are outbound automated emails.
    """
    lower = body.lower()
    system_markers = [
        "votre réservation est en cours",   # French booking processing
        "your booking is being processed",
        "votre réservation est confirmée",
        "your booking is confirmed",
        "ceci n'est pas votre billet",
        "this is not your ticket",
        "accédez à vos billets",
        "access your tickets",
        "headout inc., 82 nassau",           # email footer
        "politique d'annulation",            # cancellation policy block
        "cancellation policy",
    ]
    return any(m in lower for m in system_markers)


def _is_sp_ticket(ticket) -> bool:
    """Detect supply-partner tickets by brand name or tags."""
    brand_obj  = getattr(ticket, "brand", None)
    brand_name = str(getattr(brand_obj, "name", brand_obj) or "").lower() if brand_obj else ""
    if any(w in brand_name for w in ["supply", "partner", "sp", "ops", "vendor"]):
        return True
    tags = getattr(ticket, "tags", []) or []
    return any(t in tags for t in ["sp", "supply_partner", "operator", "vendor"])


def _classify_comment(via_channel: str, author_name: str,
                       is_public: bool, is_sp_ticket: bool) -> tuple[str, str]:
    """
    Returns (actor_key, actor_label).
    actor_key: guest | co | sp | system
    """
    # Minded AI / automation agents
    if any(bot in author_name for bot in BOT_AGENT_NAMES):
        return "system", "Minded AI"

    # Public comment from a non-agent = guest
    if is_public and via_channel in ("email", "web", "chat", ""):
        # Heuristic: if the author name looks like a Headout agent, mark as CO
        headout_markers = ["headout", "support", "team", "agent"]
        if any(m in author_name for m in headout_markers):
            return "co", "CO Agent"
        return "guest", "Guest"

    # Internal note or outbound agent comment
    if is_sp_ticket:
        return "sp", "Supply Partner"
    return "co", "CO Agent"


def _detect_flag(text: str) -> str | None:
    lower = text.lower()
    for label, keywords in FLAG_RULES.items():
        if any(kw in lower for kw in keywords):
            return label
    return None


def _fmt(dt) -> str:
    if dt is None:
        return ""
    if hasattr(dt, "strftime"):
        return dt.strftime("%Y-%m-%d %H:%M")
    return str(dt)[:16]


def _fmt_str(s: str) -> str:
    if not s:
        return ""
    # ISO 8601 → "YYYY-MM-DD HH:MM"
    return s[:16].replace("T", " ")
