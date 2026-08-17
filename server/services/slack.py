import hmac, hashlib, time, re, logging

from server.services import zendesk as _zd
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


# "Reviewed on 2 August 2026", "Posted: 02/08/2026", "2 Aug 2026" — the shapes
# Trustpilot's Slack integration puts in an attachment footer or a dated field.
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_DATE_TEXT_RE = re.compile(
    r"\b(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})\b|"          # 2 August 2026
    r"\b([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(\d{4})\b|"        # August 2, 2026
    r"\b(\d{4})-(\d{2})-(\d{2})\b")                        # 2026-08-02

# Field titles Trustpilot uses for the publish date. Matched on the TITLE, not
# on any date-looking string anywhere in the payload — a visit date, a booking
# date and a refund date all look identical to a regex, and picking the wrong
# one silently is worse than not picking one.
_DATE_TITLES = ("review date", "reviewed on", "date of experience",
                "posted", "posted on", "published", "published on", "date")


def review_published_at(event: dict):
    """When the guest actually POSTED the review, and where that came from.

    Returns (datetime | None, source) where source is one of
    "attachment_ts" / "field:<title>" / "footer" / "" — the empty string
    meaning the payload carries no publish date at all.

    WHY THIS EXISTS. The stamp on the card was the Slack message timestamp:
    when the review reached the channel. That is minutes after publication on
    a good day and hours after it on a bad one, and it was rendered as
    "Review date" and as "Review posted" on the timeline with nothing saying
    it was neither. A reader comparing the review against a ticket raised the
    same morning is reading a lead time that is wrong by however long the
    integration took.

    The sources are ordered by how directly they assert the fact:
      1. `attachment.ts` — Slack's own attachment timestamp. An integration
         that sets it is stating when the thing happened, not when it sent
         the message.
      2. a dated FIELD whose title says so.
      3. a date in the footer, which is where Trustpilot writes it in prose.

    Nothing here guesses. A date-shaped string in an untitled field is not
    used: a visit date, a booking date and a refund date are all
    indistinguishable to a regex, and quietly choosing one is exactly the
    class of failure that makes a wrong answer look like a right one.
    """
    from datetime import datetime, timezone

    def _from_epoch(v):
        try:
            f = float(str(v).strip())
        except (TypeError, ValueError):
            return None
        if 1e9 < f < 4e9:
            return datetime.fromtimestamp(f, timezone.utc).replace(tzinfo=None)
        return None

    def _from_text(s):
        m = _DATE_TEXT_RE.search(str(s or ""))
        if not m:
            return None
        g = m.groups()
        try:
            if g[0]:                                   # 2 August 2026
                mo = _MONTHS.get(g[1][:3].lower())
                return datetime(int(g[2]), mo, int(g[0])) if mo else None
            if g[3]:                                   # August 2, 2026
                mo = _MONTHS.get(g[3][:3].lower())
                return datetime(int(g[5]), mo, int(g[4])) if mo else None
            return datetime(int(g[6]), int(g[7]), int(g[8]))
        except (TypeError, ValueError):
            return None

    for att in event.get("attachments", []) or []:
        got = _from_epoch(att.get("ts"))
        if got:
            return got, "attachment_ts"

    for att in event.get("attachments", []) or []:
        for f in att.get("fields", []) or []:
            title = str(f.get("title") or "").strip()
            if title.lower().rstrip(":") in _DATE_TITLES:
                got = _from_text(f.get("value")) or _from_epoch(f.get("value"))
                if got:
                    return got, f"field:{title}"

    for att in event.get("attachments", []) or []:
        got = _from_text(att.get("footer"))
        if got:
            return got, "footer"

    return None, ""


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

    # ── Every text surface a BID can appear on ───────────────────────────────
    # The BID must be searched across the ENTIRE message. Guests write it in the
    # prose, in the headline, or in any attachment field — not just
    # attachment.text. Only text + attachment.text + attachment.fallback used to
    # be scanned, so a BID arriving via blocks (which is where the review body
    # actually comes from), via the headline, or in a field not titled
    # "reference" was never found.
    def _all_text_surfaces() -> str:
        parts = [text]
        for b in blocks:
            t = (b.get("text") or {}).get("text", "")
            if t:
                parts.append(t)
            for f in b.get("fields", []) or []:
                if f.get("text"):
                    parts.append(str(f["text"]))
        for att in attachments:
            for k in ("pretext", "title", "text", "fallback", "footer"):
                if att.get(k):
                    parts.append(str(att[k]))
            for f in att.get("fields", []) or []:
                for k in ("title", "value"):
                    if f.get(k):
                        parts.append(str(f[k]))
        return "\n".join(parts)

    full_text = _all_text_surfaces()

    # ── Booking ID: attachment fields → full-text regex fallback ─────────────
    from server.taxonomy import BID_REGEX
    booking_id = None
    # Trustpilot's "Reference number" field is free text and guests routinely
    # put something other than a booking id in it — most usefully the venue
    # ("Salt mines Krakow"). Previously a non-BID value was logged and dropped,
    # discarding what is often the only venue reference in the entire review.
    # Keep every field value that is not simply the BID as review context.
    field_context = []
    for att in attachments:
        for field in att.get("fields", []) or []:
            raw_val = str(field.get("value") or "").strip()
            if not raw_val:
                continue
            title     = (field.get("title") or "").strip()
            bid_match = re.search(BID_REGEX, raw_val)
            if "reference" in title.lower() and bid_match and not booking_id:
                booking_id = bid_match.group(0)
                log.info(f"[extract] attachment field: booking id {booking_id} (raw: {raw_val!r})")
            # Not a bare booking id → carry the text through as context.
            if not re.fullmatch(r"\s*\d{7,12}\s*", raw_val):
                entry = f"{title}: {raw_val}" if title else raw_val
                if entry not in field_context:
                    field_context.append(entry)
                    log.info(f"[extract] attachment field kept as context: {entry!r}")
    if not booking_id:
        m = re.search(BID_REGEX, full_text)
        if m:
            booking_id = m.group(0)
            log.info(f"[extract] regex: booking id {booking_id} from full message text")
        else:
            log.info("[extract] no BID on any text surface — indicators will drive "
                     "the Zendesk search")

    # ── Author ───────────────────────────────────────────────────────────────
    # Priority: bold text in blocks → attachment.author_name → Unknown
    author = None
    body = text
    # Accumulate EVERY section block, do not overwrite. `body = t` kept only the
    # last section, so a review Trustpilot split across blocks lost everything
    # before the final one - and the block carrying the author was dropped
    # whole, taking the sentences beside the name with it. A review that arrives
    # truncated can never be matched or RCA'd on what is missing.
    block_parts: list[str] = []
    for b in blocks:
        if b.get("type") == "section":
            t = (b.get("text") or {}).get("text", "")
            if not t:
                continue
            if not author and "*" in t:
                parts = t.split("*")
                if len(parts) >= 2:
                    author = parts[1].strip()
                # Keep the rest of this block: the name is a prefix, not the
                # whole block, and the remainder is review text.
                rest = t.replace(f"*{author}*", "", 1).strip() if author else t
                if rest:
                    block_parts.append(rest)
            else:
                block_parts.append(t)
    if block_parts:
        joined = "\n\n".join(dict.fromkeys(p.strip() for p in block_parts if p.strip()))
        # Slack's message `text` is usually a flattened copy of the blocks;
        # prefer the richer reconstruction, but never end up with less.
        body = joined if len(joined) >= len(body or "") else body
    if not author:
        for att in attachments:
            if att.get("author_name"):
                author = att["author_name"].strip()
                break

    # ── Body ─────────────────────────────────────────────────────────────────
    # Trustpilot puts the review HEADLINE in attachment.title and the review
    # text in attachment.text. The headline routinely carries the only venue
    # reference in the whole review ("Overpriced Acropolis tickets"), and it was
    # previously captured ONLY when the body came back empty — so whenever the
    # Slack blocks produced any text at all, the headline was silently dropped
    # and the matcher never saw the venue.
    att_title = ""
    for att in attachments:
        if (att.get("title") or "").strip():
            att_title = att["title"].strip()
            break

    # Fall back to attachment.text when message text/blocks are empty
    if not body or not body.strip():
        for att in attachments:
            att_text = (att.get("text") or "").strip()
            if att_text:
                body = att_text
                break

    # Always prepend the headline unless the body already opens with it.
    if att_title and att_title.lower() not in (body or "").lower():
        body = f"{att_title}\n\n{body}".strip() if body else att_title

    # Append non-BID attachment fields. These are part of the review card the
    # guest filled in, and they carry matching signal the prose often does not.
    for entry in field_context:
        if entry.split(": ", 1)[-1].lower() not in (body or "").lower():
            body = f"{body}\n{entry}".strip() if body else entry

    _pub_at, _pub_src = review_published_at(event)
    return {
        "slack_ts":         event["ts"],
        "slack_channel":    event["channel"],
        "rating":           stars or 1,
        # NOT "en". THIS FIELD USED TO BE HARD-CODED TO "en" ON EVERY REVIEW,
        # and nothing ever updated it, so `language == "en"` meant "nobody
        # looked" while reading exactly like "this review is English". The
        # card believed it: an Italian review whose inbound translation failed
        # drew ONE English response box and no way to reach the guest's
        # language, and the reply went out in English.
        #
        # None is the honest value at ingest — the payload carries no language
        # and we have not looked yet. `reply_language.resolve_language()` fills
        # it in from the guest's own words, and stores the NAME ("Italian",
        # "English"), so a value that is present is a value somebody detected.
        "language":         None,
        "author":           author or "Unknown",
        "body_original":    body,
        "reference_number": booking_id,
        # WHEN THE GUEST POSTED IT, and where we got that from. Empty source
        # means the payload carried no publish date — which the ingest must
        # then SAY, rather than stamping the Slack arrival time and labelling
        # it "Review date".
        "published_at":        _pub_at,
        "published_at_source": _pub_src,
        "raw_payload":      event,
    }


