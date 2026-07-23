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
    attachments = event.get("attachments", [])

    # ── Star rating ──────────────────────────────────────────────────────────
    # Check message text, then attachment text, then attachment footer
    # (Trustpilot puts stars in the footer: "★✩✩✩✩ Not verified")
    stars = text.count("★")
    if not stars:
        for a in attachments:
            stars = a.get("text", "").count("★")
            if stars:
                break
    if not stars:
        for a in attachments:
            stars = (a.get("footer") or "").count("★")
            if stars:
                break

    # ── Booking ID: attachment fields → regex fallback ───────────────────────
    from server.taxonomy import BID_REGEX
    booking_id = None
    for att in attachments:
        for field in att.get("fields", []):
            title = (field.get("title") or "").lower()
            if "reference" in title and field.get("value"):
                raw_val = str(field["value"]).strip()
                # Clean messy values like "Booking I'd 3168128" → extract numeric BID
                bid_match = re.search(BID_REGEX, raw_val)
                if bid_match:
                    booking_id = bid_match.group(0)
                    log.info(f"[extract] attachment field: booking id {booking_id} (raw: {raw_val!r})")
                else:
                    log.info(f"[extract] attachment field: reference value not a BID: {raw_val!r}")
                break
        if booking_id:
            break
    if not booking_id:
        search_text = text
        for att in attachments:
            search_text += " " + (att.get("text") or "") + " " + (att.get("fallback") or "")
        m = re.search(BID_REGEX, search_text)
        if m:
            booking_id = m.group(0)
            log.info(f"[extract] regex: booking id {booking_id} from message text")

    # ── Author ───────────────────────────────────────────────────────────────
    # Priority: bold text in blocks → attachment.author_name → Unknown
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
    if not author:
        for att in attachments:
            if att.get("author_name"):
                author = att["author_name"].strip()
                break

    # ── Body ─────────────────────────────────────────────────────────────────
    # Fall back to attachment.text when message text/blocks are empty
    if not body or not body.strip():
        for att in attachments:
            att_text = att.get("text", "").strip()
            if att_text:
                title = att.get("title", "").strip()
                body = f"{title}\n\n{att_text}".strip() if title else att_text
                break

    return {
        "slack_ts":         event["ts"],
        "slack_channel":    event["channel"],
        "rating":           stars or 1,
        "language":         "en",
        "author":           author or "Unknown",
        "body_original":    body,
        "reference_number": booking_id,
        "raw_payload":      event,
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
    """
    Format the RCA draft into the Headout Slack thread post.
    Pulls from v3-pipeline columns. NEVER includes guest response copy.
    """
    b   = draft.booking or {}
    div = "_" * 61
    nl  = "\n"

    # ── Classification ────────────────────────────────────────────────────────
    l1        = draft.l1 or "—"
    l2        = draft.l2 or "—"
    sub_theme = draft.sub_theme or ""
    issue_line = f"{l1} / {l2}" + (f" / {sub_theme}" if sub_theme else "")

    tldr          = draft.tldr or draft.stated_issue or "—"

    # ── What went wrong ───────────────────────────────────────────────────────
    wwb  = draft.what_went_wrong_bullets or []
    wwb_text = nl.join(f"• {b_}" for b_ in wwb) if wwb else "—"

    wwr  = draft.wwr_chain or []
    wwr_text = ""
    if wwr:
        wwr_text = (
            f"{nl}*Root cause chain:*{nl}"
            + nl.join(
                f"  {s['step']}. *{s.get('what','')}* — {s.get('why','')}"
                for s in wwr
            )
        )

    # ── Checklist answers ─────────────────────────────────────────────────────
    ca = draft.checklist_answers or []
    checklist_text = ""
    if ca:
        from collections import defaultdict
        by_section = defaultdict(list)
        for item in ca:
            by_section[item.get("section", "other")].append(item)
        parts = []
        for section in ["ce", "ro"] + [k for k in by_section if k not in ("ce", "ro")]:
            items = by_section.get(section)
            if not items:
                continue
            label = {"ce": "CE Errors", "ro": "RO Errors"}.get(section, section)
            parts.append(f"*{label}*")
            for it in items:
                chk = it.get("item") or it.get("check", "")
                ans = it.get("answer", "?")
                ev  = it.get("evidence", "")
                ev_part = f" ({ev})" if ev and ev != "not present in ticket or booking data" else ""
                parts.append(f"• [{section.upper()}] {chk} → *{ans}*{ev_part}")
        checklist_text = nl.join(parts)

    # ── Support interactions ───────────────────────────────────────────────────
    support_summary = draft.support_summary or "—"
    sp_frames = draft.sp_interaction_frames or []
    sp_text = ""
    if sp_frames:
        sp_text = (
            f"{nl}*SP interactions:*{nl}"
            + nl.join(
                f"• {fr.get('time','?')} — {fr.get('summary','')}" +
                (f" | comp: {fr['comp']}" if fr.get("comp") else "")
                for fr in sp_frames
            )
        )

    # ── Area of improving ─────────────────────────────────────────────────────
    aoi = draft.area_of_improving or []
    aoi_text = nl.join(f"• {a}" for a in aoi) if aoi else "—"

    # ── Actions taken ─────────────────────────────────────────────────────────
    at = draft.actions_taken or {}
    actions_parts = []
    for team in ("sp", "ce", "customer", "business", "product"):
        items = at.get(team) or []
        if items:
            for item in items:
                if isinstance(item, dict):
                    desc = item.get("with") or item.get("context") or str(item)
                    handle = item.get("handle", "")
                    actions_parts.append(f"• [{team.upper()}] {desc}" + (f" — {handle}" if handle else ""))
                else:
                    actions_parts.append(f"• [{team.upper()}] {item}")
    actions_text = nl.join(actions_parts) if actions_parts else "—"

    # ── Insights ──────────────────────────────────────────────────────────────
    ins = draft.insights or {}
    insights_parts = []
    if ins.get("similar_review_count") is not None:
        insights_parts.append(f"*Similar reviews (same VID):* {ins['similar_review_count']}")
    if ins.get("total_review_count") is not None:
        insights_parts.append(f"*Total reviews (same VID):* {ins['total_review_count']}")
    if ins.get("similar_support_count") is not None:
        insights_parts.append(f"*Similar support queries:* {ins['similar_support_count']}")
    if ins.get("avg_rating") is not None:
        insights_parts.append(f"*Avg rating (VID):* {ins['avg_rating']}")
    if ins.get("completion_rate") is not None:
        insights_parts.append(f"*Completion rate (VID):* {ins['completion_rate']}")
    insights_text = nl.join(insights_parts) if insights_parts else "—"

    return f"""*RCA — @reviewteam*

*Issue:* {issue_line}
*TL;DR:* {tldr}
{div}
*Booking Details*

Booking ID: {b.get('id', '—')}
Experience: {b.get('experienceName', '—')}
TGID / TID: {b.get('tgid', '—')} / {b.get('tid', '—')}
Date of booking: {b.get('bookedOn', '—')}
Date of visit: {b.get('visitDate', '—')}
Supply partner: {b.get('partner', '—')}
{div}
*What went wrong*

{wwb_text}{wwr_text}
{div}
*Diagnostic checks*

{checklist_text or '—'}
{div}
*Customer / CE interactions*

{support_summary}{sp_text}
{div}
*Area of improving*

{aoi_text}
{div}
*Solution offered*

{draft.resolution or '—'}
{div}
*Actions raised*

{actions_text}
{div}
*Insights*

{insights_text}"""
