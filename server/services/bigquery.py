"""
BigQuery service.

Booking match tiers:
  Tier 1: Booking ID found in review text or Zendesk — direct lookup
  Tier 2: Fuzzy guest name + recent date window — single match auto-confirmed
  Tier 3: Multiple candidates — dashboard shows them, associate picks

Insights:
  - Avg rating for TGID (last 90 days)          — from fct_reviews + fct_bookings
  - Avg rating for TID+VID (last 90 days)
  - Same-day same-VID fulfilment issues
  - Fulfilment type

Real table paths confirmed from the Retool workflow:
  headout-analytics.analytics_reporting.fct_bookings
  headout-analytics.analytics_reporting.fct_reviews
  headout-analytics.analytics_reporting.fct_fulfilments
  headout-analytics.analytics_reporting.dim_vendors
"""
import json, logging
from server.config import (
    is_live, GCP_SERVICE_ACCOUNT_JSON,
    BIGQUERY_BOOKINGS_TABLE, BIGQUERY_REVIEWS_TABLE, BIGQUERY_FULFILMENTS_TABLE,
)
from server.services.mock_data import MOCK_BOOKINGS, MOCK_INSIGHTS

log = logging.getLogger(__name__)

if is_live("bigquery"):
    from google.cloud import bigquery as _bqlib
    from google.oauth2 import service_account
    _creds = service_account.Credentials.from_service_account_info(
        json.loads(GCP_SERVICE_ACCOUNT_JSON))
    _bq = _bqlib.Client(credentials=_creds, project=_creds.project_id)
else:
    _bqlib = None
    _bq    = None


async def find_booking(review: dict) -> dict | None:
    if not is_live("bigquery"):
        return MOCK_BOOKINGS.get(review["id"])

    ref    = review.get("reference_number")
    author = (review.get("author") or "").strip()

    # ── Tier 1: direct booking ID lookup ─────────────────────────────────────
    if ref:
        sql = f"""
        SELECT
            b.booking_id,
            b.experience_name,
            b.experience_id   AS tgid,
            b.tour_id         AS tid,
            b.vendor_id       AS vid,
            v.vendor_name,
            DATE(b.created_at)       AS booked_on,
            DATE(b.experience_date)  AS visit_date,
            b.customer_name          AS guest_name,
            f.fulfilment_type
        FROM `{BIGQUERY_BOOKINGS_TABLE}` b
        LEFT JOIN `{BIGQUERY_FULFILMENTS_TABLE}` f ON b.booking_id = f.booking_id
        LEFT JOIN `headout-analytics.analytics_reporting.dim_vendors` v
               ON b.vendor_id = v.vendor_id
        WHERE b.booking_id = @ref
        LIMIT 1
        """
        rows = _run_query(sql, [_bqlib.ScalarQueryParameter("ref", "INT64", int(ref))])
        if rows:
            b = _row_to_dict(rows[0])
            b["_match"] = {"tier": 1, "confidence": "high",
                           "method": "Booking ID in review text"}
            return b

    # ── Tier 2: fuzzy guest name, recent bookings ─────────────────────────────
    if author and author.lower() != "unknown":
        sql = f"""
        SELECT
            b.booking_id,
            b.experience_name,
            b.experience_id   AS tgid,
            b.tour_id         AS tid,
            b.vendor_id       AS vid,
            v.vendor_name,
            DATE(b.created_at)       AS booked_on,
            DATE(b.experience_date)  AS visit_date,
            b.customer_name          AS guest_name,
            f.fulfilment_type
        FROM `{BIGQUERY_BOOKINGS_TABLE}` b
        LEFT JOIN `{BIGQUERY_FULFILMENTS_TABLE}` f ON b.booking_id = f.booking_id
        LEFT JOIN `headout-analytics.analytics_reporting.dim_vendors` v
               ON b.vendor_id = v.vendor_id
        WHERE LOWER(b.customer_name) LIKE LOWER(CONCAT('%', @name, '%'))
          AND DATE(b.created_at) >= DATE_SUB(CURRENT_DATE(), INTERVAL 60 DAY)
        ORDER BY b.created_at DESC
        LIMIT 5
        """
        rows = _run_query(sql, [_bqlib.ScalarQueryParameter("name", "STRING", author)])
        if len(rows) == 1:
            b = _row_to_dict(rows[0])
            b["_match"] = {"tier": 2, "confidence": "med",
                           "method": "Single name match (last 60 days)"}
            return b
        elif len(rows) > 1:
            return {
                "_match": {
                    "tier": 3, "confidence": "low",
                    "method": f"Name matched {len(rows)} bookings — associate must confirm",
                    "candidates": [_row_to_dict(r) for r in rows],
                }
            }

    return None


