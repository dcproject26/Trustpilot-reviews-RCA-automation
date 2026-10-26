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
    BMS_URL_PATTERN, TGID_URL_PATTERN,
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
# NEW: verify_bid — Tier 1 BID lookup, returns a booking dict or None
# ─────────────────────────────────────────────────────────────────────────

# Realtime `*_lambda` migration (verified against the live fct_bookings_lambda
# schema, not the migration guide's examples — which were wrong about what moved):
#   • experience_id / experience_name / vendor_id / vendor_name / is_partnered /
#     fulfilment type / fulfilment_status all live ON fct_bookings_lambda — no
#     orders join and no fulfilments join are needed; read them straight off b.
#   • experience_date -> experience_timestamp (combined date+time; DATE() recovers the day)
#   • fulfilment_type -> final_fulfilment_type (the realized fulfilment method)
#   • booking_status is SELECTed (not SQL-filtered) so verify_bid can tell a
#     Dummy/Uncaptured booking apart from a genuinely absent one (rule 1).
_VERIFY_BID_SQL = f"""
SELECT
  b.booking_id,
  -- The TIMESTAMP, not DATE(). b.created_at carries a time and DATE() threw it
  -- away at the source, so nothing downstream could show it: the booking log
  -- and the events timeline both rendered a bare date, and the formatter was
  -- blamed for truncating something it never received. The visit date stays a
  -- DATE because that is what it is — an experience is booked for a day.
  b.created_at            AS date_of_booking,
  DATE(b.experience_timestamp) AS date_of_visit,
  b.tour_id               AS tid,
  b.experience_id         AS tgid,
  b.experience_name,
  b.vendor_id,
  b.vendor_name,
  b.final_fulfilment_type AS fulfilment_type,
  b.primary_guest_name,
  b.booking_status
FROM `{BIGQUERY_BOOKINGS_TABLE}` b
WHERE b.booking_id = @bid
LIMIT 1
"""

def verify_bid(bid: str) -> dict | None:
    """Direct BID lookup in fct_bookings. Returns a booking dict or None.

    Raises on query failure so callers can distinguish "not found" from
    "could not look."  Every call site already wraps this in try/except.
    """
    from server.services import bq_connector as bqc
    rows = bqc.run_query(_VERIFY_BID_SQL, params={"bid": ("INT64", int(bid))})
    if not rows:
        return None
    r = rows[0]
    # The realtime fct_bookings_lambda now carries Dummy/Uncaptured rows the old
    # table did not. A BID resolving to one of these is NOT a real booking — say
    # so distinctly (rule 1: not the same as "no such BID") and treat as unmatched.
    _status = str(r.get("booking_status") or "")
    if _status in ("Dummy", "Uncaptured"):
        log.info(f"verify_bid({bid}): resolves to a {_status} booking — not a "
                 f"real booking, treating as unmatched")
        return None
    booking_id_str = str(r.get("booking_id") or bid)
    tgid_str       = str(r.get("tgid") or "")
    booking = {
        "id":              booking_id_str,
        "date_of_booking": str(r.get("date_of_booking") or ""),
        "date_of_visit":   str(r.get("date_of_visit") or ""),
        "tid":             str(r.get("tid") or ""),
        "tgid":            tgid_str,
        "experienceName":  str(r.get("experience_name") or ""),
        "vid":             str(r.get("vendor_id") or ""),
        "vendorName":      str(r.get("vendor_name") or ""),
        "fulfilmentType":  str(r.get("fulfilment_type") or ""),
        "primary_guest_name": str(r.get("primary_guest_name") or ""),
    }
    booking["bms_link"]  = BMS_URL_PATTERN.format(bid=booking_id_str)   if booking_id_str  else None
    booking["tgid_link"] = TGID_URL_PATTERN.format(tgid=tgid_str)       if tgid_str        else None
    # Enrich with booking_status and tid_name — isolated query, never breaks the main match
    from server.services.bigquery import _get_booking_extra
    booking.update(_get_booking_extra(booking_id_str))
    return booking


# ─────────────────────────────────────────────────────────────────────────
# NEW: run_narrowing_query — Tier 2 cascade, one attempt
# ─────────────────────────────────────────────────────────────────────────

