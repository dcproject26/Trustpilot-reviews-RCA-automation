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
import asyncio
import json, logging
import re as _re_mod
from server.config import (
    is_live, GCP_SERVICE_ACCOUNT_JSON,
    BIGQUERY_BOOKINGS_TABLE, BIGQUERY_REVIEWS_TABLE, BIGQUERY_FULFILMENTS_TABLE,
    BIGQUERY_SUPPORT_TABLE,
)
from server.services.mock_data import MOCK_BOOKINGS, MOCK_INSIGHTS

log = logging.getLogger(__name__)

# Which contact_details.type is the booking escalation contact.
#
# Confirmed against the live vocabulary - eight types, counted over
# dim_vendors:
#
#   BOOKING_INTIMATION  5712 rows / 4278 vendors
#   BUSINESS            5373 / 4725
#   FINANCE             5357 / 5011
#   ESCALATIONS         5082 / 4525
#   PARTNER_EXPERIENCE  3141 / 2893
#   BOOKING_REQUEST     1571 / 1308
#   GENERIC_SUPPORT      125 / 123
#   INTEGRATIONS         115 / 91
#
# There is no BOOKING_ESCALATION. ESCALATIONS is the escalation contact;
# BOOKING_INTIMATION and BOOKING_REQUEST are where booking notifications and
# booking requests go, which is a different conversation. Anchored rather than
# left as a substring so a future FINANCE_ESCALATION cannot quietly become the
# address an associate emails about a guest.
_ESCALATION_TYPE_RE = "^escalations$"

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


# How far back the support-anchored search looks. Guests review months after
# a visit, so this is deliberately generous — but it is bounded, because the
# alternative is scanning every support contact ever recorded on every run.
SUPPORT_LOOKBACK_DAYS = 540


def _iso_dates(raw) -> list:
    """The dates a review named, as YYYY-MM-DD, discarding anything else.

    The model is asked for ISO and usually obliges, but it also returns
    "20.06.2026", "June 20" and "2026-06-20 (the voucher date)". Those reach
    BigQuery as DATE(@d) inside the query, where one bad value raises and takes
    the whole search down — so anything that is not a clean date is dropped
    here rather than trusted.
    """
    import re as _re
    out = []
    for d in (raw or []):
        m = _re.search(r"(\d{4})-(\d{2})-(\d{2})", str(d))
        if not m:
            continue
        y, mo, dy = (int(x) for x in m.groups())
        if not (1 <= mo <= 12 and 1 <= dy <= 31):
            continue
        iso = f"{y:04d}-{mo:02d}-{dy:02d}"
        if iso not in out:
            out.append(iso)
    return out[:6]


def is_hashed_name(s: str) -> bool:
    """True for a base64/hex PII hash rather than a human name.

    fct_bookings.primary_guest_name is hashed for a large share of rows, so
    any name comparison against it is noise on those. 'ab24TSVenneb4T3CkHFUFaGM'
    is a hash that contains the letters 'SVen', and a substring search for a
    guest called Sven matched it - a Barcelona walking tour returned as the
    match for a review about a musical.
    """
    s = (s or "").strip()
    if not s or " " in s:
        return False
    return len(s) >= 16 and bool(_re_mod.fullmatch(r"[A-Za-z0-9+/=_\-]+", s))


# SQL for the same test, so hashed rows never even reach the name comparison.
NOT_A_HASH_SQL = """NOT (NOT REGEXP_CONTAINS(b.primary_guest_name, r'\\s')
                         AND LENGTH(b.primary_guest_name) >= 16
                         AND REGEXP_CONTAINS(b.primary_guest_name,
                                             r'^[A-Za-z0-9+/=_-]+$'))"""


def _search_name(raw: str) -> str:
    """The part of a display name worth matching a booking against.

    Trustpilot names are not booking names. "Frau Nicole" is a title and a
    first name; "Sven B." abbreviates the surname. A LIKE on the whole string
    finds nothing in either case, so the title comes off and — when a surname
    survives — that alone is used, since it is both the most discriminating
    token and the one most likely to be spelled identically on the booking.
    """
    try:
        from server.prompts import strip_honorifics
        cleaned = strip_honorifics(raw)
    except Exception:
        cleaned = str(raw or "")
    # The name is interpolated into a regex in the SQL, so anything with a
    # meaning there is stripped - a display name like "Ann (Annie)" would
    # otherwise break the query rather than simply not match. Characters are
    # removed rather than whitelisted, so non-Latin names survive intact.
    parts = [_re_mod.sub(r"[()\[\]{}.*+?^$|\\/]", "", p).strip(".,'-")
             for p in cleaned.split()]
    parts = [p for p in parts if p and not p.isdigit()]
    if not parts:
        return ""
    if len(parts) >= 2 and len(parts[-1]) >= 3:
        return parts[-1]        # surname
    return parts[0]


