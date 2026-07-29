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
     fulfilment_STATUS is Completed or Dirty, over every booking the dashboard
     counts. The old code read fulfilment_type, which matched no column at all.
     The denominator is bounded by the tile's own filters - status is not
     Uncaptured/Dummy, completion type is not Cancelled Fraudulent/Dummy - and
     by _GUEST_CANCEL_TYPES, because a guest cancelling says nothing about
     whether the vendor can fulfil. Amended DOES count in it. completion_type
     is otherwise only used to identify Unfulfilled, as unfulfilled_rate does.
     Computed at VID and TGID scope, because ops judges an experience on its
     TGID. Note the Looker tile compares 28 complete days; the picker here is
     7/30/90, so a number will not tie out against that tile by default.
  4. vendor_id comes from fct_fulfilments on the review queries and from
     fct_bookings on the booking and support queries - mirroring Looker, which
     mixes the two.
  5. Support queries are filtered on Looker's derived query_category, not on
     the raw query_tag column, and the NAR bucket is excluded from both the
     numerator and the denominator. Chat Abandoned is computed from `tags` and
     is not a query_tag value at all, so the old exclusion matched nothing.
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
def _norm_sql(expr: str) -> str:
    """
    BigQuery-side twin of _norm(): lowercase, collapse runs of whitespace, trim.

    Both sides of every taxonomy comparison go through this. Without it the
    warehouse is compared literally while the verification probe compares
    normalised - so the probe reports a tag as live and production quietly
    fails to match it. The tag values carry double spaces
    ("Ticket Redemption Details  Sp Information"), which makes that gap a
    question of when rather than whether.
    """
    return f"TRIM(REGEXP_REPLACE(LOWER({expr}), r'\\s+', ' '))"


def _norm(s) -> str:
    """Python-side twin of _norm_sql(). Keep the two in step."""
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def _l2_key(s) -> str:
    """
    Lookup key for the bucket mapping.

    Case-folded, whitespace-collapsed, and insensitive to spacing around a
    slash. That last part is not cosmetic: the classifier emits
    "Content - Instructions not clear / Misleading Info" while the Looker
    mapping and the warehouse both write it without the spaces. Compared
    literally, that L2 misses its own bucket, falls through to the fallback,
    and matches nothing - it reported zero similar reviews for every booking.
    """
    return re.sub(r"\s*/\s*", "/", _norm(s))


_L2_TO_BUCKET = {_l2_key(v): b for b, vs in _L2_BUCKETS.items() for v in vs}


# Our L2 -> extra spellings that fct_reviews actually stores.
#
# Kept separate from _L2_BUCKETS on purpose. Those buckets are Looker's
# parent_l2_bucket, ported verbatim, and are worth leaving exactly as Looker
# defines them. This is the other half of the problem: fct_reviews.issues is
# written by Headout's own review classifier, which is a DIFFERENT vocabulary
# from the L1/L2 framework this system classifies into. They overlap, they are
# not the same list, and where they diverge the review count reads zero however
# correct the bucket mapping is.
#
# Every entry here must be a spelling confirmed present in the warehouse -
# tools/map_l2.py lists them with counts. Guessing puts us back where we
# started, matching a string nobody stores.
_L2_LIVE_ALIASES: dict = {
    # --- guide issues -----------------------------------------------------
    # Looker's parent_l2_bucket collapses every guide complaint into one, so
    # all three of these L2s returned the same 1,714 and "Guide No Show" read
    # 1,714 when three reviews are actually guide-no-show. Split, because on an
    # RCA the question is whether THIS failure recurs, not whether guides are
    # generally a theme.
    "Guide No Show":          ["Guide no show"],                        # 3
    "Guide Behaviour Issues": ["Guide Behaviour Issues",
                               "Guide Service Issues"],                 # 437 + 17
    "Guide providing irrelevant/inexperienced/not clear": [
        "Guide providing irrelevant/inexperienced/not clear"],           # 1,257

    # --- everything below is confirmed live, counts over 180 days ---------
    "Ticket Issues":          ["Ticket Issues",
                               "Ticket & Booking Issues"],              # 3,991 + 46
    "Content - Instructions not clear / Misleading Info": [
        "Content - Instructions not clear / Misleading Info",
        "Content - Instructions not clear/misleading info"],             # 3,813 + 2
    "Meeting Point Issues":   ["Meeting Point Issues",
                               "Meeting Point Issue"],                  # 1,932 + 3
    "Customer Support Issues": ["Customer Support Issues",
                               "Customer Support",
                               "Unresponsive / No Reply",
                               "Refund Not Processed"],                 # 1,839 +17+4+4
    "Pricing Issues":         ["Pricing Issues"],                       # 1,786
    "Venue Overcrowding (Venue)": ["Venue Overcrowding (Venue)",
                                   "Queue & Crowd Management",
                                   "Long waiting time"],                # 1,734 +18+2
    "Audio Guide Issues":     ["Audio Guide Issues",
                               "Audio Guide App"],                      # 1,488 + 15
    "Venue facility issue":   ["Venue facility issue",
                               "Venue Conditions"],                     # 1,038 + 2
    "Vague review":           ["Vague review"],                         # 929
    "General negative exp":   ["General negative exp"],                 # 897
    "Rating Mismatch":        ["Rating Mismatch"],                      # 647
    "Timing Issues":          ["Timing Issues"],                        # 526
    "Tour Cancelled by Operator": ["Tour Cancelled by Operator"],       # 462
    "Customer Error":         ["Customer Error"],                       # 324
    "Customer Late":          ["Customer Late"],                        # 323
    "Negative Headout":       ["Negative Headout"],                     # 298
    "Force Majeure":          ["Force Majeure"],                        # 188
    "Weather Related":        ["Weather Related"],                      # 186
    "Food & Catering":        ["Food & Catering", "Food Issue"],        # 152 + 1
    "Seating Issues":         ["Seating Issues"],                       # 131
    "Gibberish / Profanity":  ["Gibberish / Profanity"],                # 117
    "Inventory Listing Issue": ["Inventory Listing Issue"],             # 45
    "Venue Overcrowding (External)": ["Venue Overcrowding (External)"],  # 36
    "Guide Left / Abandoned Tour": ["Guide Left / Abandoned Tour"],     # 35
    "Venue closure":          ["Venue Closure", "Venue Cancelled"],     # 2 + 1
    "App and Website Issues": ["App and website issues"],               # 1

    # Live but deliberately unmapped, pending a decision on where they belong:
    #   Staff behavior / Staff Behavior  3   venue staff or guide?
    #   Logistical Issues                2
    #   Language Issue                   1   tour language, or support language?
    #   Management & Scheduling Issue    1
    #   Partner unaware of Headout       1
    #   No Issue                         3   not an issue - correctly unmapped
    #   Manual Review Required           3   a workflow state, not an issue
    #
    # No live counterpart at all, so these are honestly zero:
    #   Customer expectation mismatch
    #   Sold Free / Discounted Admission
}
_L2_LIVE_ALIASES = {_l2_key(k): v for k, v in _L2_LIVE_ALIASES.items()}


