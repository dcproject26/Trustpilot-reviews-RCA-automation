"""Does the booking that id returns match what the review actually says?

"i had told you to make sure that the review text and the bid given should
match in indicators too."

`bigquery_patch.verify_bid` returning a row is treated as a match, full stop.
That is a check on the ID, not on the BOOKING: a guest who quotes someone
else's reference number — off a shared voucher, a forwarded email, a screenshot
in a group chat — gets a real id, a real booking, a green Tier 1 trail, and an
RCA written about a stranger's trip. Every downstream step then reasons from
the wrong facts and none of them can tell.

For a Tier 1 BID the pipeline never even extracts indicators: matching is
skipped because the id already found the booking, so the review's own venue,
city, date and name are compared against nothing. This is that comparison,
made after the fact.

RELATION TO booking_match_check. That one asks "is this the same KIND of
product" (a city card review against a guided tour booking). This one asks "is
this the same TRIP" — same place, same time, same person. They are separate
because they fail separately: the right product in the wrong city is a
mismatch that families cannot see, and a museum review against a museum
booking in another country passes the family check cleanly.

DELIBERATELY CONSERVATIVE, for the same reason and stated again because it is
the whole design: a false flag sends an associate to re-match a correct
booking, and enough of those teach them to ignore the flag — at which point it
is worse than absent. So every signal has an ambiguity guard, and anything less
than a clear contradiction returns "unchecked".

Three states, never two, at BOTH levels — each signal and the whole check:

  match      at least one signal agreed and none contradicted
  mismatch   at least one DECISIVE signal contradicted
  unchecked  nothing could be compared — which is not the same as agreement,
             and is the state most reviews land in

DECISIVE vs REPORTED. Venue, city and date decide the verdict. The guest name
does not, on its own: people book under a partner's name, a maiden name, a
company name or a nickname far too often for a name disagreement to be
evidence of a wrong booking. It is still reported — an associate reading a
flagged card wants it — and it can corroborate, but it never fires the flag by
itself. That asymmetry is the single most important thing in this file.

This NEVER unmatches anything. It is a line on the match card, exactly like
booking_match_check, and nothing gates on it.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

# A signal is decisive when a disagreement in it is, on its own, enough to say
# the booking is not this review's. See the module docstring for why "guest"
# is not in this set.
DECISIVE = ("venue", "city", "date")

# How far a stated date may sit from the booked visit date before it counts as
# a contradiction. Wide on purpose: guests write "we went in July" about a
# visit on the 2nd or the 31st, reviews are written weeks later, and a
# month-only mention is resolved to mid-month here — so anything under a month
# and a half is inside the noise this check must not fire on.
DATE_TOLERANCE_DAYS = 45


# ── vocabularies ───────────────────────────────────────────────────────────
#
# Both are deliberately small. A vocabulary is only useful here when BOTH
# sides name something in it, so a missing entry costs an "unchecked" — the
# safe direction — while a sloppy entry costs a false flag. Nothing generic
# goes in: "cathedral", "old town", "the palace" and "the tower" name a
# different place in every city Headout sells.

# Landmark -> (canonical label, city key). The city is what makes a landmark
# usable as a city signal for review text that never names the city itself,
# which is most review text.
LANDMARKS: dict[str, tuple[str, str]] = {}


def _lm(label: str, city: str, *patterns: str) -> None:
    for p in patterns:
        LANDMARKS[p] = (label, city)


_lm("Colosseum", "rome", r"colosse?um", r"coliseum")
_lm("Vatican Museums", "rome", r"vatican", r"sistine\s+chapel")
_lm("St Peter's Basilica", "rome", r"st\.?\s*peter'?s", r"saint\s+peter'?s")
_lm("Roman Forum", "rome", r"roman\s+forum", r"palatine\s+hill")
_lm("Borghese Gallery", "rome", r"borghese")
_lm("Pompeii", "naples", r"pompeii", r"herculaneum")
_lm("Mount Vesuvius", "naples", r"vesuvius")
_lm("Uffizi", "florence", r"uffizi")
_lm("Accademia", "florence", r"accademia", r"\bdavid\s+statue\b")
_lm("Doge's Palace", "venice", r"doge'?s\s+palace", r"palazzo\s+ducale")
_lm("St Mark's Basilica", "venice", r"st\.?\s*mark'?s", r"saint\s+mark'?s")
_lm("Last Supper", "milan", r"last\s+supper", r"cenacolo")
_lm("Leaning Tower of Pisa", "pisa", r"leaning\s+tower")
_lm("Eiffel Tower", "paris", r"eiffel")
_lm("Louvre", "paris", r"louvre")
_lm("Palace of Versailles", "paris", r"versailles")
_lm("Disneyland Paris", "paris", r"disneyland\s+paris")
_lm("Arc de Triomphe", "paris", r"arc\s+de\s+triomphe")
_lm("Musee d'Orsay", "paris", r"orsay")
_lm("Sagrada Familia", "barcelona", r"sagrada\s+fam[ií]lia")
_lm("Park Guell", "barcelona", r"park\s+g[uü]ell", r"parc\s+g[uü]ell")
_lm("Casa Batllo", "barcelona", r"casa\s+batll[oó]")
_lm("Casa Mila", "barcelona", r"casa\s+mil[aà]", r"la\s+pedrera")
_lm("Camp Nou", "barcelona", r"camp\s+nou", r"spotify\s+camp")
_lm("Alhambra", "granada", r"alhambra")
_lm("Prado", "madrid", r"\bprado\b")
_lm("Royal Palace of Madrid", "madrid", r"royal\s+palace\s+of\s+madrid")
_lm("Alcazar of Seville", "seville", r"alc[aá]zar")
_lm("Tower of London", "london", r"tower\s+of\s+london")
_lm("London Eye", "london", r"london\s+eye")
_lm("Buckingham Palace", "london", r"buckingham")
_lm("Westminster Abbey", "london", r"westminster\s+abbey")
_lm("Warner Bros Studio Tour London", "london", r"harry\s+potter\s+studio",
    r"warner\s+bros\.?\s+studio")
_lm("Stonehenge", "london", r"stonehenge")
_lm("Empire State Building", "new york", r"empire\s+state")
_lm("Statue of Liberty", "new york", r"statue\s+of\s+liberty", r"ellis\s+island")
_lm("Top of the Rock", "new york", r"top\s+of\s+the\s+rock")
_lm("One World Observatory", "new york", r"one\s+world\s+observ")
_lm("Edge NYC", "new york", r"\bedge\s+nyc\b", r"hudson\s+yards\s+edge")
_lm("9/11 Memorial", "new york", r"9/11\s+memorial", r"ground\s+zero")
_lm("Burj Khalifa", "dubai", r"burj\s+khalifa")
_lm("Dubai Frame", "dubai", r"dubai\s+frame")
_lm("Museum of the Future", "dubai", r"museum\s+of\s+the\s+future")
_lm("Sheikh Zayed Grand Mosque", "abu dhabi", r"sheikh\s+zayed")
_lm("Louvre Abu Dhabi", "abu dhabi", r"louvre\s+abu\s+dhabi")
_lm("Acropolis", "athens", r"acropolis", r"parthenon")
_lm("Hagia Sophia", "istanbul", r"hagia\s+sophia", r"ayasofya")
_lm("Topkapi Palace", "istanbul", r"topkapi")
_lm("Anne Frank House", "amsterdam", r"anne\s+frank")
_lm("Rijksmuseum", "amsterdam", r"rijksmuseum")
_lm("Van Gogh Museum", "amsterdam", r"van\s+gogh\s+museum")
_lm("Neuschwanstein", "munich", r"neuschwanstein")
_lm("Schonbrunn Palace", "vienna", r"sch[oö]nbrunn")
_lm("Prague Castle", "prague", r"prague\s+castle")
_lm("Sydney Opera House", "sydney", r"sydney\s+opera")
_lm("Zoomarine", "algarve", r"zoomarine")
_lm("Pena Palace", "lisbon", r"pena\s+palace")
_lm("Sintra", "lisbon", r"\bsintra\b")
_lm("Alcatraz", "san francisco", r"alcatraz")
_lm("Universal Studios Orlando", "orlando", r"universal\s+studios\s+orlando",
    r"islands\s+of\s+adventure")
_lm("Walt Disney World", "orlando", r"walt\s+disney\s+world", r"magic\s+kingdom")

# City -> the patterns that name it. Only cities distinctive enough that the
# word is unlikely to appear for any other reason.
CITIES: dict[str, tuple[str, ...]] = {
    "rome":          (r"\brome\b", r"\broma\b"),
    "naples":        (r"\bnaples\b", r"\bnapoli\b", r"\bsorrento\b", r"\bcapri\b"),
    "florence":      (r"\bflorence\b", r"\bfirenze\b"),
    "venice":        (r"\bvenice\b", r"\bvenezia\b"),
    "milan":         (r"\bmilan\b", r"\bmilano\b"),
    "pisa":          (r"\bpisa\b",),
    "paris":         (r"\bparis\b",),
    "nice":          (r"\bnice,\s", r"\bnice\s+france\b"),
    "barcelona":     (r"\bbarcelona\b",),
    "madrid":        (r"\bmadrid\b",),
    "seville":       (r"\bseville\b", r"\bsevilla\b"),
    "granada":       (r"\bgranada\b",),
    "lisbon":        (r"\blisbon\b", r"\blisboa\b"),
    "porto":         (r"\bporto\b", r"\boporto\b"),
    "algarve":       (r"\balgarve\b", r"\balbufeira\b", r"\bguia\b"),
    "london":        (r"\blondon\b",),
    "edinburgh":     (r"\bedinburgh\b",),
    "dublin":        (r"\bdublin\b",),
    "amsterdam":     (r"\bamsterdam\b",),
    "brussels":      (r"\bbrussels\b", r"\bbruxelles\b"),
    "berlin":        (r"\bberlin\b",),
    "munich":        (r"\bmunich\b", r"\bm[uü]nchen\b"),
    "vienna":        (r"\bvienna\b", r"\bwien\b"),
    "prague":        (r"\bprague\b", r"\bpraha\b"),
    "budapest":      (r"\bbudapest\b",),
    "athens":        (r"\bathens\b",),
    "istanbul":      (r"\bistanbul\b",),
    "dubai":         (r"\bdubai\b",),
    "abu dhabi":     (r"\babu\s+dhabi\b",),
    "doha":          (r"\bdoha\b",),
    "singapore":     (r"\bsingapore\b",),
    "bangkok":       (r"\bbangkok\b",),
    "tokyo":         (r"\btokyo\b",),
    "new york":      (r"\bnew\s+york\b", r"\bnyc\b", r"\bmanhattan\b"),
    "las vegas":     (r"\blas\s+vegas\b",),
    "orlando":       (r"\borlando\b",),
    "miami":         (r"\bmiami\b",),
    "san francisco": (r"\bsan\s+francisco\b",),
    "los angeles":   (r"\blos\s+angeles\b",),
    "sydney":        (r"\bsydney\b",),
    "cairo":         (r"\bcairo\b",),
    "marrakech":     (r"\bmarrakech\b", r"\bmarrakesh\b"),
    "reykjavik":     (r"\breykjav[ií]k\b",),
}

_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}

# Month words that are also ordinary English words. "may" and "march" have to
# be pinned to a day or a year before they count as a date, or "they said we
# may enter at any time" becomes a May visit and contradicts a booking in
# September. "sat"/"sun"/"mar" are not in the month table for the same reason.
_AMBIGUOUS_MONTHS = {"may", "march", "mar", "august", "aug"}


def _norm(text: str | None) -> str:
    return " " + re.sub(r"\s+", " ", (text or "").lower()) + " "


def _landmarks_in(text: str) -> list[str]:
    """Distinct canonical landmark labels named in a piece of text."""
    t = _norm(text)
    out = []
    for pat, (label, _city) in LANDMARKS.items():
        if label not in out and re.search(pat, t):
            out.append(label)
    return out


def _cities_in(text: str) -> list[str]:
    """Distinct city keys named in a piece of text, directly or by landmark."""
    t = _norm(text)
    out = []
    for city, pats in CITIES.items():
        if any(re.search(p, t) for p in pats):
            out.append(city)
    for pat, (_label, city) in LANDMARKS.items():
        if city not in out and re.search(pat, t):
            out.append(city)
    return out


def _sig(name, state, review, booking, why):
    return {"name": name, "state": state, "review": review,
            "booking": booking, "why": why}


def _booking_text(booking: dict) -> str:
    """Everything on the booking that names a place, as one string."""
    return " · ".join(str(booking.get(k) or "") for k in (
        "experienceName", "experience_name", "experience",
        "tid_name", "vendorName", "vendor_name", "partner"))


# ── the signals ────────────────────────────────────────────────────────────

def _venue_signal(review_text: str, booking: dict) -> dict:
    rl = _landmarks_in(review_text)
    bl = _landmarks_in(_booking_text(booking))
    if not rl:
        return _sig("venue", "unchecked", None, bl[0] if len(bl) == 1 else None,
                    "the review names no venue we recognise")
    if not bl:
        return _sig("venue", "unchecked", rl[0] if len(rl) == 1 else None, None,
                    "the booked experience names no venue we recognise")
    # A combo ticket names two landmarks and a guest recounting a day out names
    # three. Neither is a disagreement, and telling which one the review is
    # ABOUT is exactly the judgement this check must not make.
    if len(rl) > 1:
        return _sig("venue", "unchecked", ", ".join(rl), ", ".join(bl),
                    "the review names more than one venue")
    if len(bl) > 1:
        return _sig("venue", "unchecked", rl[0], ", ".join(bl),
                    "the booking covers more than one venue")
    if rl[0] == bl[0]:
        return _sig("venue", "match", rl[0], bl[0], f"both name {rl[0]}")
    return _sig("venue", "mismatch", rl[0], bl[0],
                f"the review is about {rl[0]}; this booking is for {bl[0]}")


def _city_label(key: str) -> str:
    """'new york' -> 'New York'. The keys are lowercase so the vocabulary can
    be matched against folded text; nothing on screen should say 'rome'."""
    return " ".join(w.capitalize() for w in (key or "").split())


def _city_signal(review_text: str, booking: dict) -> dict:
    rc = _cities_in(review_text)
    bc = _cities_in(_booking_text(booking))
    rl = [_city_label(c) for c in rc]
    bl = [_city_label(c) for c in bc]
    if not rc:
        return _sig("city", "unchecked", None, bl[0] if len(bl) == 1 else None,
                    "the review names no city we recognise")
    if not bc:
        return _sig("city", "unchecked", rl[0] if len(rl) == 1 else None, None,
                    "the booking names no city we recognise")
    # Two cities in one review is a multi-city trip or a transfer between them;
    # two on the booking is a day trip that crosses a city line.
    if len(rc) > 1:
        return _sig("city", "unchecked", ", ".join(rl), ", ".join(bl),
                    "the review names more than one city")
    if len(bc) > 1:
        return _sig("city", "unchecked", rl[0], ", ".join(bl),
                    "the booking spans more than one city")
    if rc[0] == bc[0]:
        return _sig("city", "match", rl[0], bl[0], f"both are in {rl[0]}")
    return _sig("city", "mismatch", rl[0], bl[0],
                f"the review is about {rl[0]}; this booking is in {bl[0]}")


def _dates_in(text: str, reference: date | None) -> list[date]:
    """Distinct dates a review states, resolved to a year.

    An unqualified month is resolved to the most recent one at or before the
    review's own date — that is the only reading that is ever right about a
    review, which is written after the visit. Without a reference date there
    is no honest resolution and nothing is returned.
    """
    t = _norm(text)
    found: list[date] = []

    def _add(y, m, d):
        try:
            v = date(y, m, d)
        except ValueError:
            return
        if v not in found:
            found.append(v)

    for m in re.finditer(r"\b(\d{4})-(\d{2})-(\d{2})\b", t):
        _add(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    names = "|".join(sorted(_MONTHS, key=len, reverse=True))
    # "12 July 2025" / "12th July" / "July 12, 2025" / "July 2025" / "July"
    pat = (rf"\b(?:(\d{{1,2}})(?:st|nd|rd|th)?\s+({names})|"
           rf"({names})\s+(\d{{1,2}})(?:st|nd|rd|th)?)\b(?:,?\s*(\d{{4}}))?"
           rf"|\b({names})\b(?:\s+(\d{{4}}))?")
    for m in re.finditer(pat, t):
        d1, mon1, mon2, d2, y1, mon3, y2 = m.groups()
        month_word = mon1 or mon2 or mon3
        if not month_word:
            continue
        day = int(d1 or d2 or 15)
        year = y1 or y2
        if month_word in _AMBIGUOUS_MONTHS and not (d1 or d2 or year):
            # "we may enter", "the march was long". Only a day or a year makes
            # one of these a date, and guessing costs a false contradiction.
            continue
        mo = _MONTHS[month_word]
        if year:
            _add(int(year), mo, day)
            continue
        if reference is None:
            continue
        y = reference.year
        cand = None
        for yy in (y, y - 1):
            try:
                v = date(yy, mo, day)
            except ValueError:
                continue
            # Allow a little slack forward: a review written on the 1st about a
            # visit on the 3rd is not a reference to last year.
            if v <= reference + timedelta(days=7):
                cand = v
                break
        if cand:
            _add(cand.year, cand.month, cand.day)
    return found


def _date_signal(review_text: str, booking: dict, reference: date | None) -> dict:
    visit_raw = str(booking.get("date_of_visit") or booking.get("visitDate")
                    or booking.get("experienceDate") or "")[:10]
    try:
        visit = date.fromisoformat(visit_raw)
    except ValueError:
        return _sig("date", "unchecked", None, visit_raw or None,
                    "the booking has no readable visit date")
    said = _dates_in(review_text, reference)
    if not said:
        return _sig("date", "unchecked", None, visit.isoformat(),
                    "the review states no date")
    if len(said) > 1:
        return _sig("date", "unchecked", ", ".join(d.isoformat() for d in said),
                    visit.isoformat(), "the review states more than one date")
    delta = abs((said[0] - visit).days)
    if delta <= DATE_TOLERANCE_DAYS:
        return _sig("date", "match", said[0].isoformat(), visit.isoformat(),
                    f"both fall within {DATE_TOLERANCE_DAYS} days")
    return _sig("date", "mismatch", said[0].isoformat(), visit.isoformat(),
                f"the review describes {said[0].isoformat()}; this booking was "
                f"visited {visit.isoformat()}, {delta} days apart")


def _guest_signal(author: str | None, booking: dict) -> dict:
    from server.names import is_placeholder, parse_author
    from server.services.zendesk import _name_score

    pgn = str(booking.get("primary_guest_name") or booking.get("guestName") or "").strip()
    author = (author or "").strip()
    if not author or is_placeholder(author):
        return _sig("guest", "unchecked", author or None, pgn or None,
                    "the review has no usable author name")
    if not pgn or _looks_hashed(pgn):
        return _sig("guest", "unchecked", author, pgn or None,
                    "the booking has no readable guest name")
    first, last = parse_author(author)
    score = _name_score(pgn, first, last)
    if score >= 0.7:
        return _sig("guest", "match", author, pgn, f"the names agree ({score:.1f})")
    if score > 0:
        return _sig("guest", "unchecked", author, pgn,
                    f"the names partly agree ({score:.1f}) — too weak to read "
                    f"either way")
    return _sig("guest", "mismatch", author, pgn,
                f"no part of '{author}' appears in '{pgn}'")


def _looks_hashed(s: str) -> bool:
    s = (s or "").strip()
    return " " not in s and len(s) >= 16 and all(
        c in "0123456789abcdefABCDEF-" for c in s)


# ── the check ──────────────────────────────────────────────────────────────

def check(review_text: str, booking: dict, *, author: str | None = None,
          received_at=None) -> dict:
    """Whether the booking this id returned is the trip the review describes.

    Returns {"state", "signals", "contradictions", "agreements", "checked",
             "why"}. `state` is "match", "mismatch" or "unchecked" — never a
    bare boolean, so a caller cannot read "we could not tell" as "we checked
    and it is fine".

    `why` is written for the associate, not for a log: it says what was
    compared and what came back, including when the answer is that nothing
    could be compared.
    """
    booking = booking if isinstance(booking, dict) else {}
    ref = None
    if received_at is not None:
        ref = received_at.date() if hasattr(received_at, "date") else received_at

    signals = [
        _venue_signal(review_text, booking),
        _city_signal(review_text, booking),
        _date_signal(review_text, booking, ref),
        _guest_signal(author, booking),
    ]
    by_state = lambda st: [s["name"] for s in signals if s["state"] == st]  # noqa: E731
    contradictions = by_state("mismatch")
    agreements = by_state("match")
    decisive_against = [n for n in contradictions if n in DECISIVE]

    if decisive_against:
        parts = [s["why"] for s in signals if s["name"] in decisive_against]
        why = "; ".join(parts)
        if [n for n in contradictions if n not in DECISIVE]:
            why += (" — and the guest name disagrees too, which on its own "
                    "would not have been enough to say so")
        state = "mismatch"
    elif agreements:
        state = "match"
        why = ("checked and agrees on " + ", ".join(agreements)
               + (f" — {', '.join(contradictions)} disagreed, which is not on "
                  f"its own evidence of a wrong booking" if contradictions else ""))
    elif contradictions:
        # Something DID disagree — it just was not something we let decide on
        # its own. Reporting that as "nothing could be compared" is the exact
        # inversion this file exists to avoid: a comparison ran, came back
        # negative, and the sentence said no comparison happened.
        state = "unchecked"
        why = ("nothing decisive could be compared, but "
               + "; ".join(s["why"] for s in signals
                           if s["name"] in contradictions)
               + f" — a {'/'.join(contradictions)} disagreement is not on its "
                 f"own evidence of a wrong booking, so this is not flagged")
    else:
        state = "unchecked"
        why = ("nothing in this review could be compared to this booking: "
               + "; ".join(s["why"] for s in signals))

    return {"state": state, "signals": signals,
            "contradictions": contradictions, "agreements": agreements,
            "checked": len(agreements) + len(contradictions), "why": why}


def trail_entry(result: dict) -> dict:
    """The confidence-trail line for a check result — one per state.

    Its own function because the trail is where "we ran it and found nothing"
    has to be legible: an unchecked result writes a line saying so, in words
    that cannot be confused with the check never having run. A check that
    stays silent when it finds nothing is the failure this project keeps
    repeating.
    """
    state = (result or {}).get("state")
    if state == "mismatch":
        return {"mark": "warn",
                "text": "<strong>Indicators disagree with this booking</strong> — "
                        + result["why"] + ". The id is real and the booking "
                        "exists; this may be someone else's reference number. "
                        "Not unmatched — confirm before relying on it."}
    if state == "match":
        return {"mark": "pass",
                "text": "<strong>Indicators agree</strong> — " + result["why"]}
    return {"mark": "warn",
            "text": "<strong>Indicators could not be checked</strong> against "
                    "this booking — " + (result or {}).get("why", "no result") +
                    ". This is not agreement; nothing was compared."}
