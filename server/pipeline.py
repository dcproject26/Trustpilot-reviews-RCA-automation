"""
The pipeline. Called for every new review.
Each step is wrapped in try/except — one failure never kills the rest.
"""
import logging, re
from datetime import datetime
from server.config import is_live
from server.db import SessionLocal, Review, RcaDraft, ReviewMetric
from server.services import claude, bigquery as bq, zendesk, dss, slack as slk
from server.services.canned import get_canned_responses

log = logging.getLogger(__name__)


async def process_review(review_id: str):
    db = SessionLocal()
    started = datetime.utcnow()
    try:
        review = db.query(Review).filter(Review.id == review_id).first()
        if not review:
            log.error(f"Review {review_id} not found")
            return

        log.info(f"[pipeline] Processing {review_id}")

        # ── 1. Translate ──────────────────────────────────────────────────────
        if review.language and review.language != "en" and not review.body_english:
            try:
                review.body_english = await claude.translate(
                    review.body_original, review.language, review_id)
                db.commit()
            except Exception as e:
                log.exception(f"Translation failed: {e}")

        review_text = review.body_english or review.body_original

        # ── 2. Booking match ──────────────────────────────────────────────────
        # Tier 1: booking ID regex in review text (any 8-digit number)
        booking     = None
        match_tier  = None
        try:
            ref_match = re.search(r'\b\d{8}\b', review_text or "")
            if ref_match and not review.reference_number:
                review.reference_number = ref_match.group(0)
                db.commit()

            booking = await bq.find_booking({
                "id":               review_id,
                "author":           review.author or "",
                "reference_number": review.reference_number,
            })
            if booking:
                match_tier = booking.get("_match", {}).get("tier")
        except Exception as e:
            log.exception(f"Booking match failed: {e}")

        # ── 3. Zendesk timeline ───────────────────────────────────────────────
        # Fetch even without a confident booking match — we use what we find.
        # extracted_bk contains booking fields parsed from the first ZD comment.
        timeline      = []
        extracted_bk  = {}
        booking_id_for_zd = (booking or {}).get("id") or review.reference_number
        if booking_id_for_zd:
            try:
                timeline, extracted_bk = await zendesk.get_timeline(
                    booking_id_for_zd, review_id)
                log.info(f"[pipeline] timeline: {len(timeline)} events, "
                         f"extracted: {list(extracted_bk.keys())}")
            except Exception as e:
                log.exception(f"Zendesk failed: {e}")

        # Merge extracted booking fields as fallback for missing BigQuery fields
        if extracted_bk and booking:
            for key in ["tgid", "tid", "experienceName", "visitDate", "vid", "vendorName", "pax"]:
                if not booking.get(key) and extracted_bk.get(key):
                    booking[key] = extracted_bk[key]
        elif extracted_bk and not booking:
            # No BigQuery match but we have something from ZD — use it
            booking = {
                "id":             review.reference_number or "unknown",
                "experienceName": extracted_bk.get("experienceName", ""),
                "tgid":           extracted_bk.get("tgid", ""),
                "tid":            extracted_bk.get("tid", ""),
                "vid":            extracted_bk.get("vid", ""),
                "vidName":        extracted_bk.get("vendorName", ""),
                "partner":        extracted_bk.get("vendorName", ""),
                "visitDate":      extracted_bk.get("visitDate", ""),
                "pax":            extracted_bk.get("pax", ""),
                "_match":         {"tier": None, "confidence": "low",
                                   "method": "Extracted from Zendesk comment"},
            }
            match_tier = None

        # ── 4. Insights ───────────────────────────────────────────────────────
        insights = {}
        if booking and booking.get("tgid"):
            try:
                insights = await bq.get_insights(booking)
            except Exception as e:
                log.exception(f"Insights failed: {e}")

        # ── 5. DSS ────────────────────────────────────────────────────────────
        dss_rec = {}
        try:
            dss_rec = await dss.get_recommendation(booking or {}, review_id)
        except Exception as e:
            log.exception(f"DSS failed: {e}")

        # ── 6. RCA generation ─────────────────────────────────────────────────
        rca_fields = {}
        signals    = []
        try:
            result  = await claude.generate_rca(
                review_text, booking, timeline, insights, dss_rec, review_id)
            signals    = result.pop("signals", [])
            rca_fields = result
        except Exception as e:
            log.exception(f"RCA generation failed: {e}")

        # ── 7. Response draft ─────────────────────────────────────────────────
        response_draft = ""
        try:
            canned         = await get_canned_responses()
            response_draft = await claude.draft_response(
                review_text,
                rca_fields.get("queryIssueType", ""),
                rca_fields.get("solutionOffered", ""),
                canned,
                review_id,
                guest_name=(booking or {}).get("guestName") or (review.author or ""),
                dss_rec=dss_rec,
            )
        except Exception as e:
            log.exception(f"Response draft failed: {e}")

        # ── 8. Save draft ──────────────────────────────────────────────────────
        draft = db.query(RcaDraft).filter(RcaDraft.review_id == review_id).first()
        if not draft:
            draft = RcaDraft(id=f"draft_{review_id}", review_id=review_id)
            db.add(draft)

        _match = (booking or {}).get("_match", {})
        draft.booking            = {k: v for k, v in (booking or {}).items() if k != "_match"}
        draft.match_tier         = _match.get("tier")
        draft.match_confidence   = _match.get("confidence")
        draft.match_method       = _match.get("method")
        draft.timeline           = timeline
        draft.insights           = insights
        draft.dss_rec            = dss_rec
        draft.rca_fields         = rca_fields
        draft.signals            = signals
        draft.suggested_response = response_draft
        draft.generated_at       = datetime.utcnow()
        review.status            = "draft"
        db.commit()

        # ── 9. Post RCA draft to the Slack thread — team opens dashboard directly to action.
        if is_live("slack_inbound") and review.slack_channel != "C_MANUAL":
            try:
                dashboard_url    = f"https://{_replit_host()}/review/{review_id}"
                match_label      = _match.get("confidence", "?")
                match_method_str = _match.get("method", "?")
                notify_text = (
                    f":robot_face: RCA draft ready — "
                    f"*{match_label} confidence* via {match_method_str}\n"
                    f"<{dashboard_url}|Open in dashboard>"
                )
                await slk.post_to_thread(
                    review.slack_channel, review.slack_ts, notify_text, as_user=False)
            except Exception as e:
                log.exception(f"Slack thread post failed: {e}")

        # ── 10. Log metrics (no PII) ───────────────────────────────────────────
        try:
            db.add(ReviewMetric(
                review_id        = review_id,
                received_at      = review.received_at,
                channel          = review.slack_channel,
                rating           = review.rating,
                language         = review.language,
                match_tier       = draft.match_tier,
                match_confidence = draft.match_confidence,
                auto_matched     = draft.match_tier in (1, 2),
                signals          = signals,
                edit_count       = 0,
                minutes_to_send  = None,
                sent             = False,
            ))
            db.commit()
        except Exception as e:
            log.exception(f"Metrics write failed: {e}")

    finally:
        db.close()


def _replit_host() -> str:
    import os
    slug  = os.getenv("REPL_SLUG", "your-repl")
    owner = os.getenv("REPL_OWNER", "your-username")
    return f"{slug}.{owner}.repl.co"