def l2_variants(l2: str | None) -> list:
    """
    Every spelling of the issue the classifier named.

    The values returned are the RAW spellings, only case-folded - they are
    compared against the warehouse column, so they have to look like what is
    stored. Only the lookup is slash-insensitive.

    The L2's own name is always included, so an L2 that is in a bucket can
    still match itself, and one that is in no bucket matches its own name
    rather than nothing.
    """
    key = _l2_key(l2)
    if not key:
        return []
    out = {_norm(l2)}
    aliases = _L2_LIVE_ALIASES.get(key)
    if aliases:
        # An explicit alias wins over the bucket. The buckets are Looker's
        # parent_l2_bucket and are deliberately coarse - they exist to group a
        # theme for reporting. On an RCA the question is narrower: does THIS
        # failure recur for THIS experience. Where we have named the exact
        # spellings, use them and nothing else.
        out |= {_norm(v) for v in aliases}
    else:
        bucket = _L2_TO_BUCKET.get(key)
        if bucket:
            out |= {_norm(v) for v in _L2_BUCKETS[bucket]}
    return sorted(out)


# query_category, verbatim from the fct_support_queries LookML:
#
#   CASE WHEN tags IN ("CHATBOT, CHATBOT-TRANSFER", "CHATBOT-TRANSFER, CHATBOT")
#        THEN "Chat Abandoned" ELSE query_tag END
#
# "Chat Abandoned" is DERIVED from the tags column - it is not a value query_tag
# ever holds. The old code excluded it by matching query_tag = "Chat Abandoned",
# which matched no row at all, so every abandoned chatbot session stayed in the
# support denominator and pushed the support ratio down.
_QUERY_CATEGORY_SQL = (
    'CASE WHEN sq.tags IN ("CHATBOT, CHATBOT-TRANSFER", "CHATBOT-TRANSFER, CHATBOT") '
    'THEN "Chat Abandoned" ELSE sq.query_tag END'
)

