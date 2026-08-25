"""
REPLACES existing server/pipeline.py

The pipeline for a new review. Wraps every step in try/except so one failure
doesn't kill the rest of the flow.

Steps:
  1.  Translate (if non-English)
  2.  BID regex (Tier 1)
  3.  Signal extraction (Claude) — only if no BID
  4.  BigQuery match (Tier 1 lookup or Tier 2 fuzzy search)
  5.  Persist candidate list + confidence trail (Tier 2 → picker state)
  6.  Zendesk timeline
  7.  Insights (BigQuery)
  8.  Similar complaints (BigQuery + Trustpilot)
  9.  DSS webhook (called eagerly for context; associate can also reconnect on-demand)
  10. Stated Issue summary
  11. Classification (L1/L2)
  12. Full RCA generation
  13. Response draft
  14. Save + post-back to Slack thread
  15. Metrics
"""
import asyncio, logging, re, unicodedata
from collections import Counter as _Counter
# Module scope, not inside the validation try. It used to be imported two lines
# after a statement that always raised, so `_html` was never bound — and the
# contact-note join below, which is a DIFFERENT try, then died on the NameError
# and reported itself "skipped". One dead statement took out two disclosures.
import html as _html
from datetime import datetime, timedelta

from sqlalchemy.orm.attributes import flag_modified

from server.config import is_live, MOCK_MODE
from server import prompts
from server.db import SessionLocal, Review, RcaDraft, ReviewMetric
from server.services import (claude, bigquery as bq, zendesk, dss, slack as slk,
                             reply_language)
from server.services.canned import get_canned_responses
from server.services.insights import get_insights as _get_insights
from server.taxonomy import DIAGNOSTIC_CHECKS, BID_REGEX

log = logging.getLogger(__name__)

# Generic travel/booking words that appear in almost every experience name.
# A match on one of these is NOT evidence the review is about this venue.
_VENUE_STOPWORDS = {
    "tour", "tours", "pass", "ticket", "tickets", "entry", "visit", "trip",
    "city", "day", "guided", "skip", "line", "with", "from", "and", "the",
    "experience", "admission", "access", "combo", "package", "hours", "hour",
    "half", "full", "private", "group", "small", "guide", "self", "audio",
}


def _fold_accents(s: str) -> str:
    """
    Strip diacritics so 'Oceanogràfic' and 'Oceanografic' tokenise identically.

    The token regex below is ASCII-only, so without folding, an accent splits a
    word into fragments: 'oceanogràfic' -> {'oceanogr'} but BigQuery's
    unaccented 'Oceanografic' -> {'oceanografic'}. The sets never intersect and
    a venue that matches perfectly scores zero. Hits every accented venue in the
    European inventory (Sagrada Família, Museu Picasso, Château, Köln).
    """
    return "".join(c for c in unicodedata.normalize("NFKD", s or "")
                   if not unicodedata.combining(c))


def _sig_tokens(s: str) -> set:
    """Significant venue words: len>=4, alphabetic, not a generic travel term."""
    toks = re.findall(r"[a-z]{4,}", _fold_accents(s).lower())
    return {t for t in toks if t not in _VENUE_STOPWORDS}


def _is_hashed_name(s: str) -> bool:
    """
    True for a base64/hex PII hash rather than a human name. BigQuery returns
    primary_guest_name hashed, so any name comparison against it is noise.

    DELEGATES — this held its own copy, and its copy called any long unspaced
    run of `[A-Za-z0-9+/=_-]` a hash, so `Papadopoulopoulos` was refused for
    comparison as a digest. See `names.looks_like_digest`.
    """
    from server.names import looks_like_digest
    return looks_like_digest(s)


# How far back the broad Zendesk searches look, from the review's own date.
# Guests review weeks to months after visiting, occasionally a year later;
# unbounded, a search on a common name returns more than Zendesk will hand
# back and the right ticket can fall outside what we get.
SHORTLIST_LOOKBACK_DAYS = 540


def shortlist_rows(sigs, lookup):
    """Resolve each Zendesk shortlist signature against the warehouse.

    Returns `(rows, tally)`. Each row is the dict `_make_candidate` reads,
    plus `details_lookup` saying what the warehouse said about it.

    WHY THIS EXISTS. The shortlist built its cards out of the Zendesk ticket's
    own custom fields and never asked BigQuery — so a card showed `#32885089`,
    an em-dash for the experience, and nothing else. Those fields are blank on
    exactly the tickets this path draws from: the ones found by a requester
    name search, which `matches_indicators` already keeps on the grounds that

        "the tickets with an empty guest-name field are the SAME sparse
         tickets that have an empty booking-id field"

    They are kept because an empty field cannot contradict the review. What
    nobody followed through on is that the card built from one has nothing on
    it to choose by — and the associate is being asked to pick the booking the
    whole RCA is built on.

    The sibling path already does this. Step 2b resolves every BID through
    `verify_bid` before showing it, so the SAME kind of id — a number lifted
    off a Zendesk ticket — produced a full card through one path and a blank
    one through the other, decided by which search happened to find it.

    THE WAREHOUSE IS AUTHORITATIVE WHERE IT IS READABLE; the ticket fills the
    gaps and never overwrites. The Zendesk guest name is kept separately as
    `zendesk_guest_name`, because on a desk-made or hashed booking it is the
    only readable copy of the strongest identifier after the booking id.

    THREE OUTCOMES, NEVER ONE. `lookup` returning None and `lookup` raising
    are not the same fact and neither is "it answered with an empty row":

        found   the warehouse has this booking — its fields are on the card
        absent  the warehouse does not have this id; the ticket is all there is
        failed  the lookup itself did not complete, so nothing was ruled out

    Collapsing them would put one sentence under all three, which is how "we
    looked and found nothing" comes to read the same as "we never looked".
    """
    rows, tally = [], {"found": 0, "absent": 0, "failed": 0}
    for sig in (sigs or []):
        bid = str(sig.get("booking_id") or "")
        zd_name = sig.get("guest_name", "") or ""
        base = {
            "id": bid,
            "primary_guest_name": zd_name,
            "experienceName":     sig.get("experience", "") or "",
            "date_of_visit":      sig.get("visit_date", "") or "",
            "vendorName":         sig.get("vendor_name", "") or "",
        }
        try:
            bq = lookup(bid) if bid else None
            status = "found" if bq else "absent"
        except Exception:
            bq, status = None, "failed"
        if bq:
            merged = dict(base)
            for key in ("experienceName", "date_of_visit", "vendorName",
                        "primary_guest_name", "tid", "tgid", "vid",
                        "date_of_booking", "fulfilmentType", "booking_status",
                        "bms_link", "tgid_link"):
                val = bq.get(key)
                if val not in (None, ""):
                    merged[key] = val
            base = merged
        base["zendesk_guest_name"] = zd_name
        base["details_lookup"] = status
        base["matched_on"] = list(sig.get("matched_on") or ["name"])
        tally[status] += 1
        rows.append(base)
    return rows, tally


def shortlist_lookup_trail(tally) -> dict | None:
    """The trail line for a shortlist that has just been resolved, or None.

    None ONLY when every booking was read. "0 could not be read" on every
    healthy shortlist is the noise that makes a reader stop reading the counts
    that matter — but anything else is said out loud, because a blank card is
    otherwise indistinguishable from a booking the warehouse simply has
    nothing on.
    """
    found, absent, failed = tally["found"], tally["absent"], tally["failed"]
    total = found + absent + failed
    if not total or not (absent or failed):
        return None
    bits = []
    if found:
        bits.append(f"{found} read from the warehouse")
    if absent:
        bits.append(f"{absent} not in the warehouse at all — the Zendesk "
                    f"ticket is everything we have on {'them' if absent > 1 else 'it'}")
    if failed:
        bits.append(f"{failed} could not be looked up, so nothing about "
                    f"{'them' if failed > 1 else 'it'} was ruled out")
    return {"mark": "warn",
            "text": f"<strong>Booking details for these {total} option(s):</strong> "
                    + "; ".join(bits) + "."}


def _shape_weak_bid(row: dict, why: list) -> dict:
    """A verify_bid row, in the shape the candidate picker reads.

    verify_bid names the dates date_of_visit / date_of_booking and returns no
    match reasons; the picker reads visitDate / bookedOn / matchReasons. So the
    one candidate an associate is explicitly being asked to judge - a booking
    id found in prose that the system could NOT verify - was the one that
    arrived with no date and no reasons on its card.
    """
    out = dict(row)
    dov = row.get("date_of_visit") or row.get("visitDate") or ""
    dob = row.get("date_of_booking") or row.get("bookedOn") or ""
    out.update({
        "id":             str(row.get("id") or ""),
        "experience":     row.get("experienceName") or row.get("experience_name") or "",
        "experienceName": row.get("experienceName") or row.get("experience_name") or "",
        "visitDate":      dov,
        "date_of_visit":  dov,
        "experienceDate": dov,
        "bookedOn":       dob,
        "creationDate":   dob,
        "vendorName":     row.get("vendorName") or row.get("partner") or "",
        "matched_on":     list(why),
        "matchReasons":   list(why),
        "narrowing_path": "regex_bid_unverified",
        "score":          None,
    })
    return out


def candidates_are_noise(candidates: list) -> bool:
    """Is this shortlist ranked on nothing but how close a date is?

    THE REPORTED CASE. A review about the Colosseum produced a German water
    park, a New York observatory and a Borghese Gallery booking as "possible
    matches". The venue was extracted, nothing resolved, and date proximity
    filled the gap — so three unrelated bookings were offered to an associate
    as a shortlist.

    A shortlist ranked only on date proximity is not a shortlist. It costs
    three lookups and, far worse, invites a confirmation: a wrong booking
    confirmed by a person becomes the foundation of the entire RCA, and every
    finding built on it is about somebody else's trip. "We could not identify
    this booking" is a FACT the associate can act on — they can ask the guest
    for a reference. Three bookings from three countries is a guess wearing a
    shortlist's clothes.

    So: noise when NOTHING agrees except the date. Any venue agreement, any
    name agreement and any ticket signal keeps the list — those are real, if
    weak, and suppressing them would hide matches that are simply hard.
    """
    rows = [c for c in (candidates or []) if isinstance(c, dict)]
    # Whole-list noise = a non-empty list where NOT ONE candidate survives the
    # per-candidate test. Delegated so there is one implementation of "does this
    # candidate agree on anything", not two that drift apart.
    return bool(rows) and not surviving_candidates(rows)


def _candidate_agrees(c: dict) -> bool:
    """Does this ONE candidate agree with the review on something real — venue,
    guest name, or a Zendesk ticket — rather than only a close visit date?"""
    if c.get("venue_signal"):
        return True
    for k in ("score_venue", "score_name", "score_ticket"):
        try:
            if float(c.get(k) or 0) > 0:
                return True
        except (TypeError, ValueError):
            pass
    return False


def _candidate_unscored(c: dict) -> bool:
    """A candidate from a path that recorded no sub-scores at all. It cannot be
    SHOWN to be noise, and an unproven claim is not grounds for dropping
    somebody's only lead — so it is kept, deliberately."""
    return not any(k in c for k in ("score_venue", "score_name", "score_ticket"))


def surviving_candidates(candidates: list) -> list:
    """The candidates worth showing, filtered PER CANDIDATE not per list.

    THE BUG THIS REPLACES. `candidates_are_noise` rendered a whole-list verdict
    on a per-candidate property: it kept the ENTIRE shortlist the moment any one
    candidate agreed on anything, and the call site then dropped all or kept
    all. So a review naming a real venue — one candidate genuinely agrees, three
    more ranked purely on date proximity — kept all four, and the three
    date-only bookings rode into the picker beside the real one, inviting a
    wrong confirmation. (María Victoria's Sintra / Quinta de Regaleira review was
    the reported case; four same-venue bookings at nearby dates are exactly the
    population that scores on date and belongs to someone else.)

    Keep a candidate that agrees on venue, name or ticket; keep one that
    recorded no sub-scores at all (the escape hatch — cannot be proven noise);
    drop only the ones that HAVE sub-scores and agree on nothing but the date.
    An all-noise list survives as [] — the caller keeps the existing "none
    agrees" wording for that; a mixed list loses only its date-only rows, and
    the caller says how many went and how many stayed. Those are different
    statements and must not read the same (rule 1).
    """
    rows = [c for c in (candidates or []) if isinstance(c, dict)]
    return [c for c in rows if _candidate_agrees(c) or _candidate_unscored(c)]


def candidate_noise_verdict(candidates: list) -> dict:
    """The date-only filter as one driveable decision, message and all.

    Returns {kept, dropped, state, trail}:
      state "clean"      nothing was date-only filler — the list is unchanged.
      state "filtered"   some date-only rows withheld, some real ones kept.
      state "all_noise"  nothing survived — the whole list was date-only.
    `trail` is the confidence-trail entry to append (None for "clean"). The
    three read DIFFERENTLY on purpose (rule 1): "N withheld, M kept" is not
    "none agrees, all withheld" is not silence. Kept out of process_review so
    it can be tested by calling it, not by asserting a string in the source.
    """
    rows = [c for c in (candidates or []) if isinstance(c, dict)]
    kept = surviving_candidates(rows)
    dropped = len(rows) - len(kept)
    if not rows or (kept and not dropped):
        return {"kept": rows, "dropped": 0, "state": "clean", "trail": None}
    if not kept:
        n = len(rows)
        return {"kept": [], "dropped": n, "state": "all_noise", "trail": {
            "mark": "warn",
            "text": f"<strong>{n} possible match(es) withheld</strong> — none of "
                    f"them agrees with the review on venue, guest name or any "
                    f"Zendesk ticket; they were ranked only on how close the visit "
                    f"date is to the review date. Offering bookings chosen that way "
                    f"invites a wrong confirmation, and an RCA built on somebody "
                    f"else's booking is worse than no match. Ask the guest for a "
                    f"booking reference."}}
    return {"kept": kept, "dropped": dropped, "state": "filtered", "trail": {
        "mark": "warn",
        "text": f"<strong>{dropped} date-only possible match(es) withheld</strong>, "
                f"{len(kept)} kept — the withheld ones agreed with the review on "
                f"nothing but how close the visit date is, which is not a match; the "
                f"{len(kept)} shown agree on venue, guest name or a Zendesk ticket. "
                f"Confirm one of those, or ask the guest for a booking reference."}}


def tier1_promotable(conf: float, corroboration: float,
                     threshold: float = 3.0) -> tuple:
    """May a Zendesk-sourced booking id be presented as a verified Tier 1?

    Returns (ok, why_not). Its own function so the RULE can be driven — the
    decision used to be an inline `if` inside a 2000-line coroutine, and the
    only test of it asserted that the string `_conf >= 3.0` appeared in the
    file, which is the spelling check CLAUDE.md forbids: it broke when a
    comment was added above the line and it would have passed just as happily
    against a build where the branch was unreachable.

    TWO CONDITIONS, NOT ONE. The score has to clear the threshold AND
    something other than the guest name has to agree.

    `_name_pts` returns 3.0 for a full name agreement, which cleared a 3.0
    threshold ON ITS OWN. So a review with no booking id, no venue and no city
    was auto-promoted to Tier 1 on a name alone, and the card showed
    "T1 · BID 33211960" above a trail reading venue='—' city='—' visit≈'—'.
    §10.2 already states the asymmetry for bid_indicator_check — people book
    under a partner's name, a maiden name, a company name, so venue, city and
    date decide and the name only corroborates — and this promotion rule was
    the same claim from the other direction.
    """
    if conf < threshold:
        return False, (f"indicator confidence {conf:.1f} is below the {threshold:.1f} "
                       f"needed to call this a verified match")
    if corroboration <= 0:
        return False, ("the only agreement is the guest name; nothing about the "
                       "venue, the date or the ticket corroborates it")
    return True, ""


def complete_booking_row(booking: dict, lookup) -> tuple:
    """Fill the fields the matching query never selected. (booking, trail entry)

    THE BUG. The booking panel showed Pena Palace & Park with Experience,
    TGID/TID, Vendor, Visit date, Primary guest and Booking status filled in,
    and Fulfilment type, Booking date, Partnered vendor and Lead time all "—".
    That is not a booking with empty fields; it is a PARTIAL ROW.

    _make_candidate() builds what the candidate PICKER needs — id, experience,
    tgid/tid, vendor, visit date — and the auto-promote paths hand that same
    narrow dict straight through as the matched booking. _get_booking_extra()
    IS called there, which is why booking_status and tid_name were present;
    verify_bid() — the only query that selects created_at and fulfilment_type —
    is never called on those paths at all. Those four fields were not empty in
    the warehouse. Nobody asked for them. Lead time follows, being computed
    from the booking date.

    select-candidate already merges verify_bid for exactly this reason, and
    says so in its own comment. This is that merge, in one place every path
    passes through, so no future path can forget it.

    DIRECTION OF THE MERGE: the full row underneath, the match path's values on
    top. Matching decided which booking this is and carries things the
    warehouse row does not know about (matchReasons, narrowing_path, _match);
    the warehouse fills what nobody fetched. A blank from the match path never
    beats a value from the warehouse.

    Its own function so it can be driven. The trail entry it returns is the
    part that matters most and the part a source assertion cannot check: three
    outcomes, three sentences — filled, the lookup returned nothing, the lookup
    raised — and the last two must never be readable as "this booking has no
    booking date".
    """
    if not isinstance(booking, dict) or not booking.get("id"):
        return booking, None
    before = {k for k, v in booking.items() if v not in (None, "")}
    try:
        full = lookup(str(booking["id"]))
    except Exception as e:
        log.warning(f"[complete] verify_bid raised for {booking.get('id')}: {e}")
        return booking, {"mark": "warn",
                         "text": f"<strong>Booking row not completed</strong> — the "
                                 f"lookup for the rest of BID {booking['id']} raised "
                                 f"{type(e).__name__}. The fields below came from the "
                                 f"matching query only; a dash there means we did not "
                                 f"fetch it, not that the booking has no value."}
    if not full:
        # Says the true thing. "BigQuery did not return this booking" sat on a
        # card beside a booking BigQuery had plainly returned, and a false
        # sentence in the trail is worse than a missing one.
        return booking, {"mark": "warn",
                         "text": f"<strong>Booking row not completed</strong> — BID "
                                 f"{booking['id']} matched, but the follow-up lookup "
                                 f"for its booking date and fulfilment type returned "
                                 f"nothing. Those fields are blank because they were "
                                 f"not retrieved, not because the booking has none."}
    merged = {**full, **{k: v for k, v in booking.items() if v not in (None, "")}}
    filled = sorted({k for k, v in merged.items() if v not in (None, "")} - before)
    if not filled:
        return merged, None      # nothing was missing; nothing to report
    return merged, {"mark": "pass",
                    "text": f"<strong>Booking row completed</strong> from BigQuery — "
                            f"the matching query does not select "
                            f"{', '.join(filled[:6])}"
                            + (" and others" if len(filled) > 6 else "")
                            + ", so they were fetched separately."}


def gate_name_check(booking_name, zendesk_name, zendesk_why, author_first,
                    author_last):
    """(name, checked, score, source, why) for the Tier-1 booking-id gate.

    A FUNCTION for the same reason as classify_extraction: the branch this
    came from needs a live BigQuery, so the only tests possible were source
    assertions — and a mutation forcing `name_checked = True` SURVIVED them
    all. `checked` is the entire point of the fix and nothing drove it.

    CHECKED IS NOT "THE SCORE WAS ZERO". A warehouse hash, an internal desk
    label, a blank field and a genuinely different person all score 0.0, and
    the gate read "we could not compare" as "they disagree" — routing a
    booking id the guest quoted in their own review to manual confirmation and
    reporting a disagreement that never happened.
    """
    from server.names import is_internal_booking_name
    from server.services.zendesk import _name_score

    pgn = str(booking_name or "").strip()
    if pgn and not _is_hashed_name(pgn) and not is_internal_booking_name(pgn):
        cmp_name, source = pgn, "the booking record"
        why = ""
    else:
        cmp_name = str(zendesk_name or "").strip()
        source = zendesk_why if cmp_name else ""
        why = "" if cmp_name else (zendesk_why or "")
    if not cmp_name:
        return "", False, 0.0, "", why
    return (cmp_name, True, _name_score(cmp_name, author_first, author_last),
            source, "")


async def ensure_zendesk_guest_name(booking, fallback_bid="") -> dict:
    """Ask Zendesk for the booking's guest name when the warehouse has none.

    ONE CALL SITE IS NOT EVERY PATH. `zendesk.guest_name_for_bid()` existed,
    worked, and was called from exactly one place: the Tier-1 gate, inside the
    branch that runs when the guest quoted a booking id in their own review.
    Tier-2 auto-promote, associate confirmation, manual entry and the
    attachment path never called it — so on all of those the card printed
    "the warehouse stores this as a hash — check the Zendesk ticket", telling
    the reader to go and perform a lookup THIS SYSTEM CAN PERFORM and simply
    had not attempted.

    That also makes the judgement it was retired on unsafe: "the fallbacks
    resolve it rarely" was measured, if at all, over Tier-1 traffic only.

    So it runs here, at the point every path has converged on one booking and
    just before the draft is written. Returns a dict rather than a bare name,
    because "we asked and Zendesk had nothing" and "we never asked" are the
    two things this whole function exists to keep apart:

        asked   False when the warehouse name was fine, or there was no id
        name    "" whenever nothing readable came back
        reason  why it is empty, in `GUEST_NAME_UNAVAILABLE`'s words

    NEVER RAISES and never blocks a run — a guest name is a nicety and the
    RCA is not. A failure is recorded as a failure and the run continues.
    """
    out = {"asked": False, "name": "", "reason": ""}
    if not isinstance(booking, dict):
        return out
    unusable = gate_unusable_reason(booking.get("primary_guest_name"))
    if not unusable:
        return out                      # the warehouse answered; do not ask
    if (booking.get("zendesk_guest_name") or "").strip():
        return out                      # already found, on the Tier-1 path
    bid = str(booking.get("booking_id") or booking.get("id")
              or fallback_bid or "").strip()
    if not bid:
        out["reason"] = ("there is no booking id to look a guest name up by, "
                         "so Zendesk was not asked")
        booking["zendesk_guest_name_reason"] = out["reason"]
        return out

    out["asked"] = True
    try:
        name, why = await zendesk.guest_name_for_bid(bid)
    except Exception as e:
        out["reason"] = (f"the Zendesk guest-name lookup raised "
                         f"({type(e).__name__}), so the booking's name is "
                         f"unchecked rather than absent")
        log.warning(f"[pipeline] guest-name lookup failed for {bid}: {e}")
        booking["zendesk_guest_name_reason"] = out["reason"]
        return out
    out["name"], out["reason"] = name or "", why or ""
    if out["name"]:
        booking["zendesk_guest_name"] = out["name"]
    booking["zendesk_guest_name_reason"] = out["reason"]
    return out


def gate_unusable_reason(booking_name):
    """WHICH kind of unusable the booking's own name was. A hash and a desk
    label call for different responses — one is a PII policy, the other a
    record that needs correcting — and 'no readable name' loses that."""
    from server.names import is_internal_booking_name
    pgn = str(booking_name or "").strip()
    if not pgn:
        return "no guest name"
    if _is_hashed_name(pgn):
        return "a warehouse hash"
    if is_internal_booking_name(pgn):
        return "an internal desk label"
    return ""


def classify_extraction(parsed, raw, failure=None, ai_live=True,
                        mock_mode=False):
    """(indicators, state, why) for one indicator-extraction attempt.

    A FUNCTION because the branch that used to hold this is only reachable
    with a live model, so nothing could drive it — and a mutation collapsing
    `isinstance(parsed, dict)` into `parsed or {}` SURVIVED the whole suite.
    The pure trail-line function was tested; the code deciding what to hand it
    was not, which is the same gap one layer up.

    `parsed` is what _extract_json_object returned. NOT-A-DICT IS NOT AN EMPTY
    ANSWER: coercing it to {} makes an unparseable reply indistinguishable
    from a review that named nothing, which is the whole fault this tracking
    exists to fix.
    """
    if failure is not None:
        # An unconfigured provider and a provider that broke are different
        # things to a reader: one is how this server is set up, the other is
        # worth chasing.
        if not mock_mode and not ai_live:
            return {}, "unavailable", ("the AI provider is not configured on "
                                       "this server")
        return {}, "failed", f"{type(failure).__name__}: {str(failure)[:120]}"
    if isinstance(parsed, dict):
        return parsed, "ok", ""
    return {}, "unparsed", (
        f"the model answered but the reply was not a JSON object "
        f"({len(str(raw or ''))} character(s) came back) — it may have been "
        f"truncated")


# The steps a re-run DERIVES AGAIN. Everything from the Zendesk fetch onward
# is rebuilt from scratch on every run, so the previous run's version of it is
# superseded the moment this one starts.
#
# Only the FIRST of these has to be recognised. The prior trail is written in
# order — matching, then Zendesk, then the RCA, then the reply — so the first
# re-derived entry is the boundary and everything after it goes with it,
# whatever it says. That is why this list does not have to enumerate every
# line type: a new RCA-phase line added later is dropped positionally.
_REDERIVED_LEADS = (
    "Zendesk", "Events timeline", "Internal notes", "RCA", "Reply",
    "The reply", "No approved macro", "Contact-note join", "DSS",
    "Classification failed", "The stated-issue step", "This re-run replaced",
)


