"""
PATCH — additions to server/services/bigquery.py

Only NEW methods needed; existing find_booking + get_insights stay as-is.

Two things to add:
  1. Modify find_booking so it can return {"candidates": [...]} when Tier 2 confidence
     is medium (multiple close matches). Existing single-booking return still works.
  2. Add get_similar_complaints() — pulls similar tickets/reviews from BQ.

To apply: merge these into the existing bigquery.py — do not replace the whole file
since your existing find_booking already has the fuzzy match logic that just needs
one small change at the return step.
"""
import logging
from server.config import (
    is_live, BIGQUERY_BOOKINGS_TABLE, BIGQUERY_REVIEWS_TABLE, BIGQUERY_SUPPORT_TABLE,
)
from server.taxonomy import SIMILAR_MATCH_RULE

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────
# ADD: get_similar_complaints
# ─────────────────────────────────────────────────────────────────────────
async def get_similar_complaints(booking: dict) -> tuple[list, list]:
    """
    Returns (similar_support_tickets, similar_trustpilot_reviews).

    Matches on TID+VID per SIMILAR_MATCH_RULE. Optionally filters by L1 if provided.
    Each item shape:
      {"ref": "#ZD-1234" or "#TP-5678", "desc": "short description", "date": "DD MMM"}

    STAKEHOLDER INPUT NEEDED — CX Lead:
      Confirm the matching rule. Currently: same TID+VID, last 30 days, top 5.
      See SIMILAR_MATCH_RULE in server/taxonomy.py.
    """
    if not is_live("bigquery"):
        return _mock_similar(booking)

    tid = booking.get("tid")
    vid = booking.get("vid")
    if not tid or not vid:
        return [], []

    from server.services.bigquery import _bqlib as gcb  # same lib as the live client
    client = _get_client()

    days = SIMILAR_MATCH_RULE["window_days"]
    limit = SIMILAR_MATCH_RULE["max_results"]

    support_sql = f"""
        SELECT
          CONCAT('#ZD-', q.query_id) AS ref,
          COALESCE(q.query_tag, q.query_type, q.contact_type) AS `desc`,
          FORMAT_DATE('%d %b', DATE(q.query_created_at)) AS date
        FROM `{BIGQUERY_SUPPORT_TABLE}` q
        JOIN `{BIGQUERY_BOOKINGS_TABLE}` b
          ON SAFE_CAST(q.booking_id AS INT64) = b.booking_id
        WHERE q.tour_id = @tid
          AND b.vendor_id = @vid
          AND q.query_created_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {days} DAY)
        ORDER BY q.query_created_at DESC
        LIMIT {limit}
    """
    reviews_sql = f"""
        SELECT
          CONCAT('#TP-', r.review_id)     AS ref,
          CONCAT(LEFT(r.review, 60), '…') AS `desc`,
          FORMAT_DATE('%d %b', DATE(r.reviewed_at)) AS date
        FROM `{BIGQUERY_REVIEWS_TABLE}` r
        JOIN `{BIGQUERY_BOOKINGS_TABLE}` b ON r.booking_id = b.booking_id
        WHERE b.tour_id = @tid
          AND b.vendor_id = @vid
          AND r.reviewed_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {days} DAY)
          AND r.rating <= 2
        ORDER BY r.reviewed_at DESC
        LIMIT {limit}
    """

    params = [
        gcb.ScalarQueryParameter("tid", "STRING", tid),
        gcb.ScalarQueryParameter("vid", "STRING", vid),
    ]
    cfg = gcb.QueryJobConfig(query_parameters=params)

    support = []
    reviews = []
    try:
        for row in client.query(support_sql, job_config=cfg).result():
            support.append({"ref": row.ref, "desc": row.desc, "date": row.date})
    except Exception as e:
        log.exception(f"Similar support query failed: {e}")
    try:
        for row in client.query(reviews_sql, job_config=cfg).result():
            reviews.append({"ref": row.ref, "desc": row.desc, "date": row.date})
    except Exception as e:
        log.exception(f"Similar reviews query failed: {e}")

    return support, reviews


def _mock_similar(booking: dict) -> tuple[list, list]:
    """Mock-mode fallback. Returns placeholder rows so the UI doesn't render empty."""
    vid = booking.get("vid", "?")
    return (
        [
            {"ref": "#ZD-[placeholder]", "desc": f"Same VID {vid} — entry denied (mock)", "date": "01 May"},
            {"ref": "#ZD-[placeholder]", "desc": f"Same VID {vid} — SP failure (mock)",   "date": "28 Apr"},
        ],
        [
            {"ref": "#TP-[placeholder]", "desc": "Same VID — 1★ review (mock)", "date": "25 Apr"},
        ],
    )


def _get_client():
    """Lazy BigQuery client — reuses existing config."""
    from server.services.bigquery import _bq  # existing client instance
    return _bq


# ─────────────────────────────────────────────────────────────────────────
# PATCH: find_booking return shape
#
# Modify the existing find_booking() at the point where it decides between
# a single confident match and multiple candidates:
#
#   if len(candidates) > 1 and top_score < HIGH_CONFIDENCE_THRESHOLD:
#       return {"candidates": [{
#           "id":           c["id"],
#           "score":        c["score"],
#           "matchReasons": c["match_reasons"],
#           "experience":   c["experience_name"],
#           "tgid":         c["tgid"],
#           "tid":          c["tid"],
#           "vendorName":   c["vendor_name"],
#           "experienceDate": c["visit_date"],
#           "creationDate": c["booked_on"],
#           "status":       c["status"],
#           "leadTime":     c["lead_time"],
#       } for c in candidates[:5]]}
#
# STAKEHOLDER INPUT NEEDED — Data Team:
#   - HIGH_CONFIDENCE_THRESHOLD value (currently 0.85 as placeholder)
#   - Fuzzy match scoring formula weights per field (name / experience / date)
# ─────────────────────────────────────────────────────────────────────────
