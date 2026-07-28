"""
Experience Insights - rewritten to match the Looker/BigQuery query, which is
the source of truth.

Seven queries run in parallel via asyncio.gather():
  A. Issue-specific negative reviews (same TID+VID, matching L2)
  B. Total negative reviews        (same TID+VID)
  C. Issue-specific support queries (same TID+VID, matching tags)
  D. Total support queries          (same TID+VID)
  E1. Average rating                (same TID+VID)
  E2. Average rating                (same TGID - every tour and vendor)
  F.  Total bookings                (same TID+VID) - the ratio denominator
  G.  Redemption details            (dim_vendor_tours, English)

Six things changed from the previous version, each because the Looker query
does it differently:

  1. NEGATIVE reviews only (rating <= 3) as the denominator. Counting every
     review made the issue ratio look far smaller than Looker reported.
  2. The window is anchored on the BOOKING'S EXPERIENCE DATE, not on today.
     Looker compares the 30 days before that booking's own experience date, so
     an old review is measured against its own period rather than this week's.
  3. Fulfilment rate is Looker's booking_completion_rate: bookings whose
     fulfilment_STATUS is Completed or Dirty, over EVERY booking. The old code
     read fulfilment_type, which matched no column at all. completion_type is a
     different field and is only used to identify Unfulfilled, as Looker's
     unfulfilled_rate does. Computed at VID scope and at TGID scope, because
     ops judges an experience on its TGID over four weeks.
  4. vendor_id comes from fct_fulfilments on the review queries and from
     fct_bookings on the booking and support queries - mirroring Looker, which
     mixes the two.
  5. Support queries exclude Chat Abandoned, Nar, Out Call and Vendor Query,
     which inflated the support denominator.
  6. dim_vendor_tours is read for redemption details - meeting point, cancel
     policy, instructions. Not surfaced on the dashboard yet, but it is the
     table that says what the experience was SUPPOSED to do, which is what a
     complaint gets compared against.

MOCK_MODE: bq_connector.run_query_async returns [] -> all zeros / nulls.
"""
import asyncio
import re
import logging
from datetime import datetime, timezone

from server.config import is_live, MOCK_MODE
from server.taxonomy import support_tags_for

log = logging.getLogger(__name__)

_REVIEWS_TABLE     = "headout-analytics.analytics_reporting.fct_reviews"
_BOOKINGS_TABLE    = "headout-analytics.analytics_reporting.fct_bookings"
_SUPPORT_TABLE     = "headout-analytics.analytics_reporting.fct_support_queries"
_FULFILMENTS_TABLE = "headout-analytics.analytics_reporting.fct_fulfilments"
_VENDOR_TOURS      = "headout-analytics.analytics_reporting.dim_vendor_tours"

# A review counts as negative at 3 stars or below - the threshold Looker uses.
_NEGATIVE_RATING_MAX = 3

