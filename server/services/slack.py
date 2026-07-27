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


async def search_bids(terms: list[str], limit: int = 20) -> tuple[list[str], list[dict]]:
    """
    Backup BID source: search Slack message history for the guest name / venue.

    Prior ORM threads routinely carry a booking id that never reached Zendesk —
    an associate pasting a BID into a thread, an escalation, a manual note. When
    Zendesk yields nothing this is the next place to look.

    Requires a USER token with search:read; bot tokens cannot call search.messages
    at all. Returns ([], []) when unavailable rather than raising, so the caller
    can treat it as a best-effort extra source.
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
            found = [n for n in re.findall(BID_REGEX, body)]
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
            said = fr.get("guestSaid") or fr.get("summary") or ""
            did  = fr.get("weDid") or ""
            gap  = fr.get("gap") or ""
            row = f"• {t} — {said}"
            if did: row += f" | we: {did}"
            if gap: row += f" | ⚠ {gap}"
            lines.append(row)
        return nl.join(lines)
    support_text = _frames(draft.support_interaction_frames or [], "Customer / CE interactions")
    sp_text      = _frames(draft.sp_interaction_frames or [], "SP interactions")

    # 6. Area of improvement
    aoi = draft.area_of_improving or []
    aoi_text = nl.join(f"• {a}" for a in aoi) if aoi else "—"

    # 7. Actions taken — flattened, owner in parens
    at = draft.actions_taken or {}
    action_lines = []
    for team in ("sp", "customer", "business", "ce", "product"):
        for item in (at.get(team) or []):
            txt = item if isinstance(item, str) else (
                item.get("context") or item.get("with") or "")
            if txt:
                action_lines.append(f"• {txt} ({team.upper()})")
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
    ins_parts = []
    if ins.get("similar_reviews_30d") is not None:
        ins_parts.append(f"*Similar reviews (30d):* {ins.get('similar_reviews_30d')}")
    if ins.get("similar_support_queries_30d") is not None:
        ins_parts.append(f"*Similar support queries (30d):* {ins.get('similar_support_queries_30d')}")
    r30 = ins.get("rating_30d") or {}
    if r30.get("avg") is not None:
        ins_parts.append(f"*Avg rating (30d):* {r30.get('avg')} ({r30.get('n', 0)} ratings)")
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