def matching_history(trail) -> tuple[list, int]:
    """(the matching steps worth carrying, how many superseded ones were cut).

    THE TRAIL WAS CONTRADICTING ITS OWN CARD. On a confirmed-BID re-run the
    whole previous trail is carried forward and this run appends to it — so a
    card with 20 timeline rows, 12 dated findings and 2 routed gaps still
    carried, from the run before the booking was confirmed:

        "Zendesk was not searched — the empty events timeline is a lookup
         that never ran"
        "The reply is an approved macro — no booking was matched"
        "4 of 4 case finding(s) carry no time"
        "actions taken: no unsolved gap was found in this case"

    Every one false of the card it sat on. A reader opening the trail to find
    out why a section looked thin was told the lookup never ran, on a run that
    found four tickets. That is this project's first rule inverted: a healthy
    run wearing a broken run's report.

    THE MATCHING STEPS STAY. They are the reason a human had to confirm the
    booking, they are not re-derived on a re-run, and deleting them would make
    the confirmation impossible to revisit — which is exactly what carrying
    the trail forward was for.

    THE CUT IS COUNTED, never silent. A trail that quietly shrinks is
    indistinguishable from a run that recorded less.
    """
    rows = [t for t in (trail or []) if isinstance(t, dict)]
    for i, t in enumerate(rows):
        text = re.sub(r"<[^>]+>", "", str(t.get("text") or "")).strip()
        if any(text.startswith(lead) for lead in _REDERIVED_LEADS):
            return rows[:i], len(rows) - i
    return rows, 0


def drop_superseded_block(trail) -> tuple[list, int]:
    """(trail without the pre-confirmation block, how many rows went).

    FOR THE PATH THAT DOES NOT RE-FETCH. `matching_history` keeps ONLY the
    matching steps, which is right for a full pipeline run — everything after
    is derived again. `regenerate-rca` re-runs the model and nothing else, so
    the Zendesk lines on the trail are still true of the card and cutting them
    would delete a true record.

    Its own filter drops `RCA` and reply lines and leaves the rest, which is
    correct for one run's worth of trail and wrong for two stacked. On a real
    card that left "Zendesk was not searched — the lookup that never ran"
    sitting above "Zendesk contacts for 32885089: 4 by booking-id field", both
    marked current, on a card with twenty timeline rows.

    THE BLOCK IS BOUNDED AT BOTH ENDS. It starts at the first re-derived entry
    and ends at `Associate confirmed`, which is written the moment the prior
    trail is adopted — so everything between them belongs to the run that came
    before the confirmation, and everything after it belongs to the run that
    followed. No confirmation line means one run, and one run has nothing
    superseded in it.
    """
    rows = [t for t in (trail or []) if isinstance(t, dict)]
    _kept, cut = matching_history(rows)
    if not cut:
        return rows, 0
    start = len(_kept)
    for j in range(start, len(rows)):
        text = re.sub(r"<[^>]+>", "", str(rows[j].get("text") or ""))
        if "Associate confirmed" in text:
            return rows[:start] + rows[j:], j - start
    return rows, 0


def superseded_trail_row(n: int) -> dict | None:
    """The line that says the cut happened, or None when nothing was cut."""
    if not n:
        return None
    return {"mark": "pass",
            "text": f"<strong>{n} step(s) from the earlier run were removed"
                    f"</strong> — they described a Zendesk read and an RCA "
                    f"that this run has replaced, and they contradicted the "
                    f"card they sat on. The matching steps above are kept."}


def record_validation(notes, confidence_trail, log_fn=None) -> list:
    """Log each validator note and put it on the trail. Returns the trail.

    THE WIRING ITSELF, in a function, because a mutation that replaced the
    call site with `pass` survived: `validation_trail_rows` was thoroughly
    driven and the line that USED it was not. That is the same shape as a
    validator wired into no path — the unit is green and the product is
    unchanged — and it is the third time in this file a feeder has been the
    thing left untested.
    """
    for n in (notes or []):
        if log_fn:
            log_fn(n)
    confidence_trail.extend(validation_trail_rows(notes))
    return confidence_trail


def validation_trail_rows(notes) -> list:
    """Each validator note as a confidence-trail row.

    A FUNCTION, because the two guarantees this carries were previously pinned
    by searching pipeline source for "confidence_trail.append" inside a
    900-character window of the validate() call. That is a spelling check: it
    passes against a build where the line it names is unreachable, and it broke
    on a comment being added above it — which tells you it was measuring
    layout, not behaviour. Driving this settles both properly.

    MARKED `warn`, NEVER `pass`. These sit in the same list as the pipeline's
    own step results, and "we changed the model's answer" reported as "a step
    succeeded" is how a coerced enum becomes a trusted fact.

    An empty note list yields no rows — a validator that found nothing to
    change is not the same as one that did not run, and the trail's other
    lines carry the fact that validation happened.
    """
    return [{"mark": "warn",
             "text": f"<strong>RCA</strong> — {_html.escape(str(n))}"}
            for n in (notes or [])]


def extraction_trail_line(state: str, why: str, indicators: dict) -> dict:
    """The one trail line describing what the indicator extraction produced.

    A FUNCTION, because the branch it lives in is unreachable wherever
    BigQuery is offline — the pipeline stops before Tier 2 matching — so a
    test driving process_review() cannot see any of these cases and would
    assert nothing while looking thorough. This is the part worth pinning.

    THREE OUTCOMES, and they were all one sentence:

      * the extraction DID NOT RUN or did not produce an answer — the provider
        is unconfigured, the call raised, or the reply would not parse;
      * it ran and the review named nothing the search can use;
      * it ran and the review named something.

    The first used to render as the second, because `indicators` stayed {} on
    every failure path and a later coercion wrote `visit_date_hint = None`
    into it, making the empty dict truthy. So an outage printed "nothing
    usable was found in the review text" — blaming the guest's words for our
    own failure, in the sentence a reader acts on.

    AND "USABLE" MEANS EVERY FIELD THE SEARCH READS. It used to mean venue,
    city and visit date, then assert the search had "only the author's name to
    work with" — false whenever issue_terms exist, since they gate the whole
    shortlist step and are searched one query per term, and false whenever
    dates_mentioned exist, which drive the support-anchored search.
    """
    if state != "ok":
        return {"mark": "fail" if state == "failed" else "warn",
                "text": "<strong>Indicators could not be extracted from this "
                        "review</strong> — " + (why or "no reason was recorded")
                        + ". This is NOT a review that named nothing: the "
                          "extraction produced no answer, so the search fell "
                          "back to the author's name alone. Re-run once it is "
                          "available."}
    ind = indicators if isinstance(indicators, dict) else {}
    issue = [t for t in (ind.get("issue_terms") or []) if str(t).strip()]
    dates = [d for d in (ind.get("dates_mentioned") or []) if str(d).strip()]
    found = [v for v in (ind.get("experience_or_venue"),
                         ind.get("city_or_country"),
                         ind.get("visit_date_hint")) if v and str(v).strip()]
    extra = []
    if issue:
        extra.append(f"{len(issue)} issue phrase(s)")
    if dates:
        extra.append(f"{len(dates)} date(s) mentioned")
    anything = bool(found or issue or dates)
    return {
        "mark": "pass" if anything else "warn",
        "text": "<strong>Extracted from review:</strong> "
                f"venue='{ind.get('experience_or_venue') or '—'}' · "
                f"city='{ind.get('city_or_country') or '—'}' · "
                f"visit≈'{ind.get('visit_date_hint') or '—'}'"
                + (" · " + ", ".join(extra) if extra else "")
                + ("" if anything else
                   " — nothing usable was found in the review text, so the "
                   "search has only the author's name to work with")
                + (" — no venue, city or date, so the search leads on the "
                   "problem the guest described" if anything and not found
                   else "")}


def _venue_token_overlap(review_text: str, exp_name: str) -> bool:
    """
    Robust venue signal: True only when the review and the experience name
    share a SIGNIFICANT word, compared at word level — not the old fragile
    substring scan where any 4-char fragment could match inside the name.
    """
    return bool(_sig_tokens(review_text) & _sig_tokens(exp_name))


def venue_fallthrough(tgids, venue_signal) -> bool:
    """Should the cascade CONTINUE past a Zendesk shortlist that already
    returned bookings?

    The Zendesk step finds bookings by the guest's own tickets. That is a
    strong signal about the PERSON and no signal at all about the BOOKING: a
    frequent traveller's tickets are about whatever they last complained
    about. When the review names a venue we resolved to tgids and NONE of
    those bookings is for it, the step has answered a different question than
    the one asked, and the cascade used to stop there anyway — it stopped at
    the first path returning ANYTHING rather than the first returning
    something that MATCHED. A review about the Eiffel Tower got the guest's
    Sphere, Colosseum and cruise bookings, every one scored venue 0, while the
    tgids sat unused.

    Both halves are required. Without tgids there is no venue to search on, so
    continuing gets a worse answer than staying; with a venue signal present
    the shortlist already matched and must not be second-guessed.
    """
    return bool(tgids) and not venue_signal


def shortlist_restore(cascade_done, fell_through, saved_candidates, saved_state):
    """What the picker shows after Step 3 has had its turn at a fallthrough.

    Returns (restore, candidates, state) — restore is True only when the
    fallthrough happened AND Step 3 ended with nothing.

    THE FALLTHROUGH IS ONLY FREE IF THIS EXISTS. Continuing the cascade puts
    the shortlist aside so Step 3's answer can replace it; if Step 3 finds
    nothing and the shortlist is not put back, a review that previously showed
    three weak candidates now shows an empty picker, which reads as "this
    guest has no bookings" — a stronger and falser claim than the weak list.
    """
    if cascade_done or not fell_through or not saved_candidates:
        return False, None, None
    return True, saved_candidates, saved_state


# Where each in-flight run is, keyed by review id. In-process on purpose: the
# pipeline runs as a BackgroundTask in this same process, and if the process
# restarts the task dies with it - a persisted stage would then claim a run is
# mid-flight forever. An entry disappearing IS the signal that no run exists.
#
# This exists because Re-run gave the associate nothing for two-plus minutes:
# a dozen sequential model calls plus fifteen warehouse queries with no way to
# tell working from dead, which got read as dead.
PIPELINE_PROGRESS: dict = {}

_STAGES_TOTAL = 8

# How long one review may take before the batch runner gives up on it. A run is
# a dozen model calls plus fifteen warehouse queries; twelve minutes is roughly
# four times the slowest healthy run observed. It is a JUDGEMENT, not a
# measurement, and it is stated as one wherever it is acted on.
RUN_TIMEOUT_S = 12 * 60


def _progress(review_id: str, step: int, stage: str):
    """Record where a run is, and WHEN it last moved.

    updated_at is the heartbeat. Without it the only fact on the entry was
    "a run exists", and a run wedged at step 1 for forty minutes was reported
    in exactly the words used for one that started four seconds ago — see
    server/tiers.py::liveness, which is the reader of this field.

    A queued entry is replaced rather than updated: its started_at is when the
    review joined the queue, and carrying that into the run would date the run
    from the moment it was queued. The wait is kept as queued_at so "started
    late" stays visible.
    """
    import time as _t
    now = _t.time()
    e = PIPELINE_PROGRESS.get(review_id)
    if e is None or e.get("queued"):
        e = {"started_at": now, "queued_at": (e or {}).get("started_at")}
    e.update({"step": step, "total": _STAGES_TOTAL, "stage": stage,
              "elapsed_s": int(now - e["started_at"]), "updated_at": now,
              "queued": False})
    PIPELINE_PROGRESS[review_id] = e
    # Mirror to the durable job (if this run is one), so an instance that is
    # NOT running it can still see the progress, and so a live run renews its
    # lease. Best-effort — the in-process entry above is the source of truth
    # for this instance, and a job write must never break the run.
    try:
        from server import jobs as _jobs
        _jobs.note_progress(review_id, step, _STAGES_TOTAL, stage)
    except Exception:
        pass


def mark_queued(review_id: str, position: int, of: int, reason: str = "ingest"):
    """Record that a review has been HANDED TO the runner and has not started.

    This is the missing fact behind the reported bug. Fifteen reviews were
    ingested, one run wedged, and the thirteen behind it were never started —
    and a review that was queued and never started carried exactly the same
    evidence as a review nobody ever queued: no draft row, no progress entry,
    no line anywhere. "We have work booked for this review" and "nothing has
    ever been asked of this review" have to be different sentences.
    """
    import time as _t
    now = _t.time()
    PIPELINE_PROGRESS[review_id] = {
        "step": 0, "total": _STAGES_TOTAL, "stage": "queued",
        "queued": True, "queue_position": position, "queue_size": of,
        "queue_reason": reason,
        "started_at": now, "updated_at": now, "elapsed_s": 0,
    }


def record_run_failure(review_id: str, exc: Exception, db=None) -> bool:
    """Write a run's death onto its draft. True if a draft was there to write on.

    Shared by the pipeline's own handler and by the batch runner's watchdog: a
    run killed from outside must leave the same evidence as one that raised,
    or a timeout reads as a run that simply never happened.

    Returns False when there is no draft row — which is a real outcome (the run
    died before the early persist), not a silent no-op, and the caller says so.
    """
    own = db is None
    if own:
        db = SessionLocal()
    try:
        db.rollback()
        _d = db.query(RcaDraft).filter(RcaDraft.review_id == review_id).first()
        if not _d:
            return False
        _tr = list(_d.confidence_trail or [])
        # Defect 5: an exception is not a trail step. A title, one
        # plain-language sentence, and the raw text kept alongside for
        # the reader who wants it - behind a toggle in the UI, never
        # inline. Discarding it entirely was the other extreme: the
        # only copy then lived in a log the reader cannot reach.
        _entry = failure_entry(exc)
        # Do not stack the same failure twice. A retried run appended a
        # second identical line, so the panel grew a wall of duplicate
        # stack traces that told the reader nothing new.
        if not _tr or _tr[-1].get("text") != _entry["text"]:
            _tr.append(_entry)
        _d.confidence_trail = _tr
        _d.generated_at = datetime.utcnow()
        flag_modified(_d, "confidence_trail")
        db.commit()
        return True
    finally:
        if own:
            db.close()


class RunTimeout(Exception):
    """A run the batch runner stopped, rather than one that stopped itself.

    Its own type because the sentence a reader needs is different: nothing is
    known to be broken, the run simply outlived the budget, and that budget is
    ours rather than the model's or the warehouse's.
    """


async def run_batch(review_ids: list, reason: str = "ingest",
                    force_candidates: bool = False) -> dict:
    """Run several reviews under supervision. Returns a counted account.

    THE BUG THIS EXISTS FOR. Every ingest path used to do:

        for rid in ids:
            background_tasks.add_task(lambda x: asyncio.run(_pipeline(x)), rid)

    Starlette runs those as `for task in self.tasks: await task()` with no
    try/except (starlette/background.py). Two consequences, both silent:

      * the FIRST task to raise drops every task behind it. process_review
        opens its session OUTSIDE its own try, so a pool timeout or an
        unreachable database raises straight out of the run and takes the rest
        of the ingest with it;
      * they run strictly one at a time, so one wedged run holds every review
        behind it for as long as it lasts — and the Anthropic client's default
        read timeout is 600s with two retries, i.e. half an hour of blocking
        per call before anything gives up.

    Either way the batch stops and nothing anywhere records that it did. A
    fifteen-review ingest left thirteen reviews with no draft row, no progress
    entry and no log line naming them — indistinguishable from fifteen reviews
    nobody had ever asked for.

    So: every review is marked queued BEFORE the first one starts, each run is
    isolated so one failure cannot reach the next, each is bounded by
    RUN_TIMEOUT_S, and the batch ends by logging what it could and could not
    do rather than simply ceasing.
    """
    ids = [r for r in (review_ids or []) if r]
    if not ids:
        log.info(f"[batch:{reason}] nothing to run — 0 reviews queued")
        return {"queued": 0, "completed": 0, "failed": 0, "timed_out": 0}

    for i, rid in enumerate(ids, 1):
        mark_queued(rid, i, len(ids), reason)
    log.info(f"[batch:{reason}] {len(ids)} review(s) queued, running one at a time")

    completed = failed = timed_out = 0
    for rid in ids:
        try:
            await asyncio.wait_for(
                process_review(rid, force_candidates=force_candidates),
                RUN_TIMEOUT_S)
            completed += 1
        except (asyncio.TimeoutError, TimeoutError):
            timed_out += 1
            budget = (f"{RUN_TIMEOUT_S / 60:.0f} minutes" if RUN_TIMEOUT_S >= 60
                      else f"{RUN_TIMEOUT_S:g} seconds")
            exc = RunTimeout(
                f"we stopped this run after {budget} so the reviews queued "
                f"behind it could go. That is our budget, not a failure any "
                f"service reported. Re-run the review.")
            log.error(f"[batch:{reason}] {rid} timed out after {RUN_TIMEOUT_S}s")
            try:
                if not record_run_failure(rid, exc):
                    log.error(f"[batch:{reason}] {rid} timed out with no draft "
                              f"row to record it on — it died before the early "
                              f"persist")
            except Exception:
                log.exception(f"[batch:{reason}] {rid}: could not record the timeout")
        except Exception as e:
            # One bad review must never stop the queue. This is the guarantee
            # Starlette's own loop does not give.
            failed += 1
            log.exception(f"[batch:{reason}] {rid} failed: {e}")
            try:
                record_run_failure(rid, e)
            except Exception:
                log.exception(f"[batch:{reason}] {rid}: could not record the failure")
        finally:
            # process_review pops its own entry, but a run that died before
            # reaching it would leave the review reading as queued for ever.
            PIPELINE_PROGRESS.pop(rid, None)

    log.info(f"[batch:{reason}] finished: {completed} completed, {failed} failed, "
             f"{timed_out} timed out, of {len(ids)} queued")
    return {"queued": len(ids), "completed": completed,
            "failed": failed, "timed_out": timed_out}


def run_batch_sync(review_ids: list, reason: str = "ingest",
                   force_candidates: bool = False) -> dict:
    """run_batch for a caller with no event loop — a BackgroundTask.

    A background task raising is invisible to everyone: the response has
    already gone out, so the only trace is a log line nobody is watching. The
    last resort is caught here so the batch's own account is always written.
    """
    try:
        return asyncio.run(run_batch(review_ids, reason, force_candidates))
    except Exception as e:
        log.exception(f"[batch:{reason}] the batch runner itself died: {e}")
        for rid in (review_ids or []):
            PIPELINE_PROGRESS.pop(rid, None)
        return {"queued": len(review_ids or []), "completed": 0,
                "failed": len(review_ids or []), "timed_out": 0}


def failure_entry(exc: Exception) -> dict:
    """The trail entry for a run that died, as a driveable function.

    Defect 5: an exception is not a trail step. A title, one plain-language
    sentence, and the raw text kept alongside - shown behind a toggle in the
    UI, never inline. Discarding the raw is the other failure: the only copy
    then lives in a log the reader cannot reach.

    Extracted so it can be tested by calling it. Asserting that `"raw":`
    appears somewhere in this file is a spelling check - it passes against a
    build where the line is unreachable.
    """
    return {"mark": "fail",
            "title": f"Run failed — {type(exc).__name__}",
            "text": f"<strong>Run failed</strong> — {_human_error(exc)}",
            "raw": " ".join(str(exc).split())[:4000]}


def _human_error(exc: Exception) -> str:
    """One sentence a reader can act on, never a stack trace.

    The trail rendered 500 characters of "SELECT rca_drafts.id AS
    rca_drafts_id, ..." straight into the dashboard. The SQL is in the log
    where it belongs; the panel needs to say what broke and what to do.
    """
    name = type(exc).__name__
    text = " ".join(str(exc).split())
    low = text.lower()
    # Our own watchdog, not a failure any service reported. It writes its own
    # sentence, so the generic 160-character truncation below cannot cut the
    # "Re-run the review" off the end of the only instruction in it.
    if isinstance(exc, RunTimeout):
        return text
    if "ssl connection has been closed" in low or "server closed the connection" in low:
        return ("the database connection dropped mid-run. Nothing was saved for "
                "this step. Re-run the review.")
    if "could not connect" in low or "connection refused" in low:
        return "the database was unreachable. Re-run once it is back."
    if "timeout" in low or "timed out" in low:
        return "a lookup timed out. Re-run the review."
    if "rate" in low and "limit" in low:
        return "an API rate limit was hit. Wait a minute and re-run."
    # Anything else: the exception type plus the first clause only, and never
    # the SQL SQLAlchemy staples onto the end.
    head = text.split(" [SQL:")[0].split("\n")[0]
    return f"{name}: {head[:160]}"


def partial_trail(trail: list) -> list:
    """The matching half of a trail, marked as unfinished.

    The early persist writes this and replaces whatever a completed run left,
    while generated_at is not touched until the end — so a run that dies after
    matching leaves a draft that looks finished (old timestamp, full rca_v3,
    every column populated) carrying a three-line trail with every analysis
    disclosure simply absent. Absent reads as "nothing to report".

    The final save writes the whole trail again, without this marker. If the
    marker is still on the row, the run did not finish.
    """
    return list(trail or []) + [{
        "mark": "warn",
        "text": "<strong>This run has not finished</strong> — matching is done "
                "and the analysis is still running. Everything below the match "
                "is from the PREVIOUS run. If this line is still here, the run "
                "died: re-run the review."}]


def stated_issue_entry(stated_issue: str, err: Exception | None) -> dict | None:
    """The trail line for a stated issue that came back empty, or None.

    The panel renders "Nothing was extracted — click above to write the guest's
    issue in one line." for every empty value, which is the right sentence for
    exactly one of the three ways it gets there: a review with nothing to
    extract. It is the wrong sentence for a call that threw, and the wrong
    sentence for a model that answered with whitespace — and on the card that
    prompted this, the RCA below it was full, so "nothing to extract" was
    plainly false.
    """
    if err is None and (stated_issue or "").strip():
        return None
    if err is not None:
        msg = _human_error(err).strip().rstrip(".")
        head = (f"<strong>The guest's stated issue could not be extracted"
                f"</strong> — {msg}. The line is empty because the call failed")
    else:
        head = ("<strong>The stated-issue step returned nothing</strong> — the "
                "line is empty because the model gave no answer")
    return {"mark": "warn", "text": head +
            ", which is not the same as a review with nothing to state. Write "
            "it in one line by hand, or re-run this review."}


def timeline_entry(bid, events: list, ticket_ids: list,
                   err: Exception | None) -> dict | None:
    """Why the events timeline is empty — or None when it has events.

    "No Zendesk events were found for this booking" is one sentence covering
    four situations: nothing was looked up because there was no booking id,
    the lookup threw, tickets were found but carried no usable events, and the
    honest empty where Zendesk simply has nothing for this booking. Only the
    last of those is the sentence on screen.
    """
    if events:
        return None
    if not bid:
        head = ("<strong>Zendesk was not searched</strong> — this review has "
                "no booking id or reference number to search on, so the empty "
                "events timeline is a lookup that never ran. This is not a "
                "guest who never wrote in: confirm a booking, or re-run once "
                "one is matched, and the tickets will load")
    elif type(err).__name__ == "ZendeskRateLimited":
        # NOT "the lookup failed", and the difference is what the reader should
        # do about it. A rate limit is a volume problem that clears by itself,
        # and the usual instruction — re-run it — spends more calls and makes
        # it worse. This is the one empty timeline nobody should act on.
        head = ("<strong>Zendesk rate-limited this lookup</strong> — we asked "
                "for more than the account allows per minute, usually because a "
                "bulk re-run is in flight. The events timeline is empty for that "
                "reason alone: nothing is broken and this booking's tickets are "
                "still there. Wait for the batch to finish and re-run this one "
                "review then — re-running now costs more calls and makes it "
                "worse")
    elif err is not None:
        msg = _human_error(err).strip().rstrip(".")
        head = (f"<strong>The Zendesk timeline lookup failed</strong> — {msg}. "
                f"The events timeline is empty because the search broke, not "
                f"because this booking has no tickets")
    elif ticket_ids:
        head = (f"<strong>Zendesk returned no usable events</strong> — "
                f"{len(ticket_ids)} ticket(s) matched booking {bid} "
                f"({', '.join(f'ZD-{t}' for t in ticket_ids[:4])}) but nothing "
                f"in them parsed into a timeline event")
    else:
        # The legitimate empty. Said out loud so it is not read as a failure —
        # the inverse bug, and just as bad.
        return {"mark": "pass", "text":
                f"<strong>Zendesk searched</strong> — no tickets are linked to "
                f"booking {bid}. The empty events timeline is a real answer, "
                f"not a failed lookup."}
    return {"mark": "warn", "text": head + "."}