# A Zendesk ticket id and a Headout booking id are both 7-12 digits and occupy
# the same numeric range, so a bare number in a Slack message is ambiguous. The
# message itself carries the disambiguating context, which is self-contained —
# it does not depend on what this review's Zendesk search happened to return.
_BMS_BID_RE   = re.compile(r"/bms/booking/(\d{7,12})")
_ZD_URL_RE    = re.compile(r"zendesk\.com/agent/tickets/(\d{5,12})", re.I)
_ZD_INLINE_RE = re.compile(r"(?:\bzd[-\s#]*|\bticket[-\s#]*|#)(\d{5,12})", re.I)


def _bids_from_text(body: str) -> list[str]:
    """
    Booking ids in a Slack message, with ticket references removed.

    Precedence:
      1. A number inside a BMS booking URL is definitively a booking id — take
         those and stop, they need no guessing.
      2. Otherwise harvest bare numbers, minus anything the message identifies
         as a ticket: a Zendesk ticket URL, or "ticket 123" / "ZD-123" / "#123".
    Whatever survives is still BigQuery-verified downstream.
    """
    from server.taxonomy import BID_REGEX
    body = body or ""

    explicit = _BMS_BID_RE.findall(body)
    if explicit:
        return list(dict.fromkeys(explicit))

    ticket_refs = set(_ZD_URL_RE.findall(body)) | set(_ZD_INLINE_RE.findall(body))
    return [n for n in dict.fromkeys(re.findall(BID_REGEX, body))
            if n not in ticket_refs]


# Marks a row that is NOT a Slack message but a report that the search could
# not run. Rows carrying this key must never be counted as mentions.
SEARCH_UNAVAILABLE = "search_unavailable"


def _unavailable(reason: str, text: str) -> list[dict]:
    """
    One sentinel row, shaped like a mention so no caller has to special-case it.

    The dashboard renders whatever list it is handed, so the sentinel carries
    the same keys a real match does - channel/user/ts/text/permalink - and puts
    the explanation in `text`. A caller that wants to branch reads
    SEARCH_UNAVAILABLE; a caller that just renders shows the explanation
    instead of silently asserting there were no mentions.
    """
    return [{
        SEARCH_UNAVAILABLE: True,
        "reason":    reason,
        "channel":   "",
        "user":      "",
        "ts":        "",
        "text":      text,
        "permalink": "",
    }]


# "Slack search (WIP): missing_scope." was on a real card. Two faults in six
# words: "(WIP)" says the FEATURE is unfinished when the code is actually a
# permission the workspace has not granted, and the bare API code says nothing
# about what would fix it. An error should name what would work.
_SEARCH_ERRORS = {
    "missing_scope":
        "the Slack user token is missing the search:read scope — add it under "
        "OAuth & Permissions in the Slack app, then reinstall the app to the "
        "workspace so the new scope takes effect",
    "not_allowed_token_type":
        "a BOT token was supplied where a user token is needed — Slack search "
        "is only available to user tokens (xoxp-), never bot tokens (xoxb-)",
    "invalid_auth":
        "the Slack user token was rejected — it has been revoked or rotated, "
        "and needs reissuing",
    "token_revoked":
        "the Slack user token has been revoked and needs reissuing",
    "account_inactive":
        "the Slack account behind the user token is deactivated",
    "ratelimited":
        "Slack rate-limited the search — this one is temporary, try again",
}


