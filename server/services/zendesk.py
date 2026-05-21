"""
Zendesk service — fetches tickets by booking ID and builds a structured timeline.

Improvements over v1:
- Full comment text passed through (no 200-char truncation) — Claude summarises
- Separates guest brand tickets from SP brand tickets
- Classifies actors properly: Guest / CO Agent / Minded AI / SP
- Flags key moments: refund, chargeback, escalation, AI error, SLA breach
- Returns a conversation_summary (raw material for Claude's RCA prompt)
"""
import logging
from datetime import datetime, timezone
from server.config import (
    is_live, ZENDESK_SUBDOMAIN, ZENDESK_EMAIL,
    ZENDESK_API_TOKEN, ZENDESK_BOOKING_FIELD_ID,
)
from server.services.mock_data import MOCK_TIMELINES, MOCK_BOOKINGS

log = logging.getLogger(__name__)

# Keywords that flag a comment as a key moment worth highlighting in the RCA
FLAG_RULES = {
    "chargeback":    ["chargeback", "dispute", "bank claim", "bank dispute"],
    "refund":        ["refund", "money back", "reimburse"],
    "escalation":    ["escalat", "manager", "legal", "trading standards", "complaint"],
    "AI mishandle":  ["minded", "bot", "automated", "ai agent"],
    "frustration":   ["unacceptable", "disgusting", "scam", "fraud", "terrible",
                      "awful", "never again", "worst", "furious", "outraged"],
    "SP issue":      ["supplier", "supply partner", "operator", "venue",
                      "guide", "tour leader", "cancelled at"],
    "TAT breach":    ["still waiting", "no response", "hours ago", "days ago",
                      "haven't heard"],
}

# Known automation/bot identifiers (add Minded AI's Zendesk agent name/email here)
BOT_AGENT_NAMES = ["minded", "bot", "automated", "automation", "system"]


if is_live("zendesk"):
    from zenpy import Zenpy
    _z = Zenpy(
        subdomain=ZENDESK_SUBDOMAIN,
        email=ZENDESK_EMAIL,
        token=ZENDESK_API_TOKEN,
    )
else:
    _z = None


async def get_timeline(booking_id: str, review_id: str = None) -> list:
    """
    Returns a chronological list of timeline events, each with:
      time, actor, actor_label, summary (full text), flag (optional)

    The full comment text is preserved — Claude compresses it in the RCA prompt.
    """
    if not is_live("zendesk"):
        if review_id and review_id in MOCK_TIMELINES:
            return MOCK_TIMELINES[review_id]
        for rid, b in MOCK_BOOKINGS.items():
            if b.get("id") == booking_id:
                return MOCK_TIMELINES.get(rid, [])
        return []

    timeline = []

    try:
        query = f'custom_field_{ZENDESK_BOOKING_FIELD_ID}:"{booking_id}"'
        tickets = list(_z.search(query=query, type="ticket"))

        if not tickets:
            log.info(f"No Zendesk tickets found for booking {booking_id}")
            return []

        for ticket in tickets:
            # Detect whether this is an SP ticket or guest ticket by brand/tags
            is_sp_ticket = _is_sp_ticket(ticket)
            ticket_label = "SP Ticket" if is_sp_ticket else "Guest Ticket"

            # Add ticket open event
            timeline.append({
                "time":        _fmt(ticket.created_at),
                "actor":       "sp" if is_sp_ticket else "guest",
                "actor_label": ticket_label,
                "summary":     f"Ticket #{ticket.id} opened — {ticket.subject or '(no subject)'}",
                "ticket_id":   ticket.id,
                "is_sp":       is_sp_ticket,
            })

            # Walk through comments chronologically
            try:
                comments = list(_z.tickets.comments(ticket=ticket))
            except Exception as e:
                log.warning(f"Could not fetch comments for ticket {ticket.id}: {e}")
                continue

            for comment in comments:
                actor, actor_label = _classify_actor(comment, ticket, is_sp_ticket)
                body = (comment.body or "").strip()
                flag = _detect_flag(body)

                timeline.append({
                    "time":        _fmt(comment.created_at),
                    "actor":       actor,
                    "actor_label": actor_label,
                    "summary":     body,   # full text — Claude summarises
                    "public":      comment.public,
                    "ticket_id":   ticket.id,
                    "is_sp":       is_sp_ticket,
                    **({"flag": flag} if flag else {}),
                })

    except Exception as e:
        log.exception(f"Zendesk fetch failed for booking {booking_id}: {e}")

    # Sort by time
    timeline.sort(key=lambda e: e.get("time", ""))
    return timeline


def _fmt(dt) -> str:
    """Format a datetime (aware or naive) as '24 Apr · 14:32 IST'."""
    if dt is None:
        return ""
    if hasattr(dt, "strftime"):
        return dt.strftime("%d %b · %H:%M")
    return str(dt)


def _is_sp_ticket(ticket) -> bool:
    """
    Heuristic: is this a supply-partner ticket rather than a guest ticket?
    Checks the brand name (via Zenpy's brand object) and ticket tags.
    """
    # Zenpy returns brand as an object with a 'name' attribute
    brand_obj = getattr(ticket, "brand", None)
    brand_name = ""
    if brand_obj:
        brand_name = str(getattr(brand_obj, "name", brand_obj) or "").lower()
    if any(w in brand_name for w in ["supply", "partner", "sp", "ops"]):
        return True
    tags = getattr(ticket, "tags", []) or []
    if any(t in tags for t in ["sp", "supply_partner", "operator"]):
        return True
    return False


def _classify_actor(comment, ticket, is_sp_ticket: bool):
    """
    Returns (actor_key, actor_label).
    actor_key: guest | co | sp | system
    """
    author = getattr(comment, "author", None)

    # System / automation comments (internal Zendesk events)
    via = getattr(comment, "via", None)
    channel = getattr(via, "channel", "") if via else ""
    if channel == "rule":
        return "system", "System"

    # Check if the author is a known bot/automation agent
    author_name = ""
    if author:
        author_name = (getattr(author, "name", "") or "").lower()
        if any(bot in author_name for bot in BOT_AGENT_NAMES):
            return "system", "Minded AI"

    # Public comment from a non-agent = guest
    if comment.public:
        author_role = getattr(author, "role", "") if author else ""
        if author_role not in ("agent", "admin"):
            return "guest", "Guest"

    # Internal note or agent comment
    if is_sp_ticket:
        return "sp", "Supply Partner"
    return "co", "CO Agent"


def _detect_flag(text: str) -> str | None:
    """
    Return the first matching flag label if the comment text contains
    keywords worth highlighting in the RCA.
    """
    lower = text.lower()
    for flag_label, keywords in FLAG_RULES.items():
        if any(kw in lower for kw in keywords):
            return flag_label
    return None