def tone_entry(canned_list: list, l1: str, l2: str, err: Exception | None,
               sheet_reason: str = "", source: str = "") -> dict | None:
    """Whether the reply was written against approved replies, or without them.

    The reply is the one field on the card with no visible provenance. With
    tone examples it lands in Headout's voice; without them the model writes in
    its own, and the two are told apart only by reading it — which is precisely
    what an associate is trusting the pipeline to have done. "The reply is off
    tone" is then a mystery rather than a fact with a cause.

    On the card that prompted this the cause was upstream: the canned sheet is
    keyed on L1/L2, the classifier had returned neither, so the lookup was
    given nothing to match and correctly matched nothing.
    """
    # The untraceable macro is selected by state, not matched on keywords, so
    # it arrives with no L1/L2 and would otherwise fall through to "the sheet
    # is keyed on L1/L2 and this review has neither" — which would be true and
    # entirely beside the point.
    if canned_list and (canned_list[0] or {}).get("why"):
        # Not a voice reference. This macro IS the reply, unedited — so the
        # line must not read like the others, or an associate will review it
        # as model-written text and reword approved copy.
        return {"mark": "pass", "text":
                f"<strong>The reply is an approved macro, sent as written</strong> "
                f"— {(canned_list[0] or {}).get('why')}, so the “"
                f"{(canned_list[0] or {}).get('situation')}” macro is used word "
                f"for word rather than drafted. Only the guest's first name is "
                f"filled in. Nothing here was generated."}
    if canned_list and l1 and l2:
        # Short. A match is not news — it is the expected case, and four lines
        # explaining a normal outcome is the same over-writing the RCA rules
        # exist to stop. The macro is named because it is checkable; where the
        # macros came from is not, unless it is the answer to a question.
        #
        # The exception, and the reason `source` did not simply go away: a
        # sheet somebody edited this morning that silently failed to load is
        # invisible otherwise, and the card would read exactly as it does on a
        # healthy run. Callers pass a source ONLY when it is standing in for
        # one that failed (canned.source_is_degraded), so anything arriving
        # here is news by construction.
        top = (canned_list[0] or {}).get("situation") or ""
        return {"mark": "pass", "text":
                f"<strong>Reply voice</strong> — {len(canned_list)} approved "
                f"macro(s), tone only"
                + (f". Closest: “{top}”." if top else ".")
                + (f" Using {source}." if source else "")}

    if canned_list:
        # Rows came back, so this is not the empty case — but L1/L2 are worth
        # 8 of the ranking points and word overlap with the review is the rest.
        # Without a classification the sheet was still read and still returned
        # its top three, ranked on overlap alone. "Matched" and "matched well"
        # are not the same claim, and a pass line here would make the second
        # one on the evidence for the first.
        return {"mark": "warn", "text":
                f"<strong>The reply's tone examples were picked on word "
                f"overlap alone</strong> — {len(canned_list)} came back, but "
                f"the sheet ranks mainly on L1/L2 and this review has "
                f"{'neither' if not (l1 or l2) else ('no L2' if l1 else 'no L1')}"
                f". They may be from an unrelated situation. Read the reply "
                f"before sending."}
    if err is not None:
        why = (f"the canned-responses lookup failed "
               f"({_human_error(err).strip().rstrip('.')})")
    elif sheet_reason:
        # The sheet itself never produced rows. Reporting "no approved reply
        # matches Experience Issues / Meeting Point Issues" here blames the
        # taxonomy for a document nobody shared: the two are the same empty
        # list and are fixed by different people. is_live("canned") does not
        # separate them either — it only says a sheet ID is configured.
        why = sheet_reason
    elif not (l1 and l2):
        why = ("the sheet is keyed on L1/L2 and this review has neither, so "
               "there was nothing to match on")
    else:
        # Everything worked and nothing fits. Rule 20 has the model return null
        # rather than invent a reply, so the blank box downstream is a decision
        # — and an unexplained blank is exactly what this codebase keeps
        # getting wrong. Reached only after a thrown lookup, an unreadable
        # sheet and a missing classification have each been ruled out, so
        # "nothing covers this" cannot be confused with "the lookup broke".
        return {"mark": "warn", "text":
                f"<strong>No approved macro covers {l1} / {l2}</strong> — the "
                f"reply was left blank on purpose rather than invented, because "
                f"a generated reply reads exactly like an approved one on this "
                f"card and Send puts it on a public review. Write it yourself, "
                f"or add a macro for this issue."
                + (f" Searched {source}." if source else "")}
    return {"mark": "warn", "text":
            f"<strong>The reply was written without a tone reference</strong> "
            f"— {why}. It is in the model's own voice, not Headout's. Read it "
            f"before sending."}


def classification_entry(l1: str, l2: str, err: Exception | None,
                         warnings: list | None = None) -> dict | None:
    """The trail line for a classification that produced nothing, or None.

    An empty L1/L2 is never neutral, and it used to be completely silent. The
    Classification selects render blank, both insight comparisons are skipped,
    the scenario lookup runs on a pair of empty strings — and all of that looks
    exactly like a review nobody has got to yet. It is not: the classifier ran
    and returned nothing, or it threw. Those are different problems with
    different fixes, so they get different sentences.
    """
    warnings = [w for w in (warnings or []) if str(w).strip()]
    if err is None and l1 and l2 and not warnings:
        return None
    if err is not None:
        msg = _human_error(err).strip().rstrip(".")
        head = (f"<strong>Classification failed</strong> — {msg}. L1 and L2 "
                f"are empty because the classifier errored")
    elif l1 and l2:
        # It recovered. Worth a line anyway: "Recovered L1 to 'Experience
        # Issues' based on L2 match" means the model named a category that
        # does not exist and the validator picked one for it. The selects
        # look identical to a clean run, and the reader should know a repair
        # fired before they trust the tag comparisons keyed on it.
        return {"mark": "warn", "text":
                "<strong>The classification was repaired</strong> — the model's "
                f"answer did not validate and was corrected to {l1} / {l2}. "
                + _why(warnings) +
                " Check it against the review before trusting the comparisons "
                "keyed on it."}
    else:
        absent = "L1 or L2" if not (l1 or l2) else ("L2" if l1 else "L1")
        head = (f"<strong>The classifier returned no {absent}</strong> — the "
                f"Classification selects are empty for that reason")
    return {"mark": "warn", "text": head +
            ", not because this review has no category. " + _why(warnings) +
            " Everything keyed on L1/L2 was skipped with it: the support-tag "
            "comparison, the review-variant comparison and the scenario "
            "lookup. Set the classification by hand, or re-run this review."}


# The two lookups keyed on a booking id, and what each one's silence would be
# mistaken for. Held as data rather than two near-identical functions: this is
# ONE rule about ONE guard, and "the same rule implemented twice" is the defect
# this codebase keeps rediscovering — the Python/JS isConversation split, the
# two Slack composers, the two Actions Taken generations.
# ONLY SLACK. Zendesk's version of this lives in `timeline_entry`, which
# already told the four no-events causes apart long before this function
# existed; adding a second one here put the same sentence on the card twice.
# The mention search had no equivalent, which is why this remains.
_NOT_SEARCHED = {
    "slack":   ("Slack", "mention search",
                "a booking nobody discussed internally", "the mentions"),
}


def not_searched_entry(which: str) -> dict:
    """The trail line for a lookup skipped because there is no booking id yet.

    THE MOST COMMON STATE ON THE CARD, AND IT WAS SILENT. Every ticket search
    is keyed on a booking id, so `if bid_for_zd:` skips Zendesk entirely on the
    first run of nearly every review — and it skipped it in exactly the way a
    booking with no tickets looks: empty timeline, empty contacts panel,
    nothing in the trail. The other Zendesk trail entries all hang off
    `search_tally`, which does not exist until a search has run, so they stayed
    quiet as well.

    Worse than invisible: an empty record is what tips the RCA prompt into
    narrating the guest's review instead of listing events, so the card looked
    fullest exactly when nobody had been asked anything.

    A function rather than an inline dict for the reason dss_entry is one —
    the alternative is asserting the sentence appears in pipeline.py, which
    passes just as happily against a build where the branch is unreachable.
    """
    name, lookup, mistaken_for, what_loads = _NOT_SEARCHED[which]
    return {"mark": "warn",
            "text": f"<strong>{name} was not searched.</strong> Every {lookup} "
                    f"is keyed on a booking id and this review has none yet, "
                    f"so no search ran — this is not {mistaken_for}. Confirm a "
                    f"booking, or re-run once one is matched, and "
                    f"{what_loads} will load."}


def dss_entry(dss_rec, err: Exception | None, live: bool,
              l1: str = "", l2: str = "") -> dict | None:
    """The trail line for the DSS lookup. None only when a row was matched.

    THE CARD SAYS "No DSS row was matched for this classification." That
    sentence is a claim about the playbook, and it was printed for four
    different situations, three of which it is false for:

      * the DSS sheet is not configured on this server, so nothing was opened;
      * the sheet was opened and came back with no rows at all — a broken
        share, a permissions change, an empty export;
      * the lookup raised;
      * the tabs were searched and genuinely nothing fits.

    Only the last is "no row matched". The other three are the playbook being
    unavailable, and the resolution below is then being checked against
    nothing while the card implies it was checked against everything. Every
    other lookup in this pipeline has an entry of this shape — the timeline,
    the tone reference, the classification, the stated issue. The DSS had
    none: it was the one step that could fail in total silence.
    """
    rec = dss_rec if isinstance(dss_rec, dict) else {}
    if err is not None:
        return {"mark": "warn", "text":
                f"<strong>The DSS lookup failed</strong> — "
                f"{_human_error(err).strip().rstrip('.')}. The playbook was not "
                f"read, so nothing below has been checked against it. This is "
                f"not 'no row matched'."}
    if not live:
        return {"mark": "warn", "text":
                "<strong>DSS is not connected on this server</strong> — the "
                "playbook sheet was never opened, so no row could be matched. "
                "The resolution below has been checked against nothing."}
    if rec.get("out_of_scope"):
        # The sheet's own scope, correctly reported. A finding, not a fault.
        return {"mark": "pass", "text":
                "<strong>DSS has no tab for this issue</strong> — "
                + str(rec.get("type_reason") or
                      f"the sheet does not cover {l2 or 'this L2'}") +
                ". The playbook was read and does not speak to this case."}
    if not rec:
        return {"mark": "warn", "text":
                "<strong>The DSS sheet returned no rows at all</strong> — the "
                "lookup ran and the playbook came back empty, which is a "
                "problem with the sheet rather than with this review. Nothing "
                "below has been checked against it."}
    if not rec.get("action"):
        return {"mark": "warn", "text":
                "<strong>DSS searched, no row matched</strong> — the tabs were "
                "read"
                + (f" for {l1} / {l2}" if (l1 and l2) else
                   " with no classification to key on") +
                " and nothing fits this case. The playbook is available; it "
                "simply does not cover this."}
    return None


def shape_counts_entry(timeline) -> dict | None:
    """The trail line for what the timeline shaping collapsed, dropped or
    re-attributed. `None` when no shaping happened.

    EXTRACTED FROM `process_review` BECAUSE IT COULD NOT BE TESTED THERE. The
    only guard on it was `assert "actor_corrected" in inspect.getsource(...)`,
    and a mutation replacing the condition with `if False:` survived a full
    suite: the string still appears in the source of a build where the clause
    is unreachable. That is the spelling check this repo's second rule is
    about, and it was sitting on the one clause that reports the model being
    overruled on a fact.

    A function rather than an inline block for the same reason `dss_entry` and
    `not_searched_entry` are functions.
    """
    _sc = next((e.get("_shape_counts") for e in (timeline or [])
                if isinstance(e, dict) and e.get("_shape_counts")), None)
    if not _sc:
        return None
    # WHAT THE SHAPING COLLAPSED OR DROPPED. Legitimate, and invisible: a
    # timeline that went from ten rows to eight looks exactly like a booking
    # with eight events.
    _bits = [f"{_sc['raw']} ticket event(s) read, {_sc['shown']} shown"]
    if _sc.get("dropped_by_model"):
        _bits.append(f"{_sc['dropped_by_model']} judged to have no "
                     f"readable content")
    _bits.append("the rest were collapsed as one action at one moment")
    if _sc.get("actor_corrected"):
        # A DIFFERENT KIND OF REPAIR from collapsing, so it gets its own
        # clause. Collapsing is the model doing what it was asked; this is the
        # model having been overruled on a fact, and a reader is entitled to
        # know a row is not attributed the way it was written.
        _bits.append(f"{_sc['actor_corrected']} row(s) the summariser "
                     f"labelled as the guest were re-attributed from "
                     f"the raw ticket — no guest acted on them")
    return {"mark": "warn",
            "text": "<strong>Events timeline:</strong> " + "; ".join(_bits)
                    + ". Nothing was deleted — open a ticket to see every "
                      "comment."}


def _why(warnings: list) -> str:
    """The classifier's own account of what went wrong, or a note that it gave
    none. It records a precise reason for every failure — an L1 outside the
    taxonomy, JSON that did not parse, a sub-theme with no framework — and all
    of it went to the log. "The classifier returned no L1 or L2" was the one
    part of the answer with nothing actionable in it.
    """
    if not warnings:
        return "It gave no reason for this, which is itself worth reporting."
    return "It reported: " + "; ".join(str(w).strip().rstrip(".")
                                       for w in warnings[:4]) + "."


def _prev_hand_typed_actions(review_id: str):
    """The previous Actions Taken, read in a SHORT session of its own.

    Why not on the run's `db` session: this read happens in step 7, and the
    next thing done on the session is ~120 lines below at
    `await translate_outgoing(...)` — a model call. A transaction opened here on
    `db` would still be open across that await, i.e. a connection sitting
    idle-in-transaction for the length of a model round-trip. Neon drops such a
    connection, and the next query on `db` (step 14) then dies with
    `PendingRollbackError: Can't reconnect until invalid transaction is rolled
    back`, killing every run in the save phase. Owning the session here — open,
    read, close — leaves the run's session with no transaction across the call.

    ONLY THE ROWS A PERSON TYPED. The stored column is mostly model output, so
    subtracting what the PREVIOUS gaps explain leaves a person's rows; where
    those gaps were never stored nothing can be subtracted, and the leftovers
    are counted as unattributable rather than reported as hand-added. The row is
    already on disk (step 5b persists the match), so the query is the honest way
    to reach the previous Actions Taken.
    """
    from server.checklist import hand_typed_actions
    s = SessionLocal()
    try:
        row = (s.query(RcaDraft)
               .filter(RcaDraft.review_id == review_id).first())
        actions, unattributed = hand_typed_actions(
            row.actions_taken if row else None,
            (((row.rca_v3 or {}) if row else {})
             .get("what_went_wrong") or {}).get("gaps"))
        return (actions or None), unattributed
    finally:
        s.close()


def _needs_booking_extra(booking: dict, candidate_state: bool) -> bool:
    """A matched booking that no path has enriched with _get_booking_extra yet.

    isPartnered and amountUSD (the DSS partnered filter and the value note)
    come from _get_booking_extra, a separate fulfilments/vendor query. Several
    match paths set `booking` straight from verify_bid and never merged it — the
    direct-BID path (a booking id in the review or an attachment, the commonest
    match) among them — so DSS ran with is_partnered unknown and no value.

    The signal is the PRESENCE of "isPartnered", not its truthiness: a path that
    enriched leaves it set even when the answer is None ("we asked the vendor
    join, nobody said"), which is a different fact from never having asked. Only
    a booking missing the key entirely still needs the query."""
    return bool(booking and not candidate_state and booking.get("id")
                and "isPartnered" not in booking)