# Contacts that are not a guest reaching out. This is the NAR bucket from the
# fcr_dashboard LookML, which is broader than the four values this file used to
# list - auto-resolved sessions, missed chats, outbound calls and vendor email
# are all counted as support contacts otherwise.
#
# Matched as a regex, as Looker does, so "NAR" catches the several spellings in
# the data rather than one. (?i) is ours: the column is not case-consistent, and
# the old list's "Nar" would not have matched a stored "NAR".
_NAR_PATTERN = (
    r"(?i)Blank Call/no Response|Chat Abandoned|Missed Chat|"
    r"Out Call|Vendor Query|Vendor Ticket Email|Outbound Call|NAR|"
    # Below are ours, not Looker's: families that are not a guest raising an
    # issue and that Looker's NAR list does not cover.
    #
    # On row counts these look enormous - "No Customer Interaction" alone is
    # 93,187 rows over 180 days, a fifth of the table. They are not, for this
    # metric. 91,928 of those 93,187 carry no booking_id (98.6%), so they never
    # survive the join to fct_bookings and were never in the denominator, which
    # counts DISTINCT booking_id. Measured on a real vendor the exclusion moved
    # the total by zero.
    #
    # Excluded anyway, because correct is correct and the ones that DO carry a
    # booking id would otherwise count as support contacts. But do not read the
    # row counts below as the size of the correction - they are the size of the
    # family, and most of it never reached this query.
    r"No Customer Interaction|"          # 93,187 - by its own name, no contact
    r"Bulk Resolve Email Temp|"          #  3,351 - a bulk close, not a contact
    r"Issue Not Specified/dropped Midway|"  # 1,176 - same shape as Chat Abandoned
    r"Test Chat|Test Call|"              #    491 - internal testing
    r"Bug Alert|"                        #    295 - a system alert, not a guest
    r"Email Deflected|"                  #    143 - deflected before a human
    r"Agentsforce Missed|"               #     62
    r"Minded Fallback"                   #      4 - a bot handoff state
)

# Deliberately NOT excluded, though they are unusual:
#   Fraudulent            929  a real interaction, and a real outcome
#   Reserve Now Pay Later 768  a genuine query about a payment option
#   (blank)             2,076  untagged, but a contact happened - IFNULL keeps it

# "Auto resolved" is dropped from the pattern above and handled as a column.
# Measured over 180 days: 34,241 rows have is_auto_resolved = TRUE and ZERO
# have a query_tag matching "Auto resolved", so the regex term was dead and
# every one of those contacts stayed in the denominator. Same failure as
# Chat Abandoned - a Looker bucket name is not always a stored value.
#
# The rest of the pattern is confirmed live against the same 180 days:
#   Chat Abandoned 10,122 | Nar 7,995 | Vendor Query 2,979 | Out Call 1,888
#   Missed Chat Messaging 1,842 | Blank Call/no Response 1,181
#   Vendor Ticket Email 32
# and nothing it catches is a guest contact - the "Narrative Issue" substring
# over-match that was worried about does not exist in the data.

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

# What the Looker tile actually filters out, read off the dashboard itself:
#
#   Fulfilment Status  is not   Uncaptured, Dummy
#   Completion Type    is not   Cancelled Fraudulent, Dummy
#
# These bound the DENOMINATOR and settle two questions that were open for a
# while: Amended and Cancelled By Customer are NOT in either list, so both stay
# in the total. A guest cancelling is still a booking the rate is measured over.
# Uncaptured and Dummy are not real bookings, and Cancelled Fraudulent is not a
# fulfilment failure - counting any of them would drag the rate down for
# something the vendor did not do.
# Measured 2026-07-29, all of fct_fulfilments:
#   Completed 20,170,864 | Cancelled 850,594 | Pending 79,277
#   Dummy 43,483 | Dirty 39,479
#
# "Uncaptured" is not among them. That half of the Looker filter matches
# nothing and is kept only so this list still reads as the tile does.
#
# The values also settle how the rate is defined. Excluding Dummy and counting
# Completed+Dirty gives 95.60% across the table. Treating the filter itself as
# the numerator - everything that is "not Dummy" - gives 99.79%, which is the
# same answer for every vendor and therefore no signal at all. Cancelled
# (850,594) and Pending (79,277) are not completions, and leaving them in the
# numerator is what would make the number meaningless.
_FF_EXCLUDED_STATUSES         = ["Uncaptured", "Dummy"]
_FF_EXCLUDED_COMPLETION_TYPES = ["Cancelled Fraudulent", "Dummy"]

# A guest cancelling is a guest issue. It is not a fulfilment the vendor failed
# to deliver, so it is out of the completion rate entirely - numerator and
# DENOMINATOR - not merely hidden from the list of reasons.
#
# Hiding it from the reasons alone is what produced the bug this fixes: on TGID
# 22238 the tile read "57.1% of 7" with an empty breakdown, because the three
# non-completions were all Cancelled By Customer and were filtered out of the
# explanation while still counting against the rate. A shortfall that cannot be
# explained is a shortfall that should not have been counted.
#
# Normalised, and compared through _norm_sql on the warehouse side, so a
# difference of case or spacing in the stored value cannot quietly reinstate
# one of these. One list, both sides.
_GUEST_CANCEL_TYPES = ["Cancelled By Customer", "Cancelled By Guest",
                       "Customer Error", "Change Of Plans"]

# The tile compares "the last 28 COMPLETE days" - today is excluded because it
# is still running. The window predicate is already strictly less than the
# anchor date, so complete-day semantics hold without a special case.

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