# L2 issue synonyms, ported verbatim from the parent_l2_bucket mapping in the
# fct_reviews LookML. The same issue is written several ways in the data -
# "Meeting Point Issue" and "Meeting Point Issues" are the same thing, and the
# RCA query works around it with an explicit IN list. Matching on the exact
# string the classifier produced therefore misses most of the population.
# Resolving to a bucket and matching every variant in it is what makes the
# similar-reviews count mean anything.
_L2_BUCKETS = {
    "App and website issues": [
        "App", "App Issue", "App and website issues", "Website Issue"],
    "Audio Guide Issues": [
        "Audio Guide device/headset Issues", "Audio Guide Issues",
        "Audio Guide did not work", "Audio Guide not provided",
        "Audio Guide was not informative"],
    "Wrong Booking by customer": ["Booking mistake done by the guest"],
    "Facility Issue - venue related complaints": [
        "Broken Equipment", "Facility Issue", "Inadequate Facilities", "Strikes",
        "Theft", "Unhappy with the sight", "Venue closure"],
    "Pricing and Value offering issue": [
        "Expensive", "Found Expensive", "Found It Expensive", "Found Inconvenient",
        "Found Online Purchase Unnecessary", "Not Value for Money", "Overcharged",
        "Overpriced Ticket", "Pricing Issues", "Issue with price"],
    "SP Cancelled/Venue Closure": [
        "Venue Cancelled", "tickets were canceled without notice by the venue"],
    "Content - Instructions not clear/misleading info": [
        "Content - Instructions not clear/misleading info",
        "Content - Instructions not clear/missleading info",
        "Incorrect Information", "misleading information present on ticket"],
    "Not a negative review": [
        "Pleasant Experience", "Positive Experience", "Positive Comments"],
    "Customer Error": ["Customer Error", "Customer Late Issue"],
    "Overcrowding/long wait": [
        "Crowded", "Long Queues", "Long Wait Time", "Long waiting time",
        "Overcrowding"],
    "Customer Support Issue": [
        "Customer Support Issue", "Customer Support Issues",
        "Dissatisfactory Customer Service", "Dissatisfactory Customer Support"],
    "General discontent with exp.": [
        "Did not like the experience", "General Discontent",
        "Unpleasant Experience", "Unhappy with the service"],
    "Ticket Delivery Issues(FF issues)": [
        "Ticket Issues", "Tickets", "Duplicate Tickets", "Invalid Tickets",
        "Lost Tickets", "Tickets Issue", "Tickets Canceled", "Tickets Not Used",
        "Unavailability of Tickets"],
    "Difficult redemption process": [
        "Inconvenient Redemption", "Online ticket purchase was unnecessary",
        "Requires you to download the APP to access tickets"],
    "Guide Behaviour Issues": [
        "Guide Behaviour Issues", "Guide disappeared in between tour",
        "Guide no show", "Guide Service Issues",
        "Guide Service Issues/ guide rushing the tour",
        "Guide providing irrelevant/inexperienced/not clear"],
    "Inclusions not met": ["Inclusions"],
    "Meeting Point Issues": [
        "Incorrect Meeting Point", "Meeting Point Issues", "Meeting Point Issue",
        "Unable to locate meeting point"],
    "Invalid Tickets": ["Incorrect Tickets", "Invalid Tickets"],
    "Inventory Listing Issue": ["Inventory Listing Issue"],
    "Partial closure of venue/ activity closure at venue": [
        "Ride or activity closure",
        "Partial closure of venue/ activity closure at venue"],
    "Services did not start on time": ["Service Timing"],
}
_L2_TO_BUCKET = {v.strip().lower(): b for b, vs in _L2_BUCKETS.items() for v in vs}


def l2_variants(l2: str | None) -> list:
    """
    Every spelling of the issue the classifier named.

    Falls back to the value itself when it is not in the mapping, so an L2 we
    have not seen still matches its own name rather than nothing.
    """
    key = str(l2 or "").strip().lower()
    if not key:
        return []
    bucket = _L2_TO_BUCKET.get(key)
    if not bucket:
        return [key]
    return sorted({v.strip().lower() for v in _L2_BUCKETS[bucket]})


# Support tags that are not guest contacts and would inflate the denominator.
_EXCLUDED_SUPPORT_TAGS = ["Chat Abandoned", "Nar", "Out Call", "Vendor Query"]

# Fulfilment rate, straight off the Looker fulfilments view:
#
#   count_completed_bookings: filters [fulfilment_status: "Completed, Dirty"]
#   booking_completion_rate:  SAFE_DIVIDE(count_completed_bookings, count_bookings)
#
# So the metric keys on fulfilment_STATUS and the denominator is EVERY booking -
# no exclusions. completion_type is a separate field and is only used to pick
# out Unfulfilled specifically, per the unfulfilled_rate measure:
#
#   filters [fulfilment_status: "-Completed, -Dirty", completion_type: "Unfulfilled"]
_FF_COMPLETED_STATUSES = ["Completed", "Dirty"]

# Two Looker views define a measure called booking_completion_rate, over
# different columns:
#
#   bookings     count_completed_bookings: filters [booking_status: "Completed, Dirty"]
#   fulfilments  count_completed_bookings: filters [fulfilment_status: "Completed, Dirty"]
#
# The ops guidance is about FF and unfulfilled bookings, which points at the
# fulfilments view, so fulfilment_status is what rate reports. Both are computed
# and returned - they can disagree, and a tile that silently picked one would
# be impossible to reconcile against whichever view someone had open.

