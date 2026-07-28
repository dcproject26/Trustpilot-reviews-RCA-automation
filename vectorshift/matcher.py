"""
Booking matcher — standalone port for VectorShift.

No Replit, FastAPI, SQLAlchemy, zenpy or BigQuery imports. Pure functions over
plain dicts, so it drops into a VectorShift Python node unchanged.

Pipeline shape this expects:

    1. INDICATOR_PROMPT  -> LLM node -> indicators dict
    2. build_queries(indicators, reviewer_name) -> Zendesk search queries
    3. Zendesk node runs them -> raw ticket JSON
    4. shortlist(tickets, indicators, reviewer_name) -> candidate bookings
    5. Associate confirms one -> only then look it up in BigQuery

BigQuery is deliberately absent: the booking id and every fact needed to judge
a match are on the Zendesk ticket. BQ is for enriching a CONFIRMED booking.

Every rule here is verified against five live reviews; see EXPECTED at the
bottom for the exact outcomes.
"""
import re
import unicodedata

# ─── Field IDs ──────────────────────────────────────────────────────────────
# Confirmed against a live ticket (ZD-33979875). Override per environment.
FIELDS = {
    "booking_id":   360021524471,
    "guest_name":    51116641874073,
    "guest_email":  360026670311,
    "experience":   360021471312,
    "city":         360021522151,
    "visit_date":   360024232231,
    "pax":          360021522291,
    "vendor_name":  8136487555225,
    # NOT a booking id — the itinerary/payment id, also 8 digits.
    "itinerary_id": 360021524491,
}

# ─── Extraction prompt ──────────────────────────────────────────────────────
INDICATOR_PROMPT = """You are identifying which Headout booking a Trustpilot review is about.

REVIEWER NAME: {reviewer_name}

REVIEW (posted {review_date}):
{review_text}

Return JSON:
- guest_name — copy the REVIEWER NAME above verbatim, unless the text clearly
  names a different person as the booker. It is often the only indicator
  available, so never omit it. Null only if no name exists anywhere.
- experience_or_venue — what they visited/booked, in their words.
  IMPORTANT: the review may end with a line like "Reference number: <text>".
  Guests routinely type the VENUE there instead of a booking number — e.g.
  "Reference number: Salt mines Krakow" means the venue is "Salt mines Krakow".
  If that line holds anything other than a plain number, read it as the venue.
- city_or_country — if stated or clearly implied.
- visit_date_hint — the date the guest VISITED or was due to visit. NOT the date
  they booked, were emailed, or complained. "I booked yesterday" is a booking
  date and must be ignored. Output a BARE DATE, exactly YYYY-MM-DD, nothing
  else — no ranges, no explanation. Null if the review gives no visit date.
- pax — how many people the booking was for, as a number. "9 combo tickets" -> 9,
  "my wife and I" -> 2, "family of four" -> 4. Null if not inferable.

Return ONLY valid JSON, no markdown:
{{"guest_name": "<or null>", "experience_or_venue": "<or null>",
  "city_or_country": "<or null>", "visit_date_hint": "<or null>",
  "pax": "<number or null>"}}"""


def render_prompt(review_text, reviewer_name="", review_date=""):
    return INDICATOR_PROMPT.format(
        review_text=review_text or "",
        reviewer_name=reviewer_name or "(not provided)",
        review_date=review_date or "unknown")


def clean_indicators(ind):
    """The model occasionally answers a date as prose. Keep a bare date or none."""
    ind = dict(ind or {})
    m = re.search(r"\d{4}-\d{2}-\d{2}", str(ind.get("visit_date_hint") or ""))
    ind["visit_date_hint"] = m.group(0) if m else None
    return ind


# ─── Name matching ──────────────────────────────────────────────────────────
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


def fold(s):
    """Lowercase and strip diacritics, so 'Jorg' and 'Jörg' compare equal."""
    return "".join(c for c in unicodedata.normalize("NFKD", s or "")
                   if not unicodedata.combining(c)).lower()