# Escalation is deliberately not computed. It was a boolean over these two
# ratios, and no threshold for it survived contact with the data:
#
#   Looker compares review_ratio > 0.15 and support_ratio > 15. Both come out
#   of safe_divide as fractions, so the support test can never fire - Looker's
#   escalation is review-ratio-only. Reading that as a typo and using 0.15 for
#   both fired on every booking measured: real support ratios run 0.23-0.38, so
#   a 15% bar is below the baseline rather than above it, and a flag that is
#   always true carries no information.
#
# review_ratio and support_ratio are still returned. Whoever sets a threshold
# next should set it from the distribution, not from the LookML.


def _zero_result(l2: str | None, wd: int = 30, why: str = "",
                 visit_date: str = "") -> dict:
    """
    The shape returned when there is nothing to compute - no confirmed booking,
    or BigQuery not live. A normal state, not a failure.

    It carries _window_days like the real result does. Without it the response
    contract differs between the two paths: the cache key never matches so
    every request recomputes nothing, and a caller cannot tell which window a
    zero belongs to. _zeroed_because says why, so a zero on the dashboard can
    be explained rather than guessed at.
    """
    return {
        "similar_reviews_30d":         0,
        "total_reviews_30d":           0,
        "similar_support_queries_30d": 0,
        "total_support_queries_30d":   0,
        "total_bookings_30d":          0,
        "review_ratio":                0.0,
        "support_ratio":               0.0,
        "rating_tgid":   {"avg": None, "n": 0},
        "rating_tidvid": {"avg": None, "n": 0},
        "rating_15d":    {"avg": None, "n": 0},
        "rating_30d":    {"avg": None, "n": 0},
        "redemption":                  None,
        "ff_vid":                      None,
        "ff_tgid":                     None,
        "ff_same_day":                 None,
        "vid_completion_rate":         None,
        "tgid_completion_rate":        None,
        "tgid_incomplete_why":         [],
        "vid_incomplete_why":          [],
        "vidCompletionRate":           "N/A",
        "same_day_same_vid":           None,
        "sameDaySameVidIssues":        "N/A",
        "_computed_for_l2": l2,
        "_window_days":     wd,
        # The group headings read these. Leaving them off the zero path meant a
        # review with no booking rendered "Reviews 0" under a heading with no
        # date range at all - which looks like the range failed to load rather
        # than like there was nothing to count. The range is known here; it
        # comes from the picker and the visit date, neither of which depends on
        # the booking resolving.
        "_window_label":    (f"{wd} days before {visit_date}" if visit_date
                             else f"last {wd} days"),
        "_anchored_on":     visit_date or "today",
        "_zeroed_because":  why,
        "_failed_queries":  [],
        "_failed_detail":   {},
        "_computed_at":     datetime.now(timezone.utc).isoformat(),
    }


class _Failed(list):
    """
    An empty result that remembers it is empty because the query BROKE.

    Swallowing a BigQuery error into a plain [] made a failure indistinguishable
    from an honest zero, and the dashboard states honest zeros as facts: "no
    negative reviews", "no bookings", "no contacts". So a syntax error, a
    permissions problem or a warehouse outage rendered as an affirmative claim
    about the vendor - the worst possible reading, and one an associate would
    act on.

    It subclasses list so every `isinstance(res, list)` check downstream keeps
    working and nothing has to be rewritten to be safe; it only adds the reason
    for anything that cares to look.
    """

    def __init__(self, error):
        super().__init__()
        self.error = str(error)[:300]