# Ops guidance: below 95% is terrible, but the rate is meaningless at low
# volume - one unfulfilled booking out of two is 50%. The thumb rule is over
# 100 bookings AND under 95%, so both are carried and the caller decides.
_FF_RATE_FLOOR   = 0.95
_FF_MIN_BOOKINGS = 100

# Looker compares review_ratio against 0.15 and support_ratio against 15. Both
# come out of safe_divide as fractions, so the support test can never fire and
# escalation is review-ratio-only there. Treated as a typo and both are 0.15
# here; change _SUPPORT_ESCALATION if the intended threshold is different.
_REVIEW_ESCALATION  = 0.15
_SUPPORT_ESCALATION = 0.15


def _zero_result(l2: str | None) -> dict:
    return {
        "similar_reviews_30d":         0,
        "total_reviews_30d":           0,
        "similar_support_queries_30d": 0,
        "total_support_queries_30d":   0,
        "total_bookings_30d":          0,
        "review_ratio":                0.0,
        "support_ratio":               0.0,
        "escalation":                  False,
        "rating_tgid":   {"avg": None, "n": 0},
        "rating_tidvid": {"avg": None, "n": 0},
        "rating_15d":    {"avg": None, "n": 0},
        "rating_30d":    {"avg": None, "n": 0},
        "redemption":                  None,
        "ff_vid":                      None,
        "ff_tgid":                     None,
        "ff_same_day":                 None,
        "vid_completion_rate":         None,
        "vidCompletionRate":           "N/A",
        "same_day_same_vid":           None,
        "sameDaySameVidIssues":        "N/A",
        "_computed_for_l2": l2,
        "_computed_at":     datetime.now(timezone.utc).isoformat(),
    }


async def _run(sql: str, params: dict) -> list:
    from server.services.bq_connector import run_query_async
    try:
        return await run_query_async(sql, params)
    except Exception as e:
        log.warning(f"[insights] query failed: {e}")
        return []


_WINDOWS = {"7d": 7, "4w": 28, "14d": 14, "15d": 15, "30d": 30, "90d": 90, "180d": 180}


def window_days(window: str | None, default: int = 30) -> int:
    """
    Associate-selected window -> days. Unknown values fall back to default.

    The default is 30 to match Looker's rolling comparison.
    """
    if not window:
        return default
    w = str(window).strip().lower()
    if w in _WINDOWS:
        return _WINDOWS[w]
    m = re.match(r"^(\d+)\s*([dwm])$", w)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        return n * {"d": 1, "w": 7, "m": 30}[unit]
    return default


def _fld(row, key):
    """BigQuery rows arrive as dicts or as Row objects depending on the client."""
    if row is None:
        return None
    return row.get(key) if isinstance(row, dict) else getattr(row, key, None)


def _ff(res) -> dict:
    """
    One fulfilment row -> completed / unfulfilled / total / rate.

    rate is the fulfilments view's booking_completion_rate: bookings whose
    fulfilment_status is Completed or Dirty, over EVERY booking, no exclusions.
    rate_by_booking_status is the bookings view's measure of the same name, over
    booking_status instead. None when there are no bookings, so the tile shows a
    dash rather than a confident 0%.

    needs_attention follows the ops thumb rule - over 100 bookings and under
    95%. Low volume is excluded because the rate is noise there: one unfulfilled
    booking out of two reads as 50%.
    """
    row    = res[0] if isinstance(res, list) and res else None
    done   = int(_fld(row, "completed") or 0)
    done_b = int(_fld(row, "completed_by_booking_status") or 0)
    unful  = int(_fld(row, "unfulfilled") or 0)
    total  = int(_fld(row, "total") or 0)
    rate   = round(done / total, 4) if total else None
    return {
        "completed":       done,
        "unfulfilled":     unful,
        "total":           total,
        "rate":            rate,
        # The bookings view's reading of the same measure name. Kept alongside
        # so the two can be compared rather than quietly diverging.
        "rate_by_booking_status": round(done_b / total, 4) if total else None,
        "unfulfilled_rate": round(unful / total, 4) if total else None,
        "needs_attention": bool(total >= _FF_MIN_BOOKINGS
                                and rate is not None and rate < _FF_RATE_FLOOR),
    }