def _search_error_sentence(code: str) -> str:
    """One line on the card. The fix belongs in the log, not the dashboard.

    The full guidance was on screen and it was too much: a paragraph about
    OAuth scopes in a card about a guest's refund. The card says what did not
    happen and names the cause; whoever is fixing Slack reads the log.
    """
    known = _SEARCH_ERRORS.get(code)
    log.warning(f"[slack] search unavailable ({code}): "
                f"{known or 'no guidance for this code in this build'}")
    return f"Slack was not searched — {code}."


def is_search_unavailable(mentions: list[dict] | None) -> bool:
    """True when the list is a sentinel, i.e. Slack was never actually searched."""
    return bool(mentions) and bool((mentions[0] or {}).get(SEARCH_UNAVAILABLE))


# ── why a post did not go, and what to do about it ─────────────────────────
#
# `post_to_thread` returned None for every outcome: rate-limited, wrong
# channel, revoked token, message too long — and for MOCK_MODE, where nothing
# was wrong at all. The caller turned all of that into one sentence, "Slack
# rejected the post - check the bot's channel membership and scopes", which
# named the one cause it happened to be written for and was wrong about the
# rest. Nobody can act on it: it does not say whether trying again would work.
#
# So each code carries two things — a plain sentence, and a VERDICT, which is
# the only part that changes what the reader does next:
#
#   retry   — temporary. The same click will probably work.
#   fix     — a person must change something (invite the app, add a scope,
#             shorten the post), and then the same click will work.
#   manual  — this post is not going through this route. Copy it into the
#             thread by hand.
#
# MOCK_MODE and no-token are deliberately NOT in here. They are not failures,
# they are the system doing as configured, and giving them a rejection
# sentence would be the inverse bug — a healthy run reported as broken.
POST_ERRORS = {
    "not_in_channel":
        ("the app is not a member of this channel", "fix",
         "invite it to the channel, then post again"),
    "is_archived":
        ("the channel is archived, so nothing can be posted to it", "manual",
         "un-archive the channel or copy the post somewhere else"),
    "channel_not_found":
        ("Slack does not recognise this channel — it may be private, or "
         "renamed since the review arrived", "manual",
         "copy the post into the thread by hand"),
    "thread_not_found":
        ("the thread this review came from no longer exists", "manual",
         "copy the post into the thread by hand"),
    "msg_too_long":
        ("the post is longer than Slack accepts", "fix",
         "shorten it in the editor above, then post again"),
    "missing_scope":
        ("the Slack token is missing the scope needed to post", "manual",
         "add chat:write under OAuth & Permissions and reinstall the app"),
    "invalid_auth":
        ("the Slack token was rejected — revoked or rotated", "manual",
         "reissue the token"),
    "token_revoked":
        ("the Slack token has been revoked", "manual", "reissue the token"),
    "account_inactive":
        ("the Slack account behind the token is deactivated", "manual",
         "reissue the token from an active account"),
    "restricted_action":
        ("workspace settings do not allow this app to post here", "manual",
         "copy the post into the thread by hand"),
    "ratelimited":
        ("Slack rate-limited the post — this one is temporary", "retry",
         "wait a moment and post again"),
}

# What the last post_to_thread did, whether it worked or not. Read by the API
# so the card can say which of these happened. A dict rather than a raised
# exception because post_to_thread has non-failure Nones (MOCK_MODE) that must
# stay distinguishable from a rejection.
last_post_failure: dict = {"code": "", "why": "", "verdict": "", "next": ""}


def post_failure_sentence(code: str) -> dict:
    """One rejection, described so the reader knows whether to click again.

    An unknown code is NOT flattened into a known one. It keeps its own text
    and gets the cautious verdict — "we do not know if retrying helps" beats
    inventing a confident answer about a code this build has never seen.
    """
    known = POST_ERRORS.get(code)
    if known:
        why, verdict, nxt = known
    else:
        why = f"Slack refused the post and gave the reason “{code}”"
        verdict = "manual"
        nxt = ("this build has no guidance for that code — copy the post into "
               "the thread by hand, and send the code to whoever maintains this")
    return {"code": code, "why": why, "verdict": verdict, "next": nxt}


def _api_error_code(e: Exception) -> str:
    """
    The Slack error string ("missing_scope", "not_allowed_token_type", ...).

    SlackApiError hides the useful part inside .response; str(e) is a paragraph
    of SDK prose. The code is what tells an operator which scope to add, so it
    is what gets surfaced.
    """
    resp = getattr(e, "response", None)
    try:
        return (resp.get("error") if resp is not None else None) or type(e).__name__
    except Exception:
        return type(e).__name__


async def search_mentions(bid: str, limit: int = 20) -> list[dict]:
    """
    Find every Slack message mentioning a booking id, workspace-wide.

    Part of the RCA, not the matching step: once the booking is known, ops
    channels frequently carry escalations, SP chases and manual interventions
    for that BID which never appear in Zendesk. Those pings are context the RCA
    needs, so they are surfaced on the dashboard rather than left buried.

    Not restricted to ORM channels — any channel the user token can see.
    Requires a USER token with search:read.

    Three outcomes, and the caller can tell them apart:
      []                          - searched, matched nothing (an earned claim)
      [{search_unavailable: ...}] - never searched: no user token, or the API
                                    refused (see is_search_unavailable)
      [{channel: ...}, ...]       - real matches
    """
    # A sentinel row distinguishes "searched, found nothing" from "could not
    # search". Reporting an unavailable search as "no mentions" is a claim we
    # have not earned — search.messages needs a USER token with search:read and
    # bot tokens cannot call it at all. This used to return a bare [] for all
    # three cases, so a workspace nobody had ever queried showed the dashboard's
    # "No Slack messages found for this booking" with nothing behind it.
    if not bid:
        return []
    if not _user:
        log.info("[slack] search_mentions: no user token (search:read) — cannot search")
        # Full explanation lives in tools/test_slack_search.py's own output, not
        # here - this text renders straight onto the dashboard, and a paragraph
        # of setup instructions in a warning strip is the kind of thing that
        # gets flagged as unreadable right after it gets flagged as unsearched.
        return _unavailable(
            "no_user_token",
            "Slack was not searched — SLACK_USER_TOKEN is not set. Search needs "
            "a USER token with search:read; a bot token cannot do it.")
    try:
        res = _user.search_messages(query=str(bid), count=limit)
    except Exception as e:
        code = _api_error_code(e)
        log.warning(f"[slack] search_mentions {bid} failed: {code}: {e}")
        return _unavailable(code, _search_error_sentence(code))
    out = []
    for m in (res.get("messages") or {}).get("matches") or []:
        ch = m.get("channel", {}) or {}
        out.append({
            "channel":   ch.get("name") or ch.get("id") or "",
            "user":      m.get("username") or m.get("user") or "",
            "ts":        m.get("ts") or "",
            "text":      (m.get("text") or "")[:600],
            "permalink": m.get("permalink") or "",
        })
    log.info(f"[slack] search_mentions {bid}: {len(out)} message(s)")
    return out