def _token_match(want, tokens):
    want = (want or "").strip(". ").lower()
    if not want:
        return False
    if want in tokens:
        return True
    # Initials: "C." -> "Catherine". Prefix cannot cover nicknames, because
    # "joseph" does not start with "joe" (j-o-s vs j-o-e).
    if len(want) <= 2 and any(t.startswith(want) for t in tokens):
        return True
    for a, b in _NICKNAMES:
        if (want == a and b in tokens) or (want == b and a in tokens):
            return True
    return False


def split_name(full):
    p = (full or "").strip().split()
    if not p:
        return None, None
    return (p[0], p[-1]) if len(p) > 1 else (p[0], None)


def name_matches(candidate, first, last):
    """
    BOTH names must be present. A surname alone is not a match: "Joe Christopher"
    must not pull in "Christopher McCardle", where Christopher is the FIRST name
    and Joe appears nowhere. Middle names are ignored, so "Fredrik Martin Olsen"
    matches "Fredrik Olsen" while "Fredrik Rostvold" does not.
    """
    tokens = set(re.findall(r"[a-z0-9]+", fold(candidate)))
    if not tokens:
        return False
    want = [w for w in (first, last) if w and str(w).strip()]
    return bool(want) and all(_token_match(fold(w), tokens) for w in want)


# ─── Venue matching ─────────────────────────────────────────────────────────
_VENUE_STOP = {
    "tour", "tours", "pass", "ticket", "tickets", "entry", "visit", "trip",
    "city", "day", "guided", "skip", "line", "with", "from", "and", "the",
    "experience", "admission", "access", "combo", "package", "hours", "hour",
    "half", "full", "private", "group", "small", "guide", "self", "audio",
    "optional", "direct",
}
# Venue-TYPE nouns. An overlap of only these is not evidence of the same venue:
# "palace of culture and science" must not match Pena Palace or Doge's Palace.
_VENUE_GENERIC = {
    "palace", "museum", "castle", "park", "tower", "cathedral", "church",
    "garden", "gardens", "zoo", "bridge", "square", "house", "hall", "centre",
    "center", "gallery", "temple", "arena", "stadium", "cruise", "river",
    "basilica", "chapel", "fortress", "monument", "aquarium", "observatory",
    "market", "island", "beach", "lake", "mountain", "valley", "national",
    "royal", "grand", "central", "old", "new", "great",
}


def venue_tokens(s):
    return {t for t in re.findall(r"[a-z]{4,}", fold(s)) if t not in _VENUE_STOP}


# ─── Ticket facts ───────────────────────────────────────────────────────────
def ticket_signals(ticket):
    """
    Booking facts from a ticket's custom fields.

    `ticket` is a dict as returned by the Zendesk API, with a `custom_fields`
    list of {"id": int, "value": any}.
    """
    cf = {}
    for f in (ticket.get("custom_fields") or []):
        if isinstance(f, dict) and f.get("value") not in (None, ""):
            cf[f.get("id")] = str(f["value"]).strip()

    def _v(key):
        return cf.get(FIELDS[key], "")

    bid = ""
    m = re.search(r"\b\d{7,12}\b", _v("booking_id"))
    if m:
        bid = m.group(0)

    pax_raw = _v("pax")
    pax = sum(int(n) for n in re.findall(
        r"(\d+)\s*(?:adult|child|infant|senior|youth)", pax_raw, re.I)) or None

    return {
        "ticket_id":    str(ticket.get("id") or ""),
        "created_at":   str(ticket.get("created_at") or ""),
        "booking_id":   bid,
        "guest_name":   _v("guest_name"),
        "guest_email":  _v("guest_email"),
        "experience":   _v("experience"),
        "city":         _v("city"),
        "visit_date":   _v("visit_date"),
        "pax_raw":      pax_raw,
        "pax":          pax,
        "vendor_name":  _v("vendor_name"),
        "itinerary_id": _v("itinerary_id"),
    }