def _day_month_match(col: str) -> str:
    """SQL: does this booking date fall on a day/month the review named?

    The review's day and month are rebuilt inside the BOOKING's year and then
    compared, so "20/10" matches a 20 October booking whichever year it is in,
    while a year more than one out is still rejected. SAFE.DATE because 29
    February does not exist in every year and a raw DATE() would raise.
    """
    return f"""EXISTS (
                SELECT 1 FROM UNNEST(@dates) d
                WHERE ABS(DATE_DIFF(
                        DATE({col}),
                        SAFE.DATE(EXTRACT(YEAR  FROM DATE({col})),
                                  EXTRACT(MONTH FROM DATE(d)),
                                  EXTRACT(DAY   FROM DATE(d))),
                        DAY)) <= 1
                  AND ABS(EXTRACT(YEAR FROM DATE({col}))
                          - EXTRACT(YEAR FROM DATE(d))) <= 1)"""


def support_search_sql(indicators: dict, tgids: list | None = None,
                       limit: int = 8):
    """Build the support-anchored query. Returns (sql, params) or (None, None).

    Separated from the call so tools/check_support_search.py can dry-run the
    exact query this service issues, rather than a hand-copied lookalike that
    drifts.
    """
    dates = _iso_dates(indicators.get("dates_mentioned"))
    tgids = [str(t).strip() for t in (tgids or []) if str(t).strip()]

    # The guest name is not usable here and never was. Measured over every
    # booking behind a support contact in the window: 639,109 rows, 639,109 of
    # them carrying a PII hash in primary_guest_name, none carrying a name.
    # A name filter against that column cannot match - it can only ever
    # exclude everything - so this path is anchored on the two facts BigQuery
    # really holds: which experience was booked, and when.
    #
    # Both are required. Experiences the review points at, with a booking on a
    # date it named, whose guest then contacted support, is a small and honest
    # set. Either one alone is not.
    if not (dates and tgids):
        return None, None

    # In MOCK_MODE _bqlib is None, but the parameter classes are plain data
    # holders that need no credentials — so the query can still be built and
    # printed for review by tools/check_support_search.py.
    _P = _bqlib
    if _P is None:
        from server.services import bq_connector as _P

    where, params = ["sq.booking_id IS NOT NULL"], []
    if tgids:
        # Resolved TGIDs, not a LIKE on the experience name. The pipeline has
        # already turned whatever the guest called the place into real ids, and
        # guests name product types - "musical ticket", "skip-the-line entry" -
        # which match no experience_name at all.
        where.append("CAST(b.experience_id AS STRING) IN UNNEST(@tgids)")
        params.append(_P.ArrayQueryParameter("tgids", "STRING", tgids))
    if dates:
        # A date the review named may be the visit date OR the date the guest
        # meant to book - both are on the booking, so both are checked, with a
        # day of slack for timezone rounding.
        #
        # The year is matched loosely on purpose. Guests write "20/10", not
        # "20/10/2025", so the day and month are FACTS from the review while
        # the year is something extraction inferred from the post date - the
        # prompt says as much. Filtering exactly on the inferred part while
        # throwing away the observed part is backwards, and it is what made
        # Sven's search return nothing: sixteen bookings under his name with a
        # support contact, none surviving a date filter pinned to a guessed
        # year. Day and month must agree; the year may be off by one.
        where.append(f"""(
            {_day_month_match("b.experience_date")}
            OR {_day_month_match("b.created_at")}
        )""")
        params.append(_P.ArrayQueryParameter("dates", "STRING", dates))
    sql = f"""
    SELECT
        b.booking_id,
        b.experience_name,
        b.experience_id          AS tgid,
        b.tour_id                AS tid,
        b.vendor_id              AS vid,
        v.vendor_name,
        DATE(b.created_at)       AS booked_on,
        DATE(b.experience_date)  AS visit_date,
        b.primary_guest_name     AS guest_name,
        COUNT(sq.query_id)       AS contact_count,
        STRING_AGG(DISTINCT COALESCE(sq.query_tag, sq.query_type,
                                     sq.contact_type), ' | ') AS contact_tags,
        MIN(DATE(sq.query_created_at)) AS first_contact,
        MAX(DATE(sq.query_created_at)) AS last_contact
    FROM `{BIGQUERY_SUPPORT_TABLE}` sq
    JOIN `{BIGQUERY_BOOKINGS_TABLE}` b
      ON SAFE_CAST(sq.booking_id AS INT64) = b.booking_id
    LEFT JOIN `headout-analytics.analytics_reporting.dim_vendors` v
           ON b.vendor_id = v.vendor_id
    WHERE {" AND ".join(where)}
      AND sq.query_created_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(),
                                               INTERVAL {int(SUPPORT_LOOKBACK_DAYS)} DAY)
    GROUP BY 1,2,3,4,5,6,7,8,9
    ORDER BY last_contact DESC
    LIMIT {int(limit)}
    """
    return sql, params