async def search_bids(terms: list[str], limit: int = 20) -> tuple[list[str], list[dict]]:
    """
    Backup BID source: search Slack for the SAME indicators used everywhere else
    — the guest name and the venue extracted from the review.

    Searches the whole workspace, not just ORM channels: a booking id can be
    pasted into any ops, escalation or SP channel and never reach Zendesk.

    Requires a USER token with search:read; bot tokens cannot call
    search.messages at all. Returns ([], []) when unavailable rather than
    raising, so the caller can treat it as a best-effort extra source.
    """
    from server.taxonomy import BID_REGEX
    if not _user:
        log.info("[slack] search_bids: no user token (search:read) — skipping")
        return [], []
    terms = [str(t).strip() for t in (terms or []) if str(t).strip()]
    if not terms:
        return [], []

    bids, records, seen = [], [], set()
    for term in terms[:3]:
        try:
            res = _user.search_messages(query=term, count=limit)
        except Exception as e:
            log.warning(f"[slack] search_bids {term!r} failed: {e}")
            continue
        for m in (res.get("messages") or {}).get("matches") or []:
            key = f"{m.get('channel', {}).get('id')}/{m.get('ts')}"
            if key in seen:
                continue
            seen.add(key)
            body = m.get("text") or ""
            found = _bids_from_text(body)
            if not found:
                continue
            bids += found
            records.append({
                "ticket_id":    "",
                "subject":      f"Slack {m.get('channel', {}).get('name', '')}",
                "body":         body[:4000],
                "text":         body[:4000],
                "bids":         list(dict.fromkeys(found)),
                "matched_term": term,
                "source":       "slack",
            })

    deduped = list(dict.fromkeys(bids))[:25]
    log.info(f"[slack] search_bids {terms[:3]}: {len(seen)} messages → {len(deduped)} BIDs")
    return deduped, records


async def fetch_message(channel: str, ts: str) -> dict | None:
    """
    Re-fetch a single Slack message by channel + ts.

    Needed because reviews are stored as parsed text: anything the parser of the
    day discarded is gone from the DB, and re-running the pipeline cannot bring
    it back. Pulling the original message lets a re-run re-parse with the
    current parser and recover it.
    """
    client = _bot or _user
    if not client:
        log.info(f"[MOCK] Would fetch {channel}/{ts}")
        return None
    try:
        res = client.conversations_history(
            channel=channel, latest=ts, oldest=ts, inclusive=True, limit=1)
        msgs = res.get("messages") or []
        return msgs[0] if msgs else None
    except Exception as e:
        log.warning(f"[slack] fetch_message {channel}/{ts} failed: {e}")
        return None


async def post_to_thread(channel: str, thread_ts: str, text: str) -> str | None:
    """Post into a review's Slack thread AS THE APP, never as a person.

    THE `as_user` PARAMETER IS GONE, and that is the fix. It defaulted to True,
    which selected `_user` — a WebClient built on SLACK_USER_TOKEN, an `xoxp-`
    token — and Slack attributes anything sent with one to THE HUMAN WHO
    AUTHORISED THE APP. So the RCA and the guest reply went into the thread
    under a colleague's name and face, while flag-to-biz (the one call site
    that passed False) came from the app. Same function, two identities,
    decided by a default nobody was looking at.

    A parameter that can be set wrongly will be. There is no argument now:
    posting is the bot.

    SLACK_USER_TOKEN IS STILL REQUIRED, and not for this. `search_mentions`
    calls `search_messages`, which Slack does not accept a bot token for at
    all. The user token stays for reading; it no longer writes.
    """
    # MOCK_MODE must mean nothing leaves this machine. This checked only
    # whether a client object existed, and the tokens are present in any
    # environment configured for real use - so a run started with
    # MOCK_MODE=true posted to the live Slack API. It happened to fail on
    # demo data with an invalid channel and thread_ts; against a real thread
    # it would have posted, from a run whose whole point was that it could
    # not.
    # Cleared on every attempt. A stale reason from the last failed post read
    # as a fresh rejection of a post that in fact went through.
    last_post_failure.update({"code": "", "why": "", "verdict": "", "next": ""})
    if MOCK_MODE:
        log.info(f"[MOCK] not posting to {channel}/{thread_ts}: {text[:120]}…")
        return None
    if not _bot:
        # NOT THE MOCK SENTENCE. This used to log "[MOCK] Would post…", which
        # is the same line MOCK_MODE prints above — so a production run with no
        # SLACK_BOT_TOKEN posted nothing and read exactly like a dry run that
        # was never meant to post. A missing credential is a fault; say so, and
        # say what fixes it.
        last_post_failure.update({
            "code": "no_bot_token",
            "why": "SLACK_BOT_TOKEN is not set, so there is no app identity to "
                   "post as. Nothing was sent.",
            "verdict": "not posted",
            "next": "Set SLACK_BOT_TOKEN (an xoxb- token) and make sure the app "
                    "is invited to the channel.",
        })
        log.error(f"[slack] NOT posting to {channel}/{thread_ts}: "
                  f"SLACK_BOT_TOKEN is not set")
        return None
    client = _bot
    try:
        res = client.chat_postMessage(
            channel=channel, thread_ts=thread_ts, text=text,
            unfurl_links=False, unfurl_media=False,
        )
        return res.get("ts")
    except Exception as e:
        # WHICH rejection. Without this every cause below shared one sentence,
        # and the sentence named channel membership — so a rate-limit, which
        # clears on its own, sent the reader to check the app's channels.
        code = _api_error_code(e)
        last_post_failure.update(post_failure_sentence(code))
        log.exception(f"Slack post failed ({code}): {e}")
        return None