_NARROWING_SQL_WITH_TGIDS = f"""
SELECT
  b.booking_id,
  b.primary_guest_name,
  DATE(b.experience_timestamp) AS date_of_visit,
  b.experience_name,
  b.tour_id               AS tid,
  b.experience_id         AS tgid,
  b.vendor_id,
  b.vendor_name
FROM `{BIGQUERY_BOOKINGS_TABLE}` b
WHERE CAST(b.experience_id AS STRING) IN UNNEST(@tgid_list)
  AND DATE(b.experience_timestamp) BETWEEN
      DATE_SUB(@review_pub_date, INTERVAL @date_window DAY) AND @review_pub_date
  AND b.booking_status NOT IN ('Dummy', 'Uncaptured')
  AND (
    (@author_first = '' AND @author_last = '')
    OR (@author_first != '' AND
        LOWER(b.primary_guest_name) LIKE CONCAT('%', LOWER(@author_first), '%'))
    OR (@author_last != '' AND
        LOWER(b.primary_guest_name) LIKE CONCAT('%', LOWER(@author_last), '%'))
  )
ORDER BY b.experience_timestamp DESC
LIMIT 10
"""

_NARROWING_SQL_NO_TGIDS = f"""
SELECT
  b.booking_id,
  b.primary_guest_name,
  DATE(b.experience_timestamp) AS date_of_visit,
  b.experience_name,
  b.tour_id               AS tid,
  b.experience_id         AS tgid,
  b.vendor_id,
  b.vendor_name
FROM `{BIGQUERY_BOOKINGS_TABLE}` b
WHERE DATE(b.experience_timestamp) BETWEEN
      DATE_SUB(@review_pub_date, INTERVAL @date_window DAY) AND @review_pub_date
  AND b.booking_status NOT IN ('Dummy', 'Uncaptured')
  AND (
    (@author_first = '' AND @author_last = '')
    OR (@author_first != '' AND
        LOWER(b.primary_guest_name) LIKE CONCAT('%', LOWER(@author_first), '%'))
    OR (@author_last != '' AND
        LOWER(b.primary_guest_name) LIKE CONCAT('%', LOWER(@author_last), '%'))
  )
ORDER BY b.experience_timestamp DESC
LIMIT 10
"""

def run_narrowing_query(
    tgid_list: list[int] | None,
    review_pub_date: str,
    date_window: int,
    author_first: str | None,
    author_last: str | None,
) -> list[dict]:
    """
    Run one narrowing attempt. Returns up to 10 booking dicts.
    Uses tgid UNNEST when tgid_list is non-empty, plain date+name otherwise.
    """
    from server.services import bq_connector as bqc

    params = {
        "review_pub_date": review_pub_date,
        "date_window":     ("INT64", date_window),
        "author_first":    author_first or "",
        "author_last":     author_last or "",
    }

    if tgid_list:
        # experience_id is compared as STRING (CAST in the SQL) so the match is
        # type-safe whatever fct_bookings_lambda stores it as — mirrors the
        # working find_via_support. Pass the tgids as strings to match.
        params["tgid_list"] = ("STRING", [str(t) for t in tgid_list])
        sql = _NARROWING_SQL_WITH_TGIDS
        # Null-name guard: if both are empty strings, force NULL semantics via SQL
        # (LIKE '%empty%' would still match — use a sentinel approach: pass as empty
        # and rely on the OR chain; empty LIKE '%' is a wildcard match which is fine)
    else:
        sql = _NARROWING_SQL_NO_TGIDS

    try:
        rows = bqc.run_query(sql, params=params)
    except Exception as e:
        log.warning(f"run_narrowing_query failed: {e}")
        return []

    result = []
    for r in rows:
        result.append({
            "id":               str(r.get("booking_id") or ""),
            "primary_guest_name": str(r.get("primary_guest_name") or ""),
            "date_of_visit":    str(r.get("date_of_visit") or ""),
            "experience_name":  str(r.get("experience_name") or ""),
            "tid":              str(r.get("tid") or ""),
            "tgid":             str(r.get("tgid") or ""),
            "vid":              str(r.get("vendor_id") or ""),
            "vendorName":       str(r.get("vendor_name") or ""),
        })
    return result


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