async def process_review(review_id: str, force_candidates: bool = False):
    """
    force_candidates: an associate re-ran a review whose booking they had already
    confirmed. They are asking to see the options again, so matching must present
    the candidate list rather than silently auto-promoting the best one — which
    would drop them straight back into a confirmed state they were trying to
    leave.

    THE SESSION IS OPENED INSIDE THE TRY, deliberately. It used to be opened
    one line above it, which meant a pool timeout or an unreachable database
    raised straight out of this function — past its own handler, past the
    failure it records, past its finally. run_batch catches that now so it can
    no longer take the queue down, but a function whose first statement can
    escape its own error handling is not self-consistent: the one failure it
    cannot report is the one that stops it from reporting anything.
    """
    db = None
    try:
        db = SessionLocal()
        review = db.query(Review).filter(Review.id == review_id).first()
        if not review:
            log.error(f"Review {review_id} not found")
            return

        log.info(f"[pipeline] {review_id} — start")
        _progress(review_id, 1, "matching booking")

        # ── 1. Translate ──────────────────────────────────────────────────────
        # THE RESULT IS VALIDATED BEFORE IT IS TRUSTED. The only check here used
        # to be `!= ENGLISH_ALREADY`, so a translate call that answered in the
        # WRONG language had its output stored and rendered as "English
        # translation · AI" — an English review came back in Polish and showed
        # exactly that way. `english_or_reject` runs one detect_language call on
        # the RESULT and refuses a non-English (or unverifiable) one: a
        # wrong-language translation is a failed translation, recorded `warn` on
        # the trail rather than stored as if it succeeded. "none needed" (the
        # review is English) and "wrong language" both leave body_english empty,
        # so they get DIFFERENT trail lines — the two empties must not collapse.
        _xl_trail = None
        if not review.body_english:
            try:
                result = (await claude.translate(
                    review.body_original, review.language or "auto", review_id)
                    or "").strip()
                if not result or result == "ENGLISH_ALREADY":
                    _xl_trail = {"mark": "pass",
                                 "text": "<strong>Translation</strong> — none "
                                         "needed; the review reads as English "
                                         "(ENGLISH_ALREADY)"}
                else:
                    _verdict = await reply_language.english_or_reject(result, review_id)
                    if _verdict["store"]:
                        review.body_english = result
                    _xl_trail = {"mark": "pass" if _verdict["store"] else "warn",
                                 "text": "<strong>Translation</strong> — "
                                         + _verdict["why"]}
                db.commit()
            except Exception as e:
                log.exception(f"Translation failed: {e}")
                _xl_trail = {"mark": "warn",
                             "text": f"<strong>Translation</strong> — the inbound "
                                     f"translation call failed ({e}); the review "
                                     f"is shown in its original language"}

        # ── 1b. Name the guest's language ─────────────────────────────────────
        # OUTSIDE the block above, and that is the entire fix. The detection
        # used to sit inside `if not review.body_english:`, so it ran only when
        # the inbound translation happened on THIS run. A review translated
        # before the detection existed — or simply re-run — skipped it: the
        # translation was cached, the branch never opened, `language` kept the
        # "en" that parse_review hard-codes, and the card asked the associate
        # to type the language of a review whose original text we are holding.
        #
        # `resolve_language` says which of four things happened; the trail
        # carries it so "we could not name it" cannot be read as "we did not
        # look".
        try:
            _lang_res = await reply_language.resolve_language(review)
            if _lang_res["outcome"] == "detected":
                db.commit()
            log.info(f"[pipeline] {review_id}: reply language — "
                     f"{_lang_res['outcome']}: {_lang_res['why']}")
        except Exception as e:
            log.exception(f"Language detection failed: {e}")
            _lang_res = {"outcome": "undetected", "language": "",
                         "why": f"the language check itself failed: {e}"}

        review_text = review.body_english or review.body_original

        # Text used for MATCHING indicators. Translation is lossy for anything
        # that does not read as prose: Claude drops trailing metadata lines such
        # as "Reference number: Salt mines Krakow", which is frequently the only
        # venue in the whole review. Venue and guest names are proper nouns and
        # survive untranslated, so matching reads the original alongside the
        # translation. RCA/classification keep using review_text unchanged.
        _orig = (review.body_original or "").strip()
        _eng  = (review.body_english or "").strip()
        match_text = _eng if not _orig else (
            _eng if _orig in _eng else (f"{_eng}\n{_orig}".strip() if _eng else _orig))

        # ── Instrumentation counters ──────────────────────────────────────────
        #
        # A COUNTER, NOT A DICT, AND THAT IS THE POINT. This was a plain dict
        # with a fixed key list, so `_ctr["k"] += 1` on an undeclared key threw
        # KeyError and took down the WHOLE RUN. Six keys were being incremented
        # that no one had declared:
        #
        #   indicator_mismatch, t1_name_uncheckable, t2_extraction_unavailable,
        #   t2_shortlist_crashed, t2_text_bid_dropped, t2_ticket_no_bid
        #
        # Every one of them sits on a DIAGNOSTIC branch — the code that runs
        # when something unusual happened and exists to explain it. So the
        # pipeline crashed precisely on the reviews that most needed
        # explaining, and on the way out it discarded the RCA validation:
        # `except Exception: keeping raw output` then stored the model's
        # unchecked answer, fabricated timeline rows and all. Two live drafts
        # ended that way, both reading "Run failed — KeyError:
        # 't2_ticket_no_bid'" at the bottom of an otherwise plausible card.
        #
        # An instrumentation counter must never be able to fail a run. The keys
        # below stay declared as documentation of what is measured; Counter
        # means a new or misspelled one is a wrong number rather than an
        # outage.
        _ctr = _Counter({
            "bid_attachment": 0, "bid_regex": 0, "bid_manual": 0, "bid_none": 0,
            "t1_attachment_confirmed": 0, "t1_manual_confirmed": 0,
            "t1_regex_verified": 0, "t1_regex_downgraded": 0, "t1_bq_missed": 0,
            "t1_auto_promoted": 0,
            "t2_venue_mapped": 0, "t2_venue_not_resolved": 0,
            "t2_auto_promoted": 0, "t2_candidates": 0, "t2_untraceable": 0,
            "untraceable_total": 0, "untraceable_has_signals": 0,
            # Step 2 — Zendesk requester lookup
            "t2_zendesk_lookup_attempted": 0,
            "t2_zendesk_auto":             0,
            "t2_zendesk_candidates":       0,
            "t2_zendesk_no_match":         0,
            # Step 3 — BQ venue+date paths
            "t2_bq_venue_date_30_auto":    0,
            "t2_bq_venue_date_30":         0,
            "t2_bq_venue_date_60_auto":    0,
            "t2_bq_venue_date_60":         0,
            "t2_bq_date_only_loose":       0,
            # Step 3c — BQ support-contact anchored search
            "t2_bq_support_attempted":     0,
            "t2_bq_support_candidates":    0,
            "t2_bq_support_no_match":      0,
            "t2_zendesk_truncated":        0,
            # The six that were incremented without ever being declared.
            "indicator_mismatch":          0,
            "t1_name_uncheckable":         0,
            "t2_extraction_unavailable":   0,
            "t2_shortlist_crashed":        0,
            "t2_text_bid_dropped":         0,
            "t2_ticket_no_bid":            0,
        })

        # ── 2. BID extraction + source detection ─────────────────────────────
        confidence_trail = []
        bid_source = None

        ref_in_text = re.search(BID_REGEX,
                                f"{review.body_original or ''}\n{review_text or ''}")
        if ref_in_text and not review.reference_number:
            review.reference_number = ref_in_text.group(0)
            db.commit()

        if review.reference_number:
            if review.slack_channel == "C_MANUAL":
                bid_source = "manual"
                _ctr["bid_manual"] += 1
            elif ref_in_text and ref_in_text.group(0) == review.reference_number:
                bid_source = "regex"
                _ctr["bid_regex"] += 1
            else:
                bid_source = "attachment"
                _ctr["bid_attachment"] += 1
            confidence_trail.append({
                "mark": "pass",
                "text": f"<strong>BID extracted</strong> via {bid_source}: {review.reference_number}",
            })
        else:
            _ctr["bid_none"] += 1
            # Marked "fail", the same mark the untraceable checks use for a
            # check that came back empty. "pass" rendered a green tick beside a
            # line saying nothing was found, so a review with no booking id read
            # as if that were a step which had succeeded.
            confidence_trail.append({
                "mark": "fail",
                "text": "<strong>BID</strong> — no 7–12 digit number found",
            })

        log.info(
            f"[extract] bid_source: attachment={_ctr['bid_attachment']} | "
            f"regex={_ctr['bid_regex']} | manual={_ctr['bid_manual']} | none={_ctr['bid_none']}"
        )

        # ── 3+4. Tier 1 / Tier 2 booking match ───────────────────────────────
        # An associate-confirmed candidate outranks anything matching can infer.
        # Without this, re-running the pipeline (which select-candidate does, to
        # pull Zendesk/insights for the confirmed booking) redid the search from
        # scratch and overwrote the confirmation — leaving the card half-filled
        # and the timeline empty.
        _prior = db.query(RcaDraft).filter(RcaDraft.review_id == review_id).first()
        confirmed_bid = None if force_candidates else (
            _prior.selected_candidate_bid if _prior else None)

        booking         = None
        match_tier      = None
        candidates      = []
        candidate_state = False
        narrowing_path  = None
        extracted_sigs  = {}
        narrowing_attempts = []

        if confirmed_bid and is_live("bigquery"):
            from server.services.bigquery_patch import verify_bid as _vb
            from server.services.bigquery import _get_booking_extra as _gbe
            try:
                _row = _vb(confirmed_bid)
            except Exception as e:
                log.warning(f"confirmed BID {confirmed_bid} verify failed: {e}")
                _row = None
            if _row:
                booking = _row
                booking["id"] = confirmed_bid
                try:
                    booking.update(_gbe(confirmed_bid))
                except Exception:
                    pass
                match_tier = 2
                narrowing_path = "associate_confirmed"
                # Confirming a candidate re-runs the pipeline, and this branch
                # used to start a fresh trail and write "no matching was needed"
                # over the extracted indicators. The run that actually found the
                # booking is the only thing that explains WHY this BID was on
                # offer, so confirming it erased the evidence an associate would
                # need to challenge the match later. The confirmation is one more
                # line on that history, not a replacement for it.
                # The shortlist, the extracted signals and the attempts are
                # carried WHETHER OR NOT there is a prior trail. Gated on the
                # trail they were thrown away for a reason that has nothing to
                # do with them: a draft can hold candidates and no trail — an
                # older build, or a run that stored the shortlist and died
                # before writing the trail, a case classify() explicitly
                # anticipates. The shortlist is the only record of what the
                # associate chose between, and losing it is what makes a
                # confirmation impossible to revisit.
                extracted_sigs     = dict(_prior.extracted_signals or {})
                candidates         = list(_prior.candidates_list or [])
                narrowing_attempts = list(_prior.narrowing_attempts or [])
                # THE MATCHING STEPS ONLY. Carrying the whole prior trail left
                # the previous run's Zendesk and RCA lines on a card they were
                # no longer true of — "the lookup never ran" above twenty
                # timeline rows.
                _prior_trail, _cut = matching_history(_prior.confidence_trail)
                if _prior_trail:
                    confidence_trail   = _prior_trail
                    confidence_trail.append({"mark": "pass",
                        "text": f"<strong>Associate confirmed</strong> BID {confirmed_bid} — "
                                "the steps above are from the run that found it"})
                    _sup = superseded_trail_row(_cut)
                    if _sup:
                        confidence_trail.append(_sup)
                else:
                    # Confirmed on the very first run, before any search was
                    # recorded. There is no earlier matching to preserve, so
                    # saying so is the honest answer - inventing a trail here
                    # would be worse than a short one.
                    confidence_trail.append({"mark": "pass",
                        "text": f"<strong>Associate confirmed</strong> BID {confirmed_bid} — "
                                "matching skipped"})
                    extracted_sigs["matching_skipped"] = (
                        f"An associate confirmed BID {confirmed_bid}, so no matching was needed.")
                log.info(f"[pipeline] using associate-confirmed BID {confirmed_bid}")
            else:
                confidence_trail.append({"mark": "warn",
                    "text": f"<strong>Confirmed BID {confirmed_bid}</strong> not found in "
                            "BigQuery — re-running match"})
                confirmed_bid = None
        elif confirmed_bid:
            # THE WAREHOUSE COULD NOT BE ASKED, AND THE CONFIRMATION IS STILL A
            # FACT. This branch did not exist. `select-candidate` stores the
            # booking and then re-runs the pipeline to fetch Zendesk, insights
            # and the RCA for it — and with BigQuery down the run fell straight
            # past the confirmation into the "no real booking search was
            # attempted" branch, whose save writes booking=None,
            # candidates_list=[], candidate_state=False, match_tier=None. The
            # associate's decision AND the shortlist they picked from were both
            # destroyed by the re-run their own click started, and the card came
            # back reading "Untraceable" — identical to a review nobody had ever
            # looked at.
            #
            # Step 5a already states this principle for a booking id the GUEST
            # typed: "everything above can come up empty for reasons that have
            # nothing to do with the review", so an unaskable warehouse must not
            # turn a known id into "unidentifiable". A person picking a booking
            # off a shortlist is a stronger fact than a number in review prose,
            # and it was the one the floor did not cover.
            #
            # Unverified, and said so — nothing has confirmed this booking on
            # THIS run. What it must not do is disappear.
            booking = dict(_prior.booking or {}) if _prior else {}
            booking["id"] = str(confirmed_bid)
            booking["_unverified"] = True
            booking["_unverified_reason"] = "BigQuery is not live on this server"
            booking["_match"] = {"tier": 2, "confidence": "confirmed",
                                 "method": "Associate confirmed candidate — "
                                           "not re-checked, BigQuery unavailable"}
            match_tier = 2
            narrowing_path = "associate_confirmed_unverified"
            # The run that produced the shortlist is the only explanation of why
            # this BID was on offer. Same reasoning as the verified branch above.
            # Carried unconditionally, not only when a prior TRAIL exists. The
            # shortlist is how an associate re-opens a decision they now doubt,
            # and a draft can hold one with an empty trail (an older build, or
            # a run that stored candidates and died before writing the trail) —
            # in which case an `if trail:` gate throws the shortlist away for a
            # reason that has nothing to do with it.
            extracted_sigs = dict((_prior.extracted_signals or {}) if _prior else {})
            candidates = list((_prior.candidates_list or []) if _prior else [])
            narrowing_attempts = list(
                (_prior.narrowing_attempts or []) if _prior else [])
            _prior_trail, _cut = matching_history(
                _prior.confidence_trail if _prior else [])
            if _prior_trail:
                confidence_trail = _prior_trail
                _sup = superseded_trail_row(_cut)
                if _sup:
                    confidence_trail.append(_sup)
            confidence_trail.append({"mark": "warn",
                "text": f"<strong>Associate confirmed BID {confirmed_bid}</strong>, and "
                        f"BigQuery is not live on this server — so it was NOT "
                        f"re-checked on this run. The confirmation stands because a "
                        f"person made it; nothing here has verified that the booking "
                        f"exists, and the booking panel below may be missing whatever "
                        f"only the warehouse knows."})
            log.warning(f"[pipeline] {review_id}: keeping associate-confirmed BID "
                        f"{confirmed_bid} unverified — BigQuery not live")

        if confirmed_bid and booking:
            pass   # confirmed booking in hand; skip the whole match cascade
        elif not is_live("bigquery"):
            # MOCK_MODE: fall back to existing mock-aware find_booking.
            # Say so in the trail FIRST. This branch used to record nothing at
            # all when it found nothing, so a review that was never searched
            # (BigQuery not live) was indistinguishable from one searched and
            # missed - the panel simply came up empty, which reads as "we
            # looked and there was nothing" rather than "we never looked".
            confidence_trail.append({
                "mark": "warn",
                "text": ("<strong>BigQuery is not live on this server</strong> — no real "
                         "booking search was attempted. This review is unmatched because "
                         "the warehouse was unavailable, not because it could not be "
                         "identified."),
            })
            log.error(f"[pipeline] {review_id}: BigQuery NOT live - matching ran in "
                      f"mock mode, so any review will come out unmatched")
            try:
                search_ctx = {
                    "id": review_id,
                    "author": review.author or "",
                    "reference_number": review.reference_number,
                    "signals": {},
                }
                match_result = await bq.find_booking(search_ctx)
                if match_result and match_result.get("candidates"):
                    candidates = match_result["candidates"]
                    candidate_state = True
                    booking = candidates[0]
                    match_tier = 2
                    confidence_trail.append({"mark": "pass",
                        "text": f"<strong>Mock BQ:</strong> {len(candidates)} candidates"})
                elif match_result:
                    booking = match_result
                    match_tier = booking.get("_match", {}).get("tier")
                    confidence_trail.append({"mark": "pass",
                        "text": f"<strong>Mock BQ:</strong> Tier {match_tier} — BID {booking.get('id')}"})
            except Exception as e:
                log.exception(f"Mock booking match failed: {e}")
        else:
            # ── LIVE: source-tiered Tier 1 trust ──────────────────────────────
            from server.services.bigquery_patch import verify_bid, run_narrowing_query
            from server.services import venue_resolver
            from server.prompts import venue_extraction_prompt

            if review.reference_number and bid_source:
                try:
                    bq_row = verify_bid(review.reference_number)
                except Exception as e:
                    log.warning(f"verify_bid raised: {e}")
                    bq_row = None

                if bq_row:
                    if bid_source in ("attachment", "manual"):
                        booking = bq_row
                        match_tier = 1
                        _ctr[f"t1_{bid_source}_confirmed"] += 1
                        confidence_trail.append({"mark": "pass",
                            "text": f"<strong>BQ:</strong> BID {review.reference_number} confirmed"})
                        confidence_trail.append({"mark": "pass",
                            "text": f"<strong>Tier 1</strong> confirmed via {bid_source}"})
                    else:  # regex — a found number is not yet a booking
                        # A 7-12 digit number in review prose may be an order
                        # number, a phone number or an amount, so it is only
                        # trusted once the booking it points at resembles this
                        # review. Scored, not 1-of-3 booleans: the old test used
                        # a SUBSTRING name check with a 2-char minimum ("ole"
                        # matched "Olsen") and let a visit date landing anywhere
                        # in a 30-day window promote to Tier 1 unaided.
                        from server.names import parse_author as _pa
                        from server.names import (is_internal_booking_name
                                                  as _is_internal_booking_name)
                        from server.services.zendesk import _name_score as _nsc
                        # NO `from server.services import zendesk` HERE.
                        # It is already imported at module scope, and a
                        # function-local import of the same name makes
                        # `zendesk` a LOCAL for the WHOLE of process_review —
                        # so every later use (shortlist, find_bids_by_*,
                        # get_timeline) raised UnboundLocalError on any review
                        # that did not enter this branch, inside except
                        # handlers that logged and carried on. Matching
                        # degraded in silence. This is the same shape as the
                        # validate() outage CLAUDE.md opens with.
                        # The SECOND copy of the first/last split, and it had
                        # the same fault. One rule, one place.
                        _af, _al = _pa(review.author or "")
                        verify_hits = []

                        # THE NAME, FROM WHATEVER SOURCE CAN ACTUALLY SUPPLY
                        # ONE — and an honest answer when none can.
                        #
                        # This scored the reviewer straight against
                        # bq_row["primary_guest_name"], which is a PII hash on
                        # a large share of rows. A hash, an internal desk
                        # label ("Customer Ops Lead"), a blank and a genuinely
                        # different person ALL score 0.0, so the gate below
                        # read "we could not compare" as "they disagree" and
                        # sent a booking id the guest quoted in their own
                        # review to manual confirmation.
                        #
                        # _is_hashed_name is defined at the top of THIS FILE
                        # and was applied at exactly one place — the candidate
                        # ranking — which also falls back to the ticket's own
                        # guest name. So one file held two name comparisons
                        # under different rules, and the weaker one made the
                        # more consequential decision.
                        pgn = bq_row.get("primary_guest_name") or ""
                        _zdn, _zwhy = "", ""
                        if gate_unusable_reason(pgn):
                            # The warehouse has nothing usable. Ask Zendesk —
                            # the same source the ranking path already uses.
                            _zdn, _zwhy = await zendesk.guest_name_for_bid(
                                bq_row.get("booking_id") or review.reference_number)
                        # KEPT, not just scored. This name was fetched from
                        # Zendesk, used to decide the match, and dropped — so
                        # the card read the warehouse's hash, found nothing
                        # readable, and told the associate to "check the
                        # Zendesk ticket": the exact lookup that had just been
                        # performed for them. Stored on the booking so the one
                        # readable copy survives the run that found it.
                        if _zdn and bq_row is not None:
                            bq_row["zendesk_guest_name"] = _zdn
                        _cmp_name, name_checked, name_conf, _name_src, _name_why = \
                            gate_name_check(pgn, _zdn, _zwhy, _af, _al)
                        if name_checked and name_conf >= 0.7:   # surname agrees at minimum
                            verify_hits.append(f"name({name_conf:.1f})")
                        if name_checked and _name_src != "the booking record":
                            confidence_trail.append({"mark": "pass",
                                "text": f"<strong>Guest name for BID "
                                        f"{review.reference_number}</strong> came from "
                                        f"{_name_src} — the booking record holds "
                                        f"no readable name"})
                        elif not name_checked:
                            # COUNTED AND SAID. Without this the card reports a
                            # score for a comparison that never ran.
                            _ctr["t1_name_uncheckable"] = _ctr.get(
                                "t1_name_uncheckable", 0) + 1
                            confidence_trail.append({"mark": "warn",
                                "text": f"<strong>The guest name could not be "
                                        f"compared</strong> — the booking record holds "
                                        + gate_unusable_reason(pgn)
                                        + f", and {_name_why}. This is NOT a "
                                          f"disagreement: nothing was compared."})

                        # MATCH_TEXT, NOT REVIEW_TEXT — the same surface the
                        # indicator extraction reads, and for the reason
                        # already written out where match_text is built:
                        # "Translation is lossy for anything that does not
                        # read as prose... Venue and guest names are proper
                        # nouns and survive untranslated."
                        #
                        # This check decides whether a booking id the guest
                        # quoted is trusted, and it was reading the English
                        # translation alone. A venue that survives only in the
                        # original — in a trailing "Reference number:" line,
                        # or simply not translated — could not be seen, and
                        # the card then said "whose experience is not
                        # mentioned in the review", which is a statement about
                        # the review rather than about the text searched.
                        exp_name = (bq_row.get("experienceName") or "")
                        venue_ok = bool(exp_name) and _venue_token_overlap(
                            match_text or review_text or "", exp_name)
                        if venue_ok:
                            verify_hits.append("venue")

                        date_ok = False
                        visit_str = bq_row.get("date_of_visit", "") or ""
                        if visit_str:
                            try:
                                from datetime import date as _date
                                visit_dt = _date.fromisoformat(visit_str)
                                recv_dt = (review.received_at or datetime.utcnow()).date()
                                date_ok = abs((recv_dt - visit_dt).days) <= 30
                            except Exception:
                                pass
                        if date_ok:
                            verify_hits.append("date")

                        # Date alone is not evidence — a 30-day window catches a
                        # large share of bookings. It only corroborates.
                        #
                        # The gate is UNCHANGED and deliberately strict: guests
                        # do quote reference numbers off shared vouchers,
                        # forwarded emails and screenshots, so a booking id in
                        # review text needs the name or the venue behind it.
                        # Where the name genuinely could not be compared we
                        # still cannot establish ownership, so this still asks
                        # a human — that is the correct outcome. What changes
                        # is that the card now says WHICH of the two happened,
                        # and that the name is checked against every source
                        # that can supply one before we give up on it.
                        if not (name_conf >= 0.7 or venue_ok):
                            # Weak BID — downgrade to Tier 2. booking stays
                            # unset: an unverified number must not become the
                            # matched booking.
                            bq_row["low_confidence_bid_match"] = True
                            # verify_bid returns date_of_visit / date_of_booking
                            # and no match reasons. The picker reads visitDate /
                            # bookedOn / matchReasons, so this candidate — the
                            # one case where the associate is being asked to
                            # judge a number the system does NOT trust — arrived
                            # with no date and no reasons on the card. Shape it
                            # like every other candidate and say what is weak.
                            _why = ["booking id in review text"]
                            if date_ok:
                                _why.append("date")
                            _why.append(f"name {name_conf:.1f}" if name_checked
                                        else "guest name not comparable")
                            if not venue_ok:
                                _why.append("no venue match")
                            candidates = [_shape_weak_bid(bq_row, _why)]
                            match_tier = 2
                            candidate_state = True
                            _ctr["t1_regex_downgraded"] += 1
                            # "Weak BID — number found in text" read as though
                            # the number went nowhere. It did not: BigQuery
                            # RETURNED a booking for it, and we then scored
                            # that booking's guest and venue against the
                            # review and found they disagree. Those are
                            # different facts and they need different
                            # responses — "the warehouse does not have this
                            # id" is a dead end, "the id resolves to a booking
                            # that is not this guest's" is a reference number
                            # quoted from somewhere else. The reader could not
                            # tell them apart, and a second line elsewhere on
                            # the card said the opposite.
                            _exp = (bq_row.get("experienceName") or "").strip()
                            confidence_trail.append({"mark": "warn",
                                "text": (f"<strong>BID {review.reference_number} resolves to a "
                                         f"booking that does not match this review</strong> — "
                                         if name_checked else
                                         f"<strong>BID {review.reference_number} resolves to a "
                                         f"booking we could not tie to this reviewer</strong> — ")
                                        + "BigQuery returned "
                                        + (f"'{_exp}'" if _exp else "a booking")
                                        + (f", whose guest name scores "
                                           f"{name_conf:.1f} against the reviewer"
                                           if name_checked else
                                           ", whose guest name could not be "
                                           "compared to the reviewer at all")
                                        + (" and whose experience is not mentioned in "
                                           "the review" if not venue_ok else "")
                                        + (" (the visit date does line up)" if date_ok else "")
                                        + ". The id is real; it may be someone "
                                          "else's. Needs confirmation."})
                        else:
                            booking = bq_row
                            match_tier = 1
                            _ctr["t1_regex_verified"] += 1
                            confidence_trail.append({"mark": "pass",
                                "text": f"<strong>BQ verify:</strong> regex BID confirmed via "
                                        f"{', '.join(verify_hits)}"})
                            confidence_trail.append({"mark": "pass",
                                "text": "<strong>Tier 1</strong> confirmed via regex"})
                else:
                    _ctr["t1_bq_missed"] += 1
                    confidence_trail.append({"mark": "warn",
                        "text": f"<strong>BQ:</strong> BID {review.reference_number} not found — falling through to Tier 2"})

            log.info(
                f"[tier1] source outcome: attachment_confirmed={_ctr['t1_attachment_confirmed']} | "
                f"manual_confirmed={_ctr['t1_manual_confirmed']} | regex_verified={_ctr['t1_regex_verified']} | "
                f"regex_downgraded={_ctr['t1_regex_downgraded']} | bq_missed={_ctr['t1_bq_missed']}"
            )

            # ── Tier 2 cascade (runs when no Tier 1 booking yet) ──────────────
            if booking and match_tier == 1:
                extracted_sigs["matching_skipped"] = (
                    f"The review carried booking id {review.reference_number}, "
                    "so no indicators were needed to find it.")
            if not booking or match_tier != 1:
                # Matching indicators (approved prompt): one Claude call that
                # reads the review and extracts everything usable for matching.
                # WHETHER THE EXTRACTION PRODUCED AN ANSWER AT ALL.
                #
                # `indicators` stayed {} on every failure path, and the
                # coercion twenty lines below writes `visit_date_hint = None`
                # into it — which turns {} into a TRUTHY dict. So the gate
                # `if indicators:` was always true, and a timeout, a rate
                # limit, a truncated response, unparseable prose and a review
                # that genuinely named nothing ALL rendered the same sentence:
                # the empty-review sentence (see extraction_trail_line, which
                # owns the wording). That sentence
                # blames the guest's text for our own outage, and it is the
                # one a reader acts on — they stop looking.
                #
                # Tracked explicitly rather than by truthiness, because
                # truthiness is exactly what the coercion destroyed.
                # THE ATTEMPT IS ALWAYS MADE. An earlier version vetoed the
                # call when is_live("anthropic") was false, which decided the
                # outcome from configuration rather than from what happened,
                # so a caller that HAD supplied a working client got "not
                # configured" for a call that would have succeeded.
                _parsed, _raw, _failure = None, "", None
                try:
                    from server.prompts import match_indicator_prompt
                    _pub = (review.received_at or datetime.utcnow()).date().isoformat()
                    _raw = await claude._call(
                        match_indicator_prompt(match_text or "", _pub,
                                               reviewer_name=review.author or ""),
                        max_tokens=400)
                    _parsed = claude._extract_json_object(_raw)
                except Exception as e:
                    _failure = e
                    log.warning(f"Indicator extraction failed: {e}")
                indicators, _extract_state, _extract_why = classify_extraction(
                    _parsed, _raw, _failure,
                    ai_live=is_live("anthropic"), mock_mode=MOCK_MODE)

                # THE CITY IS NOT A VENUE. "Rome, Italy" was being passed to
                # the venue resolver alongside the venue itself, which
                # guarantees a miss — no experience is named "Rome, Italy" —
                # and then reported as one of the "venues extracted", so the
                # card claimed we had extracted two venues and resolved
                # neither. The city has its own indicator and its own use in
                # bid_indicator_check; it does not belong here.
                venue_hints = [h for h in (
                    indicators.get("experience_or_venue"),) if h and str(h).strip()]
                # The model occasionally answers with a range or an explanation
                # ("2026-07-23 or 2026-07-24 (booked yesterday...)"). Anything
                # that is not a bare date cannot be parsed downstream and scored
                # zero silently, so pull the first date out or drop it.
                _vd = str(indicators.get("visit_date_hint") or "")
                _m = re.search(r"\d{4}-\d{2}-\d{2}", _vd)
                if indicators:
                    indicators["visit_date_hint"] = _m.group(0) if _m else None

                extracted_sigs["venue_hints"] = venue_hints
                extracted_sigs["match_indicators"] = indicators
                # pax is extracted and persisted, but cannot be SCORED yet: no
                # BQ query in this codebase selects a pax/quantity column, and
                # the Zendesk extractor does not populate one either.
                extracted_sigs["pax_hint"] = indicators.get("pax")
                extracted_sigs["extraction_state"] = _extract_state
                if _extract_state != "ok":
                    _ctr["t2_extraction_unavailable"] = _ctr.get(
                        "t2_extraction_unavailable", 0) + 1
                confidence_trail.append(
                    extraction_trail_line(_extract_state, _extract_why, indicators))

                # Resolve venue hints → TGIDs
                tgids = None
                if venue_hints:
                    try:
                        tgids = await venue_resolver.resolve(venue_hints)
                    except Exception as e:
                        log.warning(f"Venue resolver failed: {e}")
                if tgids:
                    _ctr["t2_venue_mapped"] += 1
                    # THE CORRECTED SPELLING GOES TO ZENDESK TOO. Resolution
                    # turns "collosseum" into the catalogue's `Colosseum`, and
                    # only BigQuery was getting the benefit — the Zendesk full
                    # text search kept the guest's misspelling, which appears
                    # in no ticket anyone wrote. The half of the search that
                    # most needed the correction never saw it.
                    _fixed = [n for n in (venue_resolver.last_resolved_names or [])
                              ][:venue_resolver.MAX_RESOLVED_NAMES]
                    if _fixed:
                        indicators["venue_names_resolved"] = _fixed
                    confidence_trail.append({"mark": "pass",
                        "text": f"<strong>Venues:</strong> {venue_hints} → {len(tgids)} TGIDs"
                                + (f", catalogue spelling: {', '.join(_fixed)} "
                                   f"— searched in Zendesk alongside what the "
                                   f"guest wrote" if _fixed else "")})
                elif venue_hints:
                    _ctr["t2_venue_not_resolved"] += 1
                    confidence_trail.append({"mark": "warn",
                        "text": (f"<strong>Venues extracted</strong> but no TGIDs "
                                 f"resolved: {venue_hints} — "
                                 f"{venue_resolver.last_failure.get('why') or 'reason not recorded'}"
                                 + (f" (table: {venue_resolver.last_failure.get('table')})"
                                    if venue_resolver.last_failure.get('table') else ""))})

                log.info(
                    f"[tier2] venue resolution: mapped_tgids={_ctr['t2_venue_mapped']} | "
                    f"not_resolved={_ctr['t2_venue_not_resolved']}"
                )

                # Author parsing — server/names.py, because this rule was
                # written twice and both copies took the LAST token as the
                # surname. On "Bhayani Salim F" that made the surname "F" and
                # threw "Salim" away entirely.
                from server.names import parse_author as _parse_author

                author_first, author_last = _parse_author(review.author or "")
                # The Trustpilot display name is often not the name the booking
                # sits under. Extraction reads the booker's name out of the
                # review body, so search that identity too instead of using it
                # only for ranking.
                ind_first, ind_last = _parse_author(
                    str(indicators.get("guest_name") or "").strip())
                extracted_sigs.update({
                    "author_first": author_first,
                    "author_last":  author_last,
                    "indicator_name_first": ind_first,
                    "indicator_name_last":  ind_last,
                })

                # review_pub_date for BQ date param
                # Date only HERE on purpose: this one is a BigQuery date
                # parameter, not a timeline timestamp. The timeline's copy is
                # `_zd_pub_date` and keeps its clock time.
                pub_date = (review.received_at or datetime.utcnow()).strftime("%Y-%m-%d")
                extracted_sigs["review_pub_date"] = pub_date

                if author_first or author_last:
                    # `last='None'` reached the screen: the f-string rendered
                    # Python's None into a sentence an associate reads. A
                    # surname we do not have is an em dash like every other
                    # absent value on this card, and the clause is dropped
                    # entirely when there is nothing to put in it — printing
                    # "last=—" beside a name that has no surname is a fact
                    # about the name, not a gap in the parse.
                    _parsed = f"first='{author_first or '—'}'"
                    if author_last:
                        _parsed += f" last='{author_last}'"
                    else:
                        _parsed += " (no surname in the display name)"
                    confidence_trail.append({"mark": "pass",
                        "text": f"<strong>Author parsed:</strong> {_parsed}"})

                # ── Name parseability check (Step 0) ─────────────────────────
                def _name_parseable(first, last):
                    if not first:
                        return False
                    if last:
                        return True
                    return len(first) >= 3 and first.isalpha()

                # Search identities, in priority order: the Trustpilot display
                # name, then the booker name extracted from the review body.
                # Deduped so a review where both agree costs one Zendesk search.
                # Triples, not pairs: the DISPLAY NAME travels with the split so
                # the search can use every token in it. "Bhayani Salim F" was
                # reduced to ("Bhayani", "F") here and the middle name was gone
                # for good — the search never saw it, and neither did the card.
                from server.names import is_placeholder as _is_placeholder

                search_identities = []
                _placeholders = []
                for _f, _l, _full in ((author_first, author_last, review.author or ""),
                                      (ind_first, ind_last,
                                       str(indicators.get("guest_name") or ""))):
                    # "customer" is not a weak name, it is the ABSENCE of one.
                    # Searched as though it were a name it returned half the
                    # desk, truncated, and produced three bookings ranked on
                    # visit date with no venue agreement - which reads as a
                    # near-miss and sends an associate through candidates
                    # assembled from nothing. Absent must end untraceable;
                    # weak may end in confirm. Different answers.
                    if _is_placeholder(_full):
                        _placeholders.append(_full.strip())
                        continue
                    if _name_parseable(_f, _l) and \
                            (_f, _l) not in [(a, b) for a, b, _ in search_identities]:
                        search_identities.append((_f, _l, _full))
                for _p in dict.fromkeys(_placeholders):
                    confidence_trail.append({"mark": "warn",
                        "text": f"<strong>No name to search</strong> — the review is "
                                f"posted as '{_p}', which identifies nobody. No name "
                                f"search was run; this is a missing identifier, not a "
                                f"search that found nothing."})
                name_parseable = bool(search_identities)

                # ── Shared helpers ────────────────────────────────────────────
                from server.services.bigquery import _get_booking_extra

                def _make_candidate(r, path_name, matched_on):
                    exp = r.get("experienceName") or r.get("experience_name") or r.get("experience") or ""
                    dov = r.get("date_of_visit") or r.get("visitDate") or ""
                    return {
                        "id":                 r.get("id", ""),
                        "primary_guest_name": r.get("primary_guest_name", ""),
                        "experience":         exp,
                        "experienceName":     exp,
                        "experience_name":    exp,
                        "date_of_visit":      dov,
                        "visitDate":          dov,
                        "vendorName":         r.get("vendorName") or r.get("partner") or "",
                        "tid":                r.get("tid"),
                        "tgid":               r.get("tgid"),
                        "vid":                r.get("vid"),
                        "matched_on":         matched_on,
                        "narrowing_path":     path_name,
                        "matchReasons":       matched_on,
                        "score":              None,
                    }

                def _run_bq_attempt(path_name, date_window, tgid_list=None):
                    """Run one BQ narrowing query — NEVER passes name (BQ has PII hashes)."""
                    try:
                        rows = run_narrowing_query(
                            tgid_list=tgid_list,
                            review_pub_date=pub_date,
                            date_window=date_window,
                            author_first="",   # never pass name — BQ has PII hash
                            author_last="",
                        )
                    except Exception as e:
                        log.warning(f"[tier2] BQ attempt {path_name} failed: {e}")
                        rows = []
                    narrowing_attempts.append({
                        "path": path_name,
                        "params": {"date_window": date_window, "use_tgids": bool(tgid_list)},
                        "result_count": len(rows),
                    })
                    confidence_trail.append({
                        "mark": "pass" if rows else "warn",
                        "text": f"<strong>BQ {path_name}:</strong> {len(rows)} row(s)",
                    })
                    return rows

                cascade_done = False
                # Set by the Zendesk branch when its shortlist matched no
                # venue; read by Step 3 below. Defined HERE so every path that
                # reaches Step 3 finds it bound — a name defined only inside
                # one branch raises NameError on all the others, which the
                # surrounding except would swallow into "matching failed".
                _venue_mismatch_fallthrough = False

                # ── Step 2: indicator shortlist ────────────────────────────
                # Search Zendesk with whatever indicators the review gave us and
                # keep only tickets satisfying ALL of them. No BigQuery here --
                # the booking id and every fact needed to judge a match are on
                # the ticket; BQ runs when the associate confirms.
                # issue_terms belongs in this gate: shortlist's second pass can
                # match on the problem alone, but only if it is called. A review
                # with no parseable name and no venue - an anonymous one, or a
                # display name like "J" - skipped this step entirely, so that
                # pass could never run on the reviews it was written for.
                _issue_terms = [t for t in (indicators.get("issue_terms") or []) if t]
                if not cascade_done and (name_parseable or venue_hints or _issue_terms):
                    # A floor for the broad searches. Guests review weeks to
                    # months after visiting, not years, and an unbounded search
                    # on a common name returns more than Zendesk will hand back.
                    _since = ((review.received_at or datetime.utcnow()).date()
                              - timedelta(days=SHORTLIST_LOOKBACK_DAYS)).isoformat()
                    _notes = []
                    try:
                        _short = await zendesk.shortlist(
                            indicators, author_first, author_last,
                            since=_since, notes=_notes,
                            review_date=(review.received_at or datetime.utcnow())
                                        .date().isoformat())
                    except Exception as e:
                        # A CRASHED SEARCH IS NOT AN EMPTY SEARCH, and until
                        # this line existed the two produced the same card:
                        # `_short = []`, no candidates, "the indicator
                        # shortlist found nothing", Untraceable. That is how an
                        # UnboundLocalError took the whole Zendesk half of
                        # matching out of service for a release without one
                        # visible symptom — every affected review simply looked
                        # unmatchable, which is a thing reviews genuinely are.
                        #
                        # The log line was already here and was not enough: the
                        # people reading these cards do not read the server log,
                        # and the card is where the claim is made.
                        log.warning(f"[tier2] shortlist failed: {e}")
                        _short = []
                        _ctr["t2_shortlist_crashed"] = _ctr.get(
                            "t2_shortlist_crashed", 0) + 1
                        confidence_trail.append({"mark": "fail",
                            "text": "<strong>The Zendesk search did not run</strong> "
                                    f"— it raised {type(e).__name__}: "
                                    f"{str(e)[:140]}. No booking was ruled out "
                                    f"and none was found: this review is "
                                    f"unmatched because the search failed, not "
                                    f"because nothing matched it. Re-run once "
                                    f"it is fixed."})

                    # Say when a search came back incomplete. Zendesk drops
                    # everything past its result cap without a word, so five
                    # candidates from a truncated search does not mean five
                    # exist - and an associate reading the card has no other
                    # way to know the right booking may never have been in it.
                    # One line per DISTINCT search, not one per call. The same
                    # label truncating twice printed the same four-line warning
                    # twice, and the trail became a wall the reader skims past -
                    # at which point the warning has stopped working. The count
                    # is kept: "twice" is a fact about how hard the search
                    # struggled, and dropping it silently would be the other bug.
                    _seen_notes = {}
                    for _n in _notes:
                        _k = (_n.get("kind"), _n.get("label"))
                        _seen_notes[_k] = _seen_notes.get(_k, 0) + 1
                    _notes = [dict(_n, _times=_seen_notes[(_n.get("kind"), _n.get("label"))])
                              for _n in {(_x.get("kind"), _x.get("label")): _x
                                         for _x in _notes}.values()]
                    for _n in _notes:
                        _again = (f" (twice)" if _n.get("_times") == 2
                                  else f" ({_n['_times']} times)" if _n.get("_times", 1) > 2
                                  else "")
                        if _n.get("kind") == "truncated":
                            _ctr["t2_zendesk_truncated"] += 1
                            confidence_trail.append({"mark": "warn",
                                "text": f"<strong>Zendesk returned too many results</strong> "
                                        f"for the {_n['label']} search{_again} and dropped "
                                        f"the rest — the right booking may not be below."})
                        elif _n.get("kind") == "failed":
                            confidence_trail.append({"mark": "warn",
                                "text": f"<strong>Zendesk {_n['label']} search failed</strong> "
                                        f"— {_n.get('detail', '')}"})
                        # The three ways a FOUND ticket still yields no
                        # candidate. Each was silent, and silence here is
                        # identical to the search never having run — which is
                        # why the reported case took three rounds to locate.
                        elif _n.get("kind") == "no_bid":
                            _ctr["t2_ticket_no_bid"] += 1
                            confidence_trail.append({"mark": "warn",
                                "text": f"<strong>A ticket matched the {_n['label']} "
                                        f"search but carried no booking id</strong>{_again} "
                                        f"— not in the booking-id field and no "
                                        f"7–12 digit number in its subject or body. "
                                        f"Ticket {_n.get('detail', '?')} was found, "
                                        f"not skipped."})
                        elif _n.get("kind") == "text_bid_unconfirmed":
                            _ctr["t2_text_bid_dropped"] += 1
                            confidence_trail.append({"mark": "warn",
                                "text": f"<strong>A booking id was read from ticket "
                                        f"text but the other indicators did not "
                                        f"agree</strong>{_again} — dropped rather than "
                                        f"offered. A number found in prose is only "
                                        f"a candidate when the name, venue and date "
                                        f"line up too (ticket "
                                        f"{_n.get('detail', '?')})."})
                        elif _n.get("kind") == "name_unverified":
                            confidence_trail.append({"mark": "warn",
                                "text": f"<strong>The ticket carries no guest "
                                        f"name</strong>{_again}, so the name was "
                                        f"not verified against it — the match "
                                        f"rests on the venue, city and date, plus "
                                        f"the search that found it. A ticket with "
                                        f"no name recorded cannot contradict the "
                                        f"reviewer, and is no longer rejected for "
                                        f"failing to repeat a name nobody filled "
                                        f"in (ticket {_n.get('detail', '?')})."})
                        elif _n.get("kind") == "ambiguous_bid":
                            _tid, _, _nums = str(_n.get("detail", "")).partition(":")
                            confidence_trail.append({"mark": "warn",
                                "text": f"<strong>A judgement was made:</strong> ticket "
                                        f"{_tid} has more than one number that could "
                                        f"be a booking id ({_nums}){_again}. The first "
                                        f"was used; the others were not."})

                    if _short:
                        # THE WAREHOUSE IS ASKED NOW, not on confirm. These
                        # cards used to be built from the ticket's custom
                        # fields alone, and those fields are blank on exactly
                        # the tickets this search returns — so the picker
                        # offered "#32885089 · —" and asked an associate to
                        # choose the booking the whole RCA is built on.
                        # `shortlist_rows` says per booking whether the
                        # warehouse had it, did not have it, or could not be
                        # asked; the card and the trail both read that rather
                        # than inferring it from an empty field.
                        from server.services.bigquery_patch import verify_bid as _vbid
                        _rows, _tally = shortlist_rows(_short, _vbid)
                        candidates = []
                        for _row in _rows:
                            _c = _make_candidate(_row, "indicator_shortlist",
                                                 _row["matched_on"])
                            _c["id"] = _row["id"]
                            _c["matched_on"] = _row["matched_on"]
                            # The Zendesk guest name, kept SEPARATELY from the
                            # one the warehouse supplies. On a desk-made or
                            # hashed booking the warehouse name is unusable and
                            # this is the only readable copy of the strongest
                            # identifier we have after the booking id — but it
                            # must not overwrite the booking's own record,
                            # which is authoritative wherever it is readable.
                            _c["zendesk_guest_name"] = _row["zendesk_guest_name"]
                            _c["details_lookup"] = _row["details_lookup"]
                            for _k in ("tid", "tgid", "vid", "bms_link",
                                       "tgid_link", "booking_status",
                                       "date_of_booking", "fulfilmentType"):
                                if _row.get(_k):
                                    _c[_k] = _row[_k]
                            candidates.append(_c)
                        _lk = shortlist_lookup_trail(_tally)
                        if _lk:
                            confidence_trail.append(_lk)
                        log.info(f"[tier2] shortlist details: {_tally}")
                        candidate_state = True
                        match_tier = 2
                        narrowing_path = "indicator_shortlist"
                        _ctr["t2_candidates"] += 1
                        confidence_trail.append({"mark": "pass",
                            "text": f"<strong>{len(candidates)} booking(s)</strong> match the "
                                    f"indicators from this review "
                                    f"({', '.join(_short[0].get('matched_on') or ['name'])}). "
                                    f"Pick the right one to continue."})
                        cascade_done = True
                    else:
                        confidence_trail.append({"mark": "warn",
                            "text": "<strong>The indicator shortlist found nothing</strong> — "
                                    "no booking on the Zendesk tickets satisfies the venue, "
                                    "city and date extracted from the review. This is that "
                                    "ONE search reporting a miss; a booking found further "
                                    "down by another route is not contradicted by it."})

                # ── Legacy requester lookup (only if the shortlist found none) ──
                if name_parseable and not cascade_done:
                    _ctr["t2_zendesk_lookup_attempted"] += 1
                    # What was ACTUALLY sent, not a reconstruction of it. The
                    # card read "Searched Zendesk as 'Bhayani F'" and that was
                    # true — which is how the dropped middle name was spotted.
                    # It stays exact for the same reason.
                    from server.names import search_tokens as _stoks
                    _names_str = ", ".join(
                        "'" + (" ".join(_stoks(full)) or
                               f"{f}{(' ' + l) if l else ''}") + "'"
                        for f, l, full in search_identities)
                    confidence_trail.append({"mark": "pass",
                        "text": f"<strong>Zendesk lookup:</strong> {_names_str}"})
                    zd_bids = []
                    bid_ticket_text = {}   # bid -> subject+body of its source ticket
                    bid_name_score  = {}   # bid -> 0..1 requester-name confidence
                    bid_signals     = {}   # bid -> ticket custom-field facts
                    for _f, _l, _full in search_identities:
                        try:
                            # No lookback_days — the service defaults to since
                            # Jan 1 of the current year. Passing 60 here silently
                            # overrode that agreed window and dropped tickets
                            # older than 60 days (guests review months later),
                            # which is how a requester's real BID went unharvested.
                            _hits, _trecs = await zendesk.find_bids_by_requester_name(
                                _f, _l, with_context=True, full_name=_full)
                            for _tr in _trecs:
                                for _tb in _tr.get("bids", []):
                                    if _tb not in bid_ticket_text:
                                        bid_ticket_text[_tb] = _tr.get("text", "")
                                    # Best name confidence seen for this BID.
                                    bid_name_score[_tb] = max(
                                        bid_name_score.get(_tb, 0.0),
                                        float(_tr.get("name_score") or 0.0))
                                    if _tr.get("signals"):
                                        bid_signals[_tb] = _tr["signals"]
                            log.info(
                                f"[tier2] zendesk requester: {len(_hits)} BIDs "
                                f"for '{_f} {_l or ''}'")
                            for _b in _hits:
                                if _b not in zd_bids:
                                    zd_bids.append(_b)
                        except Exception as e:
                            log.warning(f"[tier2] Zendesk requester lookup failed for "
                                        f"'{_f} {_l or ''}' — continuing: {e}")

                    # ── Indicator-driven Zendesk search ───────────────────────
                    # Runs ALWAYS when a venue was extracted, not as a fallback.
                    # Zendesk free-text search matches subject AND comments, so
                    # the venue the guest named is directly searchable — the
                    # indicator does the searching, it does not merely re-rank a
                    # name search. A BID returned by BOTH the name search and the
                    # venue search is the strongest evidence available short of
                    # the guest quoting the booking id.
                    name_bids  = list(zd_bids)
                    venue_bids = []
                    if venue_hints:
                        try:
                            _rq = " ".join(x for x in (author_first, author_last) if x)
                            _thits, _trecs = await zendesk.find_bids_by_text(
                                venue_hints, requester_hint=_rq or None)
                            for _tr in _trecs:
                                for _tb in _tr.get("bids", []):
                                    if _tb not in bid_ticket_text:
                                        bid_ticket_text[_tb] = _tr.get("text", "")
                            venue_bids = list(_thits)
                            for _b in _thits:
                                if _b not in zd_bids:
                                    zd_bids.append(_b)
                            log.info(f"[tier2] zendesk text search {venue_hints}: "
                                     f"{len(_thits)} BIDs")
                            confidence_trail.append({
                                "mark": "pass" if _thits else "warn",
                                "text": f"<strong>Zendesk venue search:</strong> "
                                        f"{venue_hints} → {len(_thits)} BID(s)"})
                        except Exception as e:
                            log.warning(f"[tier2] Zendesk text search failed — continuing: {e}")

                    # ── Slack search backup ───────────────────────────────────
                    # Only when Zendesk gave us nothing. Prior ORM threads often
                    # carry a BID that never made it into a ticket. Needs a user
                    # token with search:read; degrades to a no-op without one.
                    if not zd_bids:
                        _sterms = [t for t in ([" ".join(x for x in (author_first, author_last) if x)]
                                               + list(venue_hints)) if t]
                        try:
                            _shits, _srecs = await slk.search_bids(_sterms)
                            for _sr in _srecs:
                                for _sb in _sr.get("bids", []):
                                    if _sb not in bid_ticket_text:
                                        bid_ticket_text[_sb] = _sr.get("text", "")
                            for _b in _shits:
                                if _b not in zd_bids:
                                    zd_bids.append(_b)
                            if _sterms:
                                confidence_trail.append({
                                    "mark": "pass" if _shits else "warn",
                                    "text": f"<strong>Slack search (backup):</strong> "
                                            f"{len(_shits)} BID(s)"})
                        except Exception as e:
                            log.warning(f"[tier2] Slack search backup failed — continuing: {e}")

                    both_bids = {b for b in name_bids if b in set(venue_bids)}
                    if both_bids:
                        confidence_trail.append({"mark": "pass",
                            "text": f"<strong>Name + venue agree:</strong> "
                                    f"{', '.join(sorted(both_bids))}"})
                        log.info(f"[tier2] BIDs found by BOTH name and venue: {sorted(both_bids)}")

                    if zd_bids:
                        zd_candidates = []
                        for bid in zd_bids[:10]:
                            try:
                                bq_row = verify_bid(bid)
                            except Exception as e:
                                log.warning(f"[tier2] verify_bid({bid}) raised: {e}")
                                continue
                            if not bq_row:
                                log.info(f"[tier2] BID {bid} not in BQ — skipped")
                                continue

                            # Hard filter only: visit date within the current
                            # year (approved). Everything else is SCORED, not
                            # filtered — indicators rank, they don't exclude.
                            visit_str = (bq_row.get("date_of_visit") or
                                         bq_row.get("visitDate") or "")
                            if visit_str:
                                try:
                                    from datetime import date as _date
                                    visit_dt = _date.fromisoformat(visit_str[:10])
                                    if visit_dt.year < datetime.utcnow().year:
                                        log.info(f"[tier2] BID {bid} visit {visit_str} before this year — skipped")
                                        continue
                                except Exception:
                                    pass

                            zd_candidates.append((bid, bq_row))

                        # ── Indicator scoring, split so each signal is visible ──
                        # Venue is scored against venue_hints ONLY (city excluded
                        # — see the venue_hints construction above).
                        def _venue_pts(row, bid=None):
                            # Compare the review's venue hints against BOTH the
                            # BigQuery experience name and the experience/city
                            # the Zendesk ticket itself records. The ticket knows
                            # the booking's venue even when the review does not
                            # spell it out the same way.
                            hint_text = " ".join(venue_hints)
                            if not hint_text:
                                return 0.0
                            hint_toks = _sig_tokens(hint_text)
                            if not hint_toks:
                                return 0.0
                            sig = bid_signals.get(str(bid), {}) if bid else {}
                            targets = [
                                row.get("experienceName") or row.get("experience_name") or "",
                                sig.get("experience", ""),
                                sig.get("city", ""),
                                sig.get("vendor_name", ""),
                            ]
                            best = max((len(hint_toks & _sig_tokens(t)) for t in targets
                                        if t), default=0)
                            return 2.0 * best

                        def _date_pts(row):
                            v = (row.get("date_of_visit") or row.get("visitDate") or "")
                            ref = (indicators.get("visit_date_hint") or "")[:10]
                            try:
                                from datetime import date as _date
                                vd = _date.fromisoformat(str(v)[:10])
                                try:
                                    rd = _date.fromisoformat(ref)
                                except Exception:
                                    rd = (review.received_at or datetime.utcnow()).date()
                                return 1.0 / (1 + abs((rd - vd).days))
                            except Exception:
                                return 0.0

                        # Ticket relevance: how much the Zendesk ticket a BID was
                        # harvested from talks about the same thing the review
                        # does. This is the signal that survives a bare review —
                        # "it is a scam and they sell tickets at much higher
                        # prices" has no venue or date, but the requester's
                        # pricing-complaint ticket overlaps it strongly while
                        # their unrelated bookings' tickets do not.
                        # Capped so a long ticket body cannot dominate venue.
                        _review_toks = _sig_tokens(review_text or "")

                        def _ticket_pts(bid):
                            txt = bid_ticket_text.get(str(bid), "")
                            if not (txt and _review_toks):
                                return 0.0
                            return min(3.0, 0.5 * len(_sig_tokens(txt) & _review_toks))

                        def _both_pts(bid):
                            # Found independently by the name search AND the
                            # venue search — the two indicators corroborate.
                            return 4.0 if str(bid) in both_bids else 0.0

                        def _name_pts(row, bid=None):
                            # Name confidence from two places: the Zendesk
                            # requester the BID came from, and the booking's own
                            # primary guest. Scored, never a gate — a booking
                            # made under a partner's or married name is still the
                            # right booking, and the other indicators say so.
                            from server.services.zendesk import _name_score
                            zd = bid_name_score.get(str(bid), 0.0) if bid else 0.0
                            # BigQuery stores primary_guest_name as a PII hash
                            # ('qS+BQFdVbq3NdZgQ/2tJj+...'), not a name, so
                            # scoring a reviewer against it is meaningless.
                            _pg = row.get("primary_guest_name") or ""
                            bq = 0.0 if _is_hashed_name(_pg) else _name_score(
                                _pg, author_first, author_last)
                            # The ticket's own guest-name field is the cleanest
                            # source of all — "Fredrik Martin Olsen" verbatim.
                            sig = bid_signals.get(str(bid), {}) if bid else {}
                            tk = _name_score(sig.get("guest_name") or "",
                                             author_first, author_last)
                            return 3.0 * max(zd, bq, tk)

                        def _score(row, bid=None):
                            return (_venue_pts(row, bid) + _date_pts(row)
                                    + _ticket_pts(bid) + _both_pts(bid)
                                    + _name_pts(row, bid))

                        # ── Venue filter ──────────────────────────────────────
                        # When the review names a venue, only bookings for that
                        # venue are possible matches. Showing a guest's Park
                        # Guell and Lion King bookings against a salt-mine
                        # complaint is noise, not a shortlist.
                        #
                        # Applied ONLY when a venue was actually extracted AND at
                        # least one candidate matches it. With no venue, or no
                        # match at all, nothing is removed — indicators fall back
                        # to ranking, so a missing indicator never hides the
                        # right booking.
                        venue_signal = any(_venue_pts(r, b) > 0 for b, r in zd_candidates)
                        if venue_hints and venue_signal:
                            _kept = [(b, r) for b, r in zd_candidates if _venue_pts(r, b) > 0]
                            _dropped = len(zd_candidates) - len(_kept)
                            zd_candidates = _kept
                            confidence_trail.append({"mark": "pass",
                                "text": f"<strong>Venue filter:</strong> kept "
                                        f"{len(_kept)} booking(s) for {venue_hints}"
                                        + (f", dropped {_dropped} unrelated" if _dropped else "")})
                            log.info(f"[tier2] venue filter {venue_hints}: "
                                     f"kept {len(_kept)}, dropped {_dropped}")
                        ticket_signal = any(_ticket_pts(b) > 0 for b, _ in zd_candidates)
                        if not venue_signal:
                            confidence_trail.append({
                                "mark": "pass" if ticket_signal else "warn",
                                "text": "<strong>No venue match</strong> — "
                                        + (f"none of these bookings is for {venue_hints}"
                                           if venue_hints else
                                           "the review names no venue, so there is nothing to match on")
                                        + ("; ranked instead on how well each Zendesk ticket "
                                           "matches what the review complains about."
                                           if ticket_signal else
                                           "; ranked only on how close the visit date is to the "
                                           "review date, which proves very little.")})
                            log.info(f"[tier2] no venue agreement (hints={venue_hints}, "
                                     f"ticket_signal={ticket_signal})")

                        n_zd = len(zd_candidates)
                        if n_zd == 1:
                            bid, bq_row = zd_candidates[0]
                            # Being the only survivor is NOT evidence. A single
                            # wrong BID auto-promoted to Tier 1 presents as a
                            # direct match and the whole RCA is then built on
                            # another guest's booking, so promotion needs actual
                            # confidence from the indicators — not a bare count.
                            #
                            # Threshold 3.0. Reachable by a full name agreement
                            # (3.0), a two-token venue match (4.0), name+venue
                            # agreement (4.0), or partial signals combining. A
                            # first-name-only brush (0.9) cannot reach it alone.
                            _pgn  = bq_row.get("primary_guest_name") or ""
                            _conf = _score(bq_row, bid)
                            # THE NAME IS NEVER DECISIVE ON ITS OWN. _name_pts
                            # returns 3.0 for a full agreement, which met the
                            # 3.0 threshold by itself — so a review with no id,
                            # no venue and no city was auto-promoted to Tier 1
                            # on a name, and the card showed "T1 · BID
                            # 33211960" over a trail reading venue='—' city='—'
                            # visit≈'—'. People book under a partner's name, a
                            # maiden name, a company name; §10.2 already states
                            # this asymmetry for bid_indicator_check — venue,
                            # city and date decide, the name corroborates — and
                            # the promotion rule contradicted it.
                            #
                            # Corroboration is everything EXCEPT the name.
                            _corrob = (_venue_pts(bq_row, bid) + _date_pts(bq_row)
                                       + _ticket_pts(bid) + _both_pts(bid))
                            _ok, _why_not = tier1_promotable(_conf, _corrob)
                            if not _ok and _conf >= 3.0:
                                confidence_trail.append({"mark": "warn",
                                    "text": f"<strong>Not promoted to Tier 1</strong> — "
                                            f"{_why_not}. A name alone is not a verified match, "
                                            f"so this is offered as a candidate to confirm."})
                            if _ok and not force_candidates:
                                booking = bq_row.copy()
                                booking["id"] = bid
                                booking.setdefault(
                                    "experienceName",
                                    bq_row.get("experience_name", ""))
                                booking.update(_get_booking_extra(bid))
                                match_tier = 1
                                narrowing_path = "zendesk_requester_auto"
                                _ctr["t2_zendesk_auto"] += 1
                                _ctr["t1_auto_promoted"] += 1
                                _ctr["t2_auto_promoted"] += 1
                                confidence_trail.append({"mark": "pass",
                                    "text": f"<strong>Tier 1 from a Zendesk ticket</strong> — this "
                                            f"booking id came off a ticket found by guest name, not "
                                            f"from the review text, and the name is corroborated "
                                            f"({_corrob:.1f} from venue/date/ticket). Confidence "
                                            f"{_conf:.1f} (name {_name_pts(bq_row, bid):.1f} · "
                                            f"venue {_venue_pts(bq_row, bid):.1f} · date "
                                            f"{_date_pts(bq_row):.1f} · ticket {_ticket_pts(bid):.1f})"})
                            else:
                                candidates = [_make_candidate(
                                    bq_row, "zendesk_requester", ["name", "zendesk", "unconfirmed"])]
                                candidates[0]["id"] = bid
                                candidates[0]["score"] = round(_score(bq_row, bid), 2)
                                candidates[0]["score_venue"]  = round(_venue_pts(bq_row, bid), 2)
                                candidates[0]["score_date"]   = round(_date_pts(bq_row), 2)
                                candidates[0]["score_ticket"] = round(_ticket_pts(bid), 2)
                                candidates[0]["score_name"]   = round(_name_pts(bq_row, bid), 2)
                                candidates[0]["venue_signal"] = _venue_pts(bq_row, bid) > 0
                                candidate_state = True
                                match_tier = 2
                                narrowing_path = "zendesk_requester_unconfirmed"
                                _ctr["t2_zendesk_candidates"] += 1
                                _ctr["t2_candidates"] += 1
                                confidence_trail.append({"mark": "warn",
                                    "text": (f"<strong>Showing options again</strong> — re-run after a "
                                             f"confirmation, so BID {bid} (confidence {_conf:.1f}) is "
                                             f"listed for you to confirm rather than applied."
                                             if force_candidates else
                                             f"<strong>Not auto-matched</strong> — single Zendesk BID "
                                             f"{bid} at confidence {_conf:.1f} (need 3.0). The "
                                             f"booking's guest name does not match this reviewer. "
                                             f"Needs confirmation.")})
                                log.info(f"[tier2] single BID {bid} withheld: conf={_conf:.2f} "
                                         f"guest={_pgn!r} vs review={author_first} {author_last}")
                            cascade_done = True
                        elif n_zd >= 2:
                            ranked = sorted(zd_candidates,
                                            key=lambda t: _score(t[1], t[0]), reverse=True)[:5]
                            # Per candidate, not once for the set.
                            #
                            # This used to compute one list from any() over all
                            # of them and stamp it on every card: one booking
                            # with a venue match made all five say "venue", one
                            # ticket-text hit made all five say "ticket-text".
                            # The card exists to tell an associate why a booking
                            # is in front of them, and four out of five were
                            # being told a reason that belonged to a different
                            # booking. It was also literally the same list object
                            # in all five, so anything that later appended to one
                            # would have appended to all.
                            def _reasons_for(bid, row):
                                why = ["name", "zendesk"]
                                # Through the same scorers the ranking uses, so
                                # the reason on the card and the score beside it
                                # can never disagree.
                                if _both_pts(bid) > 0:
                                    why.append("name+venue")
                                elif _venue_pts(row, bid) > 0:
                                    why.append("venue")
                                elif _ticket_pts(bid) > 0:
                                    why.append("ticket-text")
                                else:
                                    why.append("date-only")
                                return why

                            candidates = [_make_candidate(row, "zendesk_requester",
                                                          _reasons_for(bid, row))
                                          for bid, row in ranked]
                            for i, (bid, row) in enumerate(ranked):
                                _vp, _dp = _venue_pts(row, bid), _date_pts(row)
                                _tp, _bp = _ticket_pts(bid), _both_pts(bid)
                                candidates[i]["id"]           = bid
                                candidates[i]["score"]        = round(_score(row, bid), 2)
                                candidates[i]["score_venue"]  = round(_vp, 2)
                                candidates[i]["score_date"]   = round(_dp, 2)
                                candidates[i]["score_ticket"] = round(_tp, 2)
                                candidates[i]["score_both"]   = round(_bp, 2)
                                candidates[i]["score_name"]   = round(_name_pts(row, bid), 2)
                                candidates[i]["venue_signal"] = _vp > 0 or _bp > 0
                            candidate_state = True
                            match_tier = 2
                            narrowing_path = ("zendesk_requester_candidates" if venue_signal
                                              else "zendesk_requester_date_only")
                            _ctr["t2_zendesk_candidates"] += 1
                            _ctr["t2_candidates"] += 1
                            confidence_trail.append({"mark": "pass" if venue_signal else "warn",
                                "text": f"<strong>{n_zd} booking(s)</strong> found on this guest's "
                                        f"Zendesk tickets and confirmed to exist in BigQuery. "
                                        f"Showing the top {len(ranked)}, ranked by "
                                        + ("venue and visit date — pick the right one."
                                           if venue_signal else
                                           "visit date only, because no venue matched. "
                                           "These are weak — check before confirming.")})
                            # STEP 3 WINS OVER A VENUE-MISMATCHED SHORTLIST.
                            #
                            # This set `cascade_done` unconditionally, so the
                            # BigQuery venue+date narrowing below never ran —
                            # even here, in the branch that has just written
                            # "no venue matched, these are weak" onto the
                            # trail. A review naming the Eiffel Tower got the
                            # guest's Sphere, Colosseum and cruise bookings,
                            # every one of them venue 0, while the tgids
                            # resolved from the review's own venue hints sat
                            # unused. The cascade stopped at the first path
                            # that returned ANYTHING, not the first that
                            # returned something that matched.
                            #
                            # So when the venue resolved to tgids and NONE of
                            # these bookings is for it, the cascade continues.
                            # Step 3 queries on the venue the review actually
                            # names; if it finds something, that is a stronger
                            # signal than "this guest once had a ticket about
                            # something else", and it replaces this shortlist.
                            # If Step 3 finds nothing, these candidates are
                            # still here — falling through costs nothing.
                            _venue_mismatch_fallthrough = venue_fallthrough(tgids, venue_signal)
                            cascade_done = not _venue_mismatch_fallthrough
                            if _venue_mismatch_fallthrough:
                                confidence_trail.append({"mark": "warn",
                                    "text": "<strong>Still looking:</strong> none of these "
                                            "is for the venue the review names, so the "
                                            "booking search continues on venue and date. "
                                            "Anything it finds replaces this list."})
                        else:
                            _ctr["t2_zendesk_no_match"] += 1
                            confidence_trail.append({"mark": "warn",
                                "text": "<strong>Zendesk:</strong> 0 verified candidates after cross-check"})
                    else:
                        _ctr["t2_zendesk_no_match"] += 1
                        confidence_trail.append({"mark": "warn",
                            "text": "<strong>Zendesk:</strong> no BIDs found for this name"})

                def _verify_and_trail(cand, window):
                    """Check a venue+date row against the review, and SAY SO.

                    Steps 3a/3b returned exactly one row for a tgid inside a
                    date window and promoted it to the confirmed booking. The
                    query never looked at the guest, so "one row came back" was
                    being reported as "this is their booking".

                    Three outcomes, three different trail lines, because
                    "checked and it disagrees" and "there was nothing to check"
                    are not the same fact and the second is the common one —
                    the guest name is a PII hash on most rows. A single line
                    saying "auto-promoted" covers all three and tells the
                    reader nothing about which one they got.
                    """
                    v = bq.verify_identifiers(cand, indicators,
                                              author_first, author_last)
                    _agree = "; ".join(v["agreed"])
                    _dis   = "; ".join(v["disagreed"])
                    _unk   = "; ".join(v["uncheckable"])
                    if v["verdict"] == "mismatch":
                        confidence_trail.append({"mark": "warn",
                            "text": f"<strong>Not auto-confirmed:</strong> one booking "
                                    f"matched {window}, but its own details disagree with "
                                    f"the review — {_dis}. Shown as a candidate to check "
                                    f"rather than confirmed."})
                    elif v["verdict"] == "match":
                        confidence_trail.append({"mark": "pass",
                            "text": f"<strong>Tier 1 auto-promote</strong> via {window} "
                                    f"(single match), and the booking's own details agree: "
                                    f"{_agree}."
                                    + (f" Could not check: {_unk}." if _unk else "")})
                    else:
                        confidence_trail.append({"mark": "warn",
                            "text": f"<strong>Tier 1 auto-promote</strong> via {window} "
                                    f"(single match) on venue and date ALONE — none of the "
                                    f"review's other identifiers could be compared against "
                                    f"this booking: {_unk}. Confirm the guest before "
                                    f"relying on it."})
                    return v

                # ── Step 3: BQ narrowing — venue+date only, never name ─────────
                # THE SHORTLIST STEP 2 LEFT BEHIND. When we fell through here
                # because none of its bookings was for the review's venue, its
                # candidates are still in `candidates` and `candidate_state` is
                # still True. Step 3 wins, so they are put aside — and RESTORED
                # if Step 3 finds nothing, because a fallthrough that ends with
                # an empty picker is worse than the weak list it replaced.
                _t2_candidates = candidates if _venue_mismatch_fallthrough else None
                _t2_state      = candidate_state if _venue_mismatch_fallthrough else None
                if _venue_mismatch_fallthrough:
                    candidates, candidate_state = [], False
                if not cascade_done:
                    if tgids:
                        # 3a: venue + date_30
                        rows = _run_bq_attempt("venue_date_30", 30, tgid_list=tgids)
                        n = len(rows)
                        if n == 1:
                            _cand = _make_candidate(rows[0], "venue_date_30_auto", ["venue", "date"])
                            _cand.update(_get_booking_extra(_cand.get("id", "")))
                            if _verify_and_trail(_cand, "venue+date_30")["verdict"] == "mismatch":
                                # OFFERED, NOT DISCARDED. A name disagreement is
                                # not proof of the wrong booking — married names,
                                # a booking made by a partner — so the row still
                                # reaches the picker. What it loses is the claim
                                # that we confirmed it.
                                candidates, candidate_state = [_cand], True
                                match_tier = 2
                                narrowing_path = "venue_date_30_unverified"
                                _ctr["t2_candidates"] += 1
                            else:
                                booking = _cand
                                match_tier = 1
                                narrowing_path = "venue_date_30_auto"
                                _ctr["t2_bq_venue_date_30_auto"] += 1
                                _ctr["t1_auto_promoted"] += 1
                                _ctr["t2_auto_promoted"] += 1
                            cascade_done = True
                        elif 2 <= n <= 5:
                            candidates = [_make_candidate(r, "venue_date_30", ["venue", "date"])
                                          for r in rows[:5]]
                            candidate_state = True
                            match_tier = 2
                            narrowing_path = "venue_date_30"
                            _ctr["t2_bq_venue_date_30"] += 1
                            _ctr["t2_candidates"] += 1
                            cascade_done = True

                    if not cascade_done and tgids:
                        # 3b: venue + date_60
                        rows = _run_bq_attempt("venue_date_60", 60, tgid_list=tgids)
                        n = len(rows)
                        if n == 1:
                            _cand = _make_candidate(rows[0], "venue_date_60_auto", ["venue", "date"])
                            _cand.update(_get_booking_extra(_cand.get("id", "")))
                            if _verify_and_trail(_cand, "venue+date_60")["verdict"] == "mismatch":
                                candidates, candidate_state = [_cand], True
                                match_tier = 2
                                narrowing_path = "venue_date_60_unverified"
                                _ctr["t2_candidates"] += 1
                            else:
                                booking = _cand
                                match_tier = 1
                                narrowing_path = "venue_date_60_auto"
                                _ctr["t2_bq_venue_date_60_auto"] += 1
                                _ctr["t1_auto_promoted"] += 1
                                _ctr["t2_auto_promoted"] += 1
                            cascade_done = True
                        elif 2 <= n <= 10:
                            candidates = [_make_candidate(r, "venue_date_60", ["venue", "date"])
                                          for r in rows[:10]]
                            candidate_state = True
                            match_tier = 2
                            narrowing_path = "venue_date_60"
                            _ctr["t2_bq_venue_date_60"] += 1
                            _ctr["t2_candidates"] += 1
                            cascade_done = True

                    # ── 3c: the guest contacted support about this ────────────
                    # Last before Untraceable, and deliberately so. Every path
                    # above is the matcher as it stands; this only ever sees a
                    # review they all gave up on, so a working match can never
                    # be displaced by it.
                    #
                    # What it adds: the fact that a booking's guest CONTACTED
                    # SUPPORT. 3a and 3b already ask which bookings match the
                    # venue and a date window; this asks which of those also
                    # produced a complaint, which is a far smaller set and one
                    # every member of which has a reason to be here. It is also
                    # a second bite at the date, matching the day and month the
                    # review named in any adjacent year rather than a window
                    # around the post date.
                    #
                    # It does NOT use the guest name. primary_guest_name is a
                    # PII hash on every booking behind a support contact -
                    # measured, 639,109 of 639,109 - so a name comparison there
                    # can only ever exclude everything.
                    if not cascade_done:
                        _sup = []
                        # Ask the search itself what it can use, rather than
                        # guessing here. "dates_mentioned: ['sometime in June']"
                        # passes a naive check and then searches nothing, and a
                        # trail line saying nothing matched when nothing was
                        # searched is the failure this codebase keeps hitting.
                        from server.services.bigquery import _iso_dates
                        _sup_dates = _iso_dates(indicators.get("dates_mentioned"))
                        if _sup_dates and tgids:
                            _ctr["t2_bq_support_attempted"] += 1
                            try:
                                _sup = await bq.find_via_support(indicators,
                                                                 tgids=tgids)
                            except Exception as e:
                                log.warning(f"[tier2] support-anchored search failed: {e}")
                                _sup = []
                            narrowing_attempts.append({
                                "path": "bq_support_contact",
                                "params": {"tgids": len(tgids), "dates": _sup_dates},
                                "result_count": len(_sup),
                            })

                        if _sup:
                            candidates = []
                            for _r in _sup[:8]:
                                # _row_to_dict names it guestName; _make_candidate
                                # reads primary_guest_name. Without this bridge
                                # every candidate card showed a blank guest — the
                                # one fact an associate picks between them on.
                                _r = dict(_r, primary_guest_name=_r.get("guestName") or "",
                                          vendorName=_r.get("partner") or "")
                                _c = _make_candidate(_r, "bq_support_contact",
                                                     _r.get("matched_on") or ["contacted support"])
                                _c["id"] = str(_r.get("id") or "")
                                _c["matched_on"] = _r.get("matched_on") or ["contacted support"]
                                _c["contact_count"] = _r.get("contact_count", 0)
                                _c["contact_tags"]  = _r.get("contact_tags", "")
                                candidates.append(_c)
                            candidate_state = True
                            match_tier = 2
                            narrowing_path = "bq_support_contact"
                            _ctr["t2_bq_support_candidates"] += 1
                            _ctr["t2_candidates"] += 1
                            confidence_trail.append({"mark": "warn",
                                "text": f"<strong>{len(candidates)} booking(s)</strong> whose guest "
                                        f"contacted support and whose name or dates match this "
                                        f"review. Nothing else matched, so these are unconfirmed — "
                                        f"check the contact before picking one."})
                            cascade_done = True
                        elif _ctr["t2_bq_support_attempted"]:
                            _ctr["t2_bq_support_no_match"] += 1
                            confidence_trail.append({"mark": "warn",
                                "text": "<strong>Support contacts:</strong> no booking with a "
                                        "support contact matches this guest or these dates"})

                    # ── the shortlist Step 3 was given the chance to beat ─────
                    # Step 3 wins WHEN IT FINDS SOMETHING. When it finds
                    # nothing, the venue-mismatched shortlist Step 2 built is
                    # still the best thing we have, and an empty picker is
                    # strictly worse than a weak one — it reads as "this guest
                    # has no bookings", which is false.
                    #
                    # This is also the reason the fallthrough is safe to take
                    # at all: the cost of continuing the cascade is zero,
                    # because nothing is thrown away by continuing it.
                    _restore, _rc, _rs = shortlist_restore(
                        cascade_done, _venue_mismatch_fallthrough,
                        _t2_candidates, _t2_state)
                    if _restore:
                        candidates, candidate_state = _rc, _rs
                        match_tier = 2
                        narrowing_path = "zendesk_requester_date_only"
                        cascade_done = True
                        confidence_trail.append({"mark": "warn",
                            "text": f"<strong>Venue search found nothing:</strong> no booking "
                                    f"for the venue this review names, in any date window. "
                                    f"Back to the {len(_t2_candidates)} booking(s) from this "
                                    f"guest's Zendesk tickets — none is for that venue, so "
                                    f"they remain weak."})

                    if not cascade_done:
                        # Date-only matching removed (approved): bookings that
                        # merely share a date window prove nothing about the
                        # reviewer. Name + venue paths exhausted → Untraceable.
                        _ctr["t2_untraceable"] += 1
                        _ctr["untraceable_total"] += 1
                        if venue_hints or author_first or author_last:
                            _ctr["untraceable_has_signals"] += 1
                        confidence_trail.append({"mark": "warn",
                            "text": "<strong>Untraceable</strong> — searched Zendesk by guest "
                                    "name and by venue, and Slack as a backup. Nothing found."})
                        cascade_done = True

                # ── Step 4: Untraceable fallback ──────────────────────────────
                if not cascade_done and not booking:
                    _ctr["t2_untraceable"] += 1
                    _ctr["untraceable_total"] += 1
                    if venue_hints or author_first or author_last:
                        _ctr["untraceable_has_signals"] += 1

                # Do NOT auto-assign Tier 2 candidates as the confirmed booking.
                # Candidates are kept for display; booking stays None until an
                # associate confirms one via select-candidate.

                log.info(
                    f"[tier1] auto_promoted_from_cascade={_ctr['t1_auto_promoted']}"
                )
                log.info(
                    f"[tier2] zendesk_lookup: attempted={_ctr['t2_zendesk_lookup_attempted']} "
                    f"auto={_ctr['t2_zendesk_auto']} candidates={_ctr['t2_zendesk_candidates']} "
                    f"no_match={_ctr['t2_zendesk_no_match']}"
                )
                log.info(
                    f"[tier2] bq_paths: v30_auto={_ctr['t2_bq_venue_date_30_auto']} "
                    f"v30={_ctr['t2_bq_venue_date_30']} v60_auto={_ctr['t2_bq_venue_date_60_auto']} "
                    f"v60={_ctr['t2_bq_venue_date_60']} date_only={_ctr['t2_bq_date_only_loose']}"
                )
                log.info(
                    f"[tier2] outcomes: auto_promoted_to_t1={_ctr['t2_auto_promoted']} | "
                    f"candidate_list={_ctr['t2_candidates']} | untraceable={_ctr['t2_untraceable']}"
                )
                log.info(
                    f"[untraceable] total={_ctr['untraceable_total']} | "
                    f"usable_signals_present={_ctr['untraceable_has_signals']}"
                )

                confidence_trail.append({
                    "mark": "pass",
                    "text": ("<strong>Result:</strong> " + (
                        "matched to one booking" if match_tier == 1 and not candidate_state else
                        f"{len(candidates)} possible match(es) — pick one to continue"
                        if candidate_state else
                        "no booking found — untraceable")),
                })

        # ── 5a. FLOOR: a BID in the review is a fact, never "untraceable" ────
        # Everything above can come up empty for reasons that have nothing to
        # do with the review: BigQuery not live (the mock branch then looks the
        # BID up in MOCK_BOOKINGS and finds nothing), a connector token
        # expired, verify_bid raising, a permissions change on the table. In
        # every one of those cases a review carrying its own booking id was
        # filed as unidentifiable, which is the one thing it demonstrably is
        # not.
        #
        # So: if the text gave us a BID and matching produced no booking, the
        # BID itself becomes the match, flagged unverified. The dashboard shows
        # the id with a warning instead of hiding the review in Untraceable,
        # and the reason is recorded rather than inferred.
        # TWO CONDITIONS WERE MISSING, and each on its own produced the card
        # the user reported: T1 "BID from the review" over a trail saying the
        # BID was not found and one booking matched the name.
        #
        # 1. `not booking` is not `nothing found`. Tier 2 puts its results in
        #    `candidates`, not in `booking`, so a review with a perfectly good
        #    name match fell into this floor and had candidate_state cleared —
        #    the picker the associate needed was deleted to make room for a
        #    booking id BigQuery had denied.
        #
        # 2. "BigQuery could not be asked" and "BigQuery was asked and said no"
        #    are different facts. The floor was written for the first: a
        #    connector down, a token expired, a permissions change. When the
        #    warehouse IS live and returns nothing, the id is a string the
        #    guest typed, not a match — and presenting it as tier 1 is a
        #    confidence claim the data contradicts.
        #
        # So: the floor is now only for the case it was written for, and only
        # when tier 2 also came up empty. A live BigQuery saying no sends the
        # review down the tier-2 indicator search and into the picker.
        # A shortlist ranked on nothing but date proximity is withheld — see
        # candidates_are_noise. Counted and SAID, never silently dropped: the
        # associate has to know a search ran and produced only noise, which is
        # a different thing from a search that produced nothing at all.
        if candidate_state and candidates:
            _v = candidate_noise_verdict(candidates)
            if _v["trail"]:
                confidence_trail.append(_v["trail"])
            if _v["state"] == "all_noise":
                # Nothing agreed on anything but the date — withhold the whole
                # list and fall back to untraceable, exactly as before.
                log.info(f"[tier2] withheld {_v['dropped']} date-only candidate(s) for {review_id}")
                candidates = []
                candidate_state = False
                match_tier = None
            elif _v["state"] == "filtered":
                # Some real, some date-only filler — keep the picker on the real
                # ones, drop only the filler.
                log.info(f"[tier2] withheld {_v['dropped']} date-only candidate(s), kept "
                         f"{len(_v['kept'])} for {review_id}")
                candidates = _v["kept"]

        untraceable_reason = None
        _bq_could_not_be_asked = not is_live("bigquery")
        if (not booking and not candidate_state and review.reference_number
                and bid_source and _bq_could_not_be_asked):
            why = "BigQuery is not live on this server"
            booking = {
                "id": str(review.reference_number),
                "_unverified": True,
                "_unverified_reason": why,
                "_match": {"tier": 1, "confidence": "unverified",
                           "method": f"BID {bid_source} from the review — {why}"},
            }
            # Tier 2, not tier 1. Tier 1 means a verified direct match, and
            # nothing here has been verified — the warehouse was never asked.
            match_tier      = 2
            candidate_state = False
            confidence_trail.append({
                "mark": "warn",
                "text": (f"<strong>BID {review.reference_number}</strong> taken from the "
                         f"review ({bid_source}) and NOT checked — {why}. Shown so "
                         f"the review is not filed as untraceable, but nothing has "
                         f"confirmed this booking exists."),
            })
            log.warning(f"[pipeline] {review_id}: unverified BID fallback "
                        f"{review.reference_number} ({why})")
        elif not booking and not candidate_state:
            # Genuinely nothing to show. Record WHY, so the panel and the
            # diagnostic can tell "nothing to search with" from "the search
            # never ran" without anyone reading the log.
            if not is_live("bigquery"):
                untraceable_reason = "BigQuery is not live on this server, so no match was attempted."
            elif review.reference_number:
                # Both facts, because they are different failures. The id was
                # denied by the warehouse AND the indicator search that ran
                # instead came up empty; saying only the first reads as though
                # nothing else was tried.
                untraceable_reason = (
                    f"BID {review.reference_number} was on the review but BigQuery "
                    f"did not return it, so it is not a match. The indicator search "
                    f"that ran instead " +
                    (f"tried {len(narrowing_attempts)} query(ies) and found nothing."
                     if narrowing_attempts else "had no usable venue, date or name "
                                                "signal to search with."))
            elif narrowing_attempts:
                untraceable_reason = (f"No BID on the review; {len(narrowing_attempts)} "
                                      f"search attempt(s) returned nothing.")
            else:
                untraceable_reason = ("No BID in the review text and no usable name or "
                                      "venue signal to search with.")
            log.info(f"[pipeline] {review_id} untraceable: {untraceable_reason}")

        # ── 5a-i. Complete the booking row before anything reads it ──────────
        # THE BUG: the booking panel showed Pena Palace & Park with Experience,
        # TGID/TID, Vendor, Visit date, Primary guest and Booking status filled
        # in, and Fulfilment type, Booking date, Partnered vendor and Lead time
        # all "—". That is not a booking with empty fields; it is a PARTIAL ROW.
        #
        # _make_candidate() builds what the candidate PICKER needs — id,
        # experience, tgid/tid, vendor, visit date — and the auto-promote paths
        # (venue_date_30_auto, venue_date_60_auto) hand that same narrow dict
        # straight through as the matched booking. _get_booking_extra() is
        # called there, which is why booking_status and tid_name ARE present,
        # but verify_bid() — the only query that selects created_at and
        # fulfilment_type — is never called on those paths at all. So those
        # four fields were not empty in the warehouse; nobody asked for them.
        # Lead time follows, because it is computed from the booking date.
        #
        # select-candidate already merges verify_bid for exactly this reason
        # and says so in its comment. This is the same merge, moved to where
        # every path passes, so no future path can forget it.
        #
        # DIRECTION OF THE MERGE: the full row underneath, the match path's own
        # values on top. Matching decided which booking this is and carries
        # things verify_bid does not know about (matchReasons, narrowing_path,
        # _match); the warehouse fills what nobody fetched. A blank from the
        # match path never beats a value from the warehouse.
        if (booking and booking.get("id") and not booking.get("_unverified")
                and is_live("bigquery")):
            from server.services.bigquery_patch import verify_bid as _vb_full
            booking, _entry = complete_booking_row(booking, _vb_full)
            if _entry:
                confidence_trail.append(_entry)

        # ── 5a-ii. Does the booking we ended up with match the review? ───────
        # A booking id verifying in BigQuery says the ID is real. It says
        # nothing about whose booking it is, and for a Tier 1 BID nothing above
        # ever asked: indicator extraction only runs when the id path fails, so
        # the review's venue, city, date and guest name were compared to
        # nothing at all. A guest quoting someone else's reference number —
        # from a shared voucher, a forwarded confirmation, a screenshot in a
        # group chat — lands here with a green trail and an RCA about a
        # stranger's trip.
        #
        # ONE PLACE, after the whole cascade, so every path that ends with a
        # booking is checked the same way: attachment, manual, regex,
        # auto-promoted tier 2, and an associate-confirmed candidate (which
        # re-enters this function with selected_candidate_bid set).
        #
        # It never unmatches. The result is a line on the trail and a line on
        # the card; the booking stands either way, because the alternative is a
        # heuristic quietly discarding correct matches.
        if booking:
            from server import bid_indicator_check as _bic
            try:
                _im = _bic.check(review_text or review.body_original or "",
                                 booking, author=review.author,
                                 received_at=review.received_at)
                confidence_trail.append(_bic.trail_entry(_im))
                if _im["state"] == "mismatch":
                    _ctr["indicator_mismatch"] = _ctr.get("indicator_mismatch", 0) + 1
                    log.warning(f"[indicators] {review_id}: booking "
                                f"{booking.get('id')} contradicts the review — "
                                f"{_im['why']}")
            except Exception as e:
                # An exception is not "no contradiction". Say which it was.
                log.warning(f"[indicators] {review_id}: check raised: {e}")
                confidence_trail.append({"mark": "warn",
                    "text": "<strong>Indicator check did not run</strong> — it "
                            f"raised {type(e).__name__}. Nothing was compared "
                            "against this booking; this is not agreement."})

        # ── 5a-bis. ENRICH THE MATCH on every path, before DSS reads it ───────
        # isPartnered and amountUSD come from _get_booking_extra, and only some
        # match paths merged it — the direct-BID path (verify_bid) did not. DSS
        # then ran with is_partnered unknown and the value note empty on the
        # commonest match. This is where every path has converged, so it is
        # where the enrichment belongs — exactly the ensure_zendesk_guest_name
        # pattern below. Idempotent: a path that already enriched has the key
        # and is skipped, so no booking is queried twice.
        if _needs_booking_extra(booking, candidate_state) and is_live("bigquery"):
            try:
                from server.services.bigquery import _get_booking_extra as _gbe2
                booking.update(_gbe2(str(booking["id"])))
                log.info(f"[enrich] {review_id}: merged booking extra for DSS "
                         f"(isPartnered={booking.get('isPartnered')}, "
                         f"amountUSD={booking.get('amountUSD')})")
            except Exception as e:
                log.warning(f"[enrich] {review_id}: booking extra fetch failed: {e}")

        # ── 5b. PERSIST THE MATCH NOW, before anything else can fail ─────────
        # The draft row used to be created only at the final save step, so a
        # confirmed Tier 1 booking sat in local variables through Zendesk,
        # classification, insights, DSS, the RCA and the response draft. Any
        # exception in those steps discarded the match, and the review then
        # showed up as Untraceable - a review whose BID we had matched
        # perfectly, presented as one we could not identify at all. Matching is
        # the expensive, load-bearing part; it gets written the moment it is
        # done. The final save updates the same row.
        try:
            _d = db.query(RcaDraft).filter(RcaDraft.review_id == review_id).first()
            if not _d:
                _d = RcaDraft(id=f"draft_{review_id}", review_id=review_id)
                db.add(_d)
            # EVERY PATH ASKS, not only Tier-1. See ensure_zendesk_guest_name:
            # the lookup existed and had one call site, so four of the five
            # ways a booking gets confirmed printed "check the Zendesk ticket"
            # for a lookup nobody had attempted. This is where they converge.
            _gn = await ensure_zendesk_guest_name(
                booking, fallback_bid=review.reference_number or "")
            if _gn["asked"]:
                _why_bq = gate_unusable_reason(
                    (booking or {}).get("primary_guest_name"))
                confidence_trail.append({
                    "mark": "pass" if _gn["name"] else "warn",
                    "text": (f"<strong>Guest name read from Zendesk</strong> "
                             f"— the warehouse stores {_why_bq}, so the ticket "
                             f"was asked instead and answered "
                             f"<strong>{_html.escape(_gn['name'])}</strong>.")
                            if _gn["name"] else
                            ("<strong>Zendesk was asked for the guest name "
                             "and had none.</strong> "
                             + _html.escape(_gn["reason"] or "")
                             + " This is a lookup that ran, not one that was "
                               "skipped.")})
            _m = (booking or {}).get("_match", {})
            _d.booking            = {k: v for k, v in (booking or {}).items()
                                     if k != "_match"}
            _d.match_tier         = match_tier or _m.get("tier")
            _d.match_confidence   = _m.get("confidence")
            _d.match_method       = _m.get("method") or narrowing_path
            _d.candidates_list    = candidates
            _d.candidate_state    = candidate_state
            # The trail written here is the MATCHING half only — the analysis
            # has not run yet. It replaces whatever a previous completed run
            # left, and `generated_at` is not touched until the end, so a run
            # that dies after this point leaves a draft that looks finished
            # (old timestamp, full rca_v3, every column populated) carrying a
            # three-line trail. Every disclosure the analysis writes is simply
            # absent, and absent reads as "nothing to report".
            #
            # Seen on a real draft: five trail entries including two warns,
            # then three and no warns, same generated_at, nothing in between
            # but a re-run that did not finish.
            #
            # The marker is removed by the final save, which writes the whole
            # trail again. If it is still on the row, the run did not finish.
            # Translation note (step 1) FIRST — prepended in place so the persist
            # line below stays the plain partial write. `not in` keeps it
            # idempotent across the early and final saves of the same list.
            if _xl_trail and _xl_trail not in confidence_trail:
                confidence_trail.insert(0, _xl_trail)
            _d.confidence_trail   = partial_trail(confidence_trail)
            _d.bid_source         = bid_source
            _d.extracted_signals  = extracted_sigs or {}
            _d.narrowing_attempts = narrowing_attempts or []
            _d.extracted_signals  = dict(_d.extracted_signals or {})
            if untraceable_reason:
                _d.extracted_signals["untraceable_reason"] = untraceable_reason
            for _c in ("booking", "candidates_list", "confidence_trail",
                       "extracted_signals", "narrowing_attempts"):
                try:
                    flag_modified(_d, _c)
                except Exception:
                    pass
            db.commit()
            log.info(f"[pipeline] match persisted early: tier={_d.match_tier} "
                     f"bid={(_d.booking or {}).get('id') or '-'} "
                     f"candidates={len(candidates or [])}")
        except Exception as e:
            # Never let the safety net itself sink the run.
            db.rollback()
            log.exception(f"[pipeline] early match persist failed: {e}")

        # RELEASE THE CONNECTION FOR THE MODEL-CALL PHASE. Everything below
        # until step 14 is model calls (stated_issue, generate_rca_v2/v3,
        # analyze_wwr, translate_outgoing). `review` is the only row read across
        # that phase, and the commit above expired it — so the next attribute
        # access (review.reference_number, a few lines down) lazy-loads,
        # checking out a connection that then sits idle-in-transaction for the
        # length of every model call. Neon drops a connection left idle-in-
        # transaction, and step 14's query dies with PendingRollbackError,
        # killing the run in the save phase. Measured: without this the pool
        # showed one connection checked out at every model call. So load
        # review's columns now while connected, detach it, and roll back the
        # empty read transaction; the phase then holds nothing and step 14
        # re-acquires a fresh connection. review is re-attached at save.
        try:
            db.refresh(review)
            db.expunge(review)
            db.rollback()
        except Exception as _rel_err:
            log.warning(f"[pipeline] could not detach review for the model "
                        f"phase, run holds its connection: {_rel_err}")

        _progress(review_id, 2, "fetching Zendesk timeline")
        # ── 6. Zendesk timeline ──────────────────────────────────────────────
        timeline      = []
        extracted_bk  = {}
        zd_meta       = {"ticket_ids": [], "timeline_raw": []}
        bid_for_zd    = (booking or {}).get("id") or review.reference_number
        # THE CLOCK TIME IS KEPT. This was "%Y-%m-%d" — date only — although
        # `received_at` is a full datetime, so the "Review posted" row reached
        # the timeline as a bare "04 Aug" and `_tlParse` reads a missing clock
        # as 00:00. The review therefore sorted to the START of its own day,
        # above a booking created at 02:43. The old "Review posted last" rule
        # in the prompt was hiding that; removing the rule exposed a timestamp
        # that had been lossy all along.
        _zd_pub_date  = (review.received_at.strftime("%Y-%m-%d %H:%M")
                         if review.received_at else "")
        _zd_err       = None
        if bid_for_zd:
            try:
                timeline, extracted_bk, zd_meta = await zendesk.get_timeline(
                    bid_for_zd, review_id,
                    booking=booking,
                    review_body=review.body_english or review.body_original or "",
                    review_pub_date=_zd_pub_date,
                )
                log.info(f"[pipeline] timeline: {len(timeline)} events "
                         f"across tickets {zd_meta.get('ticket_ids')}")
            except Exception as e:
                _zd_err = e
                log.warning(f"Zendesk failed — continuing with empty timeline: {e}")
                timeline, extracted_bk = [], {}
                zd_meta = {"ticket_ids": [], "timeline_raw": []}
        else:
            # NOT ASKED IS NOT "NOTHING FOUND", AND THIS BRANCH USED TO BE
            # SILENT. Every ticket search is keyed on a booking id, so a review
            # whose booking is not matched yet skips Zendesk entirely — and
            # skipped it in exactly the same way a booking with no tickets
            # looks: empty timeline, empty contacts panel, nothing in the
            # trail. The trail entries below all hang off `search_tally`, which
            # only exists once a search has run, so they stayed quiet too.
            #
            # This is the first run of nearly every review, which made it the
            # most common state on the card and the least visible. Worse, an
            # empty record is what tips the RCA prompt into narrating the
            # guest's review instead of listing events — so the card looked
            # fullest precisely when we had asked nobody anything.
            # NO TRAIL ENTRY HERE. `timeline_entry` below already writes
            # one for this exact case, and it distinguishes four causes where
            # this could only ever state one. Adding a second put the same
            # fact on the card twice in two different wordings — a reader
            # meeting both reasonably wonders which is the real one.
            log.info("[pipeline] Zendesk NOT searched for %s — no booking id "
                     "yet", review_id)

        # WHICH SEARCHES RAN, before the count of what they found. The card
        # said "one contact" with total confidence because the ticket search
        # stopped at the first route that returned anything; this line is what
        # makes a complete search distinguishable from a short one.
        _sr = zd_meta.get("search_tally")
        if _sr:
            _sr_entry = zendesk.collect_trail(bid_for_zd, _sr)
            if _sr_entry:
                confidence_trail.append(_sr_entry)

        # PRIOR-TRIP TICKETS, DROPPED AND SAID. The requester search casts by
        # the guest's email, so it also pulls their earlier trips — a July chat
        # about another booking that would otherwise sit at the top of an August
        # booking's timeline, wrecking the chronology. Those are kept out of the
        # timeline; this line is what stops "dropped" from looking like "never
        # there". The reason branch covers the case where the filter could not
        # run at all (no booking date) — did-not-run, not found-nothing.
        # TICKETS THAT NAME ANOTHER BOOKING. The date cutoff below can only
        # catch a trip that ended before this booking existed; this catches the
        # guest's other booking made in the same week or after, which is the
        # case still reported after the cutoff shipped.
        _ob = zd_meta.get("other_booking_excluded") or []
        if _ob:
            _ob_ids = ", ".join(
                f"ZD-{e.get('ticket_id')} (booking {e.get('names_booking')})"
                for e in _ob[:4])
            confidence_trail.append({"mark": "pass",
                "text": f"<strong>{len(_ob)} ticket(s) about another booking kept "
                        f"off the timeline</strong> — {_ob_ids}. Their own Zendesk "
                        f"booking field names a different booking, so they are the "
                        f"same guest's other trip rather than this one."})

        _pt = zd_meta.get("prior_trip_excluded") or []
        _pt_reason = zd_meta.get("prior_trip_reason") or ""
        if _pt:
            _pt_ids = ", ".join(f"ZD-{e.get('ticket_id')}" for e in _pt[:4])
            confidence_trail.append({"mark": "pass",
                "text": f"<strong>{len(_pt)} earlier-trip ticket(s) kept off the "
                        f"timeline</strong> ({_pt_ids}) — their activity predates "
                        f"booking {bid_for_zd}, so they are the same guest's "
                        f"earlier trip, not this booking. They were still found "
                        f"as contacts; they are only kept out of the chronology."})
        elif _pt_reason:
            confidence_trail.append({"mark": "warn",
                "text": f"<strong>The earlier-trip filter did not run.</strong> "
                        f"{_pt_reason.capitalize()}. A ticket from an earlier trip "
                        f"by this guest could therefore sit in the timeline as if "
                        f"it were this booking's."})

        # THE SHAPING FAILED, SAID OUT LOUD. A fallback timeline is raw ticket
        # bodies under category labels, and it rendered in the same rows as a
        # shaped one — so a failed model call read as a redesign of the card.
        if any(isinstance(e, dict) and e.get("shaping_failed") for e in (timeline or [])):
            confidence_trail.append({"mark": "fail",
                "text": "<strong>The events timeline was not summarised.</strong> "
                        "The shaping call came back unreadable, so these rows are "
                        "the RAW ticket bodies with category labels — not the "
                        "usual descriptive ones. Nothing is missing; nothing has "
                        "been rewritten. Re-run to try again."})

        # WHAT THE SHAPING COLLAPSED, DROPPED OR RE-ATTRIBUTED. Built by
        # `shape_counts_entry` so it can be driven by a test rather than
        # spell-checked in this function's source.
        _shape_line = shape_counts_entry(timeline)
        if _shape_line:
            confidence_trail.append(_shape_line)

        # HOW MANY INTERNAL NOTES CAME IN, and how many were set aside. A note
        # read and dropped and a note never fetched leave the same timeline.
        _in = (zd_meta or {}).get("internal_notes") or {}
        if _in.get("kept") or _in.get("dropped"):
            confidence_trail.append({"mark": "pass" if _in.get("kept") else "warn",
                "text": f"<strong>Internal notes:</strong> {_in.get('kept', 0)} kept "
                        f"as booking facts (reschedules, cancellations, refunds); "
                        f"{_in.get('dropped', 0)} were ticket administration and "
                        f"are not on the timeline."})

        _tl_entry = timeline_entry(bid_for_zd, timeline,
                                   zd_meta.get("ticket_ids") or [], _zd_err)
        if _tl_entry:
            confidence_trail.append(_tl_entry)

        # ── 6a. Slack pings mentioning this BID ───────────────────────────────
        # RCA context, not matching. Ops/escalation/SP channels often carry
        # chases and manual interventions for a booking that never reach
        # Zendesk. Workspace-wide, not just ORM channels.
        slack_mentions = []
        if bid_for_zd:
            try:
                slack_mentions = await slk.search_mentions(str(bid_for_zd))
                # The unavailable sentinel is one row, so a bare len() logs "1"
                # for a search that never ran - the same conflation the
                # dashboard used to make.
                if slk.is_search_unavailable(slack_mentions):
                    log.warning(f"[pipeline] slack NOT searched for {bid_for_zd}: "
                                f"{slack_mentions[0].get('reason')}")
                else:
                    log.info(f"[pipeline] slack mentions for {bid_for_zd}: "
                             f"{len(slack_mentions)}")
            except Exception as e:
                log.warning(f"Slack mention search failed — continuing: {e}")
        else:
            # THE SAME MISSING ELSE, two blocks down. `is_search_unavailable`
            # already covers Slack being unreachable, so a reader could
            # reasonably believe every silent case was handled — but that
            # sentinel is only ever produced by a search that RAN. With no
            # booking id nothing is called at all, and an empty mentions list
            # is indistinguishable from a booking nobody discussed.
            log.info("[pipeline] Slack mentions NOT searched for %s — no "
                     "booking id yet", review_id)
            confidence_trail.append(not_searched_entry("slack"))

        # ── 6b. Ticket fact extraction (Claude, runs concurrently with support frames) ──
        ticket_facts = {}
        _timeline_raw_for_extraction = zd_meta.get("timeline_raw", [])
        _timeline_raw_ticket_ids = zd_meta.get("timeline_raw_ticket_ids", [])
        async def _extract_facts():
            try:
                return await claude.extract_ticket_facts(
                    booking or {},
                    _timeline_raw_for_extraction,
                    _timeline_raw_ticket_ids,
                )
            except Exception as e:
                log.warning(f"Ticket fact extraction failed — continuing: {e}")
                return {}

        # ── Start fact extraction concurrently (result collected before save) ──
        _facts_task = asyncio.ensure_future(_extract_facts())

        # Merge Zendesk-extracted fields as fallback for missing BQ fields
        if extracted_bk and booking:
            for key in ("tgid", "tid", "vid", "experienceName", "visitDate",
                        "vendorName", "pax"):
                if not booking.get(key) and extracted_bk.get(key):
                    booking[key] = extracted_bk[key]
            if "ticket_mail_seen" in extracted_bk:
                booking["ticket_mail_seen"] = bool(extracted_bk["ticket_mail_seen"])

        _progress(review_id, 3, "summarising support events")
        # ── 7b. Support frames (Claude summarisation of each timeline event) ─
        support_frames = []
        sp_frames      = []
        # One Claude call per timeline event. These were awaited in a loop, so a
        # ticket at the 41-event cap cost 41 sequential round trips and dominated
        # re-run time. They are independent of one another — each only needs its
        # own event plus its neighbours — so they run concurrently, bounded so we
        # do not hammer the API. Order is preserved via gather's result ordering.
        _FRAME_CONCURRENCY = 8
        _sem = asyncio.Semaphore(_FRAME_CONCURRENCY)
        _blank = {"guestSaid": "", "weDid": "", "guestReply": "", "gap": ""}

        async def _frame_for(i, ev):
            prev_ = timeline[i - 1] if i > 0 else None
            next_ = timeline[i + 1] if i + 1 < len(timeline) else None
            async with _sem:
                try:
                    frame = await claude.summarise_support_event(ev, prev_, next_)
                    return {**ev, **(frame or _blank)}
                except Exception:
                    return {**ev, **_blank}

        merged_frames = await asyncio.gather(
            *(_frame_for(i, ev) for i, ev in enumerate(timeline)))
        for ev, merged in zip(timeline, merged_frames):
            if ev.get("thread") == "sp":
                sp_frames.append(merged)
            else:
                support_frames.append(merged)
        log.info(f"[pipeline] {len(timeline)} support frames summarised "
                 f"(concurrency {_FRAME_CONCURRENCY})")

        support_summary_text = ""
        try:
            support_summary_text = await claude.summarise_support_arc(support_frames)
        except Exception as e:
            log.exception(f"Support summary failed: {e}")

        # ── 7. Insights (moved to after step 11 — needs L1/L2) ──────────────

        # ── 8. Similar complaints ─────────────────────────────────────────────
        similar_support = []
        similar_reviews = []
        if booking:
            try:
                similar_support, similar_reviews = await bq.get_similar_complaints(booking)
            except Exception as e:
                log.exception(f"Similar complaints failed: {e}")

        # ── 9. DSS placeholder — runs after classification (step 11d) ───────────
        dss_rec = {}

        # Everything from here on is written by the model. If it is not
        # available, the classification, the RCA and the reply all come back
        # empty and the card renders blank - which is indistinguishable from a
        # review too thin to say anything about. The BigQuery branch above
        # already says when the warehouse was unavailable; this is the same
        # disclosure for the half of the pipeline that writes the analysis.
        # Not in MOCK_MODE. is_live() reports every service as down there, but
        # claude._call still reaches the model on that path and the RCA really
        # is generated - so warning would tell an associate the analysis in
        # front of them does not exist.
        _ai_down = not MOCK_MODE and not is_live("anthropic")
        if _ai_down:
            log.error(f"[pipeline] {review_id}: the AI provider is NOT live - "
                      f"classification, RCA and the reply will be empty")
            confidence_trail.append({"mark": "warn",
                "text": "<strong>The AI provider is not available on this "
                        "server</strong> — the classification, the RCA and the "
                        "draft reply below are empty because nothing could be "
                        "generated, not because there was nothing to say. "
                        "Re-run this review once it is connected."})

        # ── 10. Stated Issue ──────────────────────────────────────────────────
        stated_issue = ""
        _si_err = None
        try:
            stated_issue = await claude.stated_issue(review_text, review_id)
        except Exception as e:
            _si_err = e
            log.exception(f"Stated issue failed: {e}")

        # Suppressed when the provider is down — the warning above covers every
        # model-written field at once, and repeating it per field is noise.
        _si_entry = None if _ai_down else stated_issue_entry(stated_issue, _si_err)
        if _si_entry:
            confidence_trail.append(_si_entry)

        _progress(review_id, 4, "classifying issue")
        # ── 11. Classification ────────────────────────────────────────────────
        l1, l2, l1_reasoning, sub_theme = "", "", "", None
        _classify_err = None
        _classify_warnings = []
        try:
            from server.services.classifier import classify as classify_v2
            from server.services.claude import _call as claude_call
            result = await classify_v2(review_text, booking, timeline, claude_call, review_id)
            l1 = result.l1
            l2 = result.l2
            sub_theme = result.sub_theme
            l1_reasoning = result.reasoning
            # Carried to the trail, not just the log. The classifier already
            # knows precisely why it produced nothing - "Invalid L1 'Booking
            # Issues' - dropped to empty" is a taxonomy drift, "Response was
            # not valid JSON" is a model problem, and they are fixed by
            # different people. Logging them and then telling the reader
            # "the classifier returned no L1 or L2" throws away the only
            # part of the answer that was actionable.
            _classify_warnings = list(result.warnings)
            for w in _classify_warnings:
                log.warning(f"[classify {review_id}] {w}")
        except Exception as e:
            _classify_err = e
            log.exception(f"Classification failed: {e}")

        # ── 11b. Warehouse L1/L2 — comparison AND recovery ────────────────────
        # RECOVERY, not just a log line. Claude stays authoritative when it
        # classified; but when it returned NOTHING — the manual-review case that
        # rendered every L1/L2-keyed section blank — this booking's own warehouse
        # tag (the same taxonomy, keyed on the booking id) is a real answer and
        # is adopted rather than left in the log. Runs BEFORE the trail line so
        # that line tells the truth about what the selects will show, and before
        # insights/DSS/RCA (11c/11d/12) so they key on the recovered pair.
        _wh_recovery = ""
        try:
            _bid = (booking or {}).get("id")
            if _bid:
                _wh = await bq.get_l1_l2_by_bid(_bid)
                if _wh.get("l1") or _wh.get("l2"):
                    log.info(
                        f"[classify {review_id}] L1/L2 comparison for BID {_bid} — "
                        f"Claude: {l1!r} / {l2!r} | warehouse: {_wh['l1']!r} / {_wh['l2']!r}")
                from server.services.classifier import recover_l1_l2_from_warehouse
                l1, l2, _wh_recovery = recover_l1_l2_from_warehouse(
                    l1, l2, _wh.get("l1"), _wh.get("l2"))
                if _wh_recovery:
                    log.info(f"[classify {review_id}] {_wh_recovery}")
        except Exception as e:
            log.exception(f"Warehouse L1/L2 lookup/recovery failed: {e}")

        # THE TRAIL LINE, after recovery. A warehouse recovery is its OWN
        # sentence — not the model being "repaired" (classification_entry's
        # wording), but the model returning nothing and the warehouse tag
        # filling the gap.
        #
        # A RECOVERY OUTRANKS THE AI-DOWN SUPPRESSION, and the order here is the
        # whole point. `_ai_down` silences the per-field warnings because one
        # sentence already says every model-written field is empty — but a
        # recovery is the opposite fact: this field is NOT empty, and what fills
        # it did not come from the model. Checked second, it was swallowed in
        # exactly the case it matters most: the provider being down is what
        # empties the classification, which is what makes the recovery fire. The
        # card then showed a populated L1/L2 with nothing anywhere saying it was
        # the warehouse's tag rather than the model's answer.
        if _wh_recovery:
            _cls_entry = {"mark": "warn", "text":
                "<strong>Classification recovered from the warehouse</strong> — "
                f"the model returned no usable L1/L2, so this booking's own "
                f"warehouse tag ({l1} / {l2}) was used. Check it against the "
                f"review before trusting the comparisons and the scenario lookup "
                f"keyed on it."}
        elif _ai_down:
            _cls_entry = None
        else:
            _cls_entry = classification_entry(
                l1, l2, _classify_err, _classify_warnings)
        if _cls_entry:
            confidence_trail.append(_cls_entry)

        _progress(review_id, 5, "computing insights (BigQuery)")
        # ── 11c. Insights (after classification — needs L1/L2) ────────────────
        insights = {}
        if booking and booking.get("tid") and booking.get("vid"):
            try:
                insights = await _get_insights(booking, l1 or None, l2 or None,
                                               window="90d")
            except Exception as e:
                log.exception(f"Insights failed: {e}")

        # ── 11d. DSS (after classification — scores on L1/L2/review_text) ────
        _dss_err = None
        try:
            dss_rec = await dss.get_recommendation(
                booking or {}, review_id, l1=l1, l2=l2, review_text=review_text or "")
        except Exception as e:
            _dss_err = e
            log.exception(f"DSS failed: {e}")
        # The one lookup in this pipeline with no trail line of its own. The
        # card prints "No DSS row was matched for this classification." for a
        # sheet that is not configured, a sheet that came back empty and a
        # lookup that raised, all of which are the playbook being unavailable
        # rather than silent on this case. See dss_entry.
        _dss_entry = dss_entry(dss_rec, _dss_err, is_live("dss"), l1, l2)
        if _dss_entry:
            confidence_trail.append(_dss_entry)

        _progress(review_id, 6, "generating RCA")
        # ── 12. Full structured RCA ───────────────────────────────────────────
        rca_v2 = {}
        try:
            rca_v2 = await claude.generate_rca_v2(
                review_text, booking, timeline, insights, dss_rec, l1, l2, review_id)
        except Exception as e:
            log.exception(f"RCA v2 generation failed: {e}")

        # ── 6c. Collect ticket fact extraction result (before RCA — feeds it) ─
        try:
            ticket_facts = await _facts_task
            if ticket_facts:
                log.info(f"[pipeline] ticket_facts extracted: {list(ticket_facts.keys())}")
        except Exception as e:
            log.warning(f"Ticket fact extraction collect failed: {e}")
            ticket_facts = {}

        # ── 11e. Scenario routing (sub-theme → primary scenario + overlays) ──
        # actions_for() is NOT called here any more. Its result was assigned to
        # draft.actions_taken at save and overwritten by the projection before
        # the commit, so it was computed for nobody. The gated list is derived
        # inside validate(), from the routed scenarios AND the flags.
        primary_scenario, overlay_scenarios = None, []
        try:
            from server.checklist import (
                scenarios_for, compute_overlay_scenarios, SCENARIO_CHECKS)
            primary_scenario = scenarios_for(l1, l2, sub_theme)["primary"]
            overlay_scenarios = compute_overlay_scenarios(
                l1, l2, sub_theme, ticket_facts, booking)
            log.info(f"[pipeline] scenario routing: primary={primary_scenario!r} "
                     f"overlays={overlay_scenarios}")
        except Exception as e:
            log.exception(f"Scenario routing failed: {e}")

        # ── 11f. WWR analysis — one block per scenario (Task #13 §3) ─────────
        wwr_scenarios = []
        try:
            wwr_scenarios = await claude.analyze_wwr(
                review_text, timeline, ticket_facts, booking or {},
                l1 or "", l2 or "", sub_theme, primary_scenario, overlay_scenarios)
            log.info(f"[pipeline] wwr_analysis: {len(wwr_scenarios)} scenario block(s)")
        except Exception as e:
            log.exception(f"WWR analysis failed: {e}")

        # ── 12b. RCA v3 (TL;DR + WWR chain + checklist) ──────────────────────
        rca_v3 = {}
        _scenarios_routed = [s for s in ([primary_scenario] + overlay_scenarios) if s]
        try:
            from server.checklist import issue_questions_for
            from server.services.rca_checklist import get_checklist
            checklist = await get_checklist(l1, l2)
            # Approved replies as a VOICE reference for suggested_response.
            # This is a sheet lookup, not a model call - the reply is still
            # written once, inside the RCA, against this case's evidence.
            # Output rule 18 is what stops the examples becoming content.
            _tone_err = None
            try:
                # dss_rec GATES THE MACRO SET. The list files one scenario
                # several times, differing only by what it promises the guest,
                # and the review reads identically across them — the DSS is the
                # only thing that can choose. Passing it here is what stops a
                # reply offering a refund the playbook never prescribed. See
                # services/reply_macro.py.
                canned_list = await get_canned_responses(
                    l1, l2, sub_theme, review_text or "",
                    untraceable=not booking, dss_rec=dss_rec)
            except Exception as e:
                canned_list = []
                _tone_err = e
                log.warning(f"[pipeline] canned tone lookup failed: {e}")
            from server.services.canned import (last_failure_reason,
                                                last_source,
                                                source_is_degraded)
            _tone_entry = tone_entry(canned_list or [], l1, l2, _tone_err,
                                     last_failure_reason(),
                                     last_source() if source_is_degraded()
                                     else "")
            if _tone_entry:
                confidence_trail.append(_tone_entry)
            # WHAT THE GATE WITHHELD, in words. A macro set narrowed by the DSS
            # renders exactly like the whole set, and "nothing in the sheet fits
            # this review" reads the same as "eleven fitted and every one
            # promised a remedy the playbook did not name" — different problems,
            # fixed by different people. The gate counts them; this is where the
            # reader is told.
            _gate_note = (canned_list[0] or {}).get("gate_note") if canned_list \
                else last_failure_reason()
            if _gate_note:
                confidence_trail.append({"mark": "pass", "text":
                    f"<strong>Approved replies narrowed by the DSS</strong> — "
                    f"{_gate_note}"})
            rca_v3 = await claude.generate_rca_v3(
                review_text=review_text,
                booking=booking,
                timeline=timeline,
                insights=insights,
                dss_rec=dss_rec,
                l1=l1 or "",
                l2=l2 or "",
                sub_theme=sub_theme or "",
                support_summary=support_summary_text or "",
                checklist=checklist,
                review_id=review_id,
                timeline_raw=zd_meta.get("timeline_raw", []),
                ticket_facts=ticket_facts,
                scenarios_routed=_scenarios_routed,
                issue_questions=issue_questions_for(_scenarios_routed),
                canned_list=canned_list,
                # The tickets THEMSELVES, not only the worked-out arc. §1 has
                # to say why the guest reached out and whether we solved it,
                # and a one-line summary cannot answer either.
                support_frames=support_frames,
            )
        except Exception as e:
            log.exception(f"RCA v3 generation failed: {e}")

        # ── 12c. Validate the RCA before anything reads it ───────────────────
        # The dashboard renders whatever survives this with no special-casing,
        # so every enum has to be settled here rather than guarded in the UI.
        # It never raises: a malformed field is coerced and recorded, because
        # losing an RCA to one bad enum is worse than one grey chip.
        _progress(review_id, 7, "validating RCA")
        rca_notes = []
        if rca_v3:
            try:
                from server.services.rca_v4_validate import validate as _validate_rca
                # Same on a re-run from the pipeline: hand-typed rows are
                # carried forward, guideline rows are left to the AND.
                #
                # READ FROM THE DATABASE, not from `draft`. `draft` is the save
                # step's local and is not bound until step 14, thirty lines
                # BELOW this one — so naming it here raised UnboundLocalError on
                # every single run, the except swallowed it, and validate() was
                # called by nothing. Exactly the failure CLAUDE.md opens with:
                # every test green, raw model output reaching the screen.
                #
                # The read owns its own session (see _prev_hand_typed_actions):
                # doing it on `db` opened a transaction that stayed open across
                # `await translate_outgoing(...)` below, and Neon drops a
                # connection left idle-in-transaction across a model call — the
                # step-14 query then died with PendingRollbackError.
                _prev_actions, _prev_unattributed = _prev_hand_typed_actions(review_id)
                # A booking is confirmed when one was actually matched and
                # the picker is not still open. Both halves matter: a candidate
                # list means the associate has not chosen yet, so the timeline
                # has no booking to be about.
                _booking_confirmed = bool(booking and booking.get("id")
                                          and not candidate_state)
                rca_v3, rca_notes = _validate_rca(rca_v3, _scenarios_routed,
                                                  keep_actions=_prev_actions,
                                                  booking_confirmed=_booking_confirmed,
                                                  events=timeline or [],
                                                  # For the amount-claim gate.
                                                  booking=booking or {},
                                                  # For the DSS-followed gate:
                                                  # decides whether the guest
                                                  # wrote in BEFORE the review.
                                                  review_at=review.received_at,
                                                  keep_unattributed=_prev_unattributed)
                # A coercion the reader cannot see is a silent edit. The trail
                # is where this build already puts "we changed what the model
                # said, and here is why", so each note goes there verbatim.
                record_validation(
                    rca_notes, confidence_trail,
                    lambda n: log.warning(f"[pipeline] rca validation: {n}"))
            except Exception as e:
                log.exception(f"RCA validation failed, keeping raw output: {e}")
                confidence_trail.append({"mark": "warn",
                    "text": "<strong>RCA validation did not run</strong> — it "
                            f"raised {type(e).__name__}. Nothing below was "
                            "coerced or checked; this is raw model output, not "
                            "a clean bill of health."})

            # The model's contact notes join to the Zendesk frames by zd_ref.
            # A join that matches nothing looks exactly like a model that
            # returned no notes, so the miss is counted and said out loud - a
            # silent zero is the failure mode, not the safe default.
            try:
                from server.services.rca_v4_validate import contact_join_notes
                for _n in contact_join_notes(support_frames, sp_frames, rca_v3):
                    log.warning(f"[pipeline] {_n}")
                    confidence_trail.append({
                        "mark": "warn",
                        "text": f"<strong>RCA</strong> — {_html.escape(_n)}"})
            except Exception as e:
                log.warning(f"[pipeline] contact-note join check skipped: {e}")
                confidence_trail.append({"mark": "warn",
                    "text": "<strong>Contact-note join did not run</strong> — it "
                            f"raised {type(e).__name__}. No note was checked "
                            "against a Zendesk frame; an unmatched reference "
                            "would not have been reported."})

        # ── 13. Response draft ────────────────────────────────────────────────
        # There is no separate drafting call any more. v4 returns `resolution`
        # and `suggested_response` from the RCA itself, written against the full
        # evidence base - the per-issue root causes, the SOP verdict and the
        # takedown decision - which the standalone drafter never saw; it got
        # only rca_v2's one-line resolution. Running both meant paying for two
        # replies and discarding the better-grounded one, because _draft_dict()
        # reads the column the drafter wrote.
        #
        # The cost is the canned-response tone reference, which the RCA prompt
        # has no token for. Recovering it means adding one to the prompt body.
        response_draft = (rca_v3 or {}).get("suggested_response") or ""

        # An untraceable review gets the approved macro VERBATIM, not a reply
        # written in its register. There is nothing to personalise: we could
        # not find the booking, so the reply is the one ask that applies to
        # every such review — send us your booking id. The macro is signed off
        # for exactly that, word for word, and having the model rewrite it
        # produces an unapproved paraphrase of an approved reply, which is
        # worse than either.
        #
        # `<first name>` is the one token the macro is written to have filled;
        # leaving it renders literal angle brackets on a public review page.
        # Filled only when a name is actually known — a wrong name is worse
        # than a placeholder an associate can see and complete.
        _verbatim = next((c for c in (canned_list or []) if c.get("why")), None)
        if _verbatim and _verbatim.get("response"):
            response_draft = _verbatim["response"]
            _who = (review.author or "").strip().split(" ")[0]
            if _who:
                response_draft = response_draft.replace("<first name>", _who)
            # Written into rca_v3 too. _draft_dict reads the reply presence-
            # based from there now, so setting only the column would leave the
            # card showing whatever the model wrote instead.
            if isinstance(rca_v3, dict):
                rca_v3["suggested_response"] = response_draft
            log.info(f"[pipeline] {review_id}: untraceable — using the approved "
                     f"“{_verbatim.get('situation')}” macro verbatim")

        # ── 13b. The reply goes out in the REVIEW'S language ──────────────────
        # The model writes in English. The guest wrote in their own language
        # and that is what the reply is sent in, always — so the translation
        # happens HERE, on the way into the draft, and the English becomes the
        # working view rather than the thing that gets sent.
        #
        # Previously this ran only when someone pressed a button on the card,
        # and its result lived in a browser variable that Send never read: the
        # reply that actually went out was the English one.
        #
        # The rule itself lives in services/reply_language.py because the
        # "↻ RCA only" endpoint writes this same field and has to apply it too.
        from server.services.reply_language import translate_outgoing
        (_reply_out, _reply_english,
         _reply_eng_of, _reply_trail) = await translate_outgoing(
             response_draft, review, review_id)
        if _reply_trail:
            confidence_trail.append(_reply_trail)
        if _reply_eng_of and isinstance(rca_v3, dict):
            # `_draft_dict` reads the reply from rca_v3 by PRESENCE and only
            # falls back to the column, so writing the translation to the
            # column alone would leave the card — and Send — showing the
            # English the model wrote.
            rca_v3["suggested_response"] = _reply_out

        _progress(review_id, 8, "saving")
        # ── 14. Save ──────────────────────────────────────────────────────────
        draft = db.query(RcaDraft).filter(RcaDraft.review_id == review_id).first()
        if not draft:
            draft = RcaDraft(id=f"draft_{review_id}", review_id=review_id)
            db.add(draft)

        # A RE-RUN OVER A HAND-EDITED RCA DESTROYS THE EDITS. It always has —
        # `draft.rca_v3 = _v3` a few lines below replaces the blob whole — and
        # it did it in silence, which is bad enough. What made it worse is that
        # `rca_v3_edited_at` was LEFT SET afterwards. Nothing had been edited
        # since, and the marker is what `_bulk_targets` and `tools/rerun_all.py`
        # read to decide "human work would be lost here, skip it": the review
        # was then protected for ever, from every bulk run, on the strength of
        # an edit that had already been thrown away. A marker outliving the
        # thing it marks is worse than no marker — it is a claim the data
        # contradicts.
        #
        # Bulk paths never reach here (they exclude edited drafts up front), so
        # this is the card's Re-run, a candidate confirmation, and a re-ingest —
        # all of them deliberate, single-review actions. The answer is not to
        # refuse them; it is to say what was overwritten and stop claiming the
        # edit survives.
        _replaced_hand_edits = bool(rca_v3) and bool(
            getattr(draft, "rca_v3_edited_at", None))
        if _replaced_hand_edits:
            log.warning(f"[pipeline] {review_id}: re-run replaced an RCA that "
                        f"was edited by hand at {draft.rca_v3_edited_at}")
            confidence_trail.append({"mark": "warn",
                "text": "<strong>This re-run replaced a hand-edited RCA</strong> — "
                        "the RCA below was edited on the card and has been "
                        "overwritten by this run. The edits are gone; nothing "
                        "here is a person's wording any more."})

        _match = (booking or {}).get("_match", {})
        booking_to_save = {k: v for k, v in (booking or {}).items() if k != "_match"}
        zd_requester = zd_meta.get("zendesk_requester_name", "")
        if zd_requester:
            booking_to_save["zendesk_requester_name"] = zd_requester
        # THE CANCELLATION POLICY IS A PROPERTY OF THE BOOKING, not an event.
        # It was being written onto every row of the timeline — on one real
        # ticket, all of them — crowding out the fact each row existed to
        # carry. Taking it off the timeline without putting it anywhere would
        # have lost it, so it is extracted once and carried as a field.
        #
        # There is no cancellation column in the warehouse and none in the API
        # payload, so the ticket text is the only place it exists. Both the
        # answer and the REASON there is none are stored: a blank field and
        # "no ticket event states the terms" send a reader to different places.
        from server.ticket_notes import policy_from_events
        _pol, _pol_why = policy_from_events(timeline or [])
        booking_to_save["cancellation_policy"] = _pol
        booking_to_save["cancellation_policy_note"] = "" if _pol else _pol_why

        # The SP escalation email, resolved with provenance: the booking
        # record's own field first, the warehouse ESCALATIONS contact next, and
        # - the bug this closes - "we never fetched it" (not_fetched) kept
        # distinct from "the SP has none" (none_found), so no card states a
        # blank we never actually looked for. See ticket_notes.
        from server.ticket_notes import resolve_sp_escalation_email
        resolve_sp_escalation_email(booking_to_save,
                                    zd_meta.get("timeline_raw", []))
        draft.booking              = booking_to_save
        draft.match_tier           = match_tier or _match.get("tier")
        draft.match_confidence     = _match.get("confidence")
        draft.match_method         = _match.get("method") or narrowing_path
        draft.candidates_list      = candidates
        draft.candidate_state      = candidate_state
        # Translation note (step 1) FIRST, and it must survive the branches above
        # that reassign confidence_trail to a carried prior trail. Prepended in
        # place so the persist line stays the plain whole-trail write a finished
        # run needs; `not in` avoids a double when the early save already added it.
        if _xl_trail and _xl_trail not in confidence_trail:
            confidence_trail.insert(0, _xl_trail)
        draft.confidence_trail     = confidence_trail
        draft.bid_source           = bid_source
        _sigs = dict(extracted_sigs or {})
        if untraceable_reason:
            _sigs["untraceable_reason"] = untraceable_reason
        draft.extracted_signals    = _sigs
        draft.narrowing_attempts   = narrowing_attempts or []
        draft.timeline         = timeline
        draft.insights         = insights
        draft.similar_support  = similar_support
        draft.similar_reviews  = similar_reviews
        draft.dss_rec          = dss_rec
        draft.zendesk_ticket_ids = zd_meta.get("ticket_ids", [])
        draft.slack_mentions     = slack_mentions
        draft.timeline_raw       = zd_meta.get("timeline_raw", [])

        draft.stated_issue                = stated_issue
        draft.l1                          = l1
        draft.l2                          = l2
        draft.l1_reasoning                = l1_reasoning
        draft.sub_theme                   = sub_theme
        draft.primary_scenario            = primary_scenario
        draft.overlay_scenarios           = overlay_scenarios or []
        # The list columns the dashboard edits. A pipeline run classifies one
        # of each, so it seeds a single-element list; a human can add more and
        # the next re-run reads them back rather than overwriting - see
        # regenerate-rca, which keeps the stored routing when none is passed.
        draft.sub_themes                  = [sub_theme] if sub_theme else []
        draft.scenarios                   = [x for x in ([primary_scenario] +
                                             list(overlay_scenarios or [])) if x]
        draft.diagnostic_checks           = rca_v2.get("diagnosticChecks", [])
        draft.what_went_wrong_bullets     = rca_v2.get("whatWentWrongBullets", [])
        # Zendesk-derived frames (step 7b) are authoritative; RCA output is fallback.
        draft.support_interaction_frames  = support_frames or rca_v2.get("supportInteractionFrames", [])
        draft.support_summary             = support_summary_text or rca_v2.get("supportSummary", "")
        draft.sp_interaction_frames       = sp_frames or rca_v2.get("spInteractionFrames", [])
        draft.wwr_scenarios               = wwr_scenarios or []
        # Area of Improvement auto-fills from each WWR scenario's fix bullet.
        _wwr_fixes = [s.get("fix", "").strip() for s in (wwr_scenarios or []) if s.get("fix")]
        draft.area_of_improving           = _wwr_fixes or rca_v2.get("areaOfImproving", [])
        # Actions Taken is NOT assigned here. It used to be — an UNGATED list
        # straight from the guideline routing, which project_v4 then overwrote
        # four lines below with the gated one. Correct output, misleading code:
        # a reader following `draft.actions_taken =` landed on the version that
        # does not survive, and the AND that §1 is built on (the guidelines say
        # raise it AND something flagged it) appeared not to be applied.
        #
        # The one write is the projection, from validate()'s actions_raised —
        # which is the only place holding both halves of the rule. See
        # V4_PROJECTION in rca_v4_validate.
        # Resolution starts BLANK by request — it is what the guest actually
        # received, not something the model can settle. The associate types it on
        # the card (persisted via draft-v2). Neither the projected rca_v3 nor the
        # raw rca_v2 resolution is copied in.
        draft.resolution                  = ""

        # v3 fields — always assign so flag_modified never fires on an unset
        # attribute (empty dict when RCA generation failed or returned nothing)
        _v3 = rca_v3 or {}
        # The whole new-shape object (what_went_wrong 5 headings, booking_logs,
        # flags, interactions) lives in rca_fields; a failed
        # generation keeps the previous one rather than wiping it.
        draft.rca_v3                  = _v3 or draft.rca_v3 or {}
        # Stamped only when this run actually produced an RCA. A failed
        # generation keeps the previous blob, so it must keep that blob's
        # version too - claiming v4 over v3 content is worse than no stamp.
        if _v3:
            draft.rca_prompt_version  = prompts.RCA_PROMPT_VERSION
            # The blob this marker described no longer exists — see the note at
            # the top of the save. Cleared only when a new RCA actually
            # replaced it: a run whose generation failed keeps the previous
            # blob, and the previous blob's edits are still there to protect.
            draft.rca_v3_edited_at    = None
        # v4 does not emit wwr_chain or prevention — the chain moved onto each
        # guest issue. Keep whatever a v3-era run left rather than blanking it,
        # so a rollback to v3 finds its data intact.
        draft.wwr_chain               = _v3.get("wwr_chain") or draft.wwr_chain or []
        _prev = _v3.get("prevention")
        if isinstance(_prev, list):
            _prev = "\n".join(f"• {p}" for p in _prev if p)
        draft.prevention              = _prev or draft.prevention
        _aoi = _v3.get("area_of_improving")
        if _aoi:
            draft.area_of_improving   = _aoi if isinstance(_aoi, list) else [_aoi]
        # The v3 shape carries evidence ON each claim and each flag, not as a
        # flat top-level list, so it never returns this key. Assigning [] here
        # deleted the evidence appendix a legacy draft had collected.
        draft.evidence                = _v3.get("evidence") or draft.evidence or []
        # The checklist runs silently now — only failures ship, as
        # rca_fields["flags"]. Nothing renders the full answer wall anymore.
        draft.checklist_answers       = []

        # ── v4 columns ────────────────────────────────────────────────────────
        # The queryable copy of what lives inside rca_v3, which is what the
        # dashboard's editor writes and therefore the source of truth for RCA
        # content - that is why _draft_dict() reads rca_v3 first and falls back
        # to the column, not the other way round.
        #
        # One shared projection with regenerate-rca. Written out twice, the two
        # paths drift, and the drift is invisible: both look like working code.
        from server.services.rca_v4_validate import project_v4
        # The rows already on this draft are read at step 12c and handed to
        # validate() as keep_actions — which is where they have to be read,
        # because that is the call that decides which of them survive. Reading
        # them again here did nothing with the result.
        for _col, _val in project_v4(_v3).items():
            setattr(draft, _col, _val)

        draft.ticket_facts                = ticket_facts or None
        # The OUTGOING reply, in the guest's language. One store: this and
        # final_response are the only fields anything sends, copies or posts
        # from. `response_english` beside it is the working view and is never
        # sent — it is empty whenever the outgoing text is not a translation
        # of it, so the card can never show an English box that claims to
        # correspond to a reply it does not.
        draft.suggested_response          = _reply_out
        draft.response_english            = _reply_english or None
        draft.response_english_of         = _reply_eng_of
        draft.generated_at                = datetime.utcnow()
        # The RCA now describes the booking currently attached, so it is no
        # longer stale — a confirmation set this True and reaching here is what
        # earns it back. A run that dies before this line leaves it True, which
        # is the point: an unfinished rebuild must not become postable.
        draft.rca_stale                   = False
        # A review already SENT stays sent. A re-run regenerates the AI half,
        # which is a fine thing to do to an old review - but resetting the
        # status would pull it out of Sent and back into a working tab, as if
        # the reply had never gone out.
        #
        # RE-READ, NEVER MERGE. `review` was DETACHED before the model-call
        # phase to release the connection, so it holds the row as it was
        # MINUTES ago — a full RCA run is the longest window in this system.
        # `db.merge(review)` writes that whole stale object back, every column
        # of it, so anything a person did to the review while the run was in
        # flight was silently reverted on the run's way out:
        #
        #     associate closes the review   -> status=sent, sent_route=closed,
        #                                      closed_at, close_reason set
        #     the in-flight run finishes    -> merge puts back status=new,
        #                                      sent_route=None, closed_at=None,
        #                                      close_reason=None
        #
        # and the guard below could not see it, because it tested the STALE
        # object's status ("new"), not the row's ("sent"). That is the reported
        # "reviews revert out of Sent": a finished review pulled back into a
        # working tab by a run that started before it was finished, with the
        # reason it was closed erased along with it.
        #
        # So the live row is fetched and the ONE field this step owns is set on
        # it. Everything else the row carries belongs to whoever wrote it.
        _live = db.query(Review).filter(Review.id == review_id).first()
        if _live is None:
            # Deleted mid-run (a purge). Nothing to write, and re-adding it
            # from the stale copy would resurrect a row someone removed.
            log.warning(f"[pipeline] {review_id}: the review row is gone — "
                        f"skipping the status write rather than recreating it")
        else:
            review = _live
            if review.status != "sent":
                review.status             = "draft"

        # Force SQLAlchemy to detect JSON column changes on re-runs
        # (JSON type does not track in-place mutations automatically)
        for _col in (
            "booking", "candidates_list", "confidence_trail",
            "extracted_signals", "narrowing_attempts",
            "timeline", "insights", "similar_support", "similar_reviews",
            "dss_rec", "zendesk_ticket_ids", "timeline_raw", "slack_mentions",
            "diagnostic_checks", "what_went_wrong_bullets",
            "support_interaction_frames", "sp_interaction_frames",
            "area_of_improving", "actions_taken", "overlay_scenarios", "wwr_scenarios",
            "wwr_chain", "evidence", "issue_specific_answers", "checklist_answers",
            "ticket_facts", "rca_v3", "area_of_improving",
            "guest_issues", "booking_logs", "flags",
            "takedown", "dss",
        ):
            try:
                flag_modified(draft, _col)
            except Exception as _fm_err:
                log.warning(f"[pipeline] flag_modified({_col}) skipped: {_fm_err}")

        db.commit()

        # ── 15. Slack post-back — disabled until explicitly re-enabled ──────────
        # Do not post anything to Slack threads from the pipeline.

        # ── 16. Metrics ───────────────────────────────────────────────────────
        try:
            m = db.query(ReviewMetric).filter(ReviewMetric.review_id == review_id).first()
            if not m:
                m = ReviewMetric(review_id=review_id)
                db.add(m)
            m.received_at      = review.received_at
            m.channel          = review.slack_channel
            m.rating           = review.rating
            m.language         = review.language
            m.match_tier       = draft.match_tier
            m.match_confidence = draft.match_confidence
            m.auto_matched     = draft.match_tier in (1, 2)
            m.l1               = l1
            m.l2               = l2
            db.commit()
        except Exception as e:
            log.exception(f"Metrics write failed: {e}")

    except Exception as _fatal:
        # generated_at is stamped at the very end of the run, and the dashboard
        # polls on it to know a re-run finished. Any exception before that point
        # left nothing written at all: no result, no error, just a spinner that
        # timed out after three minutes. Record the failure and stamp the run so
        # it terminates visibly instead of silently.
        # Three different endings, three different sentences. "Recorded",
        # "there was no draft row to record it on" and "we could not reach the
        # database to try" are not the same event, and a reader who sees one
        # generic line cannot tell which happened.
        log.exception(f"[pipeline] {review_id} failed: {_fatal}")
        if db is None:
            log.error(f"[pipeline] {review_id}: the failure was opening the "
                      f"database session itself, so this run never had one. "
                      f"Trying a fresh session to record it.")
        try:
            # db is None when SessionLocal() was what failed; record_run_failure
            # then opens its own, which is a real second chance — a pool that
            # was exhausted a moment ago may not be now.
            if not record_run_failure(review_id, _fatal, db):
                log.error(f"[pipeline] {review_id}: died with no draft row to "
                          f"record it on — the run failed before the early "
                          f"persist, so the review carries no trace of it")
        except Exception:
            log.exception(
                f"[pipeline] {review_id}: could not record the failure — the "
                f"database was unreachable for the recording too, so the only "
                f"trace of this run is this log line")
    finally:
        # Absent entry = no run in flight. Leaving a terminal entry behind
        # would make the next poll read a finished run as a stuck one.
        PIPELINE_PROGRESS.pop(review_id, None)
        # None when SessionLocal() itself raised. Unconditionally closing here
        # would raise AttributeError out of the finally and REPLACE the real
        # exception with a misleading one.
        if db is not None:
            db.close()