async def _run(sql: str, params: dict) -> list:
    from server.services.bq_connector import run_query_async
    try:
        return await run_query_async(sql, params)
    except Exception as e:
        log.warning(f"[insights] query failed: {e}")
        return _Failed(e)


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

    # A booking id is enough. tid, vid and tgid are one lookup away in
    # fct_bookings, and verify_bid already does exactly that - it is what
    # resolves a booking everywhere else in this system. Returning zeros
    # because the draft happened not to carry them was answering a question
    # nobody asked: the ids are derivable, so derive them.
    bid = str(booking.get("bid") or booking.get("bookingId")
              or booking.get("id") or "").strip()
    if not (tid and vid and tgid) and bid.isdigit():
        try:
            from server.services.bigquery_patch import verify_bid
            loop = asyncio.get_running_loop()
            resolved = await loop.run_in_executor(None, verify_bid, bid)
        except Exception as e:
            log.warning(f"[insights] verify_bid({bid}) failed: {e}")
            resolved = None
        if resolved:
            tid  = tid  or str(resolved.get("tid") or "").strip()
            vid  = vid  or str(resolved.get("vid") or "").strip()
            tgid = tgid or str(resolved.get("tgid") or "").strip()
            visit_date = visit_date or str(resolved.get("date_of_visit") or "").strip()
            log.info(f"[insights] resolved {bid} -> tid={tid} vid={vid} tgid={tgid}")

    # Only give up when there is nothing at all to key on.
    #
    # This used to bail whenever tid or vid was missing, which threw away three
    # metrics that never needed tid: the TGID rating keys on tgid alone, and
    # both fulfilment rates key on tgid or vid alone. A booking that resolved
    # far enough to know its experience and vendor can always answer "how is
    # this experience rated" and "how often does this vendor fulfil" - those
    # are properties of the vendor, not of this booking's issue, and reporting
    # them as zero says something false about a vendor rather than nothing.
    if not tid and not vid and not tgid:
        log.warning("[insights] no tid, vid or tgid - returning zeros")
        return _zero_result(l2, wd,
                            f"booking {bid} did not resolve in fct_bookings"
                            if bid else "no booking id on the draft",
                            visit_date)

    # The issue-comparison half needs BOTH tid and vid: every one of those
    # queries filters on the pair, and running them with half of it would
    # compare this booking against the wrong population.
    pair = bool(tid and vid)
    if not pair:
        log.info("[insights] tid/vid incomplete - vendor-level metrics only")

    if not is_live("bigquery") or MOCK_MODE:
        return _zero_result(l2, wd,
                            "MOCK_MODE" if MOCK_MODE else "BigQuery not connected",
                            visit_date)

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
  AND {_norm_sql('l2v')} IN UNNEST(@l2v)
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
  AND NOT sq.is_auto_resolved
  AND NOT REGEXP_CONTAINS(IFNULL({_QUERY_CATEGORY_SQL}, ''), @nar)
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
    #
    # Both follow the window picker, like every other metric in the panel.
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
    # The dashboard's own filters, off the Looker tile:
    #   Fulfilment Status is not  Uncaptured, Dummy
    #   Completion Type  is not   Cancelled Fraudulent, Dummy
    # These bound the DENOMINATOR. Amended stays in the total; guest
    # cancellations do not - see _GUEST_CANCEL_TYPES. A guest cancelling says
    # nothing about whether the vendor can fulfil, so counting it against the
    # vendor's completion rate measures the guest.
    #
    # IFNULL because the join is LEFT: a booking with no fulfilment row has a
    # NULL status, and NULL NOT IN (...) is NULL, which would drop the row.
    # Looker's "is not" keeps nulls, and so does this.
    _ff_excl = f"""
  AND IFNULL(f.fulfilment_status, '') NOT IN UNNEST(@ff_skip_status)
  AND IFNULL(f.completion_type,   '') NOT IN UNNEST(@ff_skip_ctype)
  AND {_norm_sql("IFNULL(f.completion_type, '')")} NOT IN UNNEST(@ff_guest)"""
    sql_h  = f"{_ff_select}WHERE b.vendor_id = @vid AND {_win}{_ff_excl}"
    # Ops checks fulfilment for the same TGID over the last four weeks, because
    # that is the population that says whether this is a one-off or a pattern.
    sql_h2 = f"{_ff_select}WHERE b.experience_id = @tgid AND {_win}{_ff_excl}"
    # --- I: same-day, same issue -------------------------------------------
    # Not a fulfilment rate. The question is how many OTHER guests visiting
    # this vendor on this same date raised the SAME issue - a cluster on one
    # date is a bad day at the venue, which is a different finding from a
    # vendor that is steadily poor. Reviews and support contacts are counted
    # separately because they are different evidence: a review is written
    # afterwards, a support contact happened during.
    # Why the rest did not complete. A rate of 57% says three of seven failed
    # and stops there, which is the least useful place to stop on an RCA: the
    # next question is always whether those failures look like the one in this
    # review. Cancelled by the vendor is a supply problem, Pending is a
    # fulfilment backlog, and they lead somewhere different.
    _ff_why = f"""
SELECT
  IFNULL(f.fulfilment_status, '(none)') AS status,
  IFNULL(f.completion_type,  '(none)')  AS ctype,
  COUNT(DISTINCT b.booking_id)          AS c
FROM `{_BOOKINGS_TABLE}` b
LEFT JOIN `{_FULFILMENTS_TABLE}` f ON b.booking_id = f.booking_id
WHERE {{scope}} AND {_win}{_ff_excl}
  AND IFNULL(f.fulfilment_status, '') NOT IN UNNEST(@ff_done)
GROUP BY status, ctype
ORDER BY c DESC
LIMIT 6
"""
    sql_why_tgid = _ff_why.format(scope="b.experience_id = @tgid")
    sql_why_vid  = _ff_why.format(scope="b.vendor_id = @vid")

    sql_i_rev = f"""
SELECT COUNT(DISTINCT r.booking_id) AS c,
       ARRAY_AGG(DISTINCT CAST(r.booking_id AS STRING) LIMIT 20) AS ids
{_reviews_from}
LEFT JOIN UNNEST(r.issues) AS iss
LEFT JOIN UNNEST(iss.l2_issues) AS l2v
WHERE f.vendor_id = @vid
  AND DATE(b.experience_date) = {anchor_sql}
  AND {_norm_sql('l2v')} IN UNNEST(@l2v)
"""
    sql_i_sup = f"""
SELECT COUNT(DISTINCT sq.booking_id) AS c,
       ARRAY_AGG(DISTINCT sq.booking_id LIMIT 20) AS ids
{_support_from}
WHERE b.vendor_id = @vid
  AND DATE(b.experience_date) = {anchor_sql}
  AND NOT sq.is_auto_resolved
  AND NOT REGEXP_CONTAINS(IFNULL({_QUERY_CATEGORY_SQL}, ''), @nar)
  AND {_norm_sql(_QUERY_CATEGORY_SQL)} IN UNNEST(@tags)
"""
    # Every booking on that date for the same vendor - the denominator both
    # counts are read against.
    sql_i_tot = f"""
SELECT COUNT(DISTINCT b.booking_id) AS c
FROM `{_BOOKINGS_TABLE}` b
WHERE b.vendor_id = @vid AND DATE(b.experience_date) = {anchor_sql}
"""

    nar_par  = {"nar": _NAR_PATTERN}
    ff_par   = {"ff_done":        ("STRING", _FF_COMPLETED_STATUSES),
                "ff_skip_status": ("STRING", _FF_EXCLUDED_STATUSES),
                "ff_skip_ctype":  ("STRING", _FF_EXCLUDED_COMPLETION_TYPES),
                "ff_guest":       ("STRING", [_norm(t)
                                              for t in _GUEST_CANCEL_TYPES])}

    # A and C need a tag/L2 mapping. Without one they are skipped and the rest
    # still run - a missing framework should cost you two numbers, not all of
    # the insights.
    # Also skipped without both ids: A and C filter on the tid/vid pair, so
    # without it there is no issue comparison to make. Decided here rather than
    # at the gather, because building a coroutine and then discarding it leaves
    # it un-awaited and Python warns about it.
    # Two gates, not one. They used to share `skip_ac = tags_spec is None or
    # not pair`, so an L1/L2 pair with no SUPPORT-tag mapping also suppressed
    # the REVIEWS query - and 11 of 32 pairs are deliberately unmapped on the
    # support side. Those reviews tiles read "0 of 47 - 0.0%" as a fact while
    # l2_variants held live aliases matching thousands of rows, and the
    # same-day review tile (gated on variants alone) contradicted it on the
    # same screen.
    #
    # The reviews query needs the tid/vid pair and L2 variants. The support
    # query needs the tid/vid pair and the tag map. Neither needs the other's
    # input.
    variants = l2_variants(l2)
    skip_a = not pair or not variants
    skip_c = not pair or tags_spec is None
    coro_a = (asyncio.sleep(0) if skip_a
              else _run(sql_a, {**base, "l2v": ("STRING", variants)}))

    if skip_c:
        coro_c = asyncio.sleep(0)
    elif isinstance(tags_spec, list):
        sql_c = f"""
SELECT COUNT(DISTINCT sq.booking_id) AS c
{_support_from}{_support_where}
  AND {_norm_sql(_QUERY_CATEGORY_SQL)} IN UNNEST(@tags)
"""
        coro_c = _run(sql_c, {**base, **nar_par,
                              "tags": ("STRING", [_norm(t) for t in tags_spec])})
    else:
        pats = tags_spec.get("like_any", [])
        if pats:
            ors = " OR ".join(f"{_norm_sql(_QUERY_CATEGORY_SQL)} LIKE @pat{i}"
                              for i in range(len(pats)))
            sql_c = f"""
SELECT COUNT(DISTINCT sq.booking_id) AS c
{_support_from}{_support_where}
  AND ({ors})
"""
            coro_c = _run(sql_c, {**base, **nar_par,
                                  **{f"pat{i}": _norm(p) for i, p in enumerate(pats)}})
        else:
            coro_c, skip_c = asyncio.sleep(0), True

    results = await asyncio.gather(
        coro_a,
        _run(sql_b, base)                            if pair else asyncio.sleep(0),
        coro_c,
        _run(sql_d, {**base, **nar_par})             if pair else asyncio.sleep(0),
        _run(sql_e_tidvid, base)                     if pair else asyncio.sleep(0),
        _run(sql_e_tgid, {**anchor_par, "tgid": tgid}) if tgid else asyncio.sleep(0),
        _run(sql_f, base)                            if pair else asyncio.sleep(0),
        _run(sql_g, {"tid": tid, "vid": vid})        if pair else asyncio.sleep(0),
        _run(sql_h, {**base, **ff_par})              if vid else asyncio.sleep(0),
        _run(sql_h2, {**anchor_par, **ff_par, "tgid": tgid}) if tgid else asyncio.sleep(0),
        (_run(sql_why_tgid, {**anchor_par, **ff_par, "tgid": tgid})
         if tgid else asyncio.sleep(0)),
        (_run(sql_why_vid, {**base, **ff_par}) if vid else asyncio.sleep(0)),
        (_run(sql_i_rev, {**anchor_par, "vid": vid, "l2v": ("STRING", variants)})
         if (visit_date and vid and variants) else asyncio.sleep(0)),
        (_run(sql_i_sup, {**anchor_par, "vid": vid, **nar_par,
                          "tags": ("STRING", [_norm(t) for t in tags_spec])})
         if (visit_date and vid and isinstance(tags_spec, list)) else asyncio.sleep(0)),
        (_run(sql_i_tot, {**anchor_par, "vid": vid})
         if (visit_date and vid) else asyncio.sleep(0)),
        return_exceptions=True,
    )

    # Which queries BROKE, as opposed to returning nothing. _run hands back a
    # _Failed for a query that raised, and gather itself can hand back an
    # Exception. Both look like "no rows" to every _count below, and the
    # dashboard renders no rows as an affirmative "no negative reviews" / "no
    # bookings". Naming them lets the tiles that depend on a broken query say
    # nothing instead of saying something false.
    _RESULT_NAMES = (
        "reviews", "reviews_total", "support", "support_total",
        "rating_tidvid", "rating_tgid", "bookings", "redemption",
        "completion_vid", "completion_tgid", "incomplete_tgid",
        "incomplete_vid", "same_day_reviews", "same_day_support",
        "same_day_total",
    )
    failed, failed_detail = [], {}
    for _i, _res in enumerate(results):
        _name = _RESULT_NAMES[_i] if _i < len(_RESULT_NAMES) else f"q{_i}"
        if isinstance(_res, BaseException):
            failed.append(_name)
            failed_detail[_name] = f"{type(_res).__name__}: {_res}"[:300]
        elif isinstance(_res, _Failed):
            failed.append(_name)
            failed_detail[_name] = _res.error
    if failed:
        log.error(f"[insights] {len(failed)} queries failed: {failed_detail}")

    sim_rev = 0 if skip_a else _count(results[0])
    _l2_variant_count = len(variants)
    tot_rev = _count(results[1])
    sim_sup = 0 if skip_c else _count(results[2])
    tot_sup = _count(results[3])
    tot_bkg = _count(results[6])

    def _safe_div(n, d) -> float:
        return round(n / d, 4) if d else 0.0

    review_ratio  = _safe_div(sim_rev, tot_rev)
    support_ratio = _safe_div(sim_sup, tot_sup)

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
    # A guest changing their mind is not a fulfilment failure, and listing it
    # first drowns the reasons that are. "98 Cancelled By Customer · 51
    # Cancelled By Vendor" reads as though the vendor is fine; the 51 is the
    # story. Guest-driven cancellations are counted separately, not in the list.
    # The warehouse already drops these - _ff_excl excludes them from every
    # fulfilment query, so they cannot reach the denominator or this list.
    # Kept as a second pass because it matches on the combined status/type
    # label too, and because a stored spelling outside _GUEST_CANCEL_TYPES
    # would otherwise reappear here as a reason the vendor failed.
    _GUEST_CANCELS = tuple(_norm(t) for t in _GUEST_CANCEL_TYPES) + (
        "cancelledguest", "cancelledcustomer")

    def _why_rows(res):
        """Non-completions grouped by status and completion type, largest first."""
        if not isinstance(res, list):
            return []
        out, guest = [], [0]
        for r in res:
            n = int(_fld(r, "c") or 0)
            if not n:
                continue
            st = str(_fld(r, "status") or "").strip()
            ct = str(_fld(r, "ctype") or "").strip()
            # completion_type is the specific reason where it has one;
            # fulfilment_status is the coarse bucket. Show the specific one
            # unless it says nothing the status has not already said.
            label = ct if ct and ct not in ("(none)", st) else st
            if _norm(label) in _GUEST_CANCELS:
                guest[0] += n
                continue
            # A row with neither a status nor a completion type has no reason
            # to give. It rendered as a bare count against an empty chip -
            # "3 " - which reads as a label that failed to load rather than as
            # a booking the warehouse cannot classify.
            if not label or label == "(none)":
                out.append({"reason": "no fulfilment record", "status": st,
                            "type": ct, "count": n})
                continue
            out.append({"reason": label, "status": st, "type": ct, "count": n})
        return out

    why_tgid = _why_rows(results[10])
    why_vid  = _why_rows(results[11])
    def _ids(res):
        row = res[0] if isinstance(res, list) and res else None
        vals = _fld(row, "ids") or []
        return [str(v) for v in vals if v]

    day_rev = _count(results[12])
    day_sup = _count(results[13])
    day_tot = _count(results[14])
    day_rev_ids = _ids(results[12])
    day_sup_ids = _ids(results[13])

    out = {
        "similar_reviews_30d":         sim_rev,
        "total_reviews_30d":           tot_rev,
        "similar_support_queries_30d": sim_sup,
        "total_support_queries_30d":   tot_sup,
        "total_bookings_30d":          tot_bkg,
        "review_ratio":                review_ratio,
        "support_ratio":               support_ratio,
        "rating_tgid":                 rating_tgid,
        "rating_tidvid":               rating_tidvid,
        "redemption":                  redemption,
        "ff_vid":                      ff_vid,
        "ff_tgid":                     ff_tgid,
        "ff_same_day":                 None,
        # The dashboard still reads these names. They were never windows - the
        # TGID tile reads rating_15d and the TID.VID tile reads rating_30d -
        # so they are aliased to the right scope rather than renamed, and both
        # respect whichever window the associate picked.
        "rating_15d": rating_tgid,
        "rating_30d": rating_tidvid,
        # TGID is the headline: the tile asks how this EXPERIENCE is being
        # fulfilled, which is the population Looker's filters describe. VID is
        # kept alongside it, because a vendor failing across every experience
        # is a different finding from one experience going wrong.
        "tgid_completion_rate":  ff_tgid["rate"],
        "tgid_incomplete_why":   why_tgid,
        "vid_incomplete_why":    why_vid,
        "vid_completion_rate":   ff_vid["rate"],
        "vidCompletionRate":     _pct(ff_vid["rate"]),
        # Same visit date, same vendor, same issue - split by evidence type.
        # "total" is every booking that vendor served that day, so a count can
        # be read as a share of the day rather than as a bare number.
        # Booking ids alongside the counts: "2 reviews" is a number, and the
        # two booking ids are something an associate can open.
        "same_day": {"reviews": day_rev, "support": day_sup, "total": day_tot,
                     "review_ids": day_rev_ids, "support_ids": day_sup_ids},
        "same_day_same_vid":     ({"issues": day_rev + day_sup, "total": day_tot}
                                  if day_tot else None),
        "sameDaySameVidIssues":  (f"{day_rev} reviews / {day_sup} support of "
                                  f"{day_tot} bookings" if day_tot else "N/A"),
        "_window_days":     wd,
        "_anchored_on":     visit_date or "today",
        # Spelled out because "1 of 8" says nothing about which 8. Every count
        # in the recurrence group covers this range.
        "_window_label":    (f"{wd} days before {visit_date}" if visit_date
                             else f"last {wd} days"),
        # Empty when everything was computed. Set when part of it could not be,
        # so a zero can be explained rather than read as "no history". The
        # halves are listed separately now that they are gated separately -
        # "no support-tag mapping" says nothing about the reviews count.
        "_partial_because": " · ".join(filter(None, [
            ("" if pair else "tid or vid missing - issue comparison skipped, "
                             "vendor-level metrics computed"),
            ("" if not (pair and skip_c) else
             f"no support-tag mapping for {l1 or '?'} / {l2 or '?'} - "
             "support contacts not compared"),
            ("" if not (pair and skip_a) else
             f"no L2 variants for {l2 or '?'} - reviews not compared"),
            # An honest empty population, said out loud.
            #
            # Booking 32908218 is tour 43605 / vendor 4040 on experience 22238.
            # In the 30 days before its visit that experience sold 7 bookings -
            # every one of them through tour 46590 / vendor 3753. So every
            # TID+VID tile was correctly zero and the TGID tiles correctly were
            # not, and the panel gave no way to tell that apart from a broken
            # query. Six unexplained zeros next to a working completion rate is
            # a bug report waiting to happen; it took a warehouse round trip to
            # rule out, and the answer was in the numbers already on screen.
            ("" if not (pair and not tot_bkg and (ff_tgid or {}).get("total"))
             else f"this tour and vendor had no bookings in this window - the "
                  f"experience had {ff_tgid['total']} through others, which is "
                  f"what the TGID tiles count"),
        ])),
        # Which queries broke. A tile whose query is in here has no number, and
        # must not be rendered as a zero: "no negative reviews" is a claim
        # about the vendor, and a failed query is not evidence for it.
        "_failed_queries":  failed,
        "_failed_detail":   failed_detail,
        "_computed_for_l2": l2,
        "_l2_variants":     _l2_variant_count,
        "_computed_at":     datetime.now(timezone.utc).isoformat(),
    }

    log.info(
        f"[insights] tid={tid} vid={vid} l2={l2!r} anchor={out['_anchored_on']} "
        f"window={wd}d neg_reviews={sim_rev}/{tot_rev} queries={sim_sup}/{tot_sup} "
        f"bookings={tot_bkg} ratio_r={review_ratio} ratio_s={support_ratio} "
        f"rating_tgid={rating_tgid['avg']} rating_tidvid={rating_tidvid['avg']} "
        f"ff_vid={_pct(ff_vid['rate'])}/bs={_pct(ff_vid['rate_by_booking_status'])}"
        f"({ff_vid['total']}) "
        f"ff_tgid={_pct(ff_tgid['rate'])}({ff_tgid['total']}) "
        f"attention={ff_tgid['needs_attention']} "
        f"redemption={'yes' if redemption else 'no'}"
    )
    return out