async def get_insights(booking: dict) -> dict:
    if not is_live("bigquery"):
        for rid, b in MOCK_BOOKINGS.items():
            if b.get("id") == booking.get("id"):
                return MOCK_INSIGHTS.get(rid, {})
        return {}

    tgid       = str(booking.get("tgid", "") or "")
    tid        = str(booking.get("tid",  "") or "")
    vid        = str(booking.get("vid",  "") or "")
    visit_date = str(booking.get("visitDate", "") or "")

    if not tgid and not tid:
        return {}

    result = {}
    try:
        # ── Avg ratings: TGID-level and TID+VID-level (last 90 days) ─────────
        # Matches the Retool query3 exactly
        rating_sql = f"""
        SELECT
            ROUND(AVG(IF(b.experience_id = @tgid, fr.rating, NULL)), 2) AS tgid_avg,
            ROUND(AVG(IF(b.tour_id = @tid AND b.vendor_id = @vid, fr.rating, NULL)), 2) AS tidvid_avg,
            COUNT(IF(b.experience_id = @tgid, 1, NULL)) AS tgid_count,
            COUNT(IF(b.tour_id = @tid AND b.vendor_id = @vid, 1, NULL)) AS tidvid_count
        FROM `{BIGQUERY_BOOKINGS_TABLE}` b
        JOIN `{BIGQUERY_REVIEWS_TABLE}` fr ON b.booking_id = fr.booking_id
        WHERE DATE(b.created_at) >= DATE_SUB(CURRENT_DATE(), INTERVAL 90 DAY)
          AND fr.rating IS NOT NULL
        """
        rrows = _run_query(rating_sql, [
            _bqlib.ScalarQueryParameter("tgid", "STRING", tgid),
            _bqlib.ScalarQueryParameter("tid",  "STRING", tid),
            _bqlib.ScalarQueryParameter("vid",  "STRING", vid),
        ])
        if rrows:
            r = rrows[0]
            tgid_str   = f"{r.tgid_avg:.2f} ({r.tgid_count} ratings)" if r.tgid_avg else "N/A"
            tidvid_str = f"{r.tidvid_avg:.2f} ({r.tidvid_count} ratings)" if r.tidvid_avg else "N/A"
            result["tgidRating"]   = tgid_str
            result["tidVidRating"] = tidvid_str

        # ── Same-day VID fulfilment issues ────────────────────────────────────
        if vid and visit_date:
            sameday_sql = f"""
            SELECT
                COUNTIF(f.fulfilment_type IN ('FAILED','UNFULFILLED','CANCELLED')) AS issues,
                COUNT(*) AS total
            FROM `{BIGQUERY_BOOKINGS_TABLE}` b
            LEFT JOIN `{BIGQUERY_FULFILMENTS_TABLE}` f ON b.booking_id = f.booking_id
            WHERE b.vendor_id = @vid
              AND DATE(b.experience_date) = @vd
            """
            srows = _run_query(sameday_sql, [
                _bqlib.ScalarQueryParameter("vid", "STRING", vid),
                _bqlib.ScalarQueryParameter("vd",  "DATE",   visit_date),
            ])
            if srows:
                s = srows[0]
                result["sameDaySameVidIssues"] = f"{s.issues} of {s.total}"

        # ── Fulfilment type from booking data ─────────────────────────────────
        result["fulfilmentType"] = booking.get("fulfilmentType", "")

    except Exception as e:
        log.exception(f"Insights query failed: {e}")

    return result


def _run_query(sql: str, params: list) -> list:
    try:
        cfg  = _bqlib.QueryJobConfig(query_parameters=params)
        rows = list(_bq.query(sql, job_config=cfg).result())
        return rows
    except Exception as e:
        log.exception(f"BigQuery query failed: {e}")
        return []


def _row_to_dict(row) -> dict:
    return {
        "id":             str(row.booking_id),
        "experienceName": getattr(row, "experience_name", "") or "",
        "tgid":           str(getattr(row, "tgid", "") or ""),
        "tid":            str(getattr(row, "tid",  "") or ""),
        "vid":            str(getattr(row, "vid",  "") or ""),
        "vidName":        "",
        "partner":        getattr(row, "vendor_name", "") or "",
        "guestName":      getattr(row, "guest_name", "") or "",
        "bookedOn":       str(getattr(row, "booked_on",  "") or ""),
        "visitDate":      str(getattr(row, "visit_date", "") or ""),
        "fulfilmentType": getattr(row, "fulfilment_type", "") or "",
    }
