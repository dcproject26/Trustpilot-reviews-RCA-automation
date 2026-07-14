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
import asyncio, logging, re
from datetime import datetime

from server.config import is_live
from server.db import SessionLocal, Review, RcaDraft, ReviewMetric
from server.services import claude, bigquery as bq, zendesk, dss, slack as slk
from server.services.canned import get_canned_responses
from server.taxonomy import DIAGNOSTIC_CHECKS, BID_REGEX

log = logging.getLogger(__name__)


async def process_review(review_id: str):
    db = SessionLocal()
    try:
        review = db.query(Review).filter(Review.id == review_id).first()
        if not review:
            log.error(f"Review {review_id} not found")
            return

        log.info(f"[pipeline] {review_id} — start")

        # ── 1. Translate ──────────────────────────────────────────────────────
        if review.language and review.language != "en" and not review.body_english:
            try:
                review.body_english = await claude.translate(
                    review.body_original, review.language, review_id)
                db.commit()
            except Exception as e:
                log.exception(f"Translation failed: {e}")

        review_text = review.body_english or review.body_original

        # ── 2. BID regex (Tier 1) ─────────────────────────────────────────────
        confidence_trail = []
        ref_match = re.search(BID_REGEX, review_text or "")
        if ref_match:
            confidence_trail.append({
                "mark": "pass",
                "text": f"<strong>BID regex</strong> — matched {ref_match.group(0)} in review text",
            })
            if not review.reference_number:
                review.reference_number = ref_match.group(0)
                db.commit()
        else:
            confidence_trail.append({
                "mark": "pass",
                "text": "<strong>BID regex</strong> — no 7–12 digit number in text",
            })

        # ── 3. Signal extraction (Tier 2, only if no BID) ────────────────────
        signals = None
        if not ref_match:
            try:
                signals = await claude.extract_signals(review_text, review_id)
                bits = []
                if signals.get("guest_name"):      bits.append(f'name "{signals["guest_name"]}"')
                if signals.get("experience_hint"): bits.append(f'experience "{signals["experience_hint"]}"')
                if signals.get("venue_or_city"):   bits.append(f'venue "{signals["venue_or_city"]}"')
                if bits:
                    confidence_trail.append({
                        "mark": "pass",
                        "text": "<strong>Claude signal extraction:</strong> " + " · ".join(bits),
                    })
            except Exception as e:
                log.exception(f"Signal extraction failed: {e}")

        # ── 4. Booking match ──────────────────────────────────────────────────
        booking          = None
        match_tier       = None
        candidates       = []
        candidate_state  = False
        try:
            search_ctx = {
                "id":               review_id,
                "author":           review.author or "",
                "reference_number": review.reference_number,
                "signals":          signals or {},
            }
            match_result = await bq.find_booking(search_ctx)

            # Contract: find_booking returns either a single booking dict OR
            # a dict with {"candidates": [...]} when confidence is medium.
            if match_result and match_result.get("candidates"):
                candidates      = match_result["candidates"]
                candidate_state = True
                confidence_trail.append({
                    "mark": "pass",
                    "text": f"<strong>BigQuery:</strong> {len(candidates)} candidates returned",
                })
                confidence_trail.append({
                    "mark": "warn",
                    "text": "<strong>Confidence:</strong> medium — associate to confirm",
                })
                # Pre-select the top candidate so the pipeline can continue to build context.
                # The associate will confirm/change via /select-candidate endpoint.
                booking = candidates[0]
                match_tier = 2
            elif match_result:
                booking = match_result
                match_tier = booking.get("_match", {}).get("tier")
                confidence_trail.append({
                    "mark": "pass",
                    "text": f"<strong>BigQuery:</strong> Tier {match_tier} match — BID {booking.get('id')}",
                })
        except Exception as e:
            log.exception(f"Booking match failed: {e}")

        # ── 6. Zendesk timeline ──────────────────────────────────────────────
        timeline      = []
        extracted_bk  = {}
        bid_for_zd    = (booking or {}).get("id") or review.reference_number
        if bid_for_zd:
            try:
                timeline, extracted_bk = await zendesk.get_timeline(bid_for_zd, review_id)
                log.info(f"[pipeline] timeline: {len(timeline)} events")
            except Exception as e:
                log.exception(f"Zendesk failed: {e}")

        # Merge Zendesk-extracted fields as fallback for missing BQ fields
        if extracted_bk and booking:
            for key in ("tgid", "tid", "vid", "experienceName", "visitDate",
                        "vendorName", "pax"):
                if not booking.get(key) and extracted_bk.get(key):
                    booking[key] = extracted_bk[key]

        # ── 7. Insights ──────────────────────────────────────────────────────
        insights = {}
        if booking and booking.get("tgid"):
            try:
                insights = await bq.get_insights(booking)
            except Exception as e:
                log.exception(f"Insights failed: {e}")

        # ── 8. Similar complaints ─────────────────────────────────────────────
        similar_support = []
        similar_reviews = []
        if booking:
            try:
                similar_support, similar_reviews = await bq.get_similar_complaints(booking)
            except Exception as e:
                log.exception(f"Similar complaints failed: {e}")

        # ── 9. DSS (eager) ────────────────────────────────────────────────────
        dss_rec = {}
        try:
            dss_rec = await dss.get_recommendation(booking or {}, review_id)
        except Exception as e:
            log.exception(f"DSS failed: {e}")

        # ── 10. Stated Issue ──────────────────────────────────────────────────
        stated_issue = ""
        try:
            stated_issue = await claude.stated_issue(review_text, review_id)
        except Exception as e:
            log.exception(f"Stated issue failed: {e}")

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

        # ── 12. Full structured RCA ───────────────────────────────────────────
        rca_v2 = {}
        try:
            rca_v2 = await claude.generate_rca_v2(
                review_text, booking, timeline, insights, dss_rec, l1, l2, review_id)
        except Exception as e:
            log.exception(f"RCA v2 generation failed: {e}")

        # ── 13. Response draft ────────────────────────────────────────────────
        response_draft = ""
        try:
            canned         = await get_canned_responses()
            response_draft = await claude.draft_response_v2(
                review_text=review_text,
                l1=l1,
                l2=l2,
                resolution=rca_v2.get("resolution", ""),
                canned_responses=canned,
                review_id=review_id,
                guest_name=(booking or {}).get("guestName") or (review.author or ""),
                dss_rec=dss_rec,
            )
        except Exception as e:
            log.exception(f"Response draft failed: {e}")

        # ── 14. Save ──────────────────────────────────────────────────────────
        draft = db.query(RcaDraft).filter(RcaDraft.review_id == review_id).first()
        if not draft:
            draft = RcaDraft(id=f"draft_{review_id}", review_id=review_id)
            db.add(draft)

        _match = (booking or {}).get("_match", {})
        draft.booking          = {k: v for k, v in (booking or {}).items() if k != "_match"}
        draft.match_tier       = match_tier or _match.get("tier")
        draft.match_confidence = _match.get("confidence")
        draft.match_method     = _match.get("method")
        draft.candidates_list  = candidates
        draft.candidate_state  = candidate_state
        draft.confidence_trail = confidence_trail
        draft.timeline         = timeline
        draft.insights         = insights
        draft.similar_support  = similar_support
        draft.similar_reviews  = similar_reviews
        draft.dss_rec          = dss_rec

        draft.stated_issue                = stated_issue
        draft.l1                          = l1
        draft.l2                          = l2
        draft.l1_reasoning                = l1_reasoning
        draft.sub_theme                   = sub_theme
        draft.diagnostic_checks           = rca_v2.get("diagnosticChecks", [])
        draft.what_went_wrong_bullets     = rca_v2.get("whatWentWrongBullets", [])
        draft.support_interaction_frames  = rca_v2.get("supportInteractionFrames", [])
        draft.support_summary             = rca_v2.get("supportSummary", "")
        draft.sp_interaction_frames       = rca_v2.get("spInteractionFrames", [])
        draft.area_of_improving           = rca_v2.get("areaOfImproving", [])
        draft.actions_taken               = rca_v2.get("actionsTaken",
                                              {"sp":[],"customer":[],"business":[],"product":[],"ce":[]})
        draft.resolution                  = rca_v2.get("resolution", "")
        draft.suggested_response          = response_draft
        draft.generated_at                = datetime.utcnow()
        review.status                     = "draft"
        db.commit()

        # ── 15. Post-back to Slack thread ─────────────────────────────────────
        if is_live("slack_inbound") and review.slack_channel != "C_MANUAL":
            try:
                await slk.post_to_thread(
                    review.slack_channel,
                    review.slack_ts,
                    ":robot_face: RCA draft ready — open the dashboard to review and send.",
                    as_user=False,
                )
            except Exception as e:
                log.exception(f"Slack thread post failed: {e}")

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

    finally:
        db.close()
