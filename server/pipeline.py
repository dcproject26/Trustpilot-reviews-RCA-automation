"""
The pipeline. Called for every new review.
Each step is in try/except — one failure doesn't kill the rest.
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

        log.info(f"Processing {review_id}")

        # 1. Translate
        if review.language and review.language != "en" and not review.body_english:
            try:
                review.body_english = await claude.translate(
                    review.body_original, review.language, review_id)
                db.commit()
            except Exception as e:
                log.exception(f"Translation failed: {e}")

        review_text = review.body_english or review.body_original

        # 2. Booking match
        booking = None
        match_tier = None
        try:
            # Tier 1: regex for 8-digit ID in review text
            ref_match = re.search(r'\b\d{8}\b', review_text or "")
            if ref_match:
                review.reference_number = ref_match.group(0)
                db.commit()
            booking = await bq.find_booking({
                "id": review_id,
                "author": "",  # author not stored — parsed from Slack message separately
                "reference_number": review.reference_number,
            })
            if booking:
                match_tier = booking.get("_match", {}).get("tier")
        except Exception as e:
            log.exception(f"Booking match failed: {e}")

        # 3. Zendesk timeline (only if booking found with confidence)
        timeline = []
        if booking and match_tier in (1, 2):
            try:
                timeline = await zendesk.get_timeline(booking.get("id"), review_id)
            except Exception as e:
                log.exception(f"Zendesk failed: {e}")

        # 4. Insights
        insights = {}
        if booking and match_tier in (1, 2):
            try:
                insights = await bq.get_insights(booking)
            except Exception as e:
                log.exception(f"Insights failed: {e}")

        # 5. DSS
        dss_rec = {}
        try:
            dss_rec = await dss.get_recommendation(booking or {}, review_id)
        except Exception as e:
            log.exception(f"DSS failed: {e}")

        # 6. RCA generation
        rca_fields = {}
        signals = []
        try:
            result = await claude.generate_rca(
                review_text, booking, timeline, insights, dss_rec, review_id)
            signals = result.pop("signals", [])
            rca_fields = result
        except Exception as e:
            log.exception(f"RCA generation failed: {e}")

        # 7. Response draft
        response_draft = ""
        try:
            canned = await get_canned_responses()
            response_draft = await claude.draft_response(
                review_text,
                rca_fields.get("queryIssueType", ""),
                rca_fields.get("solutionOffered", ""),
                canned,
                review_id,
            )
        except Exception as e:
            log.exception(f"Response draft failed: {e}")

        # 8. Save draft
        draft = db.query(RcaDraft).filter(RcaDraft.review_id == review_id).first()
        if not draft:
            draft = RcaDraft(id=f"draft_{review_id}", review_id=review_id)
            db.add(draft)

        _match = (booking or {}).get("_match", {})
        draft.booking           = {k: v for k, v in (booking or {}).items() if k != "_match"}
        draft.match_tier        = _match.get("tier")
        draft.match_confidence  = _match.get("confidence")
        draft.match_method      = _match.get("method")
        draft.timeline          = timeline
        draft.insights          = insights
        draft.dss_rec           = dss_rec
        draft.rca_fields        = rca_fields
        draft.signals           = signals
        draft.suggested_response = response_draft
        draft.generated_at      = datetime.utcnow()
        review.status = "draft"
        db.commit()

        # 9. Notify associate in Slack (optional — only if Slack is configured)
        if is_live("slack_inbound") and review.slack_channel != "C_MANUAL":
            dashboard_url = f"https://{_replit_host()}/review/{review_id}"
            match_confidence = _match.get('confidence','?')
            match_method = _match.get('method','?')
            notify_text = (
                f":robot_face: RCA draft ready — "
                f"*{match_confidence} confidence* via {match_method}\n"
                f"<{dashboard_url}|Open in dashboard>"
            )
            await slk.post_to_thread(
                review.slack_channel, review.slack_ts, notify_text, as_user=False)

        # 10. Log metrics (no PII)
        try:
            minutes = (datetime.utcnow() - started).total_seconds() / 60
            db.add(ReviewMetric(
                review_id       = review_id,
                received_at     = review.received_at,
                channel         = review.slack_channel,
                rating          = review.rating,
                language        = review.language,
                match_tier      = draft.match_tier,
                match_confidence = draft.match_confidence,
                auto_matched    = draft.match_tier in (1, 2),
                signals         = signals,
                edit_count      = 0,
                minutes_to_send = None,
                sent            = False,
            ))
            db.commit()
        except Exception as e:
            log.exception(f"Metrics write failed: {e}")

    finally:
        db.close()


def _replit_host() -> str:
    """Return the Replit public URL hostname."""
    import os
    slug = os.getenv("REPL_SLUG", "your-repl")
    owner = os.getenv("REPL_OWNER", "your-username")
    return f"{slug}.{owner}.repl.co"