def format_rca_slack(review, draft) -> str:
    """
    Format the RCA draft into the #team-orm-online-reputation Slack post.
    Structure per Task #13 PART 4 — leadership view: WWR scenario blocks,
    frames, actions, resolution, FLAGGED audit findings only, insights.
    Never includes the guest response copy or the stated issue.
    """
    b   = draft.booking or {}
    div = "_" * 61
    nl  = "\n"

    # 1. Header
    stars = "★" * int(getattr(review, "rating", 0) or 0)
    header = (f"*RCA — @reviewteam*{nl}"
              f"BID {b.get('id', '—')} · {getattr(review, 'author', '') or '—'}"
              f" · {stars or '—'}")

    # An RCA in the v3 shape formats from that shape. This function is what
    # Send posts when the associate has not edited the preview, so if it kept
    # building the legacy layout the posted RCA would differ from the one on
    # screen - and checklist_answers is empty by design now, so the flags
    # section would have silently posted "No flagged checks" on every case.
    if getattr(draft, "rca_v3", None):
        return _format_rca_v3_slack(review, draft, header, div, nl)

    # 2. Classification + scenarios (all applicable, comma-separated)
    l1, l2 = draft.l1 or "—", draft.l2 or "—"
    sub = draft.sub_theme or ""
    scen_all = [s for s in ([draft.primary_scenario] +
                            list(draft.overlay_scenarios or [])) if s]
    cls_line = f"*Issue:* {l1} / {l2}" + (f" / {sub}" if sub else "")
    if scen_all:
        cls_line += f"{nl}*Scenarios:* " + ", ".join(scen_all)

    # 3. What Went Wrong — stacked scenario blocks (fallback: legacy chain)
    wwr_parts = []
    for sblock in (draft.wwr_scenarios or []):
        tag = "PRIMARY" if sblock.get("is_primary") else "overlay"
        wwr_parts.append(
            f"*[{tag}] {sblock.get('scenario_name', '')}*{nl}"
            f"• Accurate? {sblock.get('accuracy', '—')} — {sblock.get('accuracy_explanation', '')}{nl}"
            f"• Why: {sblock.get('why', '')}{nl}"
            f"• Fix: {sblock.get('fix', '')}")
    if not wwr_parts:
        for st in (draft.wwr_chain or []):
            wwr_parts.append(f"{st.get('step', '')}. *{st.get('what', '')}* — {st.get('why', '')}")
    wwr_text = (nl + nl).join(wwr_parts) if wwr_parts else "—"

    # 4/5. Interaction frames
    def _frames(frames, label):
        if not frames:
            return ""
        lines = [f"*{label}:*"]
        for fr in frames:
            t = fr.get("time", "?")
            said = _zd.guest_words(fr) or fr.get("summary") or ""
            did  = fr.get("weDid") or ""
            gap  = fr.get("gap") or ""
            row = f"• {t} — {said}"
            if did: row += f" | we: {did}"
            if gap: row += f" | ⚠ {gap}"
            lines.append(row)
        return nl.join(lines)
    # ONLY EXCHANGES A PERSON TOOK PART IN. This passed every frame through,
    # so the post carried booking dumps and vendor-API rows under a heading
    # that says "Customer / CE interactions" — "Booking details submitted to
    # vendor API: 2 Adults, 2 Children..." rendered as a conversation with the
    # guest. The card has filtered these out for a while and says how many it
    # moved; the post is the OTHER composer for the same section and never
    # learned. Same predicate, so the two cannot disagree about what a
    # conversation is.
    #
    # The moved ones are COUNTED, not dropped in silence: a section that
    # quietly shrinks reads as a guest nobody spoke to, which is the opposite
    # of what happened.
    from server.services.zendesk import (split_contact_frames as _split,
                                         moved_frames_note as _moved_note_fn)
    _convos, _moved = _split(draft.support_interaction_frames or [])
    support_text = _frames(_convos, "Customer / CE interactions")
    _mv = _moved_note_fn(_moved)
    if _mv and support_text:
        support_text += nl + f"• ({_mv})"
    elif _mv:
        # NO CONVERSATIONS AT ALL. `_frames` returns "" for an empty list, so
        # appending the note on its own left a bare parenthetical floating
        # between two rules with no heading above it. The heading has to come
        # with it, and the sentence has to say the guest was not spoken to —
        # "N system events moved" alone reads as a section that lost its
        # contents, not as a booking nobody contacted them about.
        support_text = (f"*Customer / CE interactions:*{nl}"
                        f"• No conversation with the guest on this booking — "
                        f"{_mv}, so nobody spoke to them")
    sp_text      = _frames(draft.sp_interaction_frames or [], "SP interactions")

    # 6. Area of improvement
    aoi = draft.area_of_improving or []
    aoi_text = nl.join(f"• {a}" for a in aoi) if aoi else "—"

    # 7. Actions taken — flattened, owner in parens
    at = draft.actions_taken or {}
    action_lines = []
    for team in _action_team_keys(at):
        for item in (at.get(team) or []):
            txt = item if isinstance(item, str) else (
                item.get("context") or item.get("with") or "")
            if txt:
                action_lines.append(f"• {txt} ({_action_team_label(team)})")
    actions_text = nl.join(action_lines) if action_lines else "—"

    # 8. Resolution
    resolution_text = draft.resolution or "—"

    # 9. Audit findings — FLAGGED checklist items only
    flagged = [c for c in (draft.checklist_answers or [])
               if str(c.get("answer", "")).strip().lower() == "no"]
    audit_lines = []
    for c in flagged:
        zd = c.get("zd_ref") or c.get("zd_id") or ""
        ev = c.get("evidence") or ""
        audit_lines.append(
            f"• [{str(c.get('section', '')).upper()}] {c.get('item') or c.get('check', '')}"
            + (f" — {ev}" if ev else "") + (f" ({zd})" if zd else ""))
    audit_text = nl.join(audit_lines) if audit_lines else "No flagged checks"

    # 10. Insights tiles
    ins = draft.insights or {}
    # These numbers are recomputed and overwritten for whichever window the
    # associate last clicked - 7d, 30d or 90d - so the "(30d)" that used to be
    # written here labelled 7d counts as 30d ones and leadership read them as
    # such. The keys keep their _30d spelling because the dashboard reads those
    # names, so the label has to come from the window the data carries. Rows
    # cached before the picker existed carry no window at all: an unlabelled
    # number is honest, a number labelled 30d on a guess is not.
    wd = ins.get("_window_days")
    if isinstance(wd, int) and wd > 0:
        win = f" ({wd}d)"
    elif ins.get("_window_label"):
        win = f" ({ins['_window_label']})"
    else:
        win = ""
    ins_parts = []
    if ins.get("similar_reviews_30d") is not None:
        ins_parts.append(f"*Similar reviews{win}:* {ins.get('similar_reviews_30d')}")
    if ins.get("similar_support_queries_30d") is not None:
        ins_parts.append(f"*Similar support queries{win}:* {ins.get('similar_support_queries_30d')}")
    r30 = ins.get("rating_30d") or {}
    if r30.get("avg") is not None:
        ins_parts.append(f"*Avg rating{win}:* {r30.get('avg')} ({r30.get('n', 0)} ratings)")
    if ins.get("vidCompletionRate"):
        ins_parts.append(f"*Completion rate (VID):* {ins.get('vidCompletionRate')}")
    if ins.get("sameDaySameVidIssues"):
        ins_parts.append(f"*Same-day same-VID issues:* {ins.get('sameDaySameVidIssues')}")
    insights_text = nl.join(ins_parts) if ins_parts else "—"

    return f"""{header}
{cls_line}
{div}
*What went wrong*

{wwr_text}
{div}
{support_text or "*Customer / CE interactions:* —"}
{div}
{sp_text or "*SP interactions:* —"}
{div}
*Area of improvement*

{aoi_text}
{div}
*Actions taken*

{actions_text}
{div}
*Resolution*

{resolution_text}
{div}
*Audit findings (flagged)*

{audit_text}
{div}
*Experience insights*

{insights_text}"""