# ─── The rule ───────────────────────────────────────────────────────────────
def build_queries(indicators, reviewer_name=""):
    """
    Zendesk queries for whatever indicators exist.

    Nothing here is broad enough for Zendesk to truncate. A bare
    `type:ticket <name>` matches every ticket mentioning that name anywhere and
    trips "more results than Zendesk allows"; a bare venue query returns
    everyone who booked it. Both are used only when they are the sole indicator.
    """
    name = (indicators.get("guest_name") or reviewer_name or "").strip()
    venue = (indicators.get("experience_or_venue") or "").strip()
    city = (indicators.get("city_or_country") or "").split(",")[0].strip()
    order = "order_by:created_at sort:desc"

    qs = []
    if name:
        qs.append(f'type:ticket requester:"{name}" {order}')
    if name and venue:
        qs.append(f"type:ticket {name} {venue} {order}")
    if name and city:
        qs.append(f"type:ticket {name} {city} {order}")
    if name and not venue and not city:
        qs.append(f"type:ticket {name} {order}")
    if venue and not name:
        qs.append(f'type:ticket "{venue}" {order}')
    return qs


def matches_indicators(sig, indicators, first, last):
    """
    Does this ticket satisfy EVERY indicator the review gave us?

    AND across what is present; absent indicators are skipped, never blocking.
    Returns (ok, indicators_that_agreed).
    """
    used = []

    if first or last:
        if not name_matches(sig.get("guest_name") or "", first, last):
            return False, used
        used.append("name")

    venue = (indicators.get("experience_or_venue") or "").strip()
    if venue:
        overlap = venue_tokens(venue) & venue_tokens(sig.get("experience") or "")
        # At least one DISTINCTIVE word must agree.
        if not (overlap and (overlap - _VENUE_GENERIC)):
            return False, used
        used.append("venue")

    # City only filters when there is no venue: the extractor may return a
    # COUNTRY ("Poland") which never token-matches the ticket's CITY ("Warsaw")
    # even though they agree.
    city = (indicators.get("city_or_country") or "").split(",")[0].strip()
    if city:
        want = set(re.findall(r"[a-z]{3,}", fold(city)))
        got = set(re.findall(r"[a-z]{3,}", fold(sig.get("city") or "")))
        if want & got:
            used.append("city")
        elif got and not venue:
            return False, used

    # Pax NARROWS, never rejects — see shortlist(). "two tickets" against a
    # ticket recording pax 1 is a counting difference, not a different booking.
    pax = indicators.get("pax")
    if pax and sig.get("pax"):
        try:
            if int(pax) == int(sig["pax"]):
                used.append("pax")
        except (TypeError, ValueError):
            pass

    return True, used


def shortlist(tickets, indicators, reviewer_name="", name_only_limit=5):
    """
    The bookings a review's indicators point at.

    `tickets` is the raw Zendesk ticket list from step 3 — already fetched, and
    deduplicated across queries by the caller or here. Returns candidates
    newest-first. An empty list means untraceable.
    """
    indicators = clean_indicators(indicators)
    first, last = split_name(indicators.get("guest_name") or reviewer_name or "")

    by_bid = {}
    for t in tickets or []:
        sig = ticket_signals(t)
        bid = sig.get("booking_id")
        if not bid or bid in by_bid:
            continue
        ok, used = matches_indicators(sig, indicators, first, last)
        if not ok:
            continue
        sig["matched_on"] = used
        by_bid[bid] = sig

    out = sorted(by_bid.values(), key=lambda s: s.get("created_at") or "",
                 reverse=True)

    # Pax narrows only when it actually separates the candidates.
    if len(out) > 1 and indicators.get("pax"):
        exact = [s for s in out if "pax" in s["matched_on"]]
        if exact and len(exact) < len(out):
            out = exact

    venue = (indicators.get("experience_or_venue") or "").strip()
    city = (indicators.get("city_or_country") or "").strip()
    if (first or last) and not venue and not city and len(out) > name_only_limit:
        out = out[:name_only_limit]

    return out


# ─── Verified outcomes (live Zendesk, 2026-07-28) ───────────────────────────
EXPECTED = {
    "Fredrik Olsen":   ("32885787", ["name", "venue", "city"]),
    "David":           ("32908218", ["name", "venue"]),
    "Ciprian":         ("32900044", ["name", "venue", "city", "pax"]),
    "C. Nauleau":      ("32244357", ["name", "venue", "city"]),
    "Joe Christopher": ("name-only -> 4 Joseph/Joe bookings, newest first", ["name"]),
}
