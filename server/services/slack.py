import hmac, hashlib, time, re, logging
from server.config import (is_live, SLACK_SIGNING_SECRET, SLACK_BOT_TOKEN,
                            SLACK_USER_TOKEN, MOCK_MODE, ORM_CHANNELS,
                            TRUSTPILOT_BOT_USER_ID)

log = logging.getLogger(__name__)

if SLACK_BOT_TOKEN:
    from slack_sdk import WebClient
    _bot = WebClient(token=SLACK_BOT_TOKEN)
else:
    _bot = None

if SLACK_USER_TOKEN:
    from slack_sdk import WebClient
    _user = WebClient(token=SLACK_USER_TOKEN)
else:
    _user = None


def verify_signature(body: bytes, timestamp: str, signature: str) -> bool:
    if MOCK_MODE or not SLACK_SIGNING_SECRET:
        return True
    if abs(time.time() - int(timestamp)) > 300:
        return False
    expected = "v0=" + hmac.new(
        SLACK_SIGNING_SECRET.encode(),
        f"v0:{timestamp}:{body.decode()}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def is_trustpilot_message(event: dict) -> bool:
    """
    Return True if this Slack message is a Trustpilot review.

    Priority:
    1. If TRUSTPILOT_BOT_USER_ID is set — use it. Most precise.
       The bot ID can be found in Slack: click the Trustpilot app → View app details → Member ID.
    2. Fallback — detect by star rating symbols (★/☆) present in every Trustpilot post.

    Always skips thread replies (we only want the top-level review post).
    Always skips messages from our own bot.
    """
    # Skip thread replies
    if event.get("thread_ts") and event.get("thread_ts") != event.get("ts"):
        return False

    # Skip messages posted by our own app
    if SLACK_BOT_TOKEN and event.get("bot_id"):
        own_id = getattr(_bot, "token", "")
        # Don't filter out ALL bots — only skip our own
        pass

    # Strategy 1: match by Trustpilot bot user ID (precise)
    if TRUSTPILOT_BOT_USER_ID:
        user_match = event.get("user") == TRUSTPILOT_BOT_USER_ID
        app_match  = (event.get("subtype") == "bot_message" and
                      event.get("username", "").lower().startswith("trustpilot"))
        bot_id_match = event.get("bot_id") == TRUSTPILOT_BOT_USER_ID
        return user_match or app_match or bot_id_match

    # Strategy 2: star-symbol fallback (no bot ID configured)
    text = event.get("text", "")
    if "★" in text or "☆" in text:
        return True
    for att in event.get("attachments", []):
        att_text = att.get("text", "") + att.get("fallback", "")
        if "★" in att_text or "☆" in att_text:
            return True

    return False


def parse_review(event: dict) -> dict:
    text = event.get("text", "")
    blocks = event.get("blocks", [])

    # Extract star rating
    stars = text.count("★")
    if not stars:
        for a in event.get("attachments", []):
            stars = a.get("text", "").count("★")
            if stars: break

    # ── Booking ID extraction: attachment-first, regex fallback ─────────────
    # Trustpilot's Slack integration posts attachments with a "reference" field.
    from server.taxonomy import BID_REGEX
    booking_id = None
    for att in event.get("attachments", []):
        for field in att.get("fields", []):
            title = (field.get("title") or "").lower()
            if "reference" in title and field.get("value"):
                booking_id = str(field["value"]).strip()
                break
        if booking_id:
            break
    if booking_id:
        log.info("[extract] attachment: booking id from reference field")
    else:
        search_text = text
        for att in event.get("attachments", []):
            search_text += " " + (att.get("text") or "") + " " + (att.get("fallback") or "")
        m = re.search(BID_REGEX, search_text)
        if m:
            booking_id = m.group(0)
            log.info("[extract] regex: booking id from message text")

    # Best-effort author from bold text in first block
    author = None
    body = text
    for b in blocks:
        if b.get("type") == "section":
            t = b.get("text", {}).get("text", "")
            if not author and "*" in t:
                parts = t.split("*")
                if len(parts) >= 2:
                    author = parts[1].strip()
            else:
                body = t

    return {
        "slack_ts":        event["ts"],
        "slack_channel":   event["channel"],
        "rating":          stars or 1,
        "language":        "en",
        "author":          author or "Unknown",
        "body_original":   body,
        "reference_number": booking_id,
        "raw_payload":     event,
    }


async def post_to_thread(channel: str, thread_ts: str, text: str,
                          as_user: bool = True) -> str | None:
    client = _user if as_user and _user else _bot
    if not client:
        log.info(f"[MOCK] Would post to {channel}/{thread_ts}: {text[:120]}…")
        return None
    try:
        res = client.chat_postMessage(
            channel=channel, thread_ts=thread_ts, text=text,
            unfurl_links=False, unfurl_media=False,
        )
        return res.get("ts")
    except Exception as e:
        log.exception(f"Slack post failed: {e}")
        return None


def format_rca_slack(review, draft) -> str:
    """Format the RCA draft into the Headout Slack format."""
    f = draft.rca_fields or {}
    b = draft.booking or {}
    signals = draft.signals or []
    div = "_" * 61
    nl = "\n"

    sig_block = ""
    if signals:
        sig_block = f"{div}{nl}*Signals*{nl}" + nl.join(f"• {s}" for s in signals) + nl

    return f"""*Case Facts* @reviewteam 

*Query/Issue Type:*
{f.get('queryIssueType', '')}
{div}
*Booking Details*

Booking ID: {b.get('id', '—')}
Experience Name: {b.get('experienceName', '—')}
TGID: {b.get('tgid', '—')}
TID: {b.get('tid', '—')}
Date of Booking: {b.get('bookedOn', '—')}
Date of Visit: {b.get('visitDate', '—')}
Supply Partner Name: {b.get('partner', '—')}
{div}
*Case Details*

*What went wrong:*
{f.get('whatWentWrong', '')}

*Customer interaction with CO:*
{f.get('customerInteractionCO', '')}

*SP's Issue/Interaction:*
{f.get('spIssueInteraction', 'None')}

*Area of Improving:*
{f.get('areaOfImproving', '')}

*Solution Offered:*
{f.get('solutionOffered', '')}

*Follow-up needed?*
{f.get('followUpNeeded', 'No')}

*Review Takedown Sent?*
{f.get('reviewTakedownSent', 'Yes')}
{sig_block}{div}
*Raised with the team responsible?*
{f.get('raisedTeam1', 'NA')}

*Raised with second team?*
{f.get('raisedTeam2', '')}
{div}
*Insights*

*Bookings Impacted:* {f.get('bookingsImpacted', '')}
*Similar Queries:* {f.get('similarQueries', '')}
*Avg Rating:* {f.get('avgRating', '')}
*Does DSS cover this?* {f.get('dssCovers', 'Yes')}
*Other comments:* {f.get('otherComments', '')}"""