def _points(v) -> list:
    """Pointer fields are lists in the v3 shape; older values may be a single
    string. Join-into-a-string anywhere here would print 'a,b,c' as prose.

    An area-of-improvement point is an object now - {point, from, source} - so
    that the model cannot write one it cannot derive. Slack gets the pointer
    itself; the provenance is the constraint that produced it, not something a
    thread reader needs. Printing the dict would put a Python repr in the post.
    """
    if isinstance(v, dict):
        v = [v]
    if isinstance(v, (list, tuple)):
        out = []
        for x in v:
            t = str((x.get("point") or x.get("text") or "") if isinstance(x, dict)
                    else (x or "")).strip()
            if t:
                out.append(t)
        return out
    return [str(v).strip()] if str(v or "").strip() else []


# The nine teams, and any key an older draft still holds. Hard-coding the five
# old tab names here is what would silently drop every row on a card written
# under the new vocabulary: the loop would find no key and post "—", which is
# the same thing it posts when nothing was raised.
def _action_team_keys(at) -> list:
    from server.checklist import ACTION_TEAMS
    extra = [k for k in (at or {}) if k not in ACTION_TEAMS]
    return list(ACTION_TEAMS) + sorted(extra)


def _action_team_label(key: str) -> str:
    from server.taxonomy import ACTION_TABS
    row = ACTION_TABS.get(key)
    return row["label"] if row else str(key).upper()


from server.services.rca_v4_validate import zd_key as _zd_key

_CONTACT_GAP_MIN = 30


def _note_for(key: str, notes):
    """The model's note for a ticket, or None."""
    if not key or not isinstance(notes, list):
        return None
    for n in notes:
        if isinstance(n, dict) and _zd_key(n.get("zd_ref")) == key:
            return n
    return None


def _contacts(frames):
    """Frames grouped into contacts. Returns [(key, [frame, ...]), ...].

    The two columns are deliberately different granularities: the Events
    timeline is per event, Guest ↔ support is per contact. Rendering one row
    per frame conflates them, and the "N contacts" count then reports events -
    which reads as inflated, and a count nobody trusts is worse than none.

    Grouped by ticket. Frames with no ticket id fall back to a time window,
    because consecutive messages minutes apart are one exchange however they
    were logged; without the fallback each would become its own contact, which
    is the same inflation by another route.
    """
    from datetime import datetime as _dt
    out, last_at = [], None
    for fr in (frames or []):
        key = _zd_key(fr.get("ticket_id"))
        if key:
            for k, group in out:
                if k == key:
                    group.append(fr)
                    break
            else:
                out.append((key, [fr]))
            last_at = None            # a ticketed frame does not extend a window
            continue
        at = None
        raw = fr.get("time_sort") or ""
        try:
            at = _dt.fromisoformat(str(raw).replace("Z", "+00:00"))
        except Exception:
            at = None
        near = (at and last_at and abs((at - last_at).total_seconds()) <= _CONTACT_GAP_MIN * 60)
        if near and out and not out[-1][0]:
            out[-1][1].append(fr)
        else:
            out.append(("", [fr]))
        last_at = at
    return out


# The seven the card's Booking Details panel shows, in its order, each with
# every key it has been stored under. `vendorName` / `vendor_name` and
# `fulfilmentType` / `fulfilment_type` both occur: the warehouse and the
# BigQuery enrichment spell them differently, and the client already reads
# both. Reading one would blank the field on half the drafts.
_BOOKING_DETAIL_ROWS = (
    ("Booking ID",       ("id", "bid", "booking_id")),
    ("Experience",       ("experienceName", "experience_name", "experience")),
    ("TID name",         ("tid_name", "tidName", "tour_name")),
    ("TGID / TID",       ("tgid",)),
    ("Vendor ID",        ("vid", "vendor_id", "vendorId")),
    ("Vendor name",      ("vendorName", "vendor_name", "partner")),
    ("Fulfilment type",  ("fulfilmentType", "fulfilment_type",
                          "fulfillment_type")),
)


def _booking_field(bk: dict, keys) -> str:
    for k in keys:
        v = str((bk or {}).get(k) or "").strip()
        if v:
            return v
    return ""


def _booking_details_lines(draft, nl: str) -> str:
    """The booking, as the post's opening section — or "" when there is none.

    NOTHING AT ALL means no booking was matched, and the post already says
    that elsewhere; a section of seven dashes would be a wall saying it again.
    But a booking WITH gaps prints the gaps: "Vendor ID — not recorded" is a
    fact about this booking, and dropping the row makes it indistinguishable
    from a booking that has one.
    """
    bk = getattr(draft, "booking", None) or {}
    if not _booking_field(bk, ("id", "bid", "booking_id")):
        return ""
    rows = []
    for label, keys in _BOOKING_DETAIL_ROWS:
        val = _booking_field(bk, keys)
        if label == "TGID / TID":
            # ONE ROW, TWO IDS, exactly as the panel shows it. They are read
            # together — a TGID with no TID is a product with no ticket type —
            # so splitting them across two rows loses the pairing.
            _tid = _booking_field(bk, ("tid",))
            val = f"{val or '—'} / {_tid or '—'}" if (val or _tid) else ""
        rows.append(f"• {label}: {val or '— not recorded'}")
    return nl.join(rows)


def _contact_gaps(group) -> list:
    """The distinct gap labels across a contact's frames, in order.

    A gap is recorded per FRAME ("Wrong policy applied" on the message where it
    happened) but the reader is being told about a CONTACT, so the same label on
    four messages is one fact, not four. Deduped rather than counted: the count
    of messages carrying a label says more about how chatty the exchange was
    than about the failure.
    """
    out = []
    for fr in group:
        g = (fr.get("gap") or "").strip()
        if g and g not in out:
            out.append(g)
    return out


