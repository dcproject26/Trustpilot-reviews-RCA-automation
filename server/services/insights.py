"""
Experience Insights — 5 BigQuery queries run in parallel via asyncio.gather().

get_insights(booking, l1, l2) → dict
  - A. Similar reviews (same TID+VID, matching L2, last 30d)
  - B. Total reviews (same TID+VID, last 30d)
  - C. Similar support queries (exact-tag list or LIKE-any for Content L2)
  - D. Total support queries (same TID+VID, last 30d)
  - E. Rating windows (15d + 30d)

MOCK_MODE: bq_connector.run_query_async returns [] → all zeros / nulls.
"""
import asyncio
import re
import logging
from datetime import datetime, timezone

from server.config import is_live, MOCK_MODE
from server.taxonomy import support_tags_for

log = logging.getLogger(__name__)

_REVIEWS_TABLE      = "headout-analytics.analytics_reporting.fct_reviews"
_BOOKINGS_TABLE     = "headout-analytics.analytics_reporting.fct_bookings"
_SUPPORT_TABLE      = "headout-analytics.analytics_reporting.fct_support_queries"
_FULFILMENTS_TABLE  = "headout-analytics.analytics_reporting.fct_fulfilments"


def _zero_result(l2: str | None) -> dict:
    return {
        "similar_reviews_30d":         0,
        "total_reviews_30d":           0,
        "similar_support_queries_30d": 0,
        "total_support_queries_30d":   0,
        "review_ratio":                0.0,
        "support_ratio":               0.0,
        "escalation":                  False,
        "rating_15d": {"avg": None, "n": 0},
        "rating_30d": {"avg": None, "n": 0},
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


def window_days(window: str | None, default: int = 90) -> int:
    """Associate-selected window -> days. Unknown values fall back to default."""
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


async def get_insights(booking: dict, l1: str | None, l2: str | None,
                       window: str | None = None) -> dict:
    """
    Run 7 BQ queries in parallel and return the insights dict.
    Returns zeros/nulls immediately if tid/vid are missing or BQ is not live.
    """
    # Every window below is the associate's selected range, not a hardcoded one.
    # The picker used to change only the label on the tile while the query stayed
    # at 15/30/28 days, so a tile could read "4.42 * · 90d" over a 15-day average.
    _wd        = window_days(window)
    tid        = str(booking.get("tid") or "").strip()
    vid        = str(booking.get("vid") or "").strip()
    visit_date = str(booking.get("visitDate") or booking.get("date_of_visit") or "").strip()

    if not tid or not vid:
        log.warning("[insights] tid or vid missing — returning zeros")
        return _zero_result(l2)

    if not is_live("bigquery") or MOCK_MODE:
        return _zero_result(l2)

    tags_spec = support_tags_for(l1 or "", l2 or "") if (l1 and l2) else None

    # ── Query A: similar reviews ──────────────────────────────────────────────
    sql_a = f"""
SELECT COUNT(DISTINCT r.booking_id) AS c
FROM `{_REVIEWS_TABLE}` r
LEFT JOIN `{_BOOKINGS_TABLE}` b ON r.booking_id = b.booking_id
LEFT JOIN UNNEST(r.issues) AS iss
LEFT JOIN UNNEST(iss.l2_issues) AS l2v
WHERE b.tour_id = @tid AND b.vendor_id = @vid
  AND LOWER(l2v) = LOWER(@l2)
  AND DATE(r.reviewed_at) BETWEEN DATE_SUB(CURRENT_DATE(), INTERVAL {_wd} DAY) AND CURRENT_DATE()
"""

    # ── Query B: total reviews ────────────────────────────────────────────────
    sql_b = f"""
SELECT COUNT(DISTINCT r.booking_id) AS c
FROM `{_REVIEWS_TABLE}` r
LEFT JOIN `{_BOOKINGS_TABLE}` b ON r.booking_id = b.booking_id
WHERE b.tour_id = @tid AND b.vendor_id = @vid
  AND DATE(r.reviewed_at) BETWEEN DATE_SUB(CURRENT_DATE(), INTERVAL {_wd} DAY) AND CURRENT_DATE()
"""

    # ── Query D: total support ────────────────────────────────────────────────
    sql_d = f"""
SELECT COUNT(DISTINCT sq.booking_id) AS c
FROM `{_SUPPORT_TABLE}` sq
LEFT JOIN `{_BOOKINGS_TABLE}` b
  ON CAST(b.booking_id AS STRING) = sq.booking_id
WHERE b.tour_id = @tid AND b.vendor_id = @vid
  AND DATE(sq.query_created_at) BETWEEN DATE_SUB(CURRENT_DATE(), INTERVAL {_wd} DAY) AND CURRENT_DATE()
"""

    # ── Query E: rating windows ───────────────────────────────────────────────
    sql_e = f"""
WITH bx AS (
  SELECT DISTINCT b.booking_id FROM `{_BOOKINGS_TABLE}` b
  WHERE b.tour_id = @tid AND b.vendor_id = @vid
)
SELECT
  ROUND(AVG(CASE WHEN DATE(r.reviewed_at) >= DATE_SUB(CURRENT_DATE(), INTERVAL {_wd} DAY)
                 THEN r.rating END), 2)           AS avg_rating_15d,
  COUNT(CASE WHEN DATE(r.reviewed_at) >= DATE_SUB(CURRENT_DATE(), INTERVAL {_wd} DAY)
              THEN 1 END)                          AS n_ratings_15d,
  ROUND(AVG(CASE WHEN DATE(r.reviewed_at) >= DATE_SUB(CURRENT_DATE(), INTERVAL {_wd} DAY)
                 THEN r.rating END), 2)           AS avg_rating_30d,
  COUNT(CASE WHEN DATE(r.reviewed_at) >= DATE_SUB(CURRENT_DATE(), INTERVAL {_wd} DAY)
              THEN 1 END)                          AS n_ratings_30d
FROM `{_REVIEWS_TABLE}` r
JOIN bx ON bx.booking_id = r.booking_id
WHERE r.rating IS NOT NULL
"""

    base_params = {"tid": tid, "vid": vid}

    # ── Query F: VID completion rate (last 28 days) ───────────────────────────
    sql_f = f"""
SELECT
  COUNTIF(f.fulfilment_type NOT IN ('FAILED','UNFULFILLED','CANCELLED')) AS completed,
  COUNT(*) AS total
FROM `{_BOOKINGS_TABLE}` b
LEFT JOIN `{_FULFILMENTS_TABLE}` f ON b.booking_id = f.booking_id
WHERE b.vendor_id = @vid
  AND DATE(b.experience_date) BETWEEN DATE_SUB(CURRENT_DATE(), INTERVAL {_wd} DAY) AND CURRENT_DATE()
"""

    # ── Query G: same-day same-VID fulfilment issues ──────────────────────────
    sql_g = f"""
SELECT
  COUNTIF(f.fulfilment_type IN ('FAILED','UNFULFILLED','CANCELLED')) AS issues,
  COUNT(*) AS total
FROM `{_BOOKINGS_TABLE}` b
LEFT JOIN `{_FULFILMENTS_TABLE}` f ON b.booking_id = f.booking_id
WHERE b.vendor_id = @vid
  AND DATE(b.experience_date) = @vd
"""

    has_visit_date = bool(visit_date)
    coro_f = _run(sql_f, {"vid": vid}) if has_visit_date else asyncio.sleep(0)
    coro_g = (_run(sql_g, {"vid": vid, "vd": ("DATE", visit_date)})
              if has_visit_date else asyncio.sleep(0))

    # ── Build queries C (support) depending on tag spec ───────────────────────
    skip_ac = tags_spec is None  # no framework for this L2

    if skip_ac:
        # No tag mapping — skip A + C, still run B + D + E + F + G
        coros = [
            asyncio.sleep(0),       # A placeholder
            _run(sql_b, base_params),
            asyncio.sleep(0),       # C placeholder
            _run(sql_d, base_params),
            _run(sql_e, base_params),
            coro_f,
            coro_g,
        ]
    elif isinstance(tags_spec, list):
        # Exact-tag variant C
        sql_c = f"""
SELECT COUNT(DISTINCT sq.booking_id) AS c
FROM `{_SUPPORT_TABLE}` sq
LEFT JOIN `{_BOOKINGS_TABLE}` b
  ON CAST(b.booking_id AS STRING) = sq.booking_id
WHERE b.tour_id = @tid AND b.vendor_id = @vid
  AND sq.query_tag IN UNNEST(@tags)
  AND DATE(sq.query_created_at) BETWEEN DATE_SUB(CURRENT_DATE(), INTERVAL {_wd} DAY) AND CURRENT_DATE()
"""
        params_ac = {**base_params, "l2": l2, "tags": ("STRING", tags_spec)}
        params_c  = {**base_params, "tags": ("STRING", tags_spec)}
        coros = [
            _run(sql_a, {**base_params, "l2": l2}),
            _run(sql_b, base_params),
            _run(sql_c, params_c),
            _run(sql_d, base_params),
            _run(sql_e, base_params),
            coro_f,
            coro_g,
        ]
    else:
        # LIKE-any variant C (Content L2)
        like_pats = tags_spec.get("like_any", [])
        if like_pats:
            or_clauses = " OR ".join(
                f"LOWER(sq.query_tag) LIKE @pat{i}" for i in range(len(like_pats))
            )
            sql_c_like = f"""
SELECT COUNT(DISTINCT sq.booking_id) AS c
FROM `{_SUPPORT_TABLE}` sq
LEFT JOIN `{_BOOKINGS_TABLE}` b
  ON CAST(b.booking_id AS STRING) = sq.booking_id
WHERE b.tour_id = @tid AND b.vendor_id = @vid
  AND ({or_clauses})
  AND DATE(sq.query_created_at) BETWEEN DATE_SUB(CURRENT_DATE(), INTERVAL {_wd} DAY) AND CURRENT_DATE()
"""
            params_c_like = {**base_params, **{f"pat{i}": p for i, p in enumerate(like_pats)}}
            coros = [
                _run(sql_a, {**base_params, "l2": l2}),
                _run(sql_b, base_params),
                _run(sql_c_like, params_c_like),
                _run(sql_d, base_params),
                _run(sql_e, base_params),
                coro_f,
                coro_g,
            ]
        else:
            coros = [
                asyncio.sleep(0),
                _run(sql_b, base_params),
                asyncio.sleep(0),
                _run(sql_d, base_params),
                _run(sql_e, base_params),
                coro_f,
                coro_g,
            ]
            skip_ac = True

    try:
        results = await asyncio.gather(*coros, return_exceptions=True)
    except Exception as e:
        log.warning(f"[insights] gather failed: {e}")
        return _zero_result(l2)

    def _count(res):
        if isinstance(res, (Exception, type(None))): return 0
        if not res: return 0
        row = res[0] if isinstance(res, list) else res
        if isinstance(row, dict):
            return int(row.get("c", 0) or 0)
        return int(getattr(row, "c", None) or 0)

    sim_rev   = 0 if skip_ac else _count(results[0])
    tot_rev   = _count(results[1])
    sim_sup   = 0 if skip_ac else _count(results[2])
    tot_sup   = _count(results[3])
    e_rows    = results[4] if isinstance(results[4], list) else []
    f_rows    = results[5] if isinstance(results[5], list) else []
    g_rows    = results[6] if isinstance(results[6], list) else []

    def _safe_div(n, d) -> float:
        return round(n / d, 4) if d else 0.0

    # Similar support comes from Zendesk TAGS, not the BigQuery support table:
    # a "similar" case is one whose ticket carries the same support tags inside
    # the associate's window. The BQ figures stay as the fallback for when
    # Zendesk is unavailable or the L1/L2 has no tag mapping.
    if tags_spec and is_live("zendesk"):
        try:
            from server.services.zendesk import count_tickets_by_tags
            _tags = tags_spec if isinstance(tags_spec, list) else tags_spec.get("like_any", [])
            _m, _t = await count_tickets_by_tags(_tags, days=_wd)
            if _t:
                sim_sup, tot_sup = _m, _t
                log.info(f"[insights] similar support from Zendesk tags: "
                         f"{_m}/{_t} over {_wd}d")
        except Exception as e:
            log.warning(f"[insights] Zendesk tag count failed, keeping BQ figures: {e}")

    review_ratio  = _safe_div(sim_rev, tot_rev)
    support_ratio = _safe_div(sim_sup, tot_sup)
    escalation    = review_ratio > 0.15 or support_ratio > 0.15

    avg_15d = n_15d = avg_30d = n_30d = None
    if e_rows and not isinstance(e_rows[0], Exception):
        row = e_rows[0]
        def _fld(r, k):
            return getattr(r, k, None) if not isinstance(r, dict) else r.get(k)
        avg_15d = _fld(row, "avg_rating_15d")
        n_15d   = int(_fld(row, "n_ratings_15d") or 0)
        avg_30d = _fld(row, "avg_rating_30d")
        n_30d   = int(_fld(row, "n_ratings_30d") or 0)
        avg_15d = float(avg_15d) if avg_15d is not None else None
        avg_30d = float(avg_30d) if avg_30d is not None else None

    # ── Query F results: VID completion rate ──────────────────────────────────
    vid_completion_rate = None
    vid_completion_rate_str = "N/A"
    if has_visit_date and f_rows and not isinstance(f_rows[0], Exception):
        def _fld2(r, k):
            return getattr(r, k, None) if not isinstance(r, dict) else r.get(k)
        fr = f_rows[0]
        f_completed = int(_fld2(fr, "completed") or 0)
        f_total     = int(_fld2(fr, "total") or 0)
        if f_total:
            vid_completion_rate     = round(f_completed / f_total, 3)
            vid_completion_rate_str = f"{vid_completion_rate * 100:.1f}%"

    # ── Query G results: same-day same-VID ───────────────────────────────────
    same_day_same_vid     = None
    same_day_same_vid_str = "N/A"
    if has_visit_date and g_rows and not isinstance(g_rows[0], Exception):
        def _fld3(r, k):
            return getattr(r, k, None) if not isinstance(r, dict) else r.get(k)
        gr = g_rows[0]
        g_issues = int(_fld3(gr, "issues") or 0)
        g_total  = int(_fld3(gr, "total") or 0)
        same_day_same_vid     = {"issues": g_issues, "total": g_total}
        same_day_same_vid_str = f"{g_issues} of {g_total}"

    out = {
        "similar_reviews_30d":         sim_rev,
        "total_reviews_30d":           tot_rev,
        "similar_support_queries_30d": sim_sup,
        "total_support_queries_30d":   tot_sup,
        "review_ratio":                review_ratio,
        "support_ratio":               support_ratio,
        "escalation":                  escalation,
        "rating_15d": {"avg": avg_15d, "n": n_15d or 0},
        "rating_30d": {"avg": avg_30d, "n": n_30d or 0},
        "vid_completion_rate":         vid_completion_rate,
        "vidCompletionRate":           vid_completion_rate_str,
        "same_day_same_vid":           same_day_same_vid,
        "sameDaySameVidIssues":        same_day_same_vid_str,
        "_computed_for_l2": l2,
        "_computed_at":     datetime.now(timezone.utc).isoformat(),
    }

    log.info(
        f"[insights] tid={tid} vid={vid} l2={l2!r} "
        f"similar_reviews={sim_rev}/{tot_rev} similar_queries={sim_sup}/{tot_sup} "
        f"ratio_r={review_ratio} ratio_s={support_ratio} "
        f"rating_15d={avg_15d} rating_30d={avg_30d} escalation={escalation} "
        f"completion={vid_completion_rate_str} sameday={same_day_same_vid_str}"
    )
    return out