def _pct(rate) -> str:
    return "N/A" if rate is None else f"{rate * 100:.1f}%"


def _count(res) -> int:
    if isinstance(res, (Exception, type(None))) or not res:
        return 0
    return int(_fld(res[0] if isinstance(res, list) else res, "c") or 0)


async def get_insights(booking: dict, l1: str | None, l2: str | None,
                       window: str | None = None) -> dict:
    """
    Run the insight queries in parallel and return the results.

    Every window is measured backwards from the BOOKING'S experience date, not
    from today. A review about a visit in March is compared against March, which
    is what Looker does and what makes the ratio meaningful for an old review.

    Returns zeros immediately when tid/vid are missing or BigQuery is not live -
    a review with no confirmed booking is a normal state, not a failure.
    """
    wd         = window_days(window)
    tid        = str(booking.get("tid") or "").strip()
    vid        = str(booking.get("vid") or "").strip()
    tgid       = str(booking.get("tgid") or "").strip()
    visit_date = str(booking.get("visitDate") or booking.get("date_of_visit") or "").strip()

    if not tid or not vid:
        log.warning("[insights] tid or vid missing - returning zeros")
        return _zero_result(l2)

    if not is_live("bigquery") or MOCK_MODE:
        return _zero_result(l2)

    # Without a visit date there is nothing to anchor the window to. Falling
    # back to today would silently measure a different period from Looker, so
    # anchor explicitly and say which date was used.
    anchor_sql = "@anchor" if visit_date else "CURRENT_DATE()"
    anchor_par = {"anchor": ("DATE", visit_date)} if visit_date else {}
    if not visit_date:
        log.warning("[insights] no visit date - window anchored on today, "
                    "which will not match Looker for an older review")

    tags_spec = support_tags_for(l1 or "", l2 or "") if (l1 and l2) else None
    base = {"tid": tid, "vid": vid, **anchor_par}

    # The rolling window: the wd days before the anchor, excluding the anchor
    # day itself, exactly as Looker's self-join does.
    _win = (f"DATE(b.experience_date) < {anchor_sql} "
            f"AND DATE(b.experience_date) > DATE_SUB({anchor_sql}, INTERVAL {wd} DAY)")

    # --- A / B: negative reviews -------------------------------------------
    # vendor_id comes off fct_fulfilments here, per Looker.
    _reviews_from = f"""
FROM `{_REVIEWS_TABLE}` r
LEFT JOIN `{_BOOKINGS_TABLE}` b ON r.booking_id = b.booking_id
LEFT JOIN `{_FULFILMENTS_TABLE}` f ON r.booking_id = f.booking_id
"""
    sql_b = f"""
SELECT COUNT(DISTINCT r.booking_id) AS c
{_reviews_from}
WHERE b.tour_id = @tid AND f.vendor_id = @vid
  AND r.rating <= {_NEGATIVE_RATING_MAX}
  AND {_win}
"""
    sql_a = f"""
SELECT COUNT(DISTINCT r.booking_id) AS c
{_reviews_from}
LEFT JOIN UNNEST(r.issues) AS iss
LEFT JOIN UNNEST(iss.l2_issues) AS l2v
WHERE b.tour_id = @tid AND f.vendor_id = @vid
  AND r.rating <= {_NEGATIVE_RATING_MAX}
  AND LOWER(l2v) IN UNNEST(@l2v)
  AND {_win}
"""

    # --- C / D: support queries --------------------------------------------
    # vendor_id comes off fct_bookings here, per Looker. booking_id is a STRING
    # in fct_support_queries and an INT64 in fct_bookings, hence the CAST.
    _support_from = f"""
FROM `{_SUPPORT_TABLE}` sq
LEFT JOIN `{_BOOKINGS_TABLE}` b ON CAST(b.booking_id AS STRING) = sq.booking_id
"""
    _support_where = f"""
WHERE b.tour_id = @tid AND b.vendor_id = @vid
  AND sq.query_tag NOT IN UNNEST(@excluded)
  AND {_win}
"""
    sql_d = f"""
SELECT COUNT(DISTINCT sq.booking_id) AS c
{_support_from}{_support_where}
"""

    # --- E1 / E2: average rating at two scopes ------------------------------
    # The dashboard shows "TGID Rating" and "TID . VID Rating" side by side:
    # how the experience is rated overall, against how this particular tour and
    # vendor combination is rated. They are different populations, and computing
    # both from the same TID+VID query - as this file used to - made the TGID
    # tile display TID+VID data under a TGID label.
    #
    # Over ALL reviews, not just negative ones: an average taken over reviews
    # already filtered to <= 3 stars could never exceed 3 and would say nothing
    # about how the experience is doing.
    # Looker's avg_rating is average_distinct over booking_id, filtered to
    # review_source CUSTOMER - so vendor- and system-sourced rows are excluded
    # and a booking with several review rows counts once.
    _avg_select = """
SELECT ROUND(AVG(rating), 2) AS avg_rating, COUNT(*) AS n_ratings
FROM (
  SELECT r.booking_id, ANY_VALUE(r.rating) AS rating
"""
    _avg_tail = """
  GROUP BY r.booking_id
)
"""
    sql_e_tidvid = f"""{_avg_select}{_reviews_from}
WHERE b.tour_id = @tid AND f.vendor_id = @vid
  AND r.rating IS NOT NULL
  AND r.source = 'CUSTOMER'
  AND {_win}
{_avg_tail}"""
    # TGID is the experience, so this deliberately spans every tour and vendor
    # selling it - that breadth is the point of the comparison.
    sql_e_tgid = f"""{_avg_select}{_reviews_from}
WHERE b.experience_id = @tgid
  AND r.rating IS NOT NULL
  AND r.source = 'CUSTOMER'
  AND {_win}
{_avg_tail}"""

    # --- F: total bookings --------------------------------------------------
    sql_f = f"""
SELECT COUNT(DISTINCT b.booking_id) AS c
FROM `{_BOOKINGS_TABLE}` b
WHERE b.tour_id = @tid AND b.vendor_id = @vid
  AND {_win}
"""

    # --- G: redemption details ----------------------------------------------
    # What the experience was supposed to do - meeting point, cancellation
    # policy, redemption instructions. English content only. Not on the
    # dashboard yet; carried so it is there when it is.
    sql_g = f"""
SELECT
  t.meeting_point_address,
  t.meeting_point_latitude,
  t.meeting_point_longitude,
  t.ticket_redemption_method,
  t.ticket_delivery,
  t.redemption_type,
  t.cancellation_policy,
  t.is_cancellable,
  t.cancellable_up_to,
  t.is_reschedulable,
  t.reschedulable_up_to,
  t.has_two_step_fulfillment,
  t.on_ground_contact,
  t.is_photo_id_required,
  t.has_late_arrival_policy,
  t.start_time_buffer,
  TRIM(REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(
    t.important_instructions, r'bis_size="{{[^"]*}}"', ''), r'<[^>]+>', ''), r'\\s+', ' '
  )) AS important_instructions,
  TRIM(REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(
    lc.redemption_instructions, r'bis_size="{{[^"]*}}"', ''), r'<[^>]+>', ''), r'\\s+', ' '
  )) AS redemption_instructions,
  lc.pax_selection_instructions,
  lc.callout_instructions
FROM `{_VENDOR_TOURS}` t
LEFT JOIN UNNEST(t.language_content_data) AS lc
WHERE t.tour_id = @tid AND t.vendor_id = @vid
  AND lc.language_code = 'en'
LIMIT 1
"""

    # --- H / H2 / I: fulfilment rate ---------------------------------------
    # Mirrors Looker's count_completed_bookings / count_bookings, and its
    # unfulfilled measure, which needs both fields: a status outside
    # Completed/Dirty AND completion_type = Unfulfilled.
    _ff_select = f"""
SELECT
  COUNT(DISTINCT IF(f.fulfilment_status IN UNNEST(@ff_done),
                    b.booking_id, NULL))            AS completed,
  COUNT(DISTINCT IF(b.booking_status IN UNNEST(@ff_done),
                    b.booking_id, NULL))            AS completed_by_booking_status,
  COUNT(DISTINCT IF(f.fulfilment_status NOT IN UNNEST(@ff_done)
                    AND f.completion_type = 'Unfulfilled',
                    b.booking_id, NULL))            AS unfulfilled,
  COUNT(DISTINCT b.booking_id)                      AS total
FROM `{_BOOKINGS_TABLE}` b
LEFT JOIN `{_FULFILMENTS_TABLE}` f ON b.booking_id = f.booking_id
"""
    sql_h  = f"{_ff_select}WHERE b.vendor_id = @vid AND {_win}"
    # Ops checks fulfilment for the same TGID over the last four weeks, because
    # that is the population that says whether this is a one-off or a pattern.
    sql_h2 = f"{_ff_select}WHERE b.experience_id = @tgid AND {_win}"
    sql_i  = f"{_ff_select}WHERE b.vendor_id = @vid AND DATE(b.experience_date) = {anchor_sql}"

    excluded = {"excluded": ("STRING", _EXCLUDED_SUPPORT_TAGS)}
    ff_par   = {"ff_done": ("STRING", _FF_COMPLETED_STATUSES)}

    # A and C need a tag/L2 mapping. Without one they are skipped and the rest
    # still run - a missing framework should cost you two numbers, not all of
    # the insights.
    skip_ac = tags_spec is None
    variants = l2_variants(l2)
    coro_a = (_run(sql_a, {**base, "l2v": ("STRING", variants)})
              if not skip_ac and variants else asyncio.sleep(0))

    if skip_ac:
        coro_c = asyncio.sleep(0)
    elif isinstance(tags_spec, list):
        sql_c = f"""
SELECT COUNT(DISTINCT sq.booking_id) AS c
{_support_from}{_support_where}
  AND sq.query_tag IN UNNEST(@tags)
"""
        coro_c = _run(sql_c, {**base, **excluded, "tags": ("STRING", tags_spec)})
    else:
        pats = tags_spec.get("like_any", [])
        if pats:
            ors = " OR ".join(f"LOWER(sq.query_tag) LIKE @pat{i}" for i in range(len(pats)))
            sql_c = f"""
SELECT COUNT(DISTINCT sq.booking_id) AS c
{_support_from}{_support_where}
  AND ({ors})
"""
            coro_c = _run(sql_c, {**base, **excluded,
                                  **{f"pat{i}": p for i, p in enumerate(pats)}})
        else:
            coro_c, skip_ac = asyncio.sleep(0), True

    results = await asyncio.gather(
        coro_a,
        _run(sql_b, base),
        coro_c,
        _run(sql_d, {**base, **excluded}),
        _run(sql_e_tidvid, base),
        _run(sql_e_tgid, {**anchor_par, "tgid": tgid}) if tgid else asyncio.sleep(0),
        _run(sql_f, base),
        _run(sql_g, {"tid": tid, "vid": vid}),
        _run(sql_h, {**base, **ff_par}),
        _run(sql_h2, {**anchor_par, **ff_par, "tgid": tgid}) if tgid else asyncio.sleep(0),
        _run(sql_i, {**base, **ff_par}) if visit_date else asyncio.sleep(0),
        return_exceptions=True,
    )

    sim_rev = 0 if skip_ac else _count(results[0])
    _l2_variant_count = len(variants)
    tot_rev = _count(results[1])
    sim_sup = 0 if skip_ac else _count(results[2])
    tot_sup = _count(results[3])
    tot_bkg = _count(results[6])

    def _safe_div(n, d) -> float:
        return round(n / d, 4) if d else 0.0

    review_ratio  = _safe_div(sim_rev, tot_rev)
    support_ratio = _safe_div(sim_sup, tot_sup)
    escalation    = (review_ratio > _REVIEW_ESCALATION
                     or support_ratio > _SUPPORT_ESCALATION)

    def _rating(res):
        if not isinstance(res, list) or not res:
            return {"avg": None, "n": 0}
        avg = _fld(res[0], "avg_rating")
        return {"avg": float(avg) if avg is not None else None,
                "n": int(_fld(res[0], "n_ratings") or 0)}

    rating_tidvid = _rating(results[4])
    rating_tgid   = _rating(results[5])

    g_rows = results[7] if isinstance(results[7], list) else []
    redemption = None
    if g_rows:
        row = g_rows[0]
        redemption = {k: _fld(row, k) for k in (
            "meeting_point_address", "meeting_point_latitude",
            "meeting_point_longitude", "ticket_redemption_method",
            "ticket_delivery", "redemption_type", "cancellation_policy",
            "is_cancellable", "cancellable_up_to", "is_reschedulable",
            "reschedulable_up_to", "has_two_step_fulfillment",
            "on_ground_contact", "is_photo_id_required",
            "has_late_arrival_policy", "start_time_buffer",
            "important_instructions", "redemption_instructions",
            "pax_selection_instructions", "callout_instructions",
        )}
        redemption = {k: v for k, v in redemption.items() if v not in (None, "")}

    ff_vid  = _ff(results[8])
    ff_tgid = _ff(results[9])
    ff_day  = _ff(results[10])

    out = {
        "similar_reviews_30d":         sim_rev,
        "total_reviews_30d":           tot_rev,
        "similar_support_queries_30d": sim_sup,
        "total_support_queries_30d":   tot_sup,
        "total_bookings_30d":          tot_bkg,
        "review_ratio":                review_ratio,
        "support_ratio":               support_ratio,
        "escalation":                  escalation,
        "rating_tgid":                 rating_tgid,
        "rating_tidvid":               rating_tidvid,
        "redemption":                  redemption,
        "ff_vid":                      ff_vid,
        "ff_tgid":                     ff_tgid,
        "ff_same_day":                 ff_day,
        # The dashboard still reads these names. They were never windows - the
        # TGID tile reads rating_15d and the TID.VID tile reads rating_30d -
        # so they are aliased to the right scope rather than renamed, and both
        # respect whichever window the associate picked.
        "rating_15d": rating_tgid,
        "rating_30d": rating_tidvid,
        "vid_completion_rate":   ff_vid["rate"],
        "vidCompletionRate":     _pct(ff_vid["rate"]),
        "same_day_same_vid":     ({"issues": ff_day["unfulfilled"], "total": ff_day["total"]}
                                  if ff_day["total"] else None),
        "sameDaySameVidIssues":  (f"{ff_day['unfulfilled']} of {ff_day['total']}"
                                  if ff_day["total"] else "N/A"),
        "_window_days":     wd,
        "_anchored_on":     visit_date or "today",
        "_computed_for_l2": l2,
        "_l2_variants":     _l2_variant_count,
        "_computed_at":     datetime.now(timezone.utc).isoformat(),
    }

    log.info(
        f"[insights] tid={tid} vid={vid} l2={l2!r} anchor={out['_anchored_on']} "
        f"window={wd}d neg_reviews={sim_rev}/{tot_rev} queries={sim_sup}/{tot_sup} "
        f"bookings={tot_bkg} ratio_r={review_ratio} ratio_s={support_ratio} "
        f"rating_tgid={rating_tgid['avg']} rating_tidvid={rating_tidvid['avg']} "
        f"escalation={escalation} "
        f"ff_vid={_pct(ff_vid['rate'])}/bs={_pct(ff_vid['rate_by_booking_status'])}"
        f"({ff_vid['total']}) "
        f"ff_tgid={_pct(ff_tgid['rate'])}({ff_tgid['total']}) "
        f"attention={ff_tgid['needs_attention']} "
        f"redemption={'yes' if redemption else 'no'}"
    )
    return out