def _contact_body(group, note, nl) -> list:
    """The lines under a contact's head: the account of it, then its failures.

    ONE ENTRY PER CONTACT, NOT PER MESSAGE. This used to render the head and
    then every frame in the group beneath it, so a nine-message chat became ten
    lines saying the same thing at nine timestamps, and the post read as a
    transcript rather than as a record of what happened. The model's `detail` is
    written about the whole exchange — it quotes the guest and the agent — which
    is what a reader of this section needs.

    A contact WITH NO NOTE says so and then falls back to its frames. That is
    the honest version: a summarised contact and an unsummarised one must not
    look identical, or a failed zd_ref join reads as a terse model.
    """
    lines = []
    detail = ((note or {}).get("detail") or "").strip()
    if detail:
        for ln in detail.split("\n"):
            if ln.strip():
                lines.append(f"   {ln.strip()}")
    elif note is None:
        # No model note joined to this contact. Say it, then give the raw
        # messages — unlabelled, they would look like a chosen summary.
        n_ev = len(group)
        lines.append(f"   (no summary generated for this contact \u2014 "
                     f"{n_ev} raw event{'s' if n_ev != 1 else ''} below)")
        for fr in group:
            said = _zd.guest_words(fr)
            did  = (fr.get("weDid") or "").strip()
            if not said and not did:
                continue
            ev = f"   - {fr.get('time') or '?'}"
            if said:
                ev += f" \u2014 {said}"
            if did:
                ev += f" | we: {did}"
            lines.append(ev)
    for gap in _contact_gaps(group):
        lines.append(f"   \u26a0 {gap}")
    if note and (note.get("ce_miss") or "").strip():
        lines.append(f"   \u26a0 CE miss: {note['ce_miss'].strip()}")
    return lines


def contacts_section(draft, v3, nl) -> str:
    """The "Customer / CE interactions" body \u2014 one numbered entry per contact.

    THE ONE COMPOSER. The dashboard renders this string verbatim (served as
    `contacts_slack_text`) rather than building its own, because it did build
    its own and read `rca_v3["support_interaction"]` \u2014 a field the split to
    `support_interaction_notes` had removed. Undefined is falsy, so the preview
    fell through to a raw per-frame dump with no conversation filter, and the
    booking thread went out as something the guest said. Two composers for one
    block, and the quieter one was wrong.
    """
    si_notes = (v3 or {}).get("support_interaction_notes")
    if si_notes is None:
        si_notes = (v3 or {}).get("support_interaction")   # pre-split drafts
    # §4 conversations only. The booking thread, the API posts and the review
    # are machinery, and each one counted here raised the contact count on a
    # post leadership reads as "this guest was handled". They are on the
    # timelines already; what this section owes the reader is the fact that
    # they moved, which the heading carries - a filtered list and a guest who
    # never wrote in must not read the same.
    from server.services.zendesk import split_contact_frames, moved_frames_note
    _convo, _moved = split_contact_frames(
        getattr(draft, "support_interaction_frames", None))
    rows, used = [], set()
    n = 0
    for n, (key, group) in enumerate(_contacts(_convo), 1):
        note = _note_for(key, si_notes)
        if note:
            used.add(key)
        first = group[0]
        ch = (first.get("thread") or "").strip()
        # The contact's own line: what this exchange was, in one line. The
        # model's summary is about the contact; a frame's guestSaid is about
        # one message, so it is the fallback rather than the other way round.
        summary = ((note or {}).get("summary") or _zd.guest_words(first)).strip()
        head = f"\u2022 {n:02d}. {first.get('time') or '?'}"
        if ch:
            head += f" \u00b7 {ch}"
        if summary:
            head += f" \u2014 {summary}"
        if key:
            head += f" (ZD-{key})"
        if len(group) > 1:
            head += f" [{len(group)} events]"
        rows.append(head)
        rows.extend(_contact_body(group, note, nl))
    # A contact the model reports and Zendesk has no frame for still renders,
    # marked unverified. Either the guest reached us off Zendesk or the model
    # invented a contact; both are worth seeing, neither worth hiding. A note
    # carrying a zd_ref that matched nothing is the more serious of the two -
    # that is a failed join, and the pipeline records it in the trail.
    for note in (si_notes if isinstance(si_notes, list) else []):
        if not isinstance(note, dict):
            continue
        nkey = _zd_key(note.get("zd_ref"))
        if nkey and nkey in used:
            continue
        n += 1
        ch = note.get("channel") or ""
        why = "unmatched ZD reference" if nkey else "guest's account, unverified"
        head = f"\u2022 {n:02d}. {note.get('time') or '?'}"
        if ch:
            head += f" \u00b7 {ch}"
        head += f" \u2014 {note.get('summary') or ''}"
        if nkey:
            head += f" (ZD-{nkey})"
        rows.append(head + f" ({why})")
        detail = (note.get("detail") or "").strip()
        for ln in detail.split("\n") if detail else []:
            if ln.strip():
                rows.append(f"   {ln.strip()}")
        if note.get("ce_miss"):
            rows.append(f"   \u26a0 CE miss: {note['ce_miss']}")
    _moved_note = moved_frames_note(_moved)
    if rows:
        return nl.join(rows + ([f"\u2022 ({_moved_note})"] if _moved_note else []))
    return ("No conversation with the guest on this booking \u2014 "
            f"{_moved_note}, so nobody spoke to them"
            if _moved_note
            else "No guest contact found on this booking")


