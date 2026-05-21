"""
Booking match — 3 tiers:
  Tier 1: Booking ID in review text (done before this is called, in the pipeline)
  Tier 2: Fuzzy name + experience + date window
  Tier 3: Multiple candidates → associate picks

Falls back to mock data when BigQuery credentials aren't set.
"""
import json, logging
from server.config import is_live, GCP_SERVICE_ACCOUNT_JSON, BIGQUERY_BOOKINGS_TABLE
from server.services.mock_data import MOCK_BOOKINGS, MOCK_INSIGHTS

log = logging.getLogger(__name__)

if is_live("bigquery"):
    from google.cloud import bigquery
    from google.oauth2 import service_account
    _creds = service_account.Credentials.from_service_account_info(
        json.loads(GCP_SERVICE_ACCOUNT_JSON))
    _bq = bigquery.Client(credentials=_creds, project=_creds.project_id)
else:
    _bq = None


async def find_booking(review: dict) -> dict | None:
    """
    Returns booking dict with _match metadata, or None.
    Tier 1 (ID regex) is handled upstream in the pipeline — this handles 2 & 3.
    """
    if not is_live("bigquery"):
        return MOCK_BOOKINGS.get(review["id"])

    ref = review.get("reference_number")
    if ref:
        # Tier 1 via API if ID was extracted from review text
        sql = f"""
        SELECT booking_id, experience_name, tgid, tid, vid, vid_name,
               booked_on, visit_date, pax, amount_eur, status, partner_name
        FROM `{BIGQUERY_BOOKINGS_TABLE}`
        WHERE CAST(booking_id AS STRING) = @ref LIMIT 1
        """
        cfg = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("ref", "STRING", str(ref))])
        rows = list(_bq.query(sql, job_config=cfg).result())
        if rows:
            b = _row_to_dict(rows[0])
            b["_match"] = {"tier": 1, "confidence": "high", "method": "Booking ID in review text"}
            return b

    # Tier 2 — fuzzy name + date
    author = review.get("author", "")
    if author:
        sql = f"""
        SELECT booking_id, experience_name, tgid, tid, vid, vid_name,
               booked_on, visit_date, pax, amount_eur, status, partner_name, guest_name
        FROM `{BIGQUERY_BOOKINGS_TABLE}`
        WHERE LOWER(guest_name) LIKE LOWER(CONCAT('%', @name, '%'))
          AND booked_on >= DATE_SUB(CURRENT_DATE(), INTERVAL 60 DAY)
        ORDER BY booked_on DESC LIMIT 5
        """
        cfg = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("name", "STRING", author)])
        rows = list(_bq.query(sql, job_config=cfg).result())
        if len(rows) == 1:
            b = _row_to_dict(rows[0])
            b["_match"] = {"tier": 2, "confidence": "med", "method": "Single name match last 60d"}
            return b
        elif len(rows) > 1:
            # Tier 3 — multiple candidates
            return {"_match": {"tier": 3, "confidence": "low",
                                "method": f"Name matched {len(rows)} bookings",
                                "candidates": [_row_to_dict(r) for r in rows]}}
    return None


async def get_insights(booking: dict) -> dict:
    if not is_live("bigquery"):
        for rid, b in MOCK_BOOKINGS.items():
            if b.get("id") == booking.get("id"):
                return MOCK_INSIGHTS.get(rid, {})
        return {}

    vid = booking.get("vid", "")
    tgid = booking.get("tgid", "")
    tid = booking.get("tid", "")
    visit_date = booking.get("visitDate", "")

    try:
        rating_sql = f"""
        SELECT AVG(IF(tgid=@tgid, rating, NULL)) tgid_r,
               AVG(IF(tid=@tid AND vid=@vid, rating, NULL)) tidvid_r
        FROM `{BIGQUERY_BOOKINGS_TABLE.replace('bookings','reviews')}`
        WHERE created_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 28 DAY)
        """
        cfg = bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("tgid", "STRING", tgid),
            bigquery.ScalarQueryParameter("tid",  "STRING", tid),
            bigquery.ScalarQueryParameter("vid",  "STRING", vid),
        ])
        rr = next(iter(_bq.query(rating_sql, job_config=cfg).result()), None)

        sameday_sql = f"""
        SELECT COUNTIF(status IN ('Failed','Unfulfilled','Cancelled')) issues, COUNT(*) total
        FROM `{BIGQUERY_BOOKINGS_TABLE}`
        WHERE vid=@vid AND visit_date=@vd
        """
        cfg2 = bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("vid", "STRING", vid),
            bigquery.ScalarQueryParameter("vd",  "DATE", visit_date),
        ])
        sr = next(iter(_bq.query(sameday_sql, job_config=cfg2).result()), None)

        return {
            "tgidRating":           f"{rr.tgid_r:.2f}" if rr and rr.tgid_r else "N/A",
            "tidVidRating":         f"{rr.tidvid_r:.2f}" if rr and rr.tidvid_r else "N/A",
            "sameDaySameVidIssues":  f"{sr.issues} of {sr.total}" if sr else "N/A",
            "similarOpenTickets":   "TBD",
        }
    except Exception as e:
        log.exception(f"Insights query failed: {e}")
        return {}


def _row_to_dict(row) -> dict:
    return {
        "id":             str(row.booking_id),
        "experienceName": row.experience_name,
        "tgid": str(row.tgid), "tid": str(row.tid), "vid": str(row.vid),
        "vidName":   getattr(row, "vid_name", ""),
        "bookedOn":  str(row.booked_on),
        "visitDate": str(row.visit_date),
        "pax":    row.pax,
        "amount": f"€{row.amount_eur:.2f}" if row.amount_eur else "—",
        "status": row.status,
        "partner": row.partner_name,
    }
