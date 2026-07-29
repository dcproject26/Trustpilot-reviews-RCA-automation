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
    if GCP_SERVICE_ACCOUNT_JSON:
        from google.cloud import bigquery as _bqlib
        from google.oauth2 import service_account
        _creds = service_account.Credentials.from_service_account_info(
            json.loads(GCP_SERVICE_ACCOUNT_JSON))
        _bq = _bqlib.Client(credentials=_creds, project=_creds.project_id)
    else:
        # Replit BigQuery integration — auth via connectors proxy, no key.
        from server.services import bq_connector as _bqlib
        _bq = _bqlib.Client()
else:
    _bqlib = None
    _bq    = None


async def find_booking(review: dict) -> dict | None:
    if not is_live("bigquery"):
        result = MOCK_BOOKINGS.get(review["id"])
        if result is not None:
            return result
        # Mock synthesis: activates in MOCK_MODE for review IDs not in fixtures.
        # Enables manual testing without real service calls.
        bid = review.get("reference_number")
        if bid:
            from server.config import BMS_URL_PATTERN, TGID_URL_PATTERN
            return {
                "id":                 bid,
                "experienceName":     "Mock Experience [manual test]",
                "tgid":               99999,
                "tid":                88888,
                "vid":                77777,
                "vendorName":         "MockVendor",
                "fulfilmentType":     "MANUAL",
                "visitDate":          "2026-01-15",
                "bookedOn":           "2026-01-10",
                "guestName":          review.get("author") or "Manual Test User",
                "bms_link":           BMS_URL_PATTERN.format(bid=bid),
                "tgid_link":          TGID_URL_PATTERN.format(tgid=99999),
                "_match":             {"tier": 1, "confidence": "high", "method": "mock_synthesis"},
            }
        return None

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
            b.primary_guest_name     AS guest_name,
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
            b.update(_get_booking_extra(str(rows[0].booking_id)))
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
            b.primary_guest_name     AS guest_name,
            f.fulfilment_type
        FROM `{BIGQUERY_BOOKINGS_TABLE}` b
        LEFT JOIN `{BIGQUERY_FULFILMENTS_TABLE}` f ON b.booking_id = f.booking_id
        LEFT JOIN `headout-analytics.analytics_reporting.dim_vendors` v
               ON b.vendor_id = v.vendor_id
        WHERE LOWER(b.primary_guest_name) LIKE LOWER(CONCAT('%', @name, '%'))
        ORDER BY b.created_at DESC
        LIMIT 5
        """
        rows = _run_query(sql, [_bqlib.ScalarQueryParameter("name", "STRING", author)])
        if len(rows) == 1:
            b = _row_to_dict(rows[0])
            b.update(_get_booking_extra(str(rows[0].booking_id)))
            b["_match"] = {"tier": 2, "confidence": "med",
                           "method": "Single name match"}
            return b
        elif len(rows) > 1:
            candidates = []
            for r in rows[:5]:
                d = _row_to_dict(r)
                candidates.append({
                    "id":             d["id"],
                    "score":          None,   # TODO: scoring formula (Data Team, Sprint 2)
                    "matchReasons":   ["Guest name match"],
                    "experience":     d["experienceName"],
                    "tgid":           d["tgid"],
                    "tid":            d["tid"],
                    "vendorName":     d["partner"],
                    "experienceDate": d["visitDate"],
                    "creationDate":   d["bookedOn"],
                    "status":         "",     # not in current query
                    "leadTime":       "",     # not in current query
                    "_match": {"tier": 2, "confidence": "med",
                               "method": f"Name matched {len(rows)} bookings — associate must confirm"},
                    **d,
                })
            return {"candidates": candidates}

    return None


async def get_similar_complaints(booking: dict) -> tuple[list, list]:
    """Returns (similar_support_tickets, similar_trustpilot_reviews).

    Delegates to the bigquery_patch implementation, which handles the live
    BigQuery queries and the MOCK_MODE placeholder fallback.
    """
    from server.services.bigquery_patch import get_similar_complaints as _impl
    return await _impl(booking)




async def get_l1_l2_by_bid(bid) -> dict:
    """Warehouse L1/L2 tag for a booking, from fct_reviews.issues.

    Uses the UNNEST(issues) + UNNEST(l2_issues) pattern from the ORM VS
    pipeline SQL. Returns {"l1": <str or None>, "l2": <str or None>}.
    """
    empty = {"l1": None, "l2": None}
    if not is_live("bigquery"):
        return empty
    try:
        bid_int = int(str(bid).strip())
    except (TypeError, ValueError):
        return empty
    sql = f"""
    SELECT i.l1_issue AS l1, l2 AS l2
    FROM `{BIGQUERY_REVIEWS_TABLE}` r,
         UNNEST(r.issues) AS i,
         UNNEST(i.l2_issues) AS l2
    WHERE r.booking_id = @bid
    ORDER BY r.reviewed_at DESC
    LIMIT 1
    """
    rows = _run_query(sql, [_bqlib.ScalarQueryParameter("bid", "INT64", bid_int)])
    if rows:
        return {"l1": rows[0].l1, "l2": rows[0].l2}
    return empty


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


def _get_booking_extra(bid: str) -> dict:
    """Fetch booking_status and tour_name for a matched booking.

    Runs as a separate query so that any schema mismatch never breaks the
    primary booking-match query. Returns {} silently on any failure.
    """
    if not _bq or not bid:
        return {}
    try:
        bid_int = int(bid)
    except (TypeError, ValueError):
        return {}
    try:
        # is_partnered comes off fct_fulfilments, not fct_bookings, because the
        # question is about the vendor who FULFILLED this booking. Both tables
        # carry the column and they are not interchangeable - on booking
        # 32908218 fct_bookings.vendor_id is 4040 while every booking on that
        # experience fulfilled through vendor 3753, so reading the booking row
        # would answer about a different vendor than the one that delivered.
        #
        # The escalation contact comes off dim_vendors.contact_details, a
        # repeated STRUCT<name, type, email, phone>. Being nested is why two
        # INFORMATION_SCHEMA.COLUMNS sweeps could not find it - that view lists
        # top-level columns only.
        #
        # Joined on f.vendor_id, the vendor that FULFILLED, for the same reason
        # is_partnered is: the two vendor columns disagree on real bookings.
        #
        # contact_types comes back alongside so the panel can show what kinds
        # of contact a vendor actually has. The exact spelling of the
        # escalation type is not documented anywhere, so the match is a
        # substring on 'escalat' and the type list is the evidence for whether
        # that match is finding the right rows or missing them silently.
        sql = f"""
        SELECT
            b.booking_status,
            b.tour_name,
            f.is_partnered,
            (SELECT c.email
               FROM UNNEST(v.contact_details) c
              WHERE REGEXP_CONTAINS(LOWER(IFNULL(c.type, '')), r'escalat')
                AND IFNULL(c.email, '') != ''
              LIMIT 1)                                   AS escalation_email,
            (SELECT STRING_AGG(DISTINCT IFNULL(c.type, '(untyped)'), ', ')
               FROM UNNEST(v.contact_details) c)         AS contact_types,
            ARRAY_LENGTH(v.contact_details)              AS contact_count
        FROM `{BIGQUERY_BOOKINGS_TABLE}` b
        LEFT JOIN `{BIGQUERY_FULFILMENTS_TABLE}` f ON b.booking_id = f.booking_id
        LEFT JOIN `headout-analytics.analytics_reporting.dim_vendors` v
               ON v.vendor_id = f.vendor_id
        WHERE b.booking_id = @bid
        LIMIT 1
        """
        rows = _run_query(sql, [_bqlib.ScalarQueryParameter("bid", "INT64", bid_int)])
        if rows:
            # Three states, and the join is LEFT: True, False, and "no
            # fulfilment row so nobody has said". None is not False here -
            # rendering an unknown as "Not partnered" would be a claim about
            # the vendor that no row supports.
            _partnered = getattr(rows[0], "is_partnered", None)
            # Same three states as isPartnered, and the distinction matters
            # more here: "this vendor has contacts but none of them is an
            # escalation contact" is a gap somebody can close, while "we know
            # nothing about this vendor" is not. contact_count tells them
            # apart, so the panel never renders one as the other.
            # NULL when the vendor join found nothing - ARRAY_LENGTH of a NULL
            # array is NULL, not 0. Mapping that to 0 would say "this vendor
            # has no contacts on file", which is a claim about the vendor
            # rather than an admission that we did not find the vendor.
            _n_contacts = getattr(rows[0], "contact_count", None)
            return {
                "booking_status":   getattr(rows[0], "booking_status", None) or "",
                "tid_name":         getattr(rows[0], "tour_name",      None) or "",
                "isPartnered":      None if _partnered is None else bool(_partnered),
                "escalationEmail":  getattr(rows[0], "escalation_email", None) or "",
                "contactTypes":     getattr(rows[0], "contact_types", None) or "",
                "contactCount":     None if _n_contacts is None else int(_n_contacts),
            }
    except Exception:
        pass
    return {}
