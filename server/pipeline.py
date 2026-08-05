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
from datetime import datetime, timedelta

from sqlalchemy.orm.attributes import flag_modified

from server.config import is_live, MOCK_MODE
from server import prompts
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


# How far back the broad Zendesk searches look, from the review's own date.
# Guests review weeks to months after visiting, occasionally a year later;
# unbounded, a search on a common name returns more than Zendesk will hand
# back and the right ticket can fall outside what we get.
SHORTLIST_LOOKBACK_DAYS = 540


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
                "events timeline is a lookup that never ran")
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
            # Step 3c — BQ support-contact anchored search
            "t2_bq_support_attempted":     0,
            "t2_bq_support_candidates":    0,
            "t2_bq_support_no_match":      0,
            "t2_zendesk_truncated":        0,
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
                        from server.services.zendesk import _name_score as _nsc
                        # The SECOND copy of the first/last split, and it had
                        # the same fault. One rule, one place.
                        _af, _al = _pa(review.author or "")
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
                            _why.append(f"name {name_conf:.1f}" if pgn
                                        else "no guest name on booking")
                            if not venue_ok:
                                _why.append("no venue match")
                            candidates = [_shape_weak_bid(bq_row, _why)]
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
                        log.warning(f"[tier2] shortlist failed: {e}")
                        _short = []

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
        untraceable_reason = None
        if not booking and review.reference_number and bid_source:
            why = ("BigQuery is not live on this server"
                   if not is_live("bigquery")
                   else "BigQuery did not return this booking")
            booking = {
                "id": str(review.reference_number),
                "_unverified": True,
                "_unverified_reason": why,
                "_match": {"tier": 1, "confidence": "unverified",
                           "method": f"BID {bid_source} from the review — {why}"},
            }
            match_tier      = 1
            candidate_state = False
            confidence_trail.append({
                "mark": "warn",
                "text": (f"<strong>BID {review.reference_number}</strong> taken from the "
                         f"review ({bid_source}) but NOT verified — {why}. "
                         f"Shown so the review is not filed as untraceable."),
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
                untraceable_reason = (f"BID {review.reference_number} was on the review but "
                                      f"BigQuery did not return it.")
            elif narrowing_attempts:
                untraceable_reason = (f"No BID on the review; {len(narrowing_attempts)} "
                                      f"search attempt(s) returned nothing.")
            else:
                untraceable_reason = ("No BID in the review text and no usable name or "
                                      "venue signal to search with.")
            log.info(f"[pipeline] {review_id} untraceable: {untraceable_reason}")

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

        _progress(review_id, 2, "fetching Zendesk timeline")
        # ── 6. Zendesk timeline ──────────────────────────────────────────────
        timeline      = []
        extracted_bk  = {}
        zd_meta       = {"ticket_ids": [], "timeline_raw": []}
        bid_for_zd    = (booking or {}).get("id") or review.reference_number
        _zd_pub_date  = review.received_at.strftime("%Y-%m-%d") if review.received_at else ""
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

        # Suppressed when the provider is down: the warning above has already
        # said so, and said it better.
        _cls_entry = None if _ai_down else classification_entry(
            l1, l2, _classify_err, _classify_warnings)
        if _cls_entry:
            confidence_trail.append(_cls_entry)

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
            # Approved replies as a VOICE reference for suggested_response.
            # This is a sheet lookup, not a model call - the reply is still
            # written once, inside the RCA, against this case's evidence.
            # Output rule 18 is what stops the examples becoming content.
            _tone_err = None
            try:
                canned_list = await get_canned_responses(
                    l1, l2, sub_theme, review_text or "",
                    untraceable=not booking)
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
                rca_v3, rca_notes = _validate_rca(rca_v3, _scenarios_routed)
                # A coercion the reader cannot see is a silent edit. The trail
                # is where this build already puts "we changed what the model
                # said, and here is why", so each note goes there verbatim.
                import html as _html
                for _n in rca_notes:
                    log.warning(f"[pipeline] rca validation: {_n}")
                    confidence_trail.append({
                        "mark": "warn",
                        "text": f"<strong>RCA</strong> — {_html.escape(str(_n))}",
                    })
            except Exception as e:
                log.exception(f"RCA validation failed, keeping raw output: {e}")

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
        # Actions Taken sourced from Guidelines scenario action lists (Task #13).
        # Fall back to the AI's actions only when routing produced none.
        draft.actions_taken               = (
            guideline_actions if any((guideline_actions or {}).values())
            else rca_v2.get("actionsTaken",
                            {"sp":[],"customer":[],"business":[],"product":[],"ce":[]}))
        # v4 settles the resolution off the full evidence base; rca_v2's is the
        # fallback for a draft whose RCA call failed.
        draft.resolution                  = ((rca_v3 or {}).get("resolution")
                                             or rca_v2.get("resolution", ""))

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
        for _col, _val in project_v4(_v3).items():
            setattr(draft, _col, _val)

        draft.ticket_facts                = ticket_facts or None
        draft.suggested_response          = response_draft
        draft.generated_at                = datetime.utcnow()
        # A review already SENT stays sent. A re-run regenerates the AI half,
        # which is a fine thing to do to an old review - but resetting the
        # status would pull it out of Sent and back into a working tab, as if
        # the reply had never gone out.
        if review.status != "sent":
            review.status                 = "draft"

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
        log.exception(f"[pipeline] {review_id} failed: {_fatal}")
        try:
            db.rollback()
            _d = db.query(RcaDraft).filter(RcaDraft.review_id == review_id).first()
            if _d:
                _tr = list(_d.confidence_trail or [])
                # Defect 5: an exception is not a trail step. A title, one
                # plain-language sentence, and the raw text kept alongside for
                # the reader who wants it - behind a toggle in the UI, never
                # inline. Discarding it entirely was the other extreme: the
                # only copy then lived in a log the reader cannot reach.
                _entry = failure_entry(_fatal)
                # Do not stack the same failure twice. A retried run appended a
                # second identical line, so the panel grew a wall of duplicate
                # stack traces that told the reader nothing new.
                if not _tr or _tr[-1].get("text") != _entry["text"]:
                    _tr.append(_entry)
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