def _format_rca_v3_slack(review, draft, header, div, nl) -> str:
    """The v3 layout, matching the dashboard preview section for section.
    Kept deliberately close to _genSlackText in client/index.html: the two
    must agree, because either can be what lands in the thread."""
    v3 = draft.rca_v3 or {}
    sections: list[tuple[str, str]] = []

    l1, l2 = draft.l1 or "—", draft.l2 or "—"
    sub = draft.sub_theme or ""
    scen_all = [s for s in ([draft.primary_scenario] +
                            list(draft.overlay_scenarios or [])) if s]
    cls_line = f"*Issue:* {l1} / {l2}" + (f" / {sub}" if sub else "")
    if scen_all:
        cls_line += f"{nl}*Scenarios:* " + ", ".join(scen_all)

    # ONE composer. The dashboard renders this exact string (served as
    # `wwr_slack_text` on the draft) rather than building the section again in
    # JavaScript. Two renderers for one section is how "Fix: [object
    # Object]" reached a real post from the client half while this half was
    # correct — a defect the server could not test because the server was not
    # the thing that was wrong.
    #
    # The five headings are mandated and always appear; everything the card
    # shows alongside them — evidence rows, the guest quote, `pattern`,
    # `backs_claim`, the owner chip, the accuracy note — is deliberately not
    # here. See services/wwr_post.py.
    # BOOKING DETAILS, FIRST. The post named the booking nowhere: a reader in
    # the thread got the analysis and had to open the dashboard to find out
    # which booking, which experience, or which vendor it was about. These are
    # the seven the card's own Booking Details panel shows, in its order.
    #
    # A FIELD THE WAREHOUSE DID NOT RETURN IS NAMED, not skipped. Dropping the
    # row makes a missing vendor id and a booking that never had one read the
    # same, and on a post about a vendor's failure that is the field most worth
    # knowing is absent.
    _bd = _booking_details_lines(draft, nl)
    if _bd:
        sections.append(("Booking details", _bd))

    from server.services.wwr_post import compose as _compose_wwr
    _wwr = _compose_wwr(v3.get("what_went_wrong"))
    if _wwr:
        sections.append(("What went wrong", _wwr))

    # BOOKING LOGS ARE NOT POSTED, by request. `v3["booking_logs"]` is still
    # produced, still stored and still rendered on the card — only this
    # section is gone, so bringing it back is restoring this block and nothing
    # upstream of it.

    flags = v3.get("flags") or []
    sections.append(("Flags", nl.join(
        f"• [{str(f.get('team', 'other')).upper()}] {f.get('flag', '')}"
        + (f" — {f['evidence']}" if f.get("evidence") else "")
        + (f" ({f['zd_ref']})" if f.get("zd_ref") else "")
        for f in flags) if flags else "No flags raised"))

    # Facts and interpretation, merged. The rows come from the pipeline's
    # Zendesk-derived frames - their time, channel and ticket id are verifiable
    # - and the model's summary / detail / ce_miss attach by zd_ref.
    #
    # One row per CONTACT, not per frame. The Events timeline is the per-event
    # view; this is the per-contact one, and the individual events sit under
    # their contact rather than beside it. Rendering one row per frame would
    # make the contact count report events.
    sections.append(("Customer / CE interactions",
                     contacts_section(draft, v3, nl)))

    sp_notes = v3.get("sp_interaction_notes")
    if sp_notes is None:
        sp_notes = v3.get("sp_interaction")               # pre-split drafts
    sp_notes = sp_notes if isinstance(sp_notes, dict) else {}
    sp_frames = draft.sp_interaction_frames or []
    if sp_frames or sp_notes:
        rows = ["\u2022 raised with SP: " + str(sp_notes.get("raised") or "\u2014")]
        recs, used = sp_notes.get("records") or [], set()
        for fr in sp_frames:
            key = _zd_key(fr.get("ticket_id"))
            note = _note_for(key, recs)
            if note:
                used.add(key)
            said = (_zd.guest_words(fr) or (note or {}).get("summary") or "").strip()
            did  = (fr.get("weDid") or "").strip()
            line = f"\u2022 {fr.get('time') or '?'}" + (f" \u2014 {said}" if said else "")
            if did:
                line += f" | back: {did}"
            if key:
                line += f" (ZD-{key})"
            rows.append(line)
        for rec in (recs if isinstance(recs, list) else []):
            if not isinstance(rec, dict) or _zd_key(rec.get("zd_ref")) in used:
                continue
            t = f"{rec.get('time')} \u2014 " if rec.get("time") else ""
            rows.append(f"\u2022 {t}{rec.get('summary') or ''} (unverified)")
        if len(rows) == 1:
            rows += [f"\u2022 {d}" for d in _points(sp_notes.get("detail"))]
            if not _points(sp_notes.get("detail")) and sp_notes.get("reason_if_not"):
                rows.append(f"\u2022 {sp_notes['reason_if_not']}")
        sections.append(("SP interaction", nl.join(rows)))

    aoi = _points(v3.get("area_of_improving")) or _points(draft.area_of_improving)
    sections.append(("Area of improvement",
                     nl.join(f"• {a}" for a in aoi) if aoi else "—"))

    at = draft.actions_taken or {}
    al = []
    for team in _action_team_keys(at):
        for item in (at.get(team) or []):
            txt = item if isinstance(item, str) else (
                item.get("context") or item.get("with") or "")
            if txt:
                al.append(f"• {txt} ({_action_team_label(team)})")
    sections.append(("Actions taken", nl.join(al) if al else "—"))
    sections.append(("Resolution", draft.resolution or "—"))

    td = v3.get("takedown") or {}
    if td:
        # New shape is one word; old drafts carry {recommended, reason}.
        verdict = td.get("verdict") or ("Yes" if td.get("recommended") else "No")
        sections.append(("Review takedown", f"• {verdict}"))

    # WAS THE PRESCRIBED PATH TAKEN. Only meaningful where the guest contacted
    # support BEFORE the review, which `dss_check` decides from the timeline —
    # so null here is "the check did not apply", NOT a pass and NOT a miss,
    # and it gets a sentence rather than a blank for exactly that reason.
    _followed = (v3.get("dss") or {}).get("followed")
    _FOLLOWED_TEXT = {
        "followed":      "Yes — the prescribed path was open and we took it",
        "not_followed":  "No — the path was open and we did not take it",
        "unestablished": "Unestablished — the guest wrote in, but the record "
                         "does not show what we did",
    }
    sections.append(("DSS followed", "• " + _FOLLOWED_TEXT.get(
        _followed,
        "Not applicable — no path was open on this case, so the check does "
        "not apply")))

    ins = draft.insights or {}
    wd = ins.get("_window_days")
    win = f"{wd}d" if isinstance(wd, int) and wd > 0 else (ins.get("_window_label") or "")
    rows = []
    r30 = ins.get("rating_30d") or {}
    if r30.get("avg") is not None:
        rows.append(("Avg rating", f"{r30.get('avg')} ({r30.get('n', 0)} ratings)"))
    if ins.get("vidCompletionRate"):
        rows.append(("Completion rate", str(ins["vidCompletionRate"])))
    if ins.get("similar_reviews_30d") is not None:
        rows.append(("Similar reviews", str(ins["similar_reviews_30d"])))
    if ins.get("similar_support_queries_30d") is not None:
        rows.append(("Similar queries", str(ins["similar_support_queries_30d"])))
    if ins.get("sameDaySameVidIssues"):
        rows.append(("Same-day issues", str(ins["sameDaySameVidIssues"])))
    if rows:
        table = nl.join(f"{k:<17}{v}" for k, v in rows)
        if win:
            table += f"{nl}window: {win}"
        sections.append(("Experience insights", "```" + nl + table + "```"))

    out = [header, cls_line]
    for label, body in sections:
        if not body:
            continue
        out += [div, f"*{label}*", "", body]
    return nl.join(out)
