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
from datetime import datetime

from sqlalchemy.orm.attributes import flag_modified

from server.config import is_live, MOCK_MODE
from server.db import SessionLocal, Review, RcaDraft, ReviewMetric
from server.services import claude, bigquery as bq, zendesk, dss, slack as slk
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
    """
    s = (s or "").strip()
    if not s or " " in s:
        return False
    return len(s) >= 16 and bool(re.fullmatch(r"[A-Za-z0-9+/=_\-]+", s))


def _venue_token_overlap(review_text: str, exp_name: str) -> bool:
    """
    Robust venue signal: True only when the review and the experience name
    share a SIGNIFICANT word, compared at word level — not the old fragile
    substring scan where any 4-char fragment could match inside the name.
    """
    return bool(_sig_tokens(review_text) & _sig_tokens(exp_name))


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


def _progress(review_id: str, step: int, stage: str):
    import time as _t
    e = PIPELINE_PROGRESS.get(review_id) or {"started_at": _t.time()}
    e.update({"step": step, "total": _STAGES_TOTAL, "stage": stage,
              "elapsed_s": int(_t.time() - e["started_at"])})
    PIPELINE_PROGRESS[review_id] = e


async def process_review(review_id: str, force_candidates: bool = False):
    """
    force_candidates: an associate re-ran a review whose booking they had already
    confirmed. They are asking to see the options again, so matching must present
    the candidate list rather than silently auto-promoting the best one — which
    would drop them straight back into a confirmed state they were trying to
    leave.
    """
    db = SessionLocal()
    try:
        review = db.query(Review).filter(Review.id == review_id).first()
        if not review:
            log.error(f"Review {review_id} not found")
            return

        log.info(f"[pipeline] {review_id} — start")
        _progress(review_id, 1, "matching booking")

        # ── 1. Translate ──────────────────────────────────────────────────────
        if not review.body_english:
            try:
                result = await claude.translate(
                    review.body_original, review.language or "auto", review_id)
                if result and result.strip() != "ENGLISH_ALREADY":
                    review.body_english = result.strip()
                db.commit()
            except Exception as e:
                log.exception(f"Translation failed: {e}")

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
        _ctr = {
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
        }

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
                _prior_trail = list(_prior.confidence_trail or [])
                if _prior_trail:
                    confidence_trail   = _prior_trail
                    extracted_sigs     = dict(_prior.extracted_signals or {})
                    candidates         = list(_prior.candidates_list or [])
                    narrowing_attempts = list(_prior.narrowing_attempts or [])
                    confidence_trail.append({"mark": "pass",
                        "text": f"<strong>Associate confirmed</strong> BID {confirmed_bid} — "
                                "the steps above are from the run that found it"})
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

        if confirmed_bid and booking:
            pass   # confirmed booking in hand; skip the whole match cascade
        elif not is_live("bigquery"):
            # MOCK_MODE: fall back to existing mock-aware find_booking
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
                        from server.services.zendesk import _name_score as _nsc
                        _ap = (review.author or "").strip().split()
                        _af = _ap[0] if _ap else None
                        _al = _ap[-1] if len(_ap) > 1 else None
                        verify_hits = []

                        pgn = bq_row.get("primary_guest_name") or ""
                        name_conf = _nsc(pgn, _af, _al)
                        if name_conf >= 0.7:          # surname agrees at minimum
                            verify_hits.append(f"name({name_conf:.1f})")

                        exp_name = (bq_row.get("experienceName") or "")
                        venue_ok = bool(exp_name) and _venue_token_overlap(review_text or "", exp_name)
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
                        if not (name_conf >= 0.7 or venue_ok):
                            # Weak BID — downgrade to Tier 2. booking stays
                            # unset: an unverified number must not become the
                            # matched booking.
                            bq_row["low_confidence_bid_match"] = True
                            candidates = [bq_row]
                            match_tier = 2
                            candidate_state = True
                            _ctr["t1_regex_downgraded"] += 1
                            confidence_trail.append({"mark": "warn",
                                "text": f"<strong>Weak BID</strong> — number found in text, but "
                                        f"booking guest '{pgn or '—'}' scores {name_conf:.1f} and no "
                                        f"venue match{' (date only)' if date_ok else ''}. "
                                        f"Needs confirmation."})
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
                indicators = {}
                try:
                    from server.prompts import match_indicator_prompt
                    _pub = (review.received_at or datetime.utcnow()).date().isoformat()
                    raw = await claude._call(
                        match_indicator_prompt(match_text or "", _pub,
                                               reviewer_name=review.author or ""),
                        max_tokens=400)
                    indicators = claude._extract_json_object(raw) or {}
                except Exception as e:
                    log.warning(f"Indicator extraction failed: {e}")
                venue_hints = [h for h in (
                    indicators.get("experience_or_venue"),
                    indicators.get("city_or_country")) if h and str(h).strip()]
                # The model occasionally answers with a range or an explanation
                # ("2026-07-23 or 2026-07-24 (booked yesterday...)"). Anything
                # that is not a bare date cannot be parsed downstream and scored
                # zero silently, so pull the first date out or drop it.
                _vd = str(indicators.get("visit_date_hint") or "")
                _m = re.search(r"\d{4}-\d{2}-\d{2}", _vd)
                indicators["visit_date_hint"] = _m.group(0) if _m else None

                extracted_sigs["venue_hints"] = venue_hints
                extracted_sigs["match_indicators"] = indicators
                # pax is extracted and persisted, but cannot be SCORED yet: no
                # BQ query in this codebase selects a pax/quantity column, and
                # the Zendesk extractor does not populate one either. Wiring it
                # needs the pax column name in fct_bookings.
                extracted_sigs["pax_hint"] = indicators.get("pax")
                if indicators:
                    confidence_trail.append({"mark": "pass",
                        "text": "<strong>Indicators:</strong> "
                                f"venue='{indicators.get('experience_or_venue') or '—'}' · "
                                f"city='{indicators.get('city_or_country') or '—'}' · "
                                f"visit≈'{indicators.get('visit_date_hint') or '—'}'"})

                # Resolve venue hints → TGIDs
                tgids = None
                if venue_hints:
                    try:
                        tgids = await venue_resolver.resolve(venue_hints)
                    except Exception as e:
                        log.warning(f"Venue resolver failed: {e}")
                if tgids:
                    _ctr["t2_venue_mapped"] += 1
                    confidence_trail.append({"mark": "pass",
                        "text": f"<strong>Venues:</strong> {venue_hints} → {len(tgids)} TGIDs"})
                elif venue_hints:
                    _ctr["t2_venue_not_resolved"] += 1
                    confidence_trail.append({"mark": "warn",
                        "text": f"<strong>Venues extracted</strong> but no TGIDs resolved: {venue_hints}"})

                log.info(
                    f"[tier2] venue resolution: mapped_tgids={_ctr['t2_venue_mapped']} | "
                    f"not_resolved={_ctr['t2_venue_not_resolved']}"
                )

                # Author parsing
                def _parse_author(name: str):
                    if not name:
                        return None, None
                    parts = name.strip().split()
                    if not parts:
                        return None, None
                    if len(parts) == 1:
                        t = parts[0]
                        return (t, None) if t.isalpha() and len(t) >= 2 else (None, None)
                    return parts[0], parts[-1]

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
                pub_date = (review.received_at or datetime.utcnow()).strftime("%Y-%m-%d")
                extracted_sigs["review_pub_date"] = pub_date

                if author_first or author_last:
                    confidence_trail.append({"mark": "pass",
                        "text": f"<strong>Author parsed:</strong> first='{author_first}' last='{author_last}'"})

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
                search_identities = []
                for _f, _l in ((author_first, author_last), (ind_first, ind_last)):
                    if _name_parseable(_f, _l) and (_f, _l) not in search_identities:
                        search_identities.append((_f, _l))
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

                # ── Step 2: indicator shortlist ────────────────────────────
                # Search Zendesk with whatever indicators the review gave us and
                # keep only tickets satisfying ALL of them. No BigQuery here --
                # the booking id and every fact needed to judge a match are on
                # the ticket; BQ runs when the associate confirms.
                if not cascade_done and (name_parseable or venue_hints):
                    try:
                        _short = await zendesk.shortlist(
                            indicators, author_first, author_last)
                    except Exception as e:
                        log.warning(f"[tier2] shortlist failed: {e}")
                        _short = []

                    if _short:
                        candidates = []
                        for _sig in _short:
                            _c = _make_candidate(
                                {"id": _sig["booking_id"],
                                 "primary_guest_name": _sig.get("guest_name", ""),
                                 "experienceName": _sig.get("experience", ""),
                                 "date_of_visit": _sig.get("visit_date", ""),
                                 "vendorName": _sig.get("vendor_name", "")},
                                "indicator_shortlist",
                                _sig.get("matched_on") or ["name"])
                            _c["id"] = _sig["booking_id"]
                            _c["matched_on"] = _sig.get("matched_on") or ["name"]
                            candidates.append(_c)
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
                            "text": "<strong>No booking matches these indicators</strong> — "
                                    "Zendesk returned nothing that satisfies them."})

                # ── Legacy requester lookup (only if the shortlist found none) ──
                if name_parseable and not cascade_done:
                    _ctr["t2_zendesk_lookup_attempted"] += 1
                    _names_str = ", ".join(
                        f"'{f}{(' ' + l) if l else ''}'" for f, l in search_identities)
                    confidence_trail.append({"mark": "pass",
                        "text": f"<strong>Zendesk lookup:</strong> {_names_str}"})
                    zd_bids = []
                    bid_ticket_text = {}   # bid -> subject+body of its source ticket
                    bid_name_score  = {}   # bid -> 0..1 requester-name confidence
                    bid_signals     = {}   # bid -> ticket custom-field facts
                    for _f, _l in search_identities:
                        try:
                            # No lookback_days — the service defaults to since
                            # Jan 1 of the current year. Passing 60 here silently
                            # overrode that agreed window and dropped tickets
                            # older than 60 days (guests review months later),
                            # which is how a requester's real BID went unharvested.
                            _hits, _trecs = await zendesk.find_bids_by_requester_name(
                                _f, _l, with_context=True)
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
                            if _conf >= 3.0 and not force_candidates:
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
                                    "text": f"<strong>Tier 1 auto-promote</strong> — indicator "
                                            f"confidence {_conf:.1f} (name {_name_pts(bq_row, bid):.1f} · "
                                            f"venue {_venue_pts(bq_row, bid):.1f} · ticket {_ticket_pts(bid):.1f})"})
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
                                             f"{bid} at confidence {_conf:.1f} (need 3.0). Booking guest "
                                             f"'{_pgn or '—'}'. Needs confirmation.")})
                                log.info(f"[tier2] single BID {bid} withheld: conf={_conf:.2f} "
                                         f"guest={_pgn!r} vs review={author_first} {author_last}")
                            cascade_done = True
                        elif n_zd >= 2:
                            ranked = sorted(zd_candidates,
                                            key=lambda t: _score(t[1], t[0]), reverse=True)[:5]
                            _matched_on = ["name", "zendesk"]
                            if both_bids:
                                _matched_on.append("name+venue")
                            elif venue_signal:
                                _matched_on.append("venue")
                            elif any(_ticket_pts(b) > 0 for b, _ in ranked):
                                _matched_on.append("ticket-text")
                            else:
                                _matched_on.append("date-only")
                            candidates = [_make_candidate(row, "zendesk_requester", _matched_on)
                                          for _, row in ranked]
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
                            cascade_done = True
                        else:
                            _ctr["t2_zendesk_no_match"] += 1
                            confidence_trail.append({"mark": "warn",
                                "text": "<strong>Zendesk:</strong> 0 verified candidates after cross-check"})
                    else:
                        _ctr["t2_zendesk_no_match"] += 1
                        confidence_trail.append({"mark": "warn",
                            "text": "<strong>Zendesk:</strong> no BIDs found for this name"})

                # ── Step 3: BQ narrowing — venue+date only, never name ─────────
                if not cascade_done:
                    if tgids:
                        # 3a: venue + date_30
                        rows = _run_bq_attempt("venue_date_30", 30, tgid_list=tgids)
                        n = len(rows)
                        if n == 1:
                            booking = _make_candidate(rows[0], "venue_date_30_auto", ["venue", "date"])
                            booking.update(_get_booking_extra(booking.get("id", "")))
                            match_tier = 1
                            narrowing_path = "venue_date_30_auto"
                            _ctr["t2_bq_venue_date_30_auto"] += 1
                            _ctr["t1_auto_promoted"] += 1
                            _ctr["t2_auto_promoted"] += 1
                            confidence_trail.append({"mark": "pass",
                                "text": "<strong>Tier 1 auto-promote</strong> via venue+date_30 (single match)"})
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
                            booking = _make_candidate(rows[0], "venue_date_60_auto", ["venue", "date"])
                            booking.update(_get_booking_extra(booking.get("id", "")))
                            match_tier = 1
                            narrowing_path = "venue_date_60_auto"
                            _ctr["t2_bq_venue_date_60_auto"] += 1
                            _ctr["t1_auto_promoted"] += 1
                            _ctr["t2_auto_promoted"] += 1
                            confidence_trail.append({"mark": "pass",
                                "text": "<strong>Tier 1 auto-promote</strong> via venue+date_60 (single match)"})
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
            _m = (booking or {}).get("_match", {})
            _d.booking            = {k: v for k, v in (booking or {}).items()
                                     if k != "_match"}
            _d.match_tier         = match_tier or _m.get("tier")
            _d.match_confidence   = _m.get("confidence")
            _d.match_method       = _m.get("method") or narrowing_path
            _d.candidates_list    = candidates
            _d.candidate_state    = candidate_state
            _d.confidence_trail   = confidence_trail
            _d.bid_source         = bid_source
            _d.extracted_signals  = extracted_sigs or {}
            _d.narrowing_attempts = narrowing_attempts or []
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

        _progress(review_id, 2, "fetching Zendesk timeline")
        # ── 6. Zendesk timeline ──────────────────────────────────────────────
        timeline      = []
        extracted_bk  = {}
        zd_meta       = {"ticket_ids": [], "timeline_raw": []}
        bid_for_zd    = (booking or {}).get("id") or review.reference_number
        _zd_pub_date  = review.received_at.strftime("%Y-%m-%d") if review.received_at else ""
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
                log.warning(f"Zendesk failed — continuing with empty timeline: {e}")
                timeline, extracted_bk = [], {}
                zd_meta = {"ticket_ids": [], "timeline_raw": []}

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

        # ── 10. Stated Issue ──────────────────────────────────────────────────
        stated_issue = ""
        try:
            stated_issue = await claude.stated_issue(review_text, review_id)
        except Exception as e:
            log.exception(f"Stated issue failed: {e}")

        _progress(review_id, 4, "classifying issue")
        # ── 11. Classification ────────────────────────────────────────────────
        l1, l2, l1_reasoning, sub_theme = "", "", "", None
        try:
            from server.services.classifier import classify as classify_v2
            from server.services.claude import _call as claude_call
            result = await classify_v2(review_text, booking, timeline, claude_call, review_id)
            l1 = result.l1
            l2 = result.l2
            sub_theme = result.sub_theme
            l1_reasoning = result.reasoning
            for w in result.warnings:
                log.warning(f"[classify {review_id}] {w}")
        except Exception as e:
            log.exception(f"Classification failed: {e}")

        # ── 11b. Warehouse L1/L2 comparison (log-only; Claude stays authoritative)
        try:
            _bid = (booking or {}).get("id")
            if _bid:
                _wh = await bq.get_l1_l2_by_bid(_bid)
                if _wh.get("l1") or _wh.get("l2"):
                    log.info(
                        f"[classify {review_id}] L1/L2 comparison for BID {_bid} — "
                        f"Claude: {l1!r} / {l2!r} | warehouse: {_wh['l1']!r} / {_wh['l2']!r}")
        except Exception as e:
            log.exception(f"Warehouse L1/L2 lookup failed: {e}")

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
        try:
            dss_rec = await dss.get_recommendation(
                booking or {}, review_id, l1=l1, l2=l2, review_text=review_text or "")
        except Exception as e:
            log.exception(f"DSS failed: {e}")

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
        primary_scenario, overlay_scenarios, guideline_actions = None, [], {}
        try:
            from server.checklist import (
                scenarios_for, compute_overlay_scenarios, actions_for, SCENARIO_CHECKS)
            primary_scenario = scenarios_for(l1, l2, sub_theme)["primary"]
            overlay_scenarios = compute_overlay_scenarios(
                l1, l2, sub_theme, ticket_facts, booking)
            scenario_keys = [s for s in ([primary_scenario] + overlay_scenarios)
                             if s in SCENARIO_CHECKS]
            guideline_actions = actions_for(scenario_keys)
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
            )
        except Exception as e:
            log.exception(f"RCA v3 generation failed: {e}")

        _progress(review_id, 7, "drafting response")
        # ── 13. Response draft ────────────────────────────────────────────────
        response_draft = ""
        draft_template_name = ""
        try:
            canned         = await get_canned_responses(l1, l2, sub_theme, review_text or "")
            # Name the reference the drafter actually had. An empty list is
            # worth surfacing: the response was written with no tone
            # reference at all, which is what the associate needs to know.
            draft_template_name = (
                (canned[0].get("situation") or "").strip()[:120] if canned
                else "NO MATCHING TEMPLATE — drafted without a tone reference")
            response_draft = await claude.draft_response_v2(
                review_text=review_text,
                l1=l1,
                l2=l2,
                resolution=rca_v2.get("resolution", ""),
                review_id=review_id,
                guest_name=(
                    (ticket_facts or {}).get("guest_full_name")
                    or (booking or {}).get("guestName")
                    or (review.author or "")
                ),
                dss_rec=dss_rec,
                canned_list=canned,
            )
        except Exception as e:
            log.exception(f"Response draft failed: {e}")

        _progress(review_id, 8, "saving")
        # ── 14. Save ──────────────────────────────────────────────────────────
        draft = db.query(RcaDraft).filter(RcaDraft.review_id == review_id).first()
        if not draft:
            draft = RcaDraft(id=f"draft_{review_id}", review_id=review_id)
            db.add(draft)

        _match = (booking or {}).get("_match", {})
        booking_to_save = {k: v for k, v in (booking or {}).items() if k != "_match"}
        zd_requester = zd_meta.get("zendesk_requester_name", "")
        if zd_requester:
            booking_to_save["zendesk_requester_name"] = zd_requester
        draft.booking              = booking_to_save
        draft.match_tier           = match_tier or _match.get("tier")
        draft.match_confidence     = _match.get("confidence")
        draft.match_method         = _match.get("method") or narrowing_path
        draft.candidates_list      = candidates
        draft.candidate_state      = candidate_state
        draft.confidence_trail     = confidence_trail
        draft.bid_source           = bid_source
        draft.extracted_signals    = extracted_sigs or {}
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
        # Actions Taken sourced from Guidelines scenario action lists (Task #13).
        # Fall back to the AI's actions only when routing produced none.
        draft.actions_taken               = (
            guideline_actions if any((guideline_actions or {}).values())
            else rca_v2.get("actionsTaken",
                            {"sp":[],"customer":[],"business":[],"product":[],"ce":[]}))
        draft.resolution                  = rca_v2.get("resolution", "")

        # v3 fields — always assign so flag_modified never fires on an unset
        # attribute (empty dict when RCA generation failed or returned nothing)
        _v3 = rca_v3 or {}
        # The whole new-shape object (what_went_wrong 5 headings, booking_logs,
        # flags, interactions, sop_compliance) lives in rca_fields; a failed
        # generation keeps the previous one rather than wiping it.
        draft.rca_v3                  = _v3 or draft.rca_v3 or {}
        _tldr = _v3.get("tldr")
        if isinstance(_tldr, dict):
            draft.tldr = (f"Our mistake: {_tldr.get('our_mistake', '')} "
                          f"Our fix: {_tldr.get('our_fix', '')}").strip()
        else:
            draft.tldr = _tldr or draft.tldr
        draft.wwr_chain               = _v3.get("wwr_chain") or []
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
        draft.issue_specific_answers  = _v3.get("issue_specific_answers") or {}
        # The checklist runs silently now — only failures ship, as
        # rca_fields["flags"]. Nothing renders the full answer wall anymore.
        draft.checklist_answers       = []

        draft.ticket_facts                = ticket_facts or None
        draft.suggested_response          = response_draft
        draft.template_name               = draft_template_name
        draft.generated_at                = datetime.utcnow()
        review.status                     = "draft"

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
        log.exception(f"[pipeline] {review_id} failed: {_fatal}")
        try:
            db.rollback()
            _d = db.query(RcaDraft).filter(RcaDraft.review_id == review_id).first()
            if _d:
                _tr = list(_d.confidence_trail or [])
                _tr.append({"mark": "fail",
                            "text": f"<strong>Run failed</strong> — {type(_fatal).__name__}: "
                                    f"{str(_fatal)[:300]}"})
                _d.confidence_trail = _tr
                _d.generated_at = datetime.utcnow()
                flag_modified(_d, "confidence_trail")
                db.commit()
        except Exception:
            log.exception(f"[pipeline] {review_id}: could not record the failure")
    finally:
        # Absent entry = no run in flight. Leaving a terminal entry behind
        # would make the next poll read a finished run as a stuck one.
        PIPELINE_PROGRESS.pop(review_id, None)
        db.close()
