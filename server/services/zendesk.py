"""
Zendesk service — connector-auth'd (Replit Zendesk connection, OAuth).

Data path per the 2026-07 wiring brief:
1. Search tickets: fieldvalue:<bid> first, free-text "<bid>" fallback (logged).
2. Extract tgid/tid from confirmed custom field IDs (2024 Retool workflow).
3. Surface ticket_mail_seen tag on the booking.
4. Fetch ALL comments per ticket (public + private notes), paginated by zenpy.
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
from datetime import datetime, timedelta, timezone

from server.config import (
    is_live, MOCK_MODE,
    ZENDESK_SUBDOMAIN, ZENDESK_EMAIL, ZENDESK_API_TOKEN,
    ZENDESK_TGID_FIELD, ZENDESK_TID_FIELD,
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
    if lookback_days:
        since = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    else:
        since = datetime.now().strftime("%Y-01-01")

    # Primary: full name. Fallback: the SURNAME only — the display name often
    # differs from the Zendesk requester name (nickname, initial, order).
    # Previously this picked max(first, last, key=len), which for most names
    # yields the FIRST name ("Fredrik" over "Olsen"), turning the fallback into
    # requester:Fredrik — every Fredrik in the instance. Those tickets belong to
    # strangers, their BIDs still verify in BigQuery, and nothing downstream can
    # tell them apart, so unrelated bookings surfaced as candidates.
    name_variants = [name_str]
    if author_last and author_first and len(author_last) >= 3:
        name_variants.append(author_last)

    _user_cache: dict = {}

    def _requester_name(ticket) -> str:
        rid = getattr(ticket, "requester_id", None)
        if rid in _user_cache:
            return _user_cache[rid]
        nm = ""
        try:
            nm = getattr(_z.users(id=rid), "name", "") or ""
        except Exception:
            pass
        _user_cache[rid] = nm
        return nm

    tickets = []
    for idx, variant in enumerate(name_variants):
        query = f"type:ticket requester:{_as_query(variant)} created>{since}"
        log.info(f"[zendesk] requester search: {query}")
        try:
            tickets = await asyncio.get_running_loop().run_in_executor(
                None, lambda q=query: _search_with_retry(_z, q)
            )
        except Exception as e:
            log.warning(f"[zendesk] requester search failed: {e}")
            tickets = []

        # A single-token search matches every user carrying that token, so the
        # broad variant MUST be verified: the ticket's real requester name has
        # to contain both names we were actually looking for.
        if tickets and idx > 0 and author_first and author_last:
            want = [author_first.lower(), author_last.lower()]
            before = len(tickets)
            tickets = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda ts=tickets: [
                    t for t in ts
                    if all(w in _requester_name(t).lower() for w in want)
                ],
            )
            log.info(f"[zendesk] broad variant {variant!r}: {before} tickets → "
                     f"{len(tickets)} after requester-name verification")
        if tickets:
            break

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
        t_bids = [n for n in found if n != own_id]
        bids += t_bids
        ticket_records.append({
            "ticket_id": own_id,
            "subject":   subject,
            "body":      body[:4000],
            "text":      f"{subject}\n{body[:4000]}".strip(),
            "bids":      list(dict.fromkeys(t_bids)),
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

    since = since or datetime.now().strftime("%Y-01-01")

    all_bids, all_records, seen_tickets = [], [], set()
    for term in terms[:3]:
        clauses = [f'type:ticket "{term}"', f"created>{since}"]
        if requester_hint:
            clauses.insert(1, f'requester:"{requester_hint}"')
        query = " ".join(clauses)
        log.info(f"[zendesk] text search: {query}")
        try:
            tickets = await asyncio.get_running_loop().run_in_executor(
                None, lambda q=query: _search_with_retry(_z, q)
            )
        except Exception as e:
            log.warning(f"[zendesk] text search failed for {term!r}: {e}")
            continue

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
            t_bids = [n for n in found if n != own_id]
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