async def find_via_support(indicators: dict, tgids: list | None = None,
                           limit: int = 8) -> list[dict]:
    """
    Bookings for these experiences, on a date the review named, whose guest
    then contacted support.

    The complement to the Zendesk text search, not a duplicate of it.
    fct_support_queries carries the contact as CATEGORIES - query_tag,
    query_type, contact_type - never the guest's words, so it cannot be
    searched for "falsches Datum"; that is Zendesk's job. What it adds is the
    fact of a contact, which turns "bookings for this experience around this
    date" into "bookings for this experience around this date whose guest then
    complained" - a far smaller set, and one every member of which has a
    reason to be in front of an associate.

    It does NOT match on the guest name. primary_guest_name is a PII hash on
    every row in this table: 639,109 bookings behind a support contact, every
    one of them hashed. A name comparison there cannot succeed.

    Used only as a fallback: the direct matcher answers first, and this runs
    when it has not.
    """
    if not is_live("bigquery"):
        return []
    dates = _iso_dates(indicators.get("dates_mentioned"))
    sql, params = support_search_sql(indicators, tgids, limit)
    if sql is None:
        log.info(f"[bq] support-anchored: needs both a resolved experience and "
                 f"a date (tgids={len(tgids or [])} dates={len(dates)}) - skipped")
        return []

    try:
        rows = await asyncio.get_running_loop().run_in_executor(
            None, lambda: _run_query(sql, params))
    except Exception as e:
        log.warning(f"[bq] support-anchored search failed: {e}")
        return []

    out = []
    for r in rows:
        d = _row_to_dict(r)
        # The name is a hash, so it must not be shown as though it identified
        # anyone. The card shows the experience, the date and the contact.
        if is_hashed_name(d.get("guestName") or ""):
            d["guestName"] = ""
        d["contact_count"] = getattr(r, "contact_count", 0) or 0
        d["contact_tags"]  = getattr(r, "contact_tags", "") or ""
        d["matched_on"] = ["venue", f"date:{dates[0]}" if dates else "date",
                           "contacted support"]
        out.append(d)
    log.info(f"[bq] support-anchored: tgids={len(tgids or [])} dates={len(dates)} "
             f"-> {len(out)} booking(s)")
    return out


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
            (SELECT AS STRUCT c.email, c.type, c.name
               FROM UNNEST(v.contact_details) c
              WHERE REGEXP_CONTAINS(LOWER(IFNULL(c.type, '')),
                                    r'{_ESCALATION_TYPE_RE}')
                AND IFNULL(c.email, '') != ''
              -- A vendor can list more than one ESCALATIONS contact. Order
              -- by address so the same booking always shows the same one -
              -- a value that changes between runs with no cause is worse
              -- than either candidate.
              ORDER BY c.email
              LIMIT 1)                                   AS escalation,
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
            _esc = getattr(rows[0], "escalation", None)
            return {
                "booking_status":   getattr(rows[0], "booking_status", None) or "",
                "tid_name":         getattr(rows[0], "tour_name",      None) or "",
                "isPartnered":      None if _partnered is None else bool(_partnered),
                "escalationEmail":  (_esc or {}).get("email") or "",
                "escalationType":   (_esc or {}).get("type") or "",
                "escalationName":   (_esc or {}).get("name") or "",
                "contactTypes":     getattr(rows[0], "contact_types", None) or "",
                "contactCount":     None if _n_contacts is None else int(_n_contacts),
                **_get_booking_amount(bid_int),
            }
    except Exception:
        pass
    return {}


def _get_booking_amount(bid_int: int) -> dict:
    """USD amount paid, its own query so an unexpected column name costs only
    this field. The DSS decision sheet forks policy on booking value > $125
    (the Retool app reads PRICE_PAYABLE_USD), so the pipeline needs the same
    input the app has. Absent on any failure - a missing amount must read as
    "unknown", not as $0."""
    try:
        sql = f"""
        SELECT b.price_payable_usd AS amount_usd
        FROM `{BIGQUERY_BOOKINGS_TABLE}` b
        WHERE b.booking_id = @bid
        LIMIT 1
        """
        rows = _run_query(sql, [_bqlib.ScalarQueryParameter("bid", "INT64", bid_int)])
        if rows:
            v = getattr(rows[0], "amount_usd", None)
            if v is not None:
                return {"amountUSD": float(v)}
    except Exception:
        pass
    return {}
